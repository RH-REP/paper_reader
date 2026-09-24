"""貼り付けたテキストの取り込みと、画面からの章の編集（段の上げ下げ・分ける・つなぐ・名前）のテスト。
音声・訳は、同じ文を作り直さない（控えから写す）ことを確かめる。文章はすべて架空のもの。

  .venv/bin/python -m unittest tests.test_text_sections -v
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import audio  # noqa: E402
import bundle  # noqa: E402
import extract  # noqa: E402
import server  # noqa: E402
import translate  # noqa: E402
from store import Store  # noqa: E402
from vocab import Vocab  # noqa: E402

TEXT = """A Fictional Study of Mirrors

We study nothing in particular. This is a test of Fig. 2 and Eq. (3).

1. Introduction
Deformable mirrors are widely used. They correct the wave-
front of light.

1.1 Background
Mirrors were invented long ago.

2. Methods
The method is simple. We measured it.
Short line

Results
The results were fine (Smith et al., 2020). See [3].
"""


def outline(paper):
    return [(s["level"], s["title"], len(s["sentences"])) for s in paper["sections"]]


class BuildFromTextTest(unittest.TestCase):
    def test_headings_levels_and_sentences(self):
        p = extract.build_from_text("", TEXT)
        self.assertEqual(p["title"], "A Fictional Study of Mirrors")
        self.assertEqual(outline(p), [(1, "A Fictional Study of Mirrors", 2), (1, "1. Introduction", 2),
                                      (2, "1.1 Background", 1), (1, "2. Methods", 3), (1, "Results", 2)])
        intro = p["sections"][1]
        self.assertEqual(intro["sentences"][1]["t"], "They correct the wavefront of light.")   # 行末ハイフンをつなぐ
        self.assertEqual(intro["speech_title"], "Section 1. Introduction.")
        self.assertEqual(p["sections"][4]["sentences"][0]["s"], "The results were fine.")    # 引用は読まない
        self.assertEqual(p["sections"][3]["sentences"][2]["t"], "Short line")                # 空行の前の短い行は本文

    def test_markdown_and_given_title(self):
        p = extract.build_from_text("My Title", "## Part A\nOne sentence here.\n\n### Part B\nTwo sentences. Yes.")
        self.assertEqual(p["title"], "My Title")
        self.assertEqual(outline(p), [(2, "Part A", 1), (3, "Part B", 2)])                  # 見出しより前に文が無い

    def test_plain_text_without_headings(self):
        p = extract.build_from_text("", "This is one long sentence that is clearly not a heading at all. And another.")
        self.assertEqual(len(p["sections"]), 1)
        self.assertEqual(len(p["sections"][0]["sentences"]), 2)


class StoreTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "data")
        self.m = self.store.import_text("", TEXT)
        self.pid = self.m["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_import_is_idempotent_and_has_no_pdf(self):
        d = self.store.paper_dir(self.pid)
        self.assertEqual(self.m["source"], "text")
        self.assertEqual((self.m["title"], self.m["sections"], self.m["sentences"]), ("A Fictional Study of Mirrors", 5, 10))
        self.assertTrue((d / "original.txt").exists())
        self.assertFalse((d / "original.pdf").exists())
        self.assertFalse((d / "figures.json").exists())
        self.assertTrue(self.store.import_text("", TEXT)["already"])
        with self.assertRaises(ValueError):
            self.store.import_text("x", "   ")

    def test_indent_moves_only_the_subtree(self):
        # 「2. Methods」を下げると、続く「Results」（同じ段）は動かない
        p = self.store.edit_sections(self.pid, "indent", 3)
        self.assertEqual([s["level"] for s in p["sections"]], [1, 1, 2, 2, 1])
        # 「1. Introduction」を上げられない（1段目）・下げると下にぶら下がる 1.1 と（いま2段目の）2. Methods も一緒に下がる
        with self.assertRaises(ValueError):
            self.store.edit_sections(self.pid, "outdent", 1)
        p = self.store.edit_sections(self.pid, "indent", 1)
        self.assertEqual([s["level"] for s in p["sections"]], [1, 2, 3, 3, 1])
        with self.assertRaises(ValueError):                  # 前の章より2段深くはできない
            self.store.edit_sections(self.pid, "indent", 2)
        with self.assertRaises(ValueError):                  # 先頭の章は下げられない
            self.store.edit_sections(self.pid, "indent", 0)
        p = self.store.edit_sections(self.pid, "outdent", 1)
        self.assertEqual([s["level"] for s in p["sections"]], [1, 1, 2, 2, 1])

    def test_split_merge_rename_and_manual(self):
        d = self.store.paper_dir(self.pid)
        # 長い文から分ける（文はそのまま新しい章の最初の文）
        p = self.store.edit_sections(self.pid, "split", 3, 1, title="Measurement")
        self.assertEqual(outline(p)[3:5], [(1, "2. Methods", 1), (1, "Measurement", 2)])
        # 短い文を見出しにする（本文から外す）
        p = self.store.edit_sections(self.pid, "split", 4, 1, take=True)
        self.assertEqual(outline(p)[4:6], [(1, "Measurement", 1), (1, "Short line", 0)])
        # 見出しを文に戻して前の章に入れる
        p = self.store.edit_sections(self.pid, "merge", 5)
        self.assertEqual(outline(p)[4], (1, "Measurement", 2))
        self.assertEqual(p["sections"][4]["sentences"][1]["t"], "Short line")
        p = self.store.edit_sections(self.pid, "rename", 4, title="3. Measurement")
        self.assertEqual((p["sections"][4]["number"], p["sections"][4]["speech_title"]), ("3", "Section 3. Measurement."))
        self.assertEqual([s["id"] for s in p["sections"]], [f"s{i}" for i in range(len(p["sections"]))])
        self.assertEqual(p["manual"]["by"], "user")
        self.assertTrue((d / "sentences.orig.json").exists())
        self.assertEqual(json.loads((d / "sentences.orig.json").read_text())["sections"][3]["title"], "2. Methods")
        # 取り出し方の版が変わっても、編集済みは上書きしない
        meta = json.loads((d / "meta.json").read_text())
        meta["extractor_version"] = "old"
        (d / "meta.json").write_text(json.dumps(meta))
        self.assertEqual(outline(self.store.load(self.pid)), outline(p))
        self.assertEqual(self.store.list()[0]["sections"], len(p["sections"]))
        with self.assertRaises(ValueError):
            self.store.edit_sections(self.pid, "merge", 0)
        with self.assertRaises(ValueError):
            self.store.edit_sections(self.pid, "split", 1, 0)   # 章の最初の文の前では分けない
        with self.assertRaises(ValueError):
            self.store.edit_sections(self.pid, "rename", 1, title=" ")
        with self.assertRaises(ValueError):
            self.store.edit_sections(self.pid, "jump", 1)


FAKE = """#!%s
import json, sys
j = json.load(sys.stdin)
with open(%r, "a") as f:
    f.write(str(len(j["texts"])) + "\\n")
print(json.dumps({"status": "installed", "translations": ["訳:" + t[:12] for t in j["texts"]]}))
"""


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "data")
        self.pid = self.store.import_text("", TEXT)["id"]
        self.d = self.store.paper_dir(self.pid)
        self.calls = self.tmp / "calls.txt"
        exe = self.tmp / "fake_tr"
        exe.write_text(FAKE % (sys.executable, str(self.calls)))
        exe.chmod(0o755)
        os.environ["PAPER_READER_TRANSLATOR"] = str(exe)

    def tearDown(self):
        os.environ.pop("PAPER_READER_TRANSLATOR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_translation_reuses_same_sentences(self):
        translate.generate(self.d, self.store.load(self.pid))
        self.assertEqual(self.calls.read_text().split(), ["10"])
        p = self.store.edit_sections(self.pid, "split", 3, 1, title="Measurement")   # 文は同じ、番号だけずれる
        st, ja = translate.view(self.d, p)
        self.assertEqual(st["state"], "running")                          # 作り直す前でも、控えから訳が出る
        self.assertEqual(ja["s4_1"], "訳:We measured ")
        z = bundle.make_bundle(self.store, Vocab(self.tmp / "data"), [self.pid], self.tmp / "share")
        with zipfile.ZipFile(z) as f:                                     # スマホに渡す訳も今の番号
            self.assertEqual(json.loads(f.read(f"papers/{self.pid}/translation.json"))["items"]["s4_1"], "訳:We measured ")
        st = translate.generate(self.d, p)
        self.assertEqual((st["state"], st["done"]), ("done", 10))
        self.assertEqual(self.calls.read_text().split(), ["10"])          # 訳し直していない
        self.assertEqual(st["items"]["s4_1"], "訳:We measured ")
        p = self.store.edit_sections(self.pid, "merge", 4)               # 見出しが文に戻る = 新しい文が1つ
        translate.generate(self.d, p)
        self.assertEqual(self.calls.read_text().split(), ["10", "1"])

    @unittest.skipUnless(shutil.which("say") and shutil.which("afconvert"), "macOS の say / afconvert が無い")
    def test_audio_reuses_same_sentences(self):
        st = audio.generate(self.d, self.store.load(self.pid), rate=300)
        self.assertEqual((st["state"], st["reused"]), ("done", 0), st.get("error"))
        n = len(audio.items(self.store.load(self.pid)))
        p = self.store.edit_sections(self.pid, "indent", 3)             # 段だけ変えても文の番号は同じ
        self.assertTrue(audio.is_current(self.d, p))
        p = self.store.edit_sections(self.pid, "split", 3, 1, title="Measurement")
        self.assertFalse(audio.is_current(self.d, p))
        z = bundle.make_bundle(self.store, Vocab(self.tmp / "data"), [self.pid], self.tmp / "share")
        with zipfile.ZipFile(z) as f:                                     # 番号のずれた古い音声は渡さない
            self.assertFalse([x for x in f.namelist() if "/audio/" in x or x.endswith("durations.json")])
        st = audio.generate(self.d, p, rate=300)
        self.assertEqual((st["state"], st["total"], st["reused"]), ("done", n + 1, n))   # 新しい見出しだけ作る
        self.assertTrue((self.d / "audio" / "s4_h.m4a").exists())
        self.assertEqual(len(list((self.d / "audio" / "cache").glob("*.wav"))), n + 1)
        self.assertGreater(st["durations"]["s4_1"], 0.3)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        server.Handler.app = self.app = server.App(cfg)
        self.app.start_audio = lambda pid, force=False: False            # 音声・訳は作らない（ここでは API の形だけ）
        self.app.start_translate = lambda pid, force=False: False
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            return 200, json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        return json.loads(urllib.request.urlopen(self.base + path).read())

    def test_import_text_and_edit(self):
        code, m = self.post("/api/import_text", {"title": "Paste", "text": TEXT})
        self.assertEqual((code, m["source"], m["title"]), (200, "text", "Paste"))
        p = self.get(f"/api/papers/{m['id']}")
        self.assertFalse(p["has_pdf"])
        self.assertEqual(self.get(f"/api/papers/{m['id']}/figures")["items"], [])
        code, p = self.post(f"/api/papers/{m['id']}/sections", {"op": "indent", "sec": 4})
        self.assertEqual((code, [s["level"] for s in p["sections"]]), (200, [1, 1, 2, 1, 2]))
        self.assertIn("audio", p)
        code, e = self.post(f"/api/papers/{m['id']}/sections", {"op": "indent", "sec": 4})
        self.assertEqual(code, 400)
        self.assertIn("2段", e["error"])
        code, _ = self.post("/api/import_text", {"text": ""})
        self.assertEqual(code, 400)


class JobLoopTest(unittest.TestCase):
    """作っている最中に章を編集したら、終わったあと今の文でもう一度作る。"""

    def test_reruns_when_text_changes_during_job(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = tmp / "config.json"
            cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
            app = server.App(cfg)
            pid = app.store.import_text("", TEXT)["id"]
            seen, gate = [], threading.Event()

            def run(d, paper):
                seen.append(len(paper["sections"]))
                if len(seen) == 1:
                    gate.wait(5)                                  # 1回目の途中で編集する
                (d / "fake.json").write_text(json.dumps({"n": len(paper["sections"])}))

            def current(d, paper):
                f = d / "fake.json"
                return f.exists() and json.loads(f.read_text())["n"] == len(paper["sections"])

            self.assertTrue(app._start("fake-" + pid, pid, True, run, current, ("done",)))
            time.sleep(0.2)
            app.store.edit_sections(pid, "split", 3, 1, title="Measurement")
            self.assertFalse(app._start("fake-" + pid, pid, False, run, current, ("done",)))
            gate.set()
            for _ in range(50):
                if "fake-" + pid not in app.jobs:
                    break
                time.sleep(0.1)
            self.assertEqual(seen, [5, 6])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
