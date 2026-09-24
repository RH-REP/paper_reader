"""読み方の辞書（pronounce.py）、専門用語の一覧（glossary.py）、辞書に無い語の引き方、訳の呼び直しのテスト。
文章はすべて架空のもの。

  .venv/bin/python -m unittest tests.test_pron_glossary -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import audio  # noqa: E402
import glossary  # noqa: E402
import pronounce  # noqa: E402
import server  # noqa: E402
import translate  # noqa: E402
from lookup import Dictionary  # noqa: E402

TEXT = """A Fictional Mirror

1. Introduction
A deformable mirror (DM) bends light. The DM has many actuators. Each actuator pushes the mirror.
Deformable mirrors are used in adaptive optics. The deformable mirror is thin.

2. Tests
The deformable mirror moved 7 μm at 300 K. Adaptive optics needs a fast DM.
The actuator was stable. Adaptive optics is useful. We like adaptive optics. We thank Zorblax for help. Zorblax helped.
"""

FAKE = """#!%s
import json, sys, os
j = json.load(sys.stdin)
state = %r
n = int(open(state).read()) if os.path.exists(state) else 0
open(state, "w").write(str(n + 1))
if n < %d:
    print(json.dumps({"status": "supported"}))
else:
    print(json.dumps({"status": "installed", "translations": ["訳:" + t for t in j["texts"]]}))
"""


def tiny_dict(root: Path):
    """架空の小さな辞書（mirror・light などふつうの語だけ。deformable・actuator は無い）。"""
    d = root / "dict"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "ejdict.sqlite")
    con.execute("CREATE TABLE words (word TEXT, lower TEXT, mean TEXT)")
    for w in ["mirror", "light", "bend", "push", "thin", "fast", "stable", "useful", "help", "thank", "optics",
              "adaptive", "test", "introduction", "use", "need", "move", "many", "each", "have", "fictional"]:
        con.execute("INSERT INTO words VALUES (?, ?, ?)", (w, w, f"{w}の意味"))
    con.commit()
    con.close()


class PronounceTest(unittest.TestCase):
    def test_builtin(self):
        cases = {
            "The stroke is 7 μm at 300 K.": "The stroke is 7 micrometers at 300 kelvin.",
            "Fluence 5 J/cm² and 20 °C.": "Fluence 5 joules per square centimeter and 20 degrees Celsius.",
            "The error was 0.15 rad² at λ/14.": "The error was 0.15 radians squared at lambda over 14.",
            "In the 1990s a 3 s run.": "In the 1990s a 3 seconds run.",          # 1990s は秒にしない
            "Section 2 N-type.": "Section 2 N-type.",
            "Peak of ±5 V.": "Peak of plus or minus 5 volts.",
        }
        for src, want in cases.items():
            self.assertEqual(pronounce.builtin(src), want, src)

    def test_user_rules_whole_word_and_case(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            p = pronounce.Pronouncer(tmp / "pron.json")
            p.save([{"from": "DM", "to": "D M"}, {"from": "ALPAO", "to": "al pao", "case": False}, {"from": "", "to": "x"}])
            self.assertEqual([r["from"] for r in p.rules()], ["DM", "ALPAO"])      # 空の行は捨てる
            self.assertEqual(p.apply("The DM and DMs, not ADMIN; Alpao."), "The D M and DMs, not ADMIN; al pao.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audio_items_use_pronunciation(self):
        tmp = Path(tempfile.mkdtemp())
        old = audio.PRONOUNCER
        try:
            paper = {"sections": [{"id": "s0", "speech_title": "Intro.", "sentences": [{"t": "The DM moved.", "s": "The DM moved."}]}]}
            h0 = audio.items_hash(paper)
            audio.PRONOUNCER = pronounce.Pronouncer(tmp / "pron.json")
            audio.PRONOUNCER.save([{"from": "DM", "to": "D M"}])
            self.assertEqual(audio.items(paper)[1]["text"], "The D M moved.")
            self.assertNotEqual(audio.items_hash(paper), h0)                         # 読み方が変わった文は作り直しになる
        finally:
            audio.PRONOUNCER = old
            shutil.rmtree(tmp, ignore_errors=True)


class GlossaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        tiny_dict(self.tmp / "data")
        self.state = self.tmp / "calls"
        exe = self.tmp / "fake_tr"
        exe.write_text(FAKE % (sys.executable, str(self.state), 0))
        exe.chmod(0o755)
        os.environ["PAPER_READER_TRANSLATOR"] = str(exe)
        self.old_wait = translate.RETRY_WAIT
        translate.RETRY_WAIT = 0

    def tearDown(self):
        os.environ.pop("PAPER_READER_TRANSLATOR", None)
        translate.RETRY_WAIT = self.old_wait
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_app(self):
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        app = server.App(cfg)
        app.start_audio = lambda pid, force=False: False
        return app

    def test_candidates(self):
        app = self.make_app()
        pid = app.store.import_text("", TEXT)["id"]
        terms = glossary.candidates(app.store.load(pid), Dictionary(self.tmp / "data", online=False))
        got = {(t["kind"], t["term"]): t for t in terms}
        self.assertEqual(got[("word", "deformable")]["count"], 4)
        self.assertEqual(got[("word", "actuator")]["count"], 3)                      # actuators と actuator を1つに
        self.assertEqual(got[("acronym", "DM")]["expansion"], "deformable mirror")
        self.assertIn(("phrase", "deformable mirror"), got)                          # mirrors と mirror を1つに
        self.assertIn(("phrase", "adaptive optics"), got)
        self.assertNotIn(("word", "zorblax"), got)                                   # 大文字でしか出ない固有名詞は外す
        self.assertEqual(got[("word", "deformable")]["section"], "1. Introduction")

    def test_generate_lookup_fallback_and_register(self):
        app = self.make_app()
        pid = app.store.import_text("", TEXT)["id"]
        d = app.store.paper_dir(pid)
        st = glossary.generate(d, app.store.load(pid), app.localdict)
        self.assertEqual(st["state"], "done")
        self.assertTrue(glossary.is_current(d, app.store.load(pid)))
        self.assertEqual(app.dict.lookup("actuators")["found"], False)
        r = app.lookup("actuators")                                                 # 辞書に無い語は用語集の訳
        self.assertEqual((r["found"], r["headword"], r["note"]), (True, "actuator", "専門用語"))
        self.assertEqual(r["entries"][0]["mean"], "訳:actuator")
        self.assertIn("deformable mirror", app.lookup("DM")["entries"][0]["mean"])   # 略語は元の語も出す
        self.assertEqual(app.lookup("mirror")["source"], "EJDict")                   # 辞書にある語はそのまま
        app.vocab.record(r, pid, "1. Introduction", "Each actuator pushes the mirror.")
        self.assertTrue(app.vocab.has("actuator"))

    def test_translator_retry(self):
        exe = self.tmp / "flaky"
        exe.write_text(FAKE % (sys.executable, str(self.tmp / "flaky_calls"), 1))     # 1回目だけ「未導入」
        exe.chmod(0o755)
        os.environ["PAPER_READER_TRANSLATOR"] = str(exe)
        paper = {"sections": [{"id": "s0", "sentences": [{"t": "One."}, {"t": "Two."}]}]}
        d = self.tmp / "p"
        d.mkdir()
        st = translate.generate(d, paper)
        self.assertEqual((st["state"], st["retried"]), ("done", 1))
        exe.write_text(FAKE % (sys.executable, str(self.tmp / "never"), 99))           # ずっと「未導入」なら need_install
        (d / "translation_cache.json").unlink()
        st = translate.generate(d, paper)
        self.assertEqual(st["state"], "need_install")
        self.assertEqual(st["raw"]["status"], "supported")


if __name__ == "__main__":
    unittest.main()
