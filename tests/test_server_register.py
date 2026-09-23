"""単語帳は「調べる」→「登録」の2段階（引いただけでは入らない）ことを、サーバーを立てて確かめる。

  .venv/bin/python -m unittest tests.test_server_register -v
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import server  # noqa: E402
from test_features import make_dict  # noqa: E402


class RegisterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        make_dict(cls.tmp / "data")
        cfg = cls.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        server.Handler.app = server.App(cfg)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, **q):
        return json.loads(urllib.request.urlopen(f"{self.base}{path}?{urllib.parse.urlencode(q)}", timeout=5).read())

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    def test_lookup_then_register(self):
        r = self.get("/api/lookup", w="mirrors", sentence="Two mirrors.")
        self.assertEqual((r["found"], r["headword"], r["registered"]), (True, "mirror", False))
        self.assertEqual(self.get("/api/vocab"), [])                       # 引いただけでは入らない
        r = self.post("/api/vocab/add", {"w": "mirrors", "sec": "1 INTRO", "sentence": "Two mirrors."})
        self.assertTrue(r["registered"])
        vocab = self.get("/api/vocab")
        self.assertEqual([(v["headword"], v["example"]) for v in vocab], [("mirror", "Two mirrors.")])
        self.assertTrue(self.get("/api/lookup", w="mirror")["registered"])
        self.assertEqual(self.get("/api/review")["counts"]["new"], 1)

    def test_cannot_register_unknown_word(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/api/vocab/add", {"w": "wavefront"})
        self.assertEqual(e.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
