"""単語検索・単語帳・読み上げ音声のテスト（辞書は小さな架空のものを作って使う）。

  .venv/bin/python -m unittest discover -s tests -v
"""
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
import audio  # noqa: E402
import extract  # noqa: E402
import make_sample_pdf  # noqa: E402
from lookup import Dictionary, inflection_candidates  # noqa: E402
from vocab import Vocab  # noqa: E402

WORDS = [("mirror", "鏡 / …を映す"), ("satellite", "衛星"), ("measure", "…を測る"), ("run", "走る"),
         ("deform", "…を変形させる"), ("actuate", "…を作動させる"), ("flown", "flyの過去分詞"), ("study", "勉強")]


def make_dict(root: Path):
    d = root / "dict"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "ejdict.sqlite")
    con.execute("CREATE TABLE words (word TEXT, lower TEXT, mean TEXT)")
    con.executemany("INSERT INTO words VALUES (?,?,?)", [(w, w, m) for w, m in WORDS])
    con.commit()
    con.close()


class LookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        make_dict(cls.tmp)
        cls.d = Dictionary(cls.tmp, online=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, q, head, note):
        r = self.d.lookup(q)
        self.assertTrue(r["found"], q)
        self.assertEqual((r["headword"], r["note"]), (head, note), q)

    def test_exact_and_case_and_punct(self):
        self.check("Mirror,", "mirror", None)
        self.check("flown", "flown", None)

    def test_inflections(self):
        self.check("satellites", "satellite", "原形")
        self.check("measured", "measure", "原形")
        self.check("running", "run", "原形")
        self.check("studies", "study", "原形")
        self.check("satellite's", "satellite", None)

    def test_derivations(self):
        self.check("deformable", "deform", "近い語")
        self.check("actuators", "actuate", "近い語")

    def test_hyphen_part(self):
        self.check("tip-mirror", "mirror", "部分")

    def test_not_found_offline(self):
        r = self.d.lookup("wavefront")
        self.assertFalse(r["found"])
        self.assertIn("weblio.jp/content/wavefront", r["weblio"])

    def test_rejects_non_words(self):
        self.assertFalse(self.d.lookup("12").get("found"))

    def test_candidates(self):
        self.assertIn("stop", inflection_candidates("stopped"))


class VocabTest(unittest.TestCase):
    def test_record_merges_and_deletes(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            make_dict(tmp)
            d, v = Dictionary(tmp, online=False), Vocab(tmp)
            a = v.record(d.lookup("mirrors"), "abcd1234", "1 INTRODUCTION", "Two mirrors were used.")
            b = v.record(d.lookup("mirror"), "abcd1234", "2 DESIGN", "The mirror is flat.")
            self.assertEqual(a, b)
            self.assertIsNone(v.record(d.lookup("wavefront")))       # 見つからない語は入れない
            rows = v.list()
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["headword"], rows[0]["lookups"]), ("mirror", 2))
            self.assertEqual(rows[0]["example"], "The mirror is flat.")
            self.assertTrue(v.delete(a))
            self.assertEqual(v.list(), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(shutil.which("say") and shutil.which("afconvert"), "macOS の say / afconvert が無い")
class AudioTest(unittest.TestCase):
    def test_generate_sentences_and_chapters(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            paper = extract.extract_file(make_sample_pdf.make(tmp / "s.pdf"))
            paper["sections"] = paper["sections"][:4]            # Abstract, 1, 2, 2.1 だけで速く
            d = tmp / "paper"
            d.mkdir()
            st = audio.generate(d, paper, rate=260)
            self.assertEqual(st["state"], "done", st.get("error"))
            its = audio.items(paper)
            self.assertEqual(st["total"], len(its))
            for it in its:
                self.assertTrue((d / "audio" / f"{it['id']}.m4a").stat().st_size > 1000, it["id"])
            self.assertFalse((d / "audio" / "_wav").exists())      # 作業用の WAV は消える
            files = sorted(p.name for p in (d / "export").glob("*.m4a"))
            self.assertEqual(files, ["00_all.m4a", "01_Abstract.m4a", "02_1_INTRODUCTION.m4a", "03_2_REQUIREMENTS.m4a"])
            # 章「2」は節 2.1 も含むので、2 の見出し＋文より長い
            info = subprocess.run(["afinfo", str(d / "export" / "03_2_REQUIREMENTS.m4a")], capture_output=True, text=True).stdout
            self.assertIn("aac", info)
            self.assertEqual(audio.status(d)["state"], "done")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_concat_inserts_silence(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            a = tmp / "a.wav"
            audio._say("one", a, None, 200)
            with wave.open(str(a)) as w:
                n = w.getnframes()
            audio._concat([(a, 0.5), (a, 0.0)], tmp / "o.wav")
            with wave.open(str(tmp / "o.wav")) as w:
                self.assertEqual(w.getnframes(), 2 * n + int(audio.RATE_HZ * 0.5))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
