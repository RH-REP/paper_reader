"""架空のサンプル PDF で、章・文の分け方と取り込みを確かめる。

  .venv/bin/python -m unittest discover -s tests -v
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
import extract  # noqa: E402
import make_sample_pdf  # noqa: E402
from store import Store  # noqa: E402

EXPECTED = ["Abstract", "1 INTRODUCTION", "2 REQUIREMENTS", "2.1 Optical Quality",
            "2.2 Mass and Volume Limits for a Very Small Imaginary Spacecraft",
            "3 DESIGN", "4 CONCLUSION", "ACKNOWLEDGMENTS", "REFERENCES"]


class ExtractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.pdf = make_sample_pdf.make(cls.tmp / "sample.pdf")
        cls.paper = extract.extract_file(cls.pdf)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def sec(self, title):
        return next(s for s in self.paper["sections"] if s["title"] == title)

    def test_progress_callback_per_page(self):
        seen = []
        extract.extract_file(self.pdf, lambda n, total, ocr: seen.append((n, total, ocr)))
        self.assertEqual(seen, [(1, 3, False), (2, 3, False), (3, 3, False)])

    def test_title(self):
        self.assertEqual(self.paper["title"], make_sample_pdf.TITLE)

    def test_sections(self):
        self.assertEqual([s["title"] for s in self.paper["sections"]], EXPECTED)
        self.assertEqual(self.sec("2.1 Optical Quality")["level"], 2)
        self.assertEqual(self.sec("ACKNOWLEDGMENTS")["kind"], "back")
        self.assertEqual(self.sec("4 CONCLUSION")["kind"], "body")
        self.assertEqual(self.sec("Abstract")["kind"], "front")

    def test_sentences_keep_abbreviations(self):
        t = [s["t"] for s in self.sec("1 INTRODUCTION")["sentences"]]
        self.assertEqual(len(t), 5)
        self.assertIn("e.g. the choice of glass", t[1])
        self.assertTrue(t[2].startswith("The lens is 3.5 mm thick"))

    def test_caption_header_footer_dropped(self):
        allt = " ".join(s["t"] for sec in self.paper["sections"] for s in sec["sentences"])
        self.assertNotIn("caption should not be read", allt)
        self.assertNotIn("Fictional Journal", allt)
        self.assertNotIn("Sample paper | page", allt)

    def test_citations_removed_for_speech(self):
        s = self.sec("2.1 Optical Quality")["sentences"][0]
        self.assertIn("(Lee and Park, 2019a; Kim, 2021)", s["t"])
        self.assertEqual(s["s"], "The wavefront error must stay below 20 nm RMS across the field.")
        s = self.sec("2 REQUIREMENTS")["sentences"][0]
        self.assertEqual(s["s"], "The lens must survive a launch that never happens.")

    def test_heading_speech(self):
        self.assertEqual(self.sec("2 REQUIREMENTS")["speech_title"], "Section 2. Requirements.")

    def test_import_copies_and_dedups(self):
        store = Store(self.tmp / "data")
        m1 = store.import_pdf(self.pdf)
        self.assertTrue((self.tmp / "data" / "papers" / m1["id"] / "original.pdf").exists())
        self.assertTrue(self.pdf.exists())                    # 元のファイルは残る
        m2 = store.import_pdf(self.pdf)
        self.assertTrue(m2.get("already"))
        self.assertEqual(len(store.list()), 1)
        self.assertEqual(store.load(m1["id"])["sections"][1]["title"], "1 INTRODUCTION")


def make_ao_style(path):
    """Applied Optics 風の架空 PDF: 見出しは本文（10pt）より小さい 9pt の太字で、番号と見出しが別の切れ端。
    要旨は 8pt。1か所は空白の文字が無く、字間だけ空いている。"""
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=780)
    pg.insert_text((60, 60), "A Fictional Mirror Study", fontname="hebo", fontsize=18)
    pg.insert_text((60, 90), "Ann Author and Bob Author", fontname="helv", fontsize=10)
    y = 120
    for line in ["We study a fictional mirror that exists only in this file. It is used to test",
                 "how headings and abstracts are found in a different journal style. Nothing",
                 "here is real."]:
        pg.insert_text((60, y), line, fontname="helv", fontsize=8)
        y += 10
    pg.insert_text((60, y), "OCIS codes: 010.1080, 220.1080.", fontname="helv", fontsize=8)
    y = 190
    for num, name, body in [("1.", "Introduction", ["Mirrors bend light. This sample has two sections only.",
                                                     "The second one is about a model."]),
                            ("2.", "Model", ["The model is simple. It is limited", None])]:
        pg.insert_text((60, y), num, fontname="hebo", fontsize=9)
        pg.insert_text((76, y), name, fontname="hebo", fontsize=9)
        y += 16
        for line in body:
            if line is None:                                  # 「by saturation」を空白の文字なしで、字間だけあけて置く
                x = 60
                for w in ["by", "saturation", "of", "one", "actuator."]:
                    pg.insert_text((x, y), w, fontname="helv", fontsize=10)
                    x += pymupdf.get_text_length(w, fontname="helv", fontsize=10) + 1.5
            else:
                pg.insert_text((60, y), line, fontname="helv", fontsize=10)
            y += 13
        y += 10
    doc.save(path)
    return path


class OtherJournalStyleTest(unittest.TestCase):
    def test_small_bold_split_headings_small_abstract_missing_spaces(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            p = extract.extract_file(make_ao_style(tmp / "ao.pdf"))
            self.assertEqual(p["title"], "A Fictional Mirror Study")
            self.assertEqual([s["title"] for s in p["sections"]], ["Abstract", "1. Introduction", "2. Model"])
            abstract = " ".join(x["t"] for x in p["sections"][0]["sentences"])
            self.assertTrue(abstract.startswith("We study a fictional mirror"))
            self.assertNotIn("OCIS", abstract)
            self.assertNotIn("010.1080", abstract)
            self.assertNotIn("Ann Author", abstract)                  # 著者名は要旨に入れない
            model = " ".join(x["t"] for x in p["sections"][2]["sentences"])
            self.assertIn("limited by saturation of one actuator.", model)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sentence_rejoining(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            import pymupdf
            doc = pymupdf.open()
            pg = doc.new_page(width=595, height=780)
            pg.insert_text((60, 60), "A Fictional Title", fontname="hebo", fontsize=18)
            pg.insert_text((60, 100), "1 RESULTS", fontname="hebo", fontsize=12)
            text = ("The mesh is presented in Fig. 2. The material properties are listed in Table 1. "
                    "In vector form, Eq. (1) can be rewritten. Modes are shown in Figs. 5(a) and 5(b).")
            pg.insert_textbox(pymupdf.Rect(60, 110, 540, 300), text, fontname="helv", fontsize=10)
            doc.save(tmp / "s.pdf")
            secs = extract.extract_file(tmp / "s.pdf")["sections"]
            self.assertEqual([s["title"] for s in secs], ["1 RESULTS"])
            sents = [x["t"] for x in secs[0]["sentences"]]
            self.assertEqual(sents, ["The mesh is presented in Fig. 2.", "The material properties are listed in Table 1.",
                                     "In vector form, Eq. (1) can be rewritten.", "Modes are shown in Figs. 5(a) and 5(b)."])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(extract._tessdata(), "Tesseract（eng）が無い")
class OcrTest(unittest.TestCase):
    def test_scan_is_ocred(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            scan = make_sample_pdf.make_scan(make_sample_pdf.make(tmp / "s.pdf"), tmp / "scan.pdf")
            p = extract.extract_file(scan)
            self.assertEqual(p["ocr_pages"], [1, 2, 3])
            titles = [s["title"] for s in p["sections"]]
            for want in ["1 INTRODUCTION", "2 REQUIREMENTS", "2.1 Optical Quality", "3 DESIGN"]:
                self.assertIn(want, titles)
            intro = next(s for s in p["sections"] if s["title"] == "1 INTRODUCTION")
            self.assertTrue(any("imaginary lenses" in s["t"] for s in intro["sentences"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
