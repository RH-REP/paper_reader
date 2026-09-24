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
EXTRACTOR_VERSION = 2          # 2: 同じ行の切れ端をつなぐ／本文より小さい太字の見出し（Applied Optics など）
MIN_TEXT_CHARS = 20          # これ未満のページは文字層が無いとみなして OCR
BACK_MATTER = re.compile(
    r"^(references|bibliography|literature cited|acknowledge?ments?|funding|author contributions|"
    r"data availability( statement)?|conflicts? of interest|competing interests?|declaration of competing interest|"
    r"publisher'?s note|supplementary material|ethics statement|appendix)\b", re.I)
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
# 番号の無い見出しでも見出しとみなす名前（本文より小さい太字のときに使う）
SECTION_NAMES = re.compile(
    r"^(abstract|introduction|background|related work|theory|methods?|materials and methods|experiments?|"
    r"experimental( setup)?|simulations?|results?( and discussion)?|discussion( and conclusions?)?|"
    r"conclusions?|summary|outlook|references|bibliography|acknowledge?ments?|appendix( [a-z])?)$", re.I)
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
        mid = page.rect.width * 0.45
        page_lines = []
        for block in d["blocks"]:
            for l in block.get("lines", []):
                for s in l["spans"]:
                    s["text"] = ""
                text = _line_text(l["spans"])
                spans = [s for s in l["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = unicodedata.normalize("NFKC", text).strip()
                sizes = Counter()
                for s in spans:
                    sizes[round(s["size"], 1)] += len(s["text"].strip())
                x0, y0, x1, y1 = l["bbox"]
                page_lines.append(Line(pno, int(x0 >= mid), x0, y0, y1, x1, text,
                                       sizes.most_common(1)[0][0], all(_is_bold(s) for s in spans), ocr))
        page_lines.sort(key=lambda ln: (ln.col, round(ln.y0), ln.x0))
        lines += _merge_rows(page_lines)
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


def _line_text(spans) -> str:
    """rawdict の1行を文字列にする。空白の文字が無くても、文字の間が広ければ空白を入れる。各 span に text も入れる。"""
    out, prev = [], None
    for s in spans:
        buf = []
        for ch in s.get("chars", []):
            c = ch["c"]
            if prev is not None and c != " " and prev["c"] != " ":
                gap = ch["bbox"][0] - prev["bbox"][2]
                if gap > SPACE_GAP * s["size"]:
                    buf.append(" ")
            buf.append(c)
            prev = ch
        s["text"] = "".join(buf)
        out.append(s["text"])
    return "".join(out)


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
    runs = [r for r in runs if len(r) >= 2 or sum(len(x.text) for x in r) >= 120]
    if not runs:
        return []
    best = max(runs, key=lambda r: sum(len(x.text) for x in r))
    return [x.text for x in best]


def _merge_rows(page_lines: list[Line]) -> list[Line]:
    """同じ段・同じ高さに分かれて入っている切れ端（「1.」と「Introduction」など）を1行につなぐ。"""
    out: list[Line] = []
    for ln in page_lines:
        p = out[-1] if out else None
        # 同じ高さ・間が1文字ぶん以内・文字の大きさが近いときだけ（欄外の小さい文字をつながない）
        if p and p.col == ln.col and abs(p.y0 - ln.y0) < 1.5 and ln.x0 >= p.x0 \
                and ln.x0 - p.x1 < max(p.size, ln.size) and abs(p.size - ln.size) < 1.5:
            weight_p, weight_l = len(p.text), len(ln.text)
            out[-1] = Line(p.page, p.col, p.x0, min(p.y0, ln.y0), max(p.y1, ln.y1), max(p.x1, ln.x1), f"{p.text} {ln.text}",
                           p.size if weight_p >= weight_l else ln.size, p.bold and ln.bold, p.ocr)
        else:
            out.append(ln)
    return out


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


def speech_text(sentence: str) -> str:
    """読み上げ用の文。括弧の引用（年を含むもの）と [12] 形式の引用を落とす。"""
    s = CITE_BRACKET.sub("", CITE_PAREN.sub("", sentence))
    return re.sub(r"\s+([.,;:])", r"\1", s).strip()


def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out.endswith("-") and p[:1].islower():
            out = out[:-1] + p            # 行末ハイフンでの分割をつなぐ
        else:
            out = f"{out} {p}" if out else p
    return out


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
    return [text[a:b].strip() for a, b in spans if text[a:b].strip()]


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
            return False
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
    for ln in lines:
        heading = is_heading(ln)                       # 小さい太字の見出しを先に拾う
        if not heading and cur["kind"] == "front" and not ln.ocr and ln.size >= body - 2.1:
            front.append(ln)                           # 要旨は本文より小さい雑誌がある（Applied Optics は 8pt）
            continue
        if not heading and not ln.ocr and ln.size < body - 0.4:
            continue                                   # 図表の説明・欄外・小さい注記
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
                in_back = in_back or bool(BACK_MATTER.match(name))
                cur = {"title": ln.text, "number": m.group(1) if m else "",
                       "level": m.group(1).count(".") + 1 if m else 1,
                       "kind": "back" if in_back else "body", "page": ln.page + 1, "_parts": []}
            prev_heading = ln
            continue
        prev_heading = None
        cur["_parts"].append(ln.text)
    sections.append(cur)
    if front and sections and sections[0]["kind"] == "front":
        sections[0]["_parts"] = _abstract(front) + sections[0]["_parts"]

    out = []
    for i, sec in enumerate(s for s in sections if s["_parts"] or s["kind"] != "front"):
        sents = split_sentences(_join(sec.pop("_parts")))
        sec["id"] = f"s{i}"
        sec["speech_title"] = _heading_speech(sec["title"])
        sec["sentences"] = [{"t": t, "s": speech_text(t)} for t in sents]
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
