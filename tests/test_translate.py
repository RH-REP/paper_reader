"""文ごとの日本語訳づくり（translate.py）のテスト。本物の翻訳の代わりに、決まった答えを返す偽物を使う。
本物（macOS の Translation）で1文訳せるかは、言語データが入っているときだけ確かめる。

  .venv/bin/python -m unittest tests.test_translate -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
import bundle  # noqa: E402
import extract  # noqa: E402
import make_sample_pdf  # noqa: E402
import translate  # noqa: E402
from store import Store  # noqa: E402
from vocab import Vocab  # noqa: E402

FAKE = """#!%s
import json, sys
j = json.load(sys.stdin)
mode = %r
if mode == "missing":
    print(json.dumps({"status": "supported"}))
else:
    print(json.dumps({"status": "installed", "translations": ["訳:" + t[:10] for t in j["texts"]]}))
"""


class TranslateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.paper = extract.extract_file(make_sample_pdf.make(cls.tmp / "s.pdf"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("PAPER_READER_TRANSLATOR", None)

    def fake(self, mode):
        p = self.tmp / f"fake_{mode}"
        p.write_text(FAKE % (sys.executable, mode))
        p.chmod(0o755)
        os.environ["PAPER_READER_TRANSLATOR"] = str(p)

    def test_translates_every_sentence(self):
        self.fake("ok")
        d = self.tmp / "p1"
        d.mkdir()
        st = translate.generate(d, self.paper)
        n = sum(len(s["sentences"]) for s in self.paper["sections"])
        self.assertEqual((st["state"], st["done"], st["total"]), ("done", n, n))
        intro = next(s for s in self.paper["sections"] if s["title"] == "1 INTRODUCTION")
        self.assertEqual(translate.status(d)["items"][f"{intro['id']}_1"], "訳:" + intro["sentences"][0]["t"][:10])

    def test_missing_language_data(self):
        self.fake("missing")
        d = self.tmp / "p2"
        d.mkdir()
        st = translate.generate(d, self.paper)
        self.assertEqual(st["state"], "need_install")
        self.assertIn("翻訳言語", st["error"])

    def test_bundle_carries_translation(self):
        self.fake("ok")
        store = Store(self.tmp / "data")
        m = store.import_pdf(make_sample_pdf.make(self.tmp / "s2.pdf"))
        translate.generate(store.paper_dir(m["id"]), store.load(m["id"]))
        z = bundle.make_bundle(store, Vocab(self.tmp / "data"), [m["id"]], self.tmp / "share")
        with zipfile.ZipFile(z) as f:
            tr = json.loads(f.read(f"papers/{m['id']}/translation.json"))
        self.assertEqual(tr["state"], "done")
        self.assertTrue(tr["items"])


@unittest.skipUnless(shutil.which("swiftc"), "swiftc が無い")
class AppleTranslatorTest(unittest.TestCase):
    def test_builds_and_reports_status(self):
        os.environ.pop("PAPER_READER_TRANSLATOR", None)
        tmp = Path(tempfile.mkdtemp())
        try:
            exe = tmp / "mt"
            subprocess.run(["swiftc", "-O", str(translate.SOURCE), "-o", str(exe)], check=True, capture_output=True)
            out = json.loads(subprocess.run([str(exe)], input=json.dumps({"source": "en", "target": "ja", "texts": ["Hello."]}),
                                            capture_output=True, text=True, timeout=120).stdout)
            self.assertIn(out["status"], ("installed", "supported", "unsupported"))
            if out["status"] == "installed":
                self.assertTrue(out["translations"][0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
