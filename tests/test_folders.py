"""論文のフォルダ分け（folders.json）のテスト。文章は架空のもの。

  .venv/bin/python -m unittest tests.test_folders -v
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import folders  # noqa: E402
import server  # noqa: E402

TEXT = """A Fictional Mirror

1. Introduction
Deformable mirrors correct the wavefront of light.
"""


class FoldersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = folders.Folders(self.tmp / "folders.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_put_rename_delete_move(self):
        self.assertEqual(self.f.get(), {"folders": ["論文", "ニュース", "study"], "papers": {}})
        self.f.put("aaaaaaaa", "ニュース")
        self.f.put("bbbbbbbb", "論文")
        d = self.f.edit("rename", "ニュース", "news")                     # 中の論文もついてくる
        self.assertEqual(d["papers"]["aaaaaaaa"], "news")
        d = self.f.edit("delete", "論文")                                # 消したフォルダの論文は未分類へ
        self.assertEqual((d["folders"], d["papers"]), (["news", "study"], {"aaaaaaaa": "news"}))
        self.assertEqual(self.f.edit("down", "news")["folders"], ["study", "news"])
        self.assertEqual(self.f.edit("down", "news")["folders"], ["study", "news"])   # 端では動かない
        self.f.put("aaaaaaaa", None)
        self.assertEqual(self.f.get()["papers"], {})

    def test_rejects_bad_names(self):
        for args in [("add", "study"), ("add", "未分類"), ("add", " "), ("rename", "study", "論文"), ("delete", "無い"), ("x", "study")]:
            with self.assertRaises(ValueError, msg=args):
                self.f.edit(*args)
        with self.assertRaises(ValueError):
            self.f.put("aaaaaaaa", "無い")


class FoldersApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        server.Handler.app = self.app = server.App(cfg)
        for k in ("start_audio", "start_translate", "start_glossary"):
            setattr(self.app, k, lambda pid, force=False: False)
        self.pid = self.app.store.import_text("", TEXT)["id"]
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path):
        return json.loads(urllib.request.urlopen(self.base + path).read())

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())

    def test_list_has_folder_and_move(self):
        self.assertEqual(self.get("/api/papers")[0]["folder"], "")                     # 取り込んだばかりは未分類
        self.post(f"/api/papers/{self.pid}/folder", {"folder": "study"})
        self.assertEqual(self.get("/api/papers")[0]["folder"], "study")
        self.post("/api/folders", {"op": "add", "name": "読み物"})
        self.assertEqual(self.get("/api/folders")["folders"], ["論文", "ニュース", "study", "読み物"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post(f"/api/papers/{self.pid}/folder", {"folder": "無い"})
        self.assertEqual(cm.exception.code, 400)
        self.assertTrue((self.app.store.root / "folders.json").exists())               # 利用データの側に保存する


if __name__ == "__main__":
    unittest.main()
