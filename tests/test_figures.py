"""図・表・数式の画像（figures.py）のテスト。架空の PDF に画像とキャプションを置いて確かめる。

  .venv/bin/python -m unittest tests.test_figures -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import bundle  # noqa: E402
import figures  # noqa: E402
import server  # noqa: E402
from store import Store  # noqa: E402
from vocab import Vocab  # noqa: E402


def make_pdf(path: Path) -> Path:
    """2ページ: 1ページ目に図（画像＋下にキャプション）と小さいロゴ、2ページ目に表（画像＋上にキャプション）。"""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), False)
    pix.set_rect(pix.irect, (40, 120, 200))
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=780)
    p1.insert_text((60, 60), "A Fictional Title", fontname="hebo", fontsize=18)
    p1.insert_image(pymupdf.Rect(100, 100, 400, 300), pixmap=pix)
    p1.insert_textbox(pymupdf.Rect(100, 310, 500, 360), "Fig. 1. A fictional plot of nothing.", fontname="helv", fontsize=8)
    p1.insert_image(pymupdf.Rect(520, 20, 540, 40), pixmap=pix)          # ロゴ（小さいので外す）
    p2 = doc.new_page(width=595, height=780)
    p2.insert_textbox(pymupdf.Rect(100, 60, 500, 90), "TABLE 2 | Fictional numbers.", fontname="helv", fontsize=8)
    p2.insert_image(pymupdf.Rect(100, 100, 400, 250), pixmap=pix)
    doc.save(path)
    return path


class FiguresTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.d = self.tmp / "paper"
        self.d.mkdir()
        make_pdf(self.d / "original.pdf")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_labels_and_skips_logo(self):
        st = figures.extract(self.d)
        got = [(it["page"], it["kind"], it["label"]) for it in st["items"]]
        self.assertEqual(got, [(1, "figure", "Fig. 1"), (2, "table", "Table 2")])
        self.assertTrue(st["items"][0]["caption"].startswith("Fig. 1. A fictional plot"))
        png = self.d / "figures" / st["items"][0]["file"]
        self.assertGreater(pymupdf.Pixmap(str(png)).width, 800)          # 200dpi で切り抜く（300pt 幅 → 約 833px）
        self.assertEqual(figures.check(self.d)[0], [])

    def test_manual_is_not_overwritten(self):
        figures.extract(self.d)
        st = json.loads((self.d / "figures.json").read_text())
        st["manual"] = {"by": "ai", "at": "t", "notes": "x"}
        st["items"] = st["items"][:1]
        (self.d / "figures.json").write_text(json.dumps(st))
        self.assertEqual(len(figures.extract(self.d, force=True)["items"]), 1)

    def test_check_reports_missing_file_and_bad_kind(self):
        (self.d / "figures").mkdir()
        (self.d / "figures.json").write_text(json.dumps({"version": 1, "items": [
            {"file": "fig01.png", "kind": "figure", "label": "Fig. 1"}, {"file": "x.png", "kind": "photo"}]}))
        errs, _ = figures.check(self.d)
        self.assertEqual(len(errs), 3)                                    # 2つとも画像が無い＋種類が違う

    def test_bundle_and_server(self):
        store = Store(self.tmp / "data")
        m = store.import_pdf(make_pdf(self.tmp / "f.pdf"))
        pd = store.paper_dir(m["id"])
        self.assertEqual(len(figures.status(pd)["items"]), 2)             # 取り込みで自動で作られる
        z = bundle.make_bundle(store, Vocab(self.tmp / "data"), [m["id"]], self.tmp / "share")
        with zipfile.ZipFile(z) as f:
            names = f.namelist()
            items = json.loads(f.read(f"papers/{m['id']}/figures.json"))["items"]
        for it in items:
            self.assertIn(f"papers/{m['id']}/figures/{it['file']}", names)
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        server.Handler.app = server.App(cfg)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}/api/papers/{m['id']}/figures"
            lst = json.loads(urllib.request.urlopen(base).read())
            r = urllib.request.urlopen(f"{base}/{lst['items'][0]['file']}")
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertEqual(r.read()[:4], b"\x89PNG")
            prompt = json.loads(urllib.request.urlopen(base.replace("/figures", "/ai_prompt")).read())["prompt"]
            self.assertIn("## 図・表・数式", prompt)
            self.assertNotIn("{python}", prompt)
            self.assertNotIn("{folder}", prompt)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_check_paper_lists_figures(self):
        figures.extract(self.d)
        (self.d / "sentences.json").write_text(json.dumps({"manual": {"by": "ai"}, "sections": [
            {"title": "1. Intro", "kind": "body", "sentences": [{"t": "A sentence that is long enough."}]}]}))
        r = subprocess.run([sys.executable, str(HERE / "tools" / "check_paper.py"), str(self.d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("図・表・数式 2 件", r.stdout)
        self.assertIn("[table] Table 2", r.stdout)


if __name__ == "__main__":
    unittest.main()
