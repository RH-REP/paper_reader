"""AI による手直し（sentences.json を直接直す）まわりのテスト。

- 手直し済み（manual）は、取り出し方の版が変わっても上書きしない
- 足りない項目は補い、壊れていれば分かるように止める
- 文が変わったら音声・訳を作り直す（中身の目印で判定）
- 確認のコマンド（tools/check_paper.py）とプロンプトの API

  .venv/bin/python -m unittest tests.test_ai_fix -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
import audio  # noqa: E402
import make_sample_pdf  # noqa: E402
import server  # noqa: E402
import store as storemod  # noqa: E402
from store import Store, normalize  # noqa: E402

EDITED = {
    "title": "A Fictional Lens",
    "manual": {"by": "ai", "at": "2026-09-24T12:00:00+09:00", "notes": "章を2つにまとめた"},
    "sections": [
        {"title": "Abstract", "kind": "front", "sentences": [{"t": "We describe a lens (Smith et al., 2020)."}]},
        {"title": "1. Introduction", "kind": "body", "sentences": ["Lenses bend light.", {"t": "Eq. (1) holds.", "s": "(equation) holds."}]},
        {"title": "References", "kind": "back", "sentences": []},
    ],
}


class AiFixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "data")
        self.meta = self.store.import_pdf(make_sample_pdf.make(self.tmp / "s.pdf"))
        self.d = self.store.paper_dir(self.meta["id"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, obj):
        (self.d / "sentences.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def test_normalize_fills_missing_fields(self):
        p, errs = normalize(json.loads(json.dumps(EDITED)))
        self.assertEqual(errs, [])
        intro = p["sections"][1]
        self.assertEqual((intro["id"], intro["number"], intro["level"]), ("s1", "1", 1))
        self.assertEqual(intro["speech_title"], "Section 1. Introduction.")
        self.assertEqual(intro["sentences"][0], {"t": "Lenses bend light.", "s": "Lenses bend light."})
        self.assertEqual(p["sections"][0]["sentences"][0]["s"], "We describe a lens.")      # 引用は落とす
        self.assertEqual(intro["sentences"][1]["s"], "(equation) holds.")                   # 書いてあればそのまま

    def test_normalize_reports_broken(self):
        _, errs = normalize({"sections": [{"title": "X", "kind": "main", "sentences": [{"x": 1}]}]})
        self.assertEqual(len(errs), 2)

    def test_manual_survives_extractor_upgrade_and_reextract_backs_up(self):
        self.write(EDITED)
        meta = json.loads((self.d / "meta.json").read_text())
        meta["extractor_version"] = 0                                  # 取り出し方が古い扱い
        (self.d / "meta.json").write_text(json.dumps(meta))
        p = self.store.load(self.meta["id"])
        self.assertEqual([s["title"] for s in p["sections"]], ["Abstract", "1. Introduction", "References"])
        self.assertEqual(self.store.list()[0]["sections"], 3)            # 一覧の数も手直し後に合う
        self.store.reextract(self.meta["id"])                            # 明示の取り出し直しは上書きするが退避を残す
        self.assertTrue((self.d / "sentences.manual.json").exists())
        self.assertNotIn("manual", self.store.load(self.meta["id"]))

    def test_broken_manual_file_raises(self):
        (self.d / "sentences.json").write_text('{"manual": {}, "sections": [', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load(self.meta["id"])

    def test_audio_becomes_stale_when_sentences_change(self):
        paper = self.store.load(self.meta["id"])
        st = {"state": "done", "items_hash": audio.items_hash(paper), "extractor_version": paper["extractor_version"]}
        (self.d / "audio").mkdir(exist_ok=True)
        audio._write_status(self.d, st)
        self.assertTrue(audio.is_current(self.d, paper))
        self.write(EDITED)
        self.assertFalse(audio.is_current(self.d, self.store.load(self.meta["id"])))

    def test_check_paper_tool(self):
        tool = HERE / "tools" / "check_paper.py"
        run = lambda: subprocess.run([sys.executable, str(tool), str(self.d)], capture_output=True, text=True)
        r = run()
        self.assertEqual(r.returncode, 1)                                # 自動取り出しのまま = manual が無い
        self.assertIn("manual", r.stdout)
        bad = json.loads(json.dumps(EDITED))
        bad["sections"][1]["sentences"].append({"t": "limitedbysaturationofoneorseveralactuators is bad.", "s": "x 1⁄4 y"})
        self.write(bad)
        r = run()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("空白の抜け", r.stdout)
        self.assertIn("文字化け", r.stdout)
        self.assertIn("章・節 3", r.stdout)


class PromptApiTest(unittest.TestCase):
    def test_prompt_has_folder_and_check_command(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = tmp / "config.json"
            cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
            app = server.App(cfg)
            m = app.store.import_pdf(make_sample_pdf.make(tmp / "s.pdf"))
            server.Handler.app = app
            srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                r = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/papers/{m['id']}/ai_prompt").read())
            finally:
                srv.shutdown()
                srv.server_close()
            folder = str(app.store.paper_dir(m["id"]))
            self.assertTrue(r["prompt"].startswith(folder + "\n\n"))
            self.assertIn("check_paper.py", r["prompt"])
            self.assertIn(folder, r["prompt"].split("check_paper.py", 1)[1][:400])
            for word in ["章分け", "単語の区切り", "OCR", "(equation)", '"manual"', "sentences.orig.json", "translation.json"]:
                self.assertIn(word, r["prompt"])
            self.assertNotIn("{check}", r["prompt"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
