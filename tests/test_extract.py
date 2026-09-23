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
