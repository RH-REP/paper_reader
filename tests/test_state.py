"""しおりと文の印（state.py）、Mac ⇄ スマホの受け渡し、印の Markdown、shared.js のテスト。文章はすべて架空のもの。

  .venv/bin/python -m unittest tests.test_state -v
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
import bundle  # noqa: E402
import server  # noqa: E402
from state import State  # noqa: E402

TEXT = """A Fictional Note

1. Introduction
Mirrors bend light. Figure 2 shows the setup.

2. Results
The mirror worked. We are done.
"""


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.st = State(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_toggle_note_and_tombstone(self):
        m = self.st.toggle_mark("p1", "Mirrors bend light.", "1. Introduction")
        self.assertEqual(len(self.st.marks("p1")), 1)
        self.st.set_note(m["uid"], "  use in section 2  ")
        self.assertEqual(self.st.marks("p1")[0]["note"], "use in section 2")
        self.assertIsNone(self.st.toggle_mark("p1", "Mirrors bend light."))   # 2回目で外れる
        self.assertEqual(self.st.marks("p1"), [])
        self.assertEqual(self.st.marks("p1", include_deleted=True)[0]["deleted"], 1)   # 消したことは残す

    def test_merge_newer_wins_and_is_idempotent(self):
        self.st.set_position({"paper_id": "p1", "sentence": "old", "done": 1, "total": 9, "updated_at": "2026-09-25T01:00:00Z"})
        phone = {"positions": [{"paper_id": "p1", "sentence": "new", "done": 5, "total": 9, "updated_at": "2026-09-25T02:00:00.000Z"},
                               {"paper_id": "p2", "sentence": "x", "updated_at": "2026-09-25T02:00:00Z"}],
                 "marks": [{"uid": "u1", "paper_id": "p1", "sentence": "s", "updated_at": "2026-09-25T02:00:00Z", "deleted": False}]}
        self.assertEqual(self.st.merge(phone), {"positions": 2, "marks": 1})
        self.assertEqual(self.st.merge(phone), {"positions": 0, "marks": 0})     # 同じ記録は二重にならない
        self.assertEqual(self.st.positions()["p1"]["sentence"], "new")
        # Mac の現地時刻（+09:00）で新しいものは、古い UTC の記録に負けない
        self.st.set_position({"paper_id": "p1", "sentence": "mac", "updated_at": "2026-09-25T12:00:00+09:00"})
        self.st.merge({"positions": [{"paper_id": "p1", "sentence": "stale", "updated_at": "2026-09-25T02:30:00Z"}]})
        self.assertEqual(self.st.positions()["p1"]["sentence"], "mac")
        # スマホで外した印は、Mac でも外れる
        self.st.merge({"marks": [{"uid": "u1", "paper_id": "p1", "sentence": "s", "updated_at": "2026-09-25T03:00:00Z", "deleted": 1}]})
        self.assertEqual(self.st.marks("p1"), [])


class ServerStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        server.Handler.app = self.app = server.App(cfg)
        self.app.start_audio = lambda pid, force=False: False
        self.app.start_translate = lambda pid, force=False: False
        self.pid = self.app.store.import_text("", TEXT)["id"]
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())

    def get(self, path):
        return urllib.request.urlopen(self.base + path).read().decode()

    def test_marks_position_export_and_bundle(self):
        r = self.post("/api/marks/toggle", {"paper_id": self.pid, "sentence": "We are done.", "section": "2. Results"})
        self.post("/api/marks/toggle", {"paper_id": self.pid, "sentence": "Mirrors bend light.", "section": "1. Introduction"})
        self.post(f"/api/marks/{r['mark']['uid']}/note", {"note": "結論に使う"})
        self.post("/api/position", {"paper_id": self.pid, "sentence": "The mirror worked.", "section": "2. Results",
                                    "item_id": "s2_1", "offset": 1.5, "done": 6, "total": 9})
        paper = json.loads(self.get(f"/api/papers/{self.pid}"))
        self.assertEqual((len(paper["marks"]), paper["position"]["sentence"]), (2, "The mirror worked."))
        lst = json.loads(self.get("/api/papers"))[0]
        self.assertEqual((lst["marks"], lst["position"]["done"]), (2, 6))
        md = self.get(f"/api/marks/export?paper={self.pid}")
        # 論文の中の順（印を付けた順ではない）に、章ごとに並ぶ
        self.assertLess(md.index("### 1. Introduction"), md.index("### 2. Results"))
        self.assertIn("> We are done.\n\nメモ: 結論に使う", md)
        z = bundle.make_bundle(self.app.store, self.app.vocab, [self.pid], self.tmp / "share", self.app.state)
        with zipfile.ZipFile(z) as f:
            st = json.loads(f.read("state.json"))
        self.assertEqual((len(st["marks"]), len(st["positions"])), (2, 1))
        # スマホから戻る記録にしおりと印が入っていれば合わさる
        prog = {"kind": bundle.PROGRESS_KIND, "version": bundle.VERSION, "lookups": [], "reviews": [],
                "marks": [{**st["marks"][0], "deleted": 1, "updated_at": "2099-01-01T00:00:00Z"}]}
        res = self.app.merge_progress(json.dumps(prog).encode())
        self.assertEqual(res["state_marks"], 1)
        self.assertEqual(len(self.app.state.marks(self.pid)), 1)


class SharedJsTest(unittest.TestCase):
    def test_pwa_copy_is_identical(self):
        self.assertEqual((HERE / "assets" / "shared.js").read_bytes(), (HERE / "pwa" / "js" / "shared.js").read_bytes(),
                         "assets/shared.js を pwa/js/shared.js に写してください")

    @unittest.skipUnless(shutil.which("node"), "node が無い")
    def test_refs_and_resume(self):
        js = f"""
import {{ refKeys, labelKey, resumeIndex }} from "{(HERE / 'assets' / 'shared.js').as_posix()}";
const out = {{
  refs: ["as shown in Fig. 3(a) and Figs. 4 and 5.", "Figure A-1 shows it.", "see Table 2 and Eq. (3).", "the figure shows"].map(refKeys),
  labels: ["Fig. 3", "FIGURE 2 |", "Table 1", "Eq. (3)", "Fig. A-1", "Eq. (unnumbered)"].map(labelKey),
  resume: resumeIndex([{{show: "A", id: "s0_h", heading: true}}, {{show: "x", id: "s0_1"}}, {{show: "y", id: "s0_2"}}],
                      {{sentence: "y", item_id: "s9_9", offset: 3}}),
  fallback: resumeIndex([{{show: "x", id: "s0_1"}}, {{show: "z", id: "s0_2"}}], {{sentence: "gone", item_id: "s0_2"}}),
}};
console.log(JSON.stringify(out));
"""
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        self.assertEqual(o["refs"], [["figure:3", "figure:4", "figure:5"], ["figure:A-1"], ["table:2", "equation:3"], []])
        self.assertEqual(o["labels"], ["figure:3", "figure:2", "table:1", "equation:3", "figure:A-1", None])
        self.assertEqual(o["resume"], {"index": 2, "at": 3, "exact": True})       # 番号がずれても文の中身で戻る
        self.assertEqual(o["fallback"], {"index": 1, "at": 0, "exact": False})    # 文が無ければ音声 id


if __name__ == "__main__":
    unittest.main()
