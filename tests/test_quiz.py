"""章ごとの理解クイズ（quiz.json）のテスト: 確認のコマンド・クイズだけを頼む依頼文・API・スマホへの zip。文章は架空のもの。

  .venv/bin/python -m unittest tests.test_quiz -v
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

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
import bundle  # noqa: E402
import check_paper  # noqa: E402
import server  # noqa: E402
from store import normalize  # noqa: E402

TEXT = """A Fictional Mirror

1. Introduction
Deformable mirrors correct the wavefront of light. They use many actuators to change shape.

2. Results
The mirror reached a stroke of 10 micrometers. It worked at 300 kelvin.
"""


def good_quiz():
    return {"version": 1, "by": "ai", "at": "t", "chapters": [
        {"chapter": "1. Introduction", "questions": [
            {"q": "What do deformable mirrors correct?", "choices": ["The wavefront", "The color", "The weight", "The size"],
             "answer": 0, "explain": "波面を直す。", "evidence": ["Deformable mirrors correct the wavefront of light."]}]},
        {"chapter": "2. Results", "questions": [
            {"q": "What stroke was reached?", "choices": ["1 um", "5 um", "10 micrometers", "20 um"], "answer": 2,
             "explain": "10 マイクロメートル。", "evidence": ["The mirror reached a stroke of 10 micrometers."]}]}]}


class QuizCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        self.app = server.App(cfg)
        for k in ("start_audio", "start_translate", "start_glossary"):
            setattr(self.app, k, lambda pid, force=False: False)
        self.pid = self.app.store.import_text("", TEXT)["id"]
        self.d = self.app.store.paper_dir(self.pid)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_quiz_check_errors_and_warnings(self):
        paper, _ = normalize(json.loads((self.d / "sentences.json").read_text()))
        self.assertEqual(check_paper.quiz_check(good_quiz(), paper), ([], []))
        bad = good_quiz()
        bad["chapters"][0]["chapter"] = "Intro"                                  # 章の名前が違う
        bad["chapters"][1]["questions"][0]["choices"] = ["a", "b", "c"]          # 3択
        bad["chapters"][1]["questions"][0]["answer"] = 4
        bad["chapters"][1]["questions"][0]["evidence"] = ["Not in the paper."]
        errs, warns = check_paper.quiz_check(bad, paper)
        self.assertEqual(len(errs), 3)
        self.assertEqual(len(warns), 1)
        (self.d / "sentences.json").write_text(json.dumps({**json.loads((self.d / "sentences.json").read_text()),
                                                            "manual": {"by": "ai"}}))
        (self.d / "quiz.json").write_text(json.dumps(good_quiz()))
        r = subprocess.run([sys.executable, str(HERE / "tools" / "check_paper.py"), str(self.d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("理解クイズ 2 章・2 問", r.stdout)

    def test_quiz_prompt_api_and_bundle(self):
        d, prompt = self.app.quiz_prompt(self.pid)
        self.assertIn("理解クイズ（quiz.json）だけ", prompt)
        self.assertIn("## 理解クイズ", prompt)                                   # 決まりは ai_fix_prompt.md の節をそのまま
        self.assertIn("check_paper.py", prompt)
        self.assertNotIn("## 図・表・数式", prompt)
        (self.d / "quiz.json").write_text(json.dumps(good_quiz()))
        server.Handler.app = self.app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}/api/papers/{self.pid}"
            q = json.loads(urllib.request.urlopen(base + "/quiz").read())
            self.assertEqual(len(q["chapters"]), 2)
            p = json.loads(urllib.request.urlopen(base).read())
            self.assertEqual(p["quiz"], {"chapters": 2, "questions": 2})
        finally:
            srv.shutdown()
            srv.server_close()
        z = bundle.make_bundle(self.app.store, self.app.vocab, [self.pid], self.tmp / "share", self.app.state)
        with zipfile.ZipFile(z) as f:
            self.assertEqual(json.loads(f.read(f"papers/{self.pid}/quiz.json"))["chapters"][1]["questions"][0]["answer"], 2)


@unittest.skipUnless(shutil.which("node"), "node が無い")
class QuizJsTest(unittest.TestCase):
    def test_helpers(self):
        js = f"""import * as S from "{(HERE / 'assets' / 'shared.js').as_posix()}";
const secs = [{{id: "s0", title: "1. Intro", level: 1, sentences: [{{t: "A."}}]}}, {{id: "s1", title: "1.1 Sub", level: 2, sentences: [{{t: "B."}}]}},
              {{id: "s2", title: "2. Res", level: 1, sentences: [{{t: "C."}}]}}];
console.log(JSON.stringify({{ch: S.chapterTitle(secs, secs[1]), id: S.findSentenceId(secs, "B."), none: S.findSentenceId(secs, "Z."),
  q: S.quizChapter({{chapters: [{{chapter: "2. Res", questions: [1]}}]}}, "2. Res") !== null, nq: S.quizChapter({{chapters: []}}, "x")}}));"""
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"ch": "1. Intro", "id": "s1_1", "none": None, "q": True, "nq": None})


if __name__ == "__main__":
    unittest.main()
