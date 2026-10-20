"""画面の !○（改善のコメント）と記録（log/）のテスト。GitHub には送らず、送る中身と手順を確かめる。

  .venv/bin/python -m unittest tests.test_feedback -v
"""
import base64
import json
import shutil
import subprocess
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
import applog  # noqa: E402
import feedback  # noqa: E402
import server  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class FakeGitHub:
    """feedback._api の代わり。呼ばれた順に覚え、決まった答えを返す。"""

    def __init__(self, branch_exists=False):
        self.calls = []
        self.branch_exists = branch_exists

    def __call__(self, token, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/branches/feedback-assets"):
            return (200, {}) if self.branch_exists else (404, {})
        if path.endswith("/git/trees"):
            return 201, {"sha": "tree1"}
        if path.endswith("/git/commits"):
            return 201, {"sha": "commit1"}
        if path.endswith("/git/refs"):
            return 201, {}
        if "/contents/feedback/" in path:
            return 201, {}
        if path.endswith("/labels"):
            return 422, {}                               # もうある
        if path.endswith("/issues"):
            return 201, {"number": 12, "html_url": "https://github.com/o/r/issues/12"}
        return 404, {}


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.orig = feedback._api

    def tearDown(self):
        feedback._api = self.orig

    def report(self, **kw):
        return {"screen": "reader.dictation", "comment": "再生ボタンが小さい\n2行目", "app": "paper_reader", "version": "Mac abc1234",
                "context": {"paper": "A paper (x)", "sentence": "The mirror bends.", "device": "Mac・Chrome"},
                "screenshot": PNG, "log": "line1\nline2", **kw}

    def test_send_creates_branch_uploads_and_files_issue(self):
        fake = feedback._api = FakeGitHub()
        res = feedback.send("o/r", "acct", self.report(), token="t")
        self.assertEqual(res["number"], 12)
        paths = [(m, p) for m, p, _ in fake.calls]
        self.assertIn(("POST", "/repos/o/r/git/commits"), paths)          # 写真の置き場の枝をコードなしで作る
        commit = next(b for m, p, b in fake.calls if p.endswith("/git/commits"))
        self.assertEqual(commit["parents"], [])
        put = next((p, b) for m, p, b in fake.calls if m == "PUT")
        self.assertTrue(put[0].startswith("/repos/o/r/contents/feedback/") and put[0].endswith("_reader.dictation.png"))
        self.assertEqual(put[1]["branch"], "feedback-assets")
        issue = next(b for m, p, b in fake.calls if p.endswith("/issues"))
        self.assertEqual(issue["title"], "[reader.dictation] 再生ボタンが小さい")
        self.assertEqual(issue["labels"], ["feedback"])
        self.assertIn("raw.githubusercontent.com/o/r/feedback-assets/feedback/", issue["body"])
        self.assertIn("**今の文**: > The mirror bends.", issue["body"])
        self.assertIn("<summary>システムの記録", issue["body"])

    def test_existing_branch_and_no_screenshot(self):
        fake = feedback._api = FakeGitHub(branch_exists=True)
        feedback.send("o/r", "acct", self.report(screenshot=None), token="t")
        self.assertFalse([p for m, p, _ in fake.calls if "contents" in p or "git/" in p])   # 写真が無ければ上げない

    def test_bad_repo_and_api_error(self):
        with self.assertRaises(feedback.FeedbackError):
            feedback.send("not a repo", "acct", self.report(), token="t")
        feedback._api = lambda *a, **k: (403, {"message": "Resource not accessible"})
        with self.assertRaises(feedback.FeedbackError) as cm:
            feedback.send("o/r", "acct", self.report(screenshot=None), token="t")
        self.assertIn("403", str(cm.exception))


class AppLogTest(unittest.TestCase):
    def test_scrub_crash_and_tail(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            home = str(Path.home())
            s = applog.scrub(f"open {home}/Desktop/x/data/papers/ab and {tmp}/data/q", tmp / "data")
            self.assertNotIn(home, s)
            self.assertIn("<data>/q", s)
            log = applog.AppLog(tmp / "log", echo=False)
            log("hello")
            self.assertTrue((tmp / "log" / "README.txt").exists())
            d = log.crash("request", "Traceback\nValueError: boom\n")
            self.assertIn("ValueError: boom", (d / "report.md").read_text())
            self.assertIn("hello", log.tail(5))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ServerFeedbackTest(unittest.TestCase):
    def test_endpoint_scrubs_and_decodes(self):
        tmp = Path(tempfile.mkdtemp())
        got = {}
        orig = feedback.send
        try:
            cfg = tmp / "config.json"
            cfg.write_text(json.dumps({"data_root": "data", "online_dict": False, "feedback_repo": "o/r", "feedback_account": "acct"}))
            server.Handler.app = app = server.App(cfg)
            self.assertTrue((tmp / "log" / "README.txt").exists())          # 記録は config.json の隣の log/
            app.log(f"path {app.store.root}/papers/x and {Path.home()}/secret")
            feedback.send = lambda repo, acct, report: got.update(repo=repo, acct=acct, report=report) or {"number": 3, "url": "u"}
            srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            body = {"screen": "review", "comment": "c", "version": "v", "context": {"paper": f"{Path.home()}/p"},
                    "screenshot": "data:image/png;base64," + base64.b64encode(PNG).decode(), "client_log": "play log"}
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/feedback", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            res = json.loads(urllib.request.urlopen(req).read())
            srv.shutdown()
            srv.server_close()
            self.assertEqual(res["number"], 3)
            r = got["report"]
            self.assertEqual((got["repo"], got["acct"], r["screen"], r["screenshot"]), ("o/r", "acct", "review", PNG))
            self.assertNotIn(str(Path.home()), r["log"] + json.dumps(r["context"]))   # この Mac の場所は伏せる
            self.assertIn("<data>/papers/x", r["log"])
            self.assertIn("play log", r["log"])
        finally:
            feedback.send = orig
            shutil.rmtree(tmp, ignore_errors=True)


class FeedbackJsTest(unittest.TestCase):
    def test_pwa_copy_is_identical(self):
        self.assertEqual((HERE / "assets" / "feedback.js").read_bytes(), (HERE / "pwa" / "js" / "feedback.js").read_bytes(),
                         "assets/feedback.js を pwa/js/feedback.js に写してください")

    @unittest.skipUnless(shutil.which("node"), "node が無い")
    def test_issue_url_and_body(self):
        js = f'import * as F from "{(HERE / "assets" / "feedback.js").as_posix()}";\n' + r"""
const r = { screen: "pwa.dictation", comment: "ボタンが小さい", app: "paper_reader", version: "スマホ版 19",
            context: { paper: "P", sentence: "S".repeat(800), device: "スマホ" }, log: "あ".repeat(5000), imageNote: "添付して" };
const url = F.newIssueUrl("o/r", r);
const short = F.newIssueUrl("o/r", { ...r, log: "x", context: { device: "d" } });
console.log(JSON.stringify({ len: url.length, start: url.startsWith("https://github.com/o/r/issues/new?labels=feedback&title="),
  title: F.issueTitle(r), shortHasLog: decodeURIComponent(short).includes("x\n```"), body: F.issueBody({ ...r, log: "" }).split("\n").slice(0, 5) }));"""
        p = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        o = json.loads(p.stdout)
        self.assertTrue(o["start"])
        self.assertLessEqual(o["len"], 7000)                                     # 長い記録は削って、開ける長さに
        self.assertEqual(o["title"], "[pwa.dictation] ボタンが小さい")
        self.assertTrue(o["shortHasLog"])
        self.assertEqual(o["body"][0], "**画面**: `pwa.dictation`")


if __name__ == "__main__":
    unittest.main()
