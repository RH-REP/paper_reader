#!/usr/bin/env python3
"""架空の2段組論文 samples/sample_paper.pdf を作る（開発・テスト用。実在の論文は app_dev に入れない）。

  python3 tools/make_sample_pdf.py            # samples/sample_paper.pdf
  python3 tools/make_sample_pdf.py --scan     # 同じ内容を画像だけにした samples/sample_scan.pdf も作る（OCR の確認用）
"""
import argparse
import math
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent.parent
W, H = 595, 780
COLS = [(45, 289), (306, 550)]
TOP, BOTTOM = 60, 720

TITLE = "A Fictional Lens for Imaginary Telescopes"
ABSTRACT = ("We describe a fictional lens that exists only in this sample file. "
            "It is used to test how the reader splits a paper into sections and sentences. "
            "Nothing here refers to a real instrument.")
BODY = [
    ("h", "1 INTRODUCTION"),
    ("p", "Imaginary telescopes need imaginary lenses (Smith et al., 2020). "
          "This paper walks through the design of one such lens, e.g. the choice of glass and the size of the pupil. "
          "The lens is 3.5 mm thick and weighs 12 g."),
    ("p", "Section 2 lists the requirements. Section 3 describes the design, and Section 4 closes the paper."),
    ("h", "2 REQUIREMENTS"),
    ("p", "The lens must survive a launch that never happens [3]. It must also focus light onto a detector that was never built."),
    ("h", "2.1 Optical Quality"),
    ("p", "The wavefront error must stay below 20 nm RMS across the field (Lee and Park, 2019a; Kim, 2021). "
          "We verify this with a made-up test bench."),
    ("h", "2.2 Mass and Volume Limits for a Very Small Imaginary Spacecraft"),
    ("p", "The whole assembly must fit in 1U. Fig. 1 shows the layout that satisfies this limit."),
    ("c", "FIGURE 1 | The layout of the fictional lens. This caption should not be read aloud."),
    ("h", "3 DESIGN"),
    ("p", "We chose a single aspheric surface. Dr. Jones suggested this approach in a conversation that did not take place. "
          "The resulting design meets every requirement listed above."),
    ("h", "4 CONCLUSION"),
    ("p", "A fictional lens can be designed in a few paragraphs. Real lenses take longer."),
    ("h", "ACKNOWLEDGMENTS"),
    ("p", "We thank nobody in particular."),
    ("h", "REFERENCES"),
    ("p", "Kim, A. (2021). An imaginary paper. J. Fict. Opt. 1, 1-2."),
]
STYLE = {"h": ("hebo", 12.0, 1.3), "p": ("helv", 9.5, 1.25), "c": ("helv", 7.5, 1.2)}


def height(text, font, size, lead, width):
    n = math.ceil(pymupdf.get_text_length(text, fontname=font, fontsize=size) / (width * 0.92)) + 1
    return n * size * lead + 8


def make(out: Path):
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_textbox(pymupdf.Rect(45, 60, 550, 120), TITLE, fontname="hebo", fontsize=20)
    page.insert_textbox(pymupdf.Rect(45, 130, 550, 200), ABSTRACT, fontname="helv", fontsize=10)
    y, col, pno = 210, 0, 1

    def stamp(p, n):
        p.insert_text((45, 34), "Fictional Journal of Imaginary Optics", fontname="helv", fontsize=7)
        p.insert_text((45, 750), f"Sample paper | page {n}", fontname="helv", fontsize=7)
    stamp(page, pno)
    for kind, text in BODY:
        font, size, lead = STYLE[kind]
        x0, x1 = COLS[col]
        h = height(text, font, size, lead, x1 - x0)
        if y + h > BOTTOM:
            if col == 0:
                col, y = 1, (210 if pno == 1 else TOP)
            else:
                page = doc.new_page(width=W, height=H)
                pno += 1
                stamp(page, pno)
                col, y = 0, TOP
            x0, x1 = COLS[col]
        page.insert_textbox(pymupdf.Rect(x0, y, x1, y + h), text, fontname=font, fontsize=size, lineheight=lead)
        y += h
    # ヘッダー・フッターが3ページ以上に出るよう、空の続きページを足す
    while len(doc) < 3:
        page = doc.new_page(width=W, height=H)
        pno += 1
        stamp(page, pno)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


def make_scan(src: Path, out: Path):
    """文字層の無い、画像だけの PDF にする。"""
    img = pymupdf.open()
    with pymupdf.open(src) as d:
        for p in d:
            pix = p.get_pixmap(dpi=200)
            q = img.new_page(width=p.rect.width, height=p.rect.height)
            q.insert_image(q.rect, pixmap=pix)
    img.save(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "samples" / "sample_paper.pdf")
    ap.add_argument("--scan", action="store_true")
    a = ap.parse_args()
    print(make(a.out))
    if a.scan:
        print(make_scan(a.out, a.out.with_name("sample_scan.pdf")))
