"""取り出し方 v3（extract.py）、図の切り抜き v2（figures.py）、取り出しの点検（quality.py）、
この Mac の Claude Code に頼む手直し（aifix.py）のテスト。PDF はテストの中で作る架空のもの。

  .venv/bin/python -m unittest tests.test_v3 -v
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import aifix  # noqa: E402
import extract  # noqa: E402
import figures  # noqa: E402
import quality  # noqa: E402

BODY = ("Deformable mirrors correct the wavefront of light in telescopes. They are thin and have many actuators. "
        "The print-through effect is small in this design. ")


def textbox(page, rect, text, font="tiro", size=10):
    page.insert_textbox(pymupdf.Rect(*rect), text, fontname=font, fontsize=size)


def spie_like(path: Path):
    """1段組み。中央寄せの「ABSTRACT」が x0 > 45% にある。番号と題が離れた太字の小見出し。"""
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((150, 70), "A Fictional Mirror Study", fontname="tibo", fontsize=16)
    p.insert_text((280, 110), "ABSTRACT", fontname="tibo", fontsize=11)
    textbox(p, (63, 120, 550, 170), "We study a fictional mirror for this test. It is only a test of the reader.")
    p.insert_text((63, 200), "1. INTRODUCTION", fontname="tibo", fontsize=11)
    textbox(p, (63, 210, 550, 290), BODY * 2)
    p.insert_text((63, 310), "1.1", fontname="tibo", fontsize=10)
    p.insert_text((99, 310), "Make the Mirror Stiff", fontname="tibo", fontsize=10)
    textbox(p, (63, 318, 550, 400), BODY + "The design uses a deform-\nable face sheet and a print-\nthrough control. It was cooled to 300 K. The mirror worked.")
    p.insert_text((63, 430), "REFERENCES", fontname="tibo", fontsize=11)
    textbox(p, (63, 440, 550, 520), "[1] A. Author, \"A fictional paper,\" Proc. SPIE 1234, 1-2 (2020).\n"
                                    "[2] B. Author, \"Another,\" J. Opt. Soc. Am. 5, 3-4 (2019).")
    doc.save(path)


def optics_express_like(path: Path):
    """要旨の直後に References and links（本文より小さい字）。小節は本文と同じ大きさの斜体。"""
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((100, 60), "A Fictional Unimorph Mirror", fontname="tibo", fontsize=16)
    textbox(p, (150, 90, 480, 150), "Abstract: We made a fictional mirror. It has 44 actuators and is tested here.")
    p.insert_text((117, 180), "References and links", fontname="tibo", fontsize=10)
    textbox(p, (117, 190, 480, 330), "\n".join(f"{i}. A. Author, \"Fictional title number {i},\" Opt. Express {i}, 1-2 (2010)."
                                              for i in range(1, 9)), size=8)
    p.insert_text((117, 360), "1. Introduction", fontname="tibo", fontsize=10)
    textbox(p, (117, 370, 480, 520), BODY * 4)
    p.insert_text((117, 550), "1.1.", fontname="tiit", fontsize=10)
    p.insert_text((143, 550), "Design considerations", fontname="tiit", fontsize=10)
    textbox(p, (117, 560, 480, 720), BODY * 4)
    doc.save(path)


def strips(path: Path):
    """1行を縦の短冊に分けて描き、境目の1文字を両側に重ねて描く（Wolf 2018 のような PDF）。"""
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((150, 70), "A Fictional Strip Paper", fontname="tibo", fontsize=16)
    p.insert_text((72, 110), "1. INTRODUCTION", fontname="tibo", fontsize=11)
    font = pymupdf.Font("tiro")
    line = "The hexapod assemblies support the mirror segments."
    for y in (140, 152):
        x, cuts = 72.0, [10, 24, 38]
        pieces = []
        prev = 0
        for c in cuts + [len(line)]:
            pieces.append((prev, c))
            prev = c
        for a, b in pieces:
            start = max(0, a - 1) if a else 0                          # 境目の1文字を前の短冊と重ねる
            xs = 72.0 + font.text_length(line[:start], fontsize=10)
            p.insert_text((xs, y), line[start:b], fontname="tiro", fontsize=10)
    textbox(p, (72, 170, 540, 260), BODY * 2)
    doc.save(path)


def figure_pdf(path: Path):
    """線で描いたグラフ（埋め込み画像ではない）と、横罫だけの表、番号付きの数式。"""
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((100, 60), "A Fictional Figure Paper", fontname="tibo", fontsize=16)
    p.insert_text((72, 100), "1. Introduction", fontname="tibo", fontsize=11)
    textbox(p, (72, 110, 520, 170), BODY * 2)
    for i in range(6):                                              # 線で描いたグラフ
        p.draw_line((120, 200 + i * 30), (460, 200 + i * 30), color=(0.2, 0.2, 0.8))
    p.draw_rect(pymupdf.Rect(110, 190, 470, 360), color=(0, 0, 0))
    p.insert_text((250, 375), "time (s)", fontname="helv", fontsize=7)
    textbox(p, (72, 385, 520, 410), "Fig. 1. A fictional plot drawn with lines.", size=9)
    textbox(p, (72, 420, 520, 470), BODY)
    textbox(p, (72, 480, 520, 495), "Table 1. Fictional numbers.", size=9)
    for y in (500, 515, 560):                                       # 横罫だけの表
        p.draw_line((72, y), (520, y), color=(0, 0, 0))
    for k, row in enumerate([("Name", "Value"), ("alpha", "1"), ("beta", "2")]):
        p.insert_text((90, 511 + k * 16), row[0], fontname="tiro", fontsize=10)
        p.insert_text((300, 511 + k * 16), row[1], fontname="tiro", fontsize=10)
    textbox(p, (72, 580, 520, 630), BODY)
    p.insert_text((200, 660), "E = m c + k x", fontname="tiit", fontsize=10)
    p.insert_text((500, 660), "(1)", fontname="tiro", fontsize=10)
    textbox(p, (72, 675, 520, 740), BODY)
    doc.save(path)


def titles(paper):
    return [(s["kind"], s["level"], s["title"]) for s in paper["sections"]]


class ExtractV3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spie_single_column_heading_hyphen_units_references(self):
        spie_like(self.tmp / "a.pdf")
        p = extract.extract_file(self.tmp / "a.pdf")
        self.assertEqual(titles(p), [("front", 1, "Abstract"), ("body", 1, "1. INTRODUCTION"),
                                     ("body", 2, "1.1 Make the Mirror Stiff"), ("back", 1, "REFERENCES")])
        self.assertTrue(p["sections"][0]["sentences"][0]["t"].startswith("We study a fictional mirror"))
        body = " ".join(x["t"] for x in p["sections"][2]["sentences"])
        self.assertIn("deformable face sheet", body)                       # ふつうの行末ハイフンはつなぐ
        self.assertIn("print-through control", body)                       # 同じ文書にある複合語は残す
        sents = [x["t"] for x in p["sections"][2]["sentences"]]
        self.assertIn("It was cooled to 300 K.", sents)                     # 「K. The」で切る
        refs = p["sections"][3]["sentences"]
        self.assertEqual(len(refs), 2)                                      # Proc. や J. で切らず1件1文
        self.assertEqual({r["s"] for r in refs}, {""})                     # 参考文献は読まない

    def test_optics_express_early_references_and_italic_subsection(self):
        optics_express_like(self.tmp / "b.pdf")
        p = extract.extract_file(self.tmp / "b.pdf")
        self.assertEqual(titles(p), [("front", 1, "Abstract"), ("body", 1, "1. Introduction"),
                                     ("body", 2, "1.1. Design considerations"), ("back", 1, "References and links")])
        self.assertEqual(p["sections"][0]["sentences"][0]["t"], "We made a fictional mirror.")   # 文献ではなく要旨

    def test_strips_with_overlapping_letters(self):
        strips(self.tmp / "c.pdf")
        p = extract.extract_file(self.tmp / "c.pdf")
        text = " ".join(x["t"] for s in p["sections"] for x in s["sentences"])
        self.assertIn("The hexapod assemblies support the mirror segments.", text)

    def test_symbol_private_use_characters(self):
        sp = {"font": "NBMOIB+SymbolMT", "chars": [{"c": "", "bbox": (0, 0, 5, 5)}, {"c": "", "bbox": (5, 0, 9, 5)}]}
        self.assertEqual("".join(c["c"] for c in extract._span_chars(sp)), "μλ")
        sp = {"font": "Arial", "chars": [{"c": "", "bbox": (0, 0, 5, 5)}, {"c": "", "bbox": (0, 0, 5, 5)}]}
        self.assertEqual("".join(c["c"] for c in extract._span_chars(sp)), "•")   # 分からない私用領域の文字は捨てる

    def test_speech_drops_superscript_citations_but_keeps_exponents(self):
        self.assertEqual(extract.speech_text("The Webb Observatory⁴ and tests⁵, with 0.15 rad² error."),
                         "The Webb Observatory and tests, with 0.15 rad² error.")


class FiguresV2Test(unittest.TestCase):
    def test_vector_figure_rule_table_and_equation(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            figure_pdf(tmp / "original.pdf")
            st = figures.extract(tmp, force=True)
            got = {it["label"]: it for it in st["items"]}
            self.assertEqual(set(got), {"Fig. 1", "Table 1", "Eq. (1)"})
            self.assertTrue(got["Fig. 1"]["caption"].startswith("Fig. 1. A fictional plot"))
            w = pymupdf.Pixmap(str(tmp / "figures" / got["Fig. 1"]["file"])).width
            self.assertGreater(w, 900)                                      # 線のグラフ全体（約 360pt × 200dpi）
            self.assertEqual(figures.check(tmp)[0], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class QualityTest(unittest.TestCase):
    def paper(self, secs):
        return {"sections": [{"title": t, "kind": k, "level": 1, "sentences": [{"t": x} for x in ss]} for t, k, ss in secs]}

    def test_flags_structural_problems(self):
        p = self.paper([("Abstract", "front", ["14. J. Opt. Soc. Am. 5 (2010).", "Proc. SPIE 12, Opt. Lett. 3.", "Appl. Opt. 4."]),
                        ("1. Introduction", "body", ["The actuator moves the mirror.", "The Actua ator is fine.", "A  m stroke."]),
                        ("3. Results", "body", ["It worked."]),
                        ("ABSTRACT", "body", ["Out of place."])])
        codes = {i["code"] for i in quality.inspect(p)}
        self.assertTrue({"abstract_order", "numbering", "pua", "abstract_refs", "doubled_text"} <= codes, codes)
        self.assertTrue(quality.summary(quality.inspect(p))["recommend"])

    def test_clean_paper_is_not_flagged(self):
        p = self.paper([("Abstract", "front", ["We study the error of mirrors."]),
                        ("1. Introduction", "body", ["The error is small.", "The mirror is thin."]),
                        ("2. Methods", "body", ["We measured the error."])])
        self.assertEqual(quality.summary(quality.inspect(p))["major"], 0)


FAKE_CLI = """#!%s
import json, sys, time, pathlib
args = sys.argv[1:]
assert args[0] == "-p" and "--allowedTools" in args
pathlib.Path("sentences.json").write_text(json.dumps({"manual": {"by": "ai"}, "sections": []}))
for ev in [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/x/original.pdf"}}]}},
           {"type": "assistant", "message": {"content": [{"type": "text", "text": "直しました"}]}},
           {"type": "result", "result": "章を 3 つ直しました", "total_cost_usd": 0.42, "is_error": False}]:
    print(json.dumps(ev), flush=True)
    time.sleep(0.05)
"""


class AiFixTest(unittest.TestCase):
    def test_runs_cli_and_records_progress(self):
        tmp = Path(tempfile.mkdtemp())
        old = os.environ.get("PATH", "")
        try:
            bindir = tmp / "bin"
            bindir.mkdir()
            cli = bindir / "claude"
            cli.write_text(FAKE_CLI % sys.executable)
            cli.chmod(0o755)
            os.environ["PATH"] = f"{bindir}:{old}"
            d = tmp / "paper"
            d.mkdir()
            done = []
            fx = aifix.AiFixer()
            fx.start("abcd1234", d, "直して", sys.executable, done.append)  # sys.executable には pymupdf・pysbd がある
            with self.assertRaises(RuntimeError):
                fx.start("ffff0000", d, "直して", sys.executable)          # 同時に2本は走らせない
            for _ in range(600):                                          # 重い時（テストをまとめて走らせる時）でも待てるよう 30 秒まで
                if done:
                    break
                time.sleep(0.05)
            st = aifix.status(d)
            self.assertEqual((st["state"], st["tools"], st["cost_usd"]), ("done", 1, 0.42))
            self.assertEqual(st["result"], "章を 3 つ直しました")
            self.assertTrue((d / "sentences.json").exists())                 # claude はその論文のフォルダで動く
            self.assertIn('"type": "result"', (d / "ai_fix.log").read_text())
        finally:
            os.environ["PATH"] = old
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
