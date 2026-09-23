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

EXTRACTOR_VERSION = 1
MIN_TEXT_CHARS = 20          # これ未満のページは文字層が無いとみなして OCR
BACK_MATTER = re.compile(
    r"^(references|bibliography|literature cited|acknowledge?ments?|funding|author contributions|"
    r"data availability( statement)?|conflicts? of interest|competing interests?|declaration of competing interest|"
    r"publisher'?s note|supplementary material|ethics statement|appendix)\b", re.I)
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
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


def read_lines(doc) -> tuple[list[Line], list[int]]:
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
                continue
        d = page.get_text("dict", textpage=textpage) if textpage else page.get_text("dict")
        mid = page.rect.width * 0.45
        page_lines = []
        for block in d["blocks"]:
            for l in block.get("lines", []):
                spans = [s for s in l["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = unicodedata.normalize("NFKC", "".join(s["text"] for s in l["spans"])).strip()
                sizes = Counter()
                for s in spans:
                    sizes[round(s["size"], 1)] += len(s["text"].strip())
                x0, y0, _, y1 = l["bbox"]
                page_lines.append(Line(pno, int(x0 >= mid), x0, y0, y1, text,
                                       sizes.most_common(1)[0][0], all(_is_bold(s) for s in spans), ocr))
        page_lines.sort(key=lambda ln: (ln.col, round(ln.y0), ln.x0))
        lines += page_lines
    return lines, ocr_pages


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


def build_paper(doc) -> dict:
    lines, ocr_pages = read_lines(doc)
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
        return ln.bold and ln.size >= body + 0.9

    sections: list[dict] = []
    cur = {"title": "Abstract", "number": "", "level": 1, "kind": "front", "page": 1, "_parts": []}
    prev_heading: Line | None = None
    in_back = False
    for ln in lines:
        if not ln.ocr and ln.size < body - 0.4:
            continue                                   # 図表の説明・欄外・小さい注記
        if is_heading(ln):
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

    seg = pysbd.Segmenter(language="en", clean=False)
    out = []
    for i, sec in enumerate(s for s in sections if s["_parts"] or s["kind"] != "front"):
        text = _join(sec.pop("_parts"))
        sents: list[str] = []
        for t in (seg.segment(text) if text else []):
            t = t.strip()
            if not t:
                continue
            if sents and len(re.findall(r"[A-Za-z0-9]", t)) < 3:
                sents[-1] += t                         # 「).」のような切れ端は前の文につなぐ
            else:
                sents.append(t)
        sec["id"] = f"s{i}"
        sec["speech_title"] = _heading_speech(sec["title"])
        sec["sentences"] = [{"t": t, "s": speech_text(t)} for t in sents]
        out.append(sec)
    return {"title": title, "pages": len(doc), "ocr_pages": ocr_pages, "body_size": body,
            "extractor_version": EXTRACTOR_VERSION, "sections": out}


def extract_file(pdf_path: str | Path) -> dict:
    with pymupdf.open(pdf_path) as doc:
        return build_paper(doc)
