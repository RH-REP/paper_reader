"""PDF の論文を「章 → 文」に分ける。

  lines = read_lines(doc)      # ページごとの行（文字層が無いページは OCR）
  paper = build_paper(doc)     # {title, sections: [{title, level, kind, sentences: [{t, s}]}]}

見出しの見分け方（フォントの情報を使う）:
- 本文の文字サイズ = 文字数で重みをつけた最頻値。これより小さい行（図表の説明、欄外、ヘッダー・フッター）は捨てる
- 見出し = 太字で、本文より 0.9pt 以上大きい短い行。続けて並ぶ見出し行は1つにつなぐ
- OCR したページはフォントが分からないので「1 INTRODUCTION」「2.1 Pupil Size」のような番号付きの行を見出しにする
- 題名 = 1ページ目でいちばん大きい文字の行
2段組は、行の左端がページ幅の 45% より右なら右の段として、左の段 → 右の段の順に並べる。
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import pysbd

TEXT_VERSION = 1               # 貼り付けたテキストの分け方の版（build_from_text）
EXTRACTOR_VERSION = 3          # 2: 同じ行の切れ端をつなぐ／本文より小さい太字の見出し（Applied Optics など）
# 3: 1段組みのページは段を分けない／短冊に分けて描かれた行と重なった文字／斜体の小節／番号と題が離れた見出し（SPIE）／
#    要旨の前の References and links／Symbol フォントの私用領域の文字／複合語のハイフン／「300 K. The」「. [4] An」で切る／
#    参考文献は1件1文（読まない）／表の中身・数式の化けを外す／上付き（rad²・引用番号）
MIN_TEXT_CHARS = 20          # これ未満のページは文字層が無いとみなして OCR
BACK_MATTER = re.compile(
    r"^(references|bibliography|literature cited|acknowledge?ments?|funding|author contributions|"
    r"data availability( statement)?|conflicts? of interest|competing interests?|declaration of competing interest|"
    r"publisher'?s note|supplementary material|ethics statement|appendix)\b", re.I)
REFERENCES = re.compile(r"^(references?( and (links|notes))?|bibliography|literature cited)\s*:?$", re.I)
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
# 番号の無い見出しでも見出しとみなす名前（本文より小さい太字のときに使う）
SECTION_NAMES = re.compile(
    r"^(abstract|introduction|background|related work|theory|methods?|materials and methods|experiments?|"
    r"experimental( setup)?|simulations?|results?( and discussion)?|discussion( and conclusions?)?|"
    r"conclusions?|summary|outlook|references?( and (links|notes))?|bibliography|acknowledge?ments?|appendix( [a-z])?)\s*:?$", re.I)
# Symbol フォントの私用領域の文字（U+F0xx）→ Unicode。下位の1バイトが Symbol の文字の番号
SYMBOL_PUA = {0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι", 0x6B: "κ",
              0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x74: "τ", 0x75: "υ", 0x77: "ω",
              0x78: "ξ", 0x79: "ψ", 0x7A: "ζ", 0x44: "Δ", 0x46: "Φ", 0x47: "Γ", 0x4C: "Λ", 0x50: "Π", 0x53: "Σ", 0x57: "Ω",
              0x2D: "−", 0x2B: "+", 0x3D: "=", 0x3C: "<", 0x3E: ">", 0xB1: "±", 0xB4: "×", 0xB0: "°", 0xB7: "•", 0xA3: "≤",
              0xB3: "≥", 0xBB: "≈", 0xB9: "≠", 0xAE: "→", 0xAC: "←", 0xA5: "∞", 0xB6: "∂", 0xD6: "√", 0xE5: "Σ", 0xF2: "∫"}
MATH_FONT = re.compile(r"math|cmmi|cmsy|cmex|standardsym|mtextra|mt ?symbol|euclid|stix", re.I)
GARBAGE = re.compile(r"[\u0700-\u0DFF]+")         # 壊れた数式のフォントの文字（NKo・タミル・マラヤーラムなど）。英語の論文には出ない
SUP_MAP = str.maketrans("0123456789+-−,()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻˒⁽⁾")
CITE_PAREN = re.compile(r"\s*\((?=[^()]*\b(?:19|20)\d{2}[a-z]?\b)[^()]*\)")   # (Smith et al., 2020; Lee 2019a)
CITE_BRACKET = re.compile(r"\s*\[\d+(?:\s*[-–,]\s*\d+)*\]")                   # [12] [3–5] [1, 4]
TESSDATA_CANDIDATES = ["/opt/homebrew/share/tessdata", "/usr/local/share/tessdata", "/usr/share/tesseract-ocr/5/tessdata"]


@dataclass
class Line:
    page: int
    col: int
    x0: float
    y0: float
    y1: float
    x1: float
    text: str
    size: float
    bold: bool
    ocr: bool
    italic: bool = False
    math: float = 0.0          # 数式のフォントの文字の割合
    lspace: bool = False       # 元の行の頭・終わりに空白があったか
    rspace: bool = False


def _is_italic(span) -> bool:
    return bool(span["flags"] & 2) or bool(re.search(r"italic|oblique|-it$|,it", span["font"], re.I))


def _is_bold(span) -> bool:
    return bool(span["flags"] & 16) or bool(re.search(r"bold|black|heavy", span["font"], re.I)) \
        or bool(re.search(r"\.BI?$", span["font"]))


def _tessdata() -> str | None:
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        return env
    return next((p for p in TESSDATA_CANDIDATES if Path(p, "eng.traineddata").exists()), None)


def read_lines(doc, on_page=None) -> tuple[list[Line], list[int]]:
    """全ページの行を読む順に返す。2つ目は OCR したページ番号（1始まり）。"""
    lines, ocr_pages = [], []
    for pno, page in enumerate(doc):
        textpage, ocr = None, False
        if len(page.get_text().strip()) < MIN_TEXT_CHARS:
            try:
                textpage = page.get_textpage_ocr(language="eng", dpi=300, full=True, tessdata=_tessdata())
                ocr = True
                ocr_pages.append(pno + 1)
            except Exception as e:  # Tesseract が無いなど。そのページは空のまま進む
                print(f"OCR できなかった（{pno + 1} ページ）: {e}")
                if on_page:
                    on_page(pno + 1, len(doc), False)
                continue
        # rawdict: 1文字ずつの位置が取れる。空白の文字が無く間隔だけ空いている PDF でも、間隔から空白を入れる
        d = page.get_text("rawdict", textpage=textpage) if textpage else page.get_text("rawdict")
        tables = [] if ocr else _table_boxes(page)
        page_lines = []
        for block in d["blocks"]:
            for l in block.get("lines", []):
                for sp in l["spans"]:
                    sp["text"] = ""
                text = _line_text(l["spans"])
                spans = [sp for sp in l["spans"] if sp["text"].strip()]
                if not spans:
                    continue
                lsp, rsp = text[:1].isspace(), text[-1:].isspace()   # 前後の空白（短冊の境目で語が続くかを見るのに使う）
                text = GARBAGE.sub("", text).strip()             # NFKC は _line_text で1文字ずつ（上付きを戻さないため）
                if not text:
                    continue
                x0, y0, x1, y1 = l["bbox"]
                if any(t[0] - 1 <= (x0 + x1) / 2 <= t[2] + 1 and t[1] - 1 <= (y0 + y1) / 2 <= t[3] + 1 for t in tables):
                    continue                           # 表の中身は本文に入れない（表は図として切り抜く）
                sizes = Counter()
                n_math = n_all = 0
                for sp in spans:
                    k = len(sp["text"].strip())
                    sizes[round(sp["size"], 1)] += k
                    n_all += k
                    n_math += k if MATH_FONT.search(sp["font"]) else 0
                page_lines.append(Line(pno, 0, x0, y0, y1, x1, text, sizes.most_common(1)[0][0],
                                       all(_is_bold(sp) for sp in spans), ocr, all(_is_italic(sp) for sp in spans),
                                       n_math / max(1, n_all), lsp, rsp))
        rows = _merge_rows(page_lines)
        _assign_columns(rows, page.rect.width)
        rows.sort(key=lambda ln: (ln.col, round(ln.y0), ln.x0))
        lines += rows
        if on_page:
            on_page(pno + 1, len(doc), ocr)            # 取り込みのプログレスバー用
    return lines, ocr_pages


# 最初の見出しより前（題名の下）にある、読まない行: 著作権・分類コード・受付日・キーワードなど
FRONT_NOISE = re.compile(r"(©.*$|\bOCIS codes?:.*$|\b(received|revised|accepted|posted|published)\b.*\d{4}.*$|"
                         r"\bdoi:?\s*10\.\S+|\bkeywords?:.*$|^[\d.,;\s]+$)", re.I)
# 文末に来たら、pysbd が切っても次の文とつなぐ略語（Eq. (5) / Fig. 3 / Ref. 12 など）
ABBREV_END = re.compile(r"\b(Eqs?|Figs?|Refs?|Secs?|Sects?|Tabs?|Nos?|Vol|pp|cf|ca|approx|resp|Ch|Chap)\.$")
# 「… in Fig.」「… in Table」のあとに「2. The …」が来たら、番号だけ前の文に付けて残りは次の文にする
REF_WORD_END = re.compile(r"\b(Eqs?\.|Figs?\.|Refs?\.|Secs?\.|Sects?\.|Tabs?\.|Table|Figure|Section|Equation|Chapter|Appendix)$")
NUM_THEN_SENTENCE = re.compile(r"^(\(?\d{1,3}[a-z]?\)?\.)\s+(?=[A-Z])(.+)$", re.S)
JOIN_WORD_END = re.compile(r"\b(and|or|of|to|in|the|a|an|with|for|by|from|between|vs\.?)$", re.I)
SPACE_GAP = 0.1                   # 文字の間がこの割合（× 文字の大きさ）より空いていたら空白を入れる（ふつうの字間は 0〜0.08、空白の抜けは 0.14 前後）


def _span_chars(sp) -> list[dict]:
    """Symbol などのフォントの私用領域の文字（U+F06D など）を Unicode（μ）に直した1文字ずつ。"""
    out = []
    for ch in sp.get("chars", []):
        c = ch["c"]
        o = ord(c)
        if 0xE000 <= o <= 0xF8FF:                     # 分からない私用領域の文字は捨てる（画面で見えない文字になる）
            known = 0xF020 <= o <= 0xF0FF and (re.search(r"symbol|mtextra", sp["font"], re.I) or o in (0xF0B7, 0xF06D))
            c = SYMBOL_PUA.get(o - 0xF000, "") if known else ""
        out.append({**ch, "c": c})
    return out


def _line_text(spans) -> str:
    """rawdict の1行を文字列にする。空白の文字が無くても、文字の間が広ければ空白を入れる。各 span に text も入れる。
    本文より小さく、上にずれた数字の span は上付き（²・引用番号）にする。"""
    main = max(spans, key=lambda sp: len(sp.get("chars", [])), default=None)
    base_y = main["origin"][1] if main and "origin" in main else None
    out, prev = [], None
    for s in spans:
        chars = _span_chars(s)
        raw = "".join(ch["c"] for ch in chars)
        sup = (main is not None and s is not main and base_y is not None and s["size"] < main["size"] * 0.8
               and s.get("origin", [0, base_y])[1] < base_y - main["size"] * 0.2 and re.fullmatch(r"[\d,\-−–()+ ]+", raw.strip() or "x"))
        buf = []
        for ch in chars:
            c = ch["c"]
            if not c:
                continue
            if prev is not None and c != " " and prev["c"] != " " and not sup:
                gap = ch["bbox"][0] - prev["bbox"][2]
                if gap > SPACE_GAP * s["size"]:
                    buf.append(" ")
            buf.append(c.translate(SUP_MAP) if sup else unicodedata.normalize("NFKC", c))
            prev = ch
        s["text"] = "".join(buf)
        out.append(s["text"])
    return "".join(out)


def _table_boxes(page) -> list[tuple]:
    """罫線で描かれた表の範囲（2行2列以上、ページの 6 割より小さいもの）。"""
    try:
        found = page.find_tables()
    except Exception:  # 古い PyMuPDF など
        return []
    area = page.rect.width * page.rect.height
    out = []
    for t in found.tables:
        x0, y0, x1, y1 = t.bbox
        if t.row_count >= 2 and t.col_count >= 2 and (x1 - x0) * (y1 - y0) < area * 0.6:
            out.append((x0, y0, x1, y1))
    return out


def _continues(prev: str, nxt: str) -> bool:
    """pysbd が切った2つが、本当は1つの文か。"""
    if ABBREV_END.search(prev) or JOIN_WORD_END.search(prev):
        return True                                    # 「… in Fig.」「… Figs. 5 and」
    if nxt[:1].islower():
        return True                                    # 小文字で始まる = 文の途中
    return bool(re.match(r"^[(\[]?([a-h]|[ivx]{1,4}|\d{1,3})[)\]]", nxt))   # 「(b) and (c)」「(iv) to (v)」


def _abstract(front: list[Line]) -> list[str]:
    """最初の見出しより前の行から要旨を取り出す: 続いている段落のうち、いちばん長いもの（著者名・所属・受付日などは外れる）。"""
    runs: list[list[Line]] = []
    for ln in front:
        text = FRONT_NOISE.sub("", ln.text).strip()
        if not text:
            continue
        ln = Line(ln.page, ln.col, ln.x0, ln.y0, ln.y1, ln.x1, text, ln.size, ln.bold, ln.ocr)
        p = runs[-1][-1] if runs else None
        # 行の枠は少し重なることがある（行間が詰まっている PDF）ので、少しの重なりも続きとみなす
        if p and p.page == ln.page and abs(p.size - ln.size) < 0.3 and -0.5 * ln.size <= ln.y0 - p.y1 < ln.size * 1.2:
            runs[-1].append(ln)
        else:
            runs.append([ln])
    runs = [r for r in runs if len(r) >= 2 or sum(len(x.text) for x in r) >= 120 or re.match(r"^abstract\b", r[0].text, re.I)]
    if not runs:
        return []
    labeled = [r for r in runs if re.match(r"^abstract\b", r[0].text, re.I)]
    body_size = Counter(x.size for r in runs for x in r).most_common(1)[0][0]
    # 「Abstract」で始まる塊を先に。無ければ、いちばん長い塊（文献の塊のように本文より小さい字の塊は後回し）
    best = labeled[0] if labeled else max(runs, key=lambda r: (r[0].size >= body_size - 0.3, sum(len(x.text) for x in r)))
    texts = [x.text for x in best]
    texts[0] = re.sub(r"^abstract\s*[:.—-]?\s*", "", texts[0], flags=re.I) if labeled else texts[0]
    return [t for t in texts if t]


def _merge_rows(page_lines: list[Line]) -> list[Line]:
    """同じ高さに分かれて入っている切れ端を1行につなぐ。
      ・隣り合う切れ端（「1.」と「Introduction」、span の区切り）
      ・縦の短冊に分けて描かれた行（Wolf 2018 の JWST）。境目の1文字が両側に重ねて描かれていれば1つにする
      ・番号だけの太字と、同じ高さの太字の題（SPIE の「2.1」…「Make the Mirror …」、間が字の2倍ほど）"""
    rows: list[list[Line]] = []
    for ln in sorted(page_lines, key=lambda l: (l.y0, l.x0)):
        for row in rows[::-1][:4]:
            r0 = row[0]
            if abs(r0.y0 - ln.y0) < 0.35 * max(r0.size, ln.size) and abs(r0.size - ln.size) < 1.5:
                row.append(ln)
                break
        else:
            rows.append([ln])
    out: list[Line] = []
    for row in rows:
        row.sort(key=lambda l: l.x0)
        cur = row[0]
        for ln in row[1:]:
            size = max(cur.size, ln.size)
            gap = ln.x0 - cur.x1
            text = None
            if gap < -0.1 * size and cur.text[-1:] == ln.text[:1] and ln.x0 < cur.x1:
                text = cur.text + ln.text[1:]                  # 短冊の境目で重ねて描かれた文字
            elif -0.5 * size <= gap < 0.6 * size:
                glue = " " if gap > SPACE_GAP * size or cur.rspace or ln.lspace else ""
                text = cur.text + glue + ln.text
            elif gap < 4 * size and cur.bold == ln.bold and cur.italic == ln.italic and ln.text[:1].isupper() \
                    and re.fullmatch(r"\d+(\.\d+)*\.?", cur.text.strip()):
                text = f"{cur.text.strip()} {ln.text}"          # 番号と題が離れている見出し（SPIE の太字、Optics Express の斜体）
            if text is None:
                out.append(cur)
                cur = ln
                continue
            weight_c, weight_l = len(cur.text), len(ln.text)
            cur = Line(cur.page, 0, cur.x0, min(cur.y0, ln.y0), max(cur.y1, ln.y1), max(cur.x1, ln.x1), text,
                       cur.size if weight_c >= weight_l else ln.size, cur.bold and ln.bold, cur.ocr,
                       cur.italic and ln.italic, (cur.math * weight_c + ln.math * weight_l) / max(1, weight_c + weight_l),
                       cur.lspace, ln.rspace)
        out.append(cur)
    return out


def _assign_columns(rows: list[Line], width: float):
    """段を決める。本文の行の 35% 以上がページの真ん中をまたぐなら1段組み（段を分けない）。
    SPIE のような1段組みで、中央寄せの見出し（x0 がページ幅の 45% を少し越える）を右の段にしないため。"""
    mid = width / 2
    body = [r for r in rows if len(r.text) >= 25]
    wide = [r for r in body if r.x0 < mid - width * 0.05 and r.x1 > mid + width * 0.05]
    single = bool(body) and len(wide) >= 0.35 * len(body)
    for r in rows:
        r.col = 0 if single else int(r.x0 >= width * 0.45)


def _repeated_margins(lines: list[Line], doc) -> set[str]:
    """3ページ以上で上下の余白に出る同じ文字列（数字を除いて比べる）＝ヘッダー・フッター。"""
    seen = Counter()
    for ln in lines:
        h = doc[ln.page].rect.height
        if ln.y0 < h * 0.08 or ln.y1 > h * 0.92:
            seen[re.sub(r"\d+", "#", ln.text)] += 1
    return {k for k, n in seen.items() if n >= 3}


def _heading_speech(title: str) -> str:
    m = NUMBERED.match(title)
    body = m.group(2) if m else title
    body = body.title() if body.isupper() else body
    return f"Section {m.group(1)}. {body}." if m else f"{body}."


SUP_CITE = re.compile(r"(?:(?<=[A-Za-z]{4})|(?<=[.,;:)\]]))[¹²³⁴⁵⁶⁷⁸⁹⁰](?:[⁰¹²³⁴⁵⁶⁷⁸⁹]|[˒⁻,–-](?=[⁰¹²³⁴⁵⁶⁷⁸⁹]))*")   # 上付きの引用番号（Observatory⁴、tests⁴,）


def speech_text(sentence: str) -> str:
    """読み上げ用の文。括弧の引用（年を含むもの）と [12] 形式の引用、上付きの引用番号を落とす（rad² のような指数は残す）。"""
    s = SUP_CITE.sub("", CITE_BRACKET.sub("", CITE_PAREN.sub("", sentence)))
    return re.sub(r"\s+([.,;:])", r"\1", s).strip()


PARA = "\x00"                     # 段落の区切り（箇条書きの点など）。ここをまたいで文をつながない
REF_START = re.compile(r"^(\[\d+\]|\d{1,3}\.\s|\d{1,3}\s+[A-Z])")


def _hyphen_words(texts) -> set[str]:
    """文書の中で、行の途中にハイフン付きで書かれている語（print-through、peak-to-valley）。"""
    out = set()
    for t in texts:
        for m in re.finditer(r"\b([A-Za-z]+(?:-[A-Za-z]+)+)(?=[\s.,;:)]|$)", t[:-1] if t.endswith("-") else t):
            w = m.group(1).lower()
            out.add(w)
            parts = w.split("-")
            for i in range(1, len(parts)):
                out.add("-".join(parts[:i]) + "-" + "-".join(parts[i:]))
    return out


def _join(parts: list[str], compounds: set[str] | None = None) -> str:
    """行をつなぐ。行末ハイフンは、同じ文書にハイフン付きの形があるか、前が大文字1字（G-release）なら残す。"""
    out = ""
    for p in parts:
        if out.endswith("-") and p[:1].islower():
            head = re.search(r"([A-Za-z]+(?:-[A-Za-z]+)*)-$", out)
            tail = re.match(r"[A-Za-z]+(?:-[A-Za-z]+)*", p)
            keep = bool(head and tail) and (f"{head.group(1)}-{tail.group(0)}".lower() in (compounds or ())
                                            or re.fullmatch(r"[A-Z]", head.group(1).split("-")[-1]) is not None
                                            or "-" in tail.group(0))     # degrees- + of-freedom
            out = out + p if keep else out[:-1] + p     # 行末ハイフンでの分割をつなぐ（複合語は残す）
        else:
            out = f"{out} {p}" if out else p
    return out


def _references(lines: list[str], compounds: set[str]) -> list[str]:
    """参考文献の行を1件ずつにまとめる。行頭の [n] / n. / n Name が新しい1件。"""
    out: list[list[str]] = []
    for x in lines:
        if REF_START.match(x) or not out:
            out.append([x])
        else:
            out[-1].append(x)
    return [_join(r, compounds) for r in out]


_SEG = None


def split_sentences(text: str) -> list[str]:
    """文に分ける。文の位置（char_span）で切り出す。pysbd が返す文字列は空白がずれることがあるので、元の文から切り取る。"""
    global _SEG
    if _SEG is None:
        _SEG = pysbd.Segmenter(language="en", clean=False, char_span=True)
    spans: list[list[int]] = []                        # [始まり, 終わり]
    for sp in (_SEG.segment(text) if text else []):
        a, b = sp.start, sp.end
        t = text[a:b].strip()
        if not t:
            continue
        prev = text[spans[-1][0]:spans[-1][1]].strip() if spans else ""
        m = NUM_THEN_SENTENCE.match(t)
        if spans and len(re.findall(r"[A-Za-z0-9]", t)) < 3:
            spans[-1][1] = b                           # 「).」のような切れ端は前の文につなぐ
        elif spans and REF_WORD_END.search(prev) and m:
            lead = a + (len(text[a:b]) - len(text[a:b].lstrip()))
            spans[-1][1] = lead + len(m.group(1))      # 「… presented in Fig.」+「2.」で1文
            spans.append([lead + m.start(2), b])       # 「The material …」は次の文
        elif spans and _continues(prev, t):
            spans[-1][1] = b                           # 略語や「(b) and (c)」で切られた文をつなぐ
        else:
            spans.append([a, b])
    out = [text[a:b].strip() for a, b in spans if text[a:b].strip()]
    return _resplit(out)


# pysbd が略語と見て切らないところ: 「300 K. The」「5 V. We」「… or better. [4] An」
UNIT_END = re.compile(r"(?<=\d)\s?(K|V|Hz|nm|mm|cm|μm|µm|kHz|MHz|s|h|W|mW|%)\.\s+(?=[A-Z][a-z])")
CITE_THEN = re.compile(r"(?<=[.!?])\s*(\[\d+(?:\s*[,–-]\s*\d+)*\])\s+(?=[A-Z][a-z])")
LEAD_CITE = re.compile(r"^(\[\d+(?:\s*[,–-]\s*\d+)*\])\s+(?=[A-Z])")


def _resplit(sents: list[str]) -> list[str]:
    out: list[str] = []
    for t in sents:
        t = CITE_THEN.sub(lambda m: f" {m.group(1)}\x01", t)       # 引用は前の文の終わりに付ける
        t = UNIT_END.sub(lambda m: f" {m.group(1)}.\x01", t)
        for piece in (x.strip() for x in t.split("\x01")):
            if not piece:
                continue
            m = LEAD_CITE.match(piece)
            if m and out:                                          # 文の頭に来た前の文の引用 [6, 11] は前の文へ
                out[-1] = f"{out[-1]} {m.group(1)}"
                piece = piece[m.end():]
            out.append(piece)
    return out


def build_paper(doc, on_page=None) -> dict:
    lines, ocr_pages = read_lines(doc, on_page)
    if not lines:
        return {"title": "", "pages": len(doc), "ocr_pages": ocr_pages, "sections": []}
    margins = _repeated_margins(lines, doc)
    lines = [ln for ln in lines if re.sub(r"\d+", "#", ln.text) not in margins]

    font_lines = [ln for ln in lines if not ln.ocr]
    sizes = Counter()
    for ln in font_lines:
        sizes[ln.size] += len(ln.text)
    body = sizes.most_common(1)[0][0] if sizes else 0.0

    # 題名: 1ページ目でいちばん大きい文字（本文の 1.4 倍以上のときだけ）
    first = [ln for ln in font_lines if ln.page == 0]
    title = ""
    if first:
        top = max(ln.size for ln in first)
        if top >= body * 1.4:
            title = _join([ln.text for ln in first if ln.size == top])
            lines = [ln for ln in lines if not (ln.page == 0 and ln.size == top and not ln.ocr)]

    def is_heading(ln: Line) -> bool:
        if len(ln.text) > 120:
            return False
        if ln.ocr:
            m = NUMBERED.match(ln.text)
            return bool(m) and len(ln.text) < 70 and not ln.text.endswith(".") and m.group(2)[:1].isupper()
        if not ln.bold:
            # 本文と同じ大きさの斜体で「2.1. 題」だけの行（Optics Express の小節）
            m = NUMBERED.match(ln.text)
            return bool(ln.italic and m and "." in m.group(1).rstrip(".") and abs(ln.size - body) < 0.6 and len(ln.text) < 80
                        and not ln.text.endswith(".") and m.group(2)[:1].isupper())
        if ln.size >= body + 0.9:
            return True
        # 本文と同じか少し小さい太字（Applied Optics など）: 番号付きか、よくある章の名前のときだけ
        if ln.size >= body - 1.5 and len(ln.text) < 80 and not ln.text.endswith("."):
            m = NUMBERED.match(ln.text)
            name = m.group(2) if m else ln.text
            return bool(m and name[:1].isupper()) or bool(SECTION_NAMES.match(name.strip()))
        return False

    sections: list[dict] = []
    cur = {"title": "Abstract", "number": "", "level": 1, "kind": "front", "page": 1, "_parts": []}
    prev_heading: Line | None = None
    in_back = False
    front: list[Line] = []                         # 最初の見出しより前の行（要旨を探す）
    in_abs = False                                 # 「ABSTRACT」の見出しのあと（その下の行をそのまま要旨にする）
    for ln in lines:
        heading = is_heading(ln)                       # 小さい太字の見出しを先に拾う
        if heading and cur["kind"] == "front" and not sections and re.match(r"^abstract\b", ln.text, re.I):
            in_abs = True                              # 本文より前の「ABSTRACT」は見出しにせず、要旨の始まりにする
            rest = re.sub(r"^abstract\s*[:.—-]?\s*", "", ln.text, flags=re.I)
            if rest:
                cur["_parts"].append(rest)
            continue
        if not heading and in_abs and cur["kind"] == "front":
            if not ln.ocr and ln.size >= body - 2.1 and not FRONT_NOISE.fullmatch(ln.text.strip()):
                cur["_parts"].append(FRONT_NOISE.sub("", ln.text).strip() or ln.text)
            continue
        if not heading and cur["kind"] == "front" and not ln.ocr and ln.size >= body - 2.1:
            front.append(ln)                           # 要旨は本文より小さい雑誌がある（Applied Optics は 8pt）
            continue
        if not heading and not ln.ocr and ln.size < body - 0.4:
            continue                                   # 図表の説明・欄外・小さい注記
        if not heading and ln.math >= 0.6:
            continue                                   # 独立した行の数式（本文には入れない。数式は図として切り抜く）
        if heading:
            cont = (prev_heading is not None and not NUMBERED.match(ln.text) and ln.page == prev_heading.page
                    and ln.col == prev_heading.col and abs(ln.size - prev_heading.size) < 0.2
                    and 0 <= ln.y0 - prev_heading.y1 < ln.size * 0.9)
            if cont:
                cur["title"] = f"{cur['title']} {ln.text}"
            else:
                sections.append(cur)
                m = NUMBERED.match(ln.text)
                name = m.group(2) if m else ln.text
                is_back = bool(BACK_MATTER.match(name))
                seen_body = any(sec["kind"] == "body" for sec in sections + [cur])
                # 本文より前の後付け（Optics Express は要旨の直後に References and links）では、後ろを後付けにしない
                in_back = in_back or (is_back and seen_body)
                cur = {"title": ln.text, "number": m.group(1) if m else "",
                       "level": m.group(1).count(".") + 1 if m else 1,
                       "kind": "back" if in_back or is_back else "body", "page": ln.page + 1, "_parts": []}
            prev_heading = ln
            continue
        prev_heading = None
        if ln.text.startswith("•"):
            cur["_parts"].append(PARA)                 # 箇条書きの点で文を切る
            cur["_parts"].append(ln.text.lstrip("• ").strip())
            continue
        cur["_parts"].append(ln.text)
    sections.append(cur)
    # 本文より前に出た後付け（要旨の直後の References and links）は後ろへ回す（ほかの論文と同じ並びにする）
    first_body = next((i for i, sec in enumerate(sections) if sec["kind"] == "body"), None)
    if first_body is not None:
        early = [sec for sec in sections[:first_body] if sec["kind"] == "back"]
        sections = [sec for sec in sections if sec not in early] + early
    if front and sections and sections[0]["kind"] == "front" and not in_abs:
        sections[0]["_parts"] = _abstract(front) + sections[0]["_parts"]

    compounds = _hyphen_words(ln.text for ln in lines)
    out = []
    for i, sec in enumerate(s for s in sections if s["_parts"] or s["kind"] != "front"):
        parts = sec.pop("_parts")
        name = (NUMBERED.match(sec["title"]).group(2) if NUMBERED.match(sec["title"]) else sec["title"]).strip()
        if sec["kind"] == "back" and REFERENCES.match(name):
            # 参考文献は文に分けず1件1文（Proc. や J. で切らない）。読み上げない（s = ""）
            sec["sentences"] = [{"t": t, "s": ""} for t in _references([x for x in parts if x != PARA], compounds)]
        else:
            groups, g = [], []
            for x in parts:
                if x == PARA:
                    groups.append(g)
                    g = []
                else:
                    g.append(x)
            groups.append(g)
            sents = [t for g in groups if g for t in split_sentences(_join(g, compounds))]
            sec["sentences"] = [{"t": t, "s": speech_text(t)} for t in sents]
        sec["id"] = f"s{i}"
        sec["speech_title"] = _heading_speech(sec["title"])
        out.append(sec)
    return {"title": title, "pages": len(doc), "ocr_pages": ocr_pages, "body_size": body,
            "extractor_version": EXTRACTOR_VERSION, "sections": out}


def extract_file(pdf_path: str | Path, on_page=None) -> dict:
    """on_page(何ページ目, 全ページ数, OCR したか) を1ページごとに呼ぶ（進み具合の表示用）。"""
    with pymupdf.open(pdf_path) as doc:
        return build_paper(doc, on_page)


MD_HEADING = re.compile(r"^(#{1,3})\s+(\S.*?)\s*#*$")


def _text_heading(line: str, alone: bool) -> tuple[str, int] | None:
    """貼り付けたテキストの1行が見出しなら (見出し, 段) を返す。
    「## 見出し」「1. Introduction」「2.1 Methods」、前後が空行の短い行（句読点で終わらない）を見出しとみなす。"""
    m = MD_HEADING.match(line)
    if m:
        return m.group(2), len(m.group(1))
    words = line.split()
    if not words or len(words) > 12 or re.search(r"[.!?,;:]$", line) or not re.search(r"[A-Za-z]", line):
        return None
    m = NUMBERED.match(line)
    if m and m.group(2)[:1].isupper():
        return line, min(3, m.group(1).count(".") + 1)
    if alone and len(words) <= 8 and (line[:1].isupper() or line[:1].isdigit()):
        return line, 1
    return None


def build_from_text(title: str, text: str) -> dict:
    """貼り付けた英文 → 章と文。見出しより前の文は、文書の題名を章名にする。"""
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    title = title.strip()
    if not title:
        first = next((ln for ln in lines if ln), "")
        if first and len(first.split()) <= 12 and not re.search(r"[.!?]$", first):
            title = MD_HEADING.sub(r"\2", first)
            lines.remove(first)
        else:
            title = " ".join(first.split()[:8]) or "Untitled"
    sections: list[dict] = []
    cur = {"title": title, "level": 1, "paras": [[]]}
    for i, ln in enumerate(lines):
        if not ln:
            cur["paras"].append([])
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        alone = (i == 0 or not lines[i - 1]) and (not nxt or nxt[:1].isupper())   # 折り返しの途中の行は見出しにしない
        h = _text_heading(ln, alone)
        if h:
            sections.append(cur)
            cur = {"title": h[0], "level": h[1], "paras": [[]]}
            continue
        cur["paras"][-1].append(ln)
    sections.append(cur)
    out = []
    for sec in sections:
        sents = [t for para in sec["paras"] if para for t in split_sentences(_join(para))]
        if not sents and sec is sections[0] and len(sections) > 1:
            continue                                   # 最初の見出しより前に文が無ければ、題名の章は作らない
        m = NUMBERED.match(sec["title"])
        out.append({"id": f"s{len(out)}", "title": sec["title"], "number": m.group(1) if m else "",
                    "level": sec["level"], "kind": "body", "page": 1,
                    "speech_title": _heading_speech(sec["title"]),
                    "sentences": [{"t": t, "s": speech_text(t)} for t in sents]})
    return {"title": title, "pages": 0, "ocr_pages": [], "source": "text",
            "extractor_version": f"text{TEXT_VERSION}", "sections": out}
