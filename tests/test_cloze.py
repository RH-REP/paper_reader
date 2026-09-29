"""復習の「空欄を埋める」モードのテスト。判定は shared.js（node で）、例文の訳と引いた形はサーバー（/api/review）で確かめる。
文章はすべて架空のもの。

  .venv/bin/python -m unittest tests.test_cloze -v
"""
import json
import os
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
import translate  # noqa: E402

TEXT = """A Fictional Mirror

1. Introduction
Each of the actuators pushes the mirror. The mirror is thin.
"""

FAKE = """#!%s
import json, sys
j = json.load(sys.stdin)
print(json.dumps({"status": "installed", "translations": ["訳:" + t for t in j["texts"]]}))
"""


@unittest.skipUnless(shutil.which("node"), "node が無い")
class ClozeJsTest(unittest.TestCase):
    def run_js(self, body: str):
        js = f'import * as S from "{(HERE / "assets" / "shared.js").as_posix()}";\n{body}'
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_parts_check_and_suggest(self):
        o = self.run_js("""console.log(JSON.stringify({
  a: S.clozeParts("Each of the actuators pushes the mirror.", "actuator", "actuators"),
  b: S.clozeParts("The mirror was deformed by heat.", "deform", null),
  none: S.clozeParts("Nothing here.", "actuator", "actuators"),
  checks: ["actuators", "Actuator", "actuater", "mirror", ""].map((x) => S.checkCloze(x, "actuators", "actuator")),
  forms: [S.checkCloze("deforms", "deformed", "deform"), S.checkCloze("stop", "stopped", "stop")],
  suggest: [S.suggestRating("wrong", false), S.suggestRating("near", false), S.suggestRating("exact", true), S.suggestRating("form", false)],
  pick: S.pickExample({headword: "actuator", reviews: 1, examples: [{sentence: "No word."}, {sentence: "The actuator moved.", ja: "動いた"},
                                                                     {sentence: "Actuators are small.", ja: "小さい"}]}).sentence,
  nopick: S.pickExample({headword: "actuator", examples: [{sentence: "No word."}]}),
  cite: S.pickExample({headword: "actuator", examples: [{sentence: "The actuator moved (Smith et al., 2020) as in [3]."}]}).parts,
}));""")
        self.assertEqual(o["a"], {"before": "Each of the ", "answer": "actuators", "after": " pushes the mirror."})
        self.assertEqual(o["b"]["answer"], "deformed")
        self.assertIsNone(o["none"])
        self.assertEqual(o["checks"], ["exact", "form", "near", "wrong", "wrong"])   # 活用形も正解、1字違いは惜しい
        self.assertEqual(o["forms"], ["form", "form"])
        self.assertEqual(o["suggest"], [1, 2, 2, 3])                                  # 間違い→1、惜しい・ヒント→2、正解→3
        self.assertEqual(o["pick"], "Actuators are small.")                           # 訳のある例文を答えた回数で順に
        self.assertIsNone(o["nopick"])
        self.assertEqual(o["cite"]["after"], " moved as in.")                         # 問題の文から引用を外す


@unittest.skipUnless(shutil.which("node"), "node が無い")
class DictationJsTest(unittest.TestCase):
    def test_blanks_levels_and_grading(self):
        js = f'import * as S from "{(HERE / "assets" / "shared.js").as_posix()}";\n' + r"""
const t = S.tokenize("Deformable mirrors correct the wavefront of light in the 3 large telescopes of 2020.");
let seed = 7; const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const sizes = [1, 2, 3, 4, 5].map((lv) => S.pickBlanks(t, lv, { rand }).size);
const words = t.filter((x) => x.word).length;
const blanked = [...S.pickBlanks(t, 4, { rand })].map((i) => t[i].t);
const counts = {}; for (let k = 0; k < 3000; k++) { const i = [...S.pickBlanks(t, 1)][0]; counts[t[i].t] = (counts[t[i].t] || 0) + 1; }
console.log(JSON.stringify({ sizes, words, blanked, spread: Object.keys(counts).length, min: Math.min(...Object.values(counts)),
  g: [S.gradeWord("Mirrors", "mirrors"), S.gradeWord("mirror", "mirrors"), S.gradeWord("xx", "mirrors"), S.gradeWord("", "mirrors")],
  full: S.gradeSentence("deformable mirors correct wavefront of light in the large telescopes extra", "Deformable mirrors correct the wavefront of light."),
  adv: [S.levelAdvice(Array(12).fill({ level: 2, score: 0.95 }), 2).advice, S.levelAdvice(Array(12).fill({ level: 3, score: 0.3 }), 3).advice,
        S.levelAdvice(Array(5).fill({ level: 2, score: 1 }), 2).advice] }));"""
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        self.assertEqual(o["sizes"][0], 1)
        self.assertTrue(o["sizes"][0] <= o["sizes"][1] <= o["sizes"][2] <= o["sizes"][3] < o["sizes"][4] == o["words"])   # レベルで空欄が増える
        self.assertTrue({"the", "of", "in"}.isdisjoint(o["blanked"]))            # Lv4 は内容語だけ（数字も空欄にしない）
        self.assertEqual((o["spread"], o["min"] > 200), (7, True))               # Lv1 の1語はランダムで偏りが無い（7語×約430回）
        self.assertEqual(o["g"], ["ok", "near", "ng", "ng"])
        self.assertEqual([w["r"] for w in o["full"]["words"]], ["ok", "near", "ok", "ng", "ok", "ok", "ok"])   # 抜けた the は ×
        self.assertEqual(o["full"]["extra"][-1], "extra")
        self.assertEqual(o["adv"], ["up", "down", None])                          # 10 文たまるまでは案内しない


class ClozeServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        exe = self.tmp / "fake_tr"
        exe.write_text(FAKE % sys.executable)
        exe.chmod(0o755)
        os.environ["PAPER_READER_TRANSLATOR"] = str(exe)
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": "data", "online_dict": False}))
        server.Handler.app = self.app = server.App(cfg)
        self.app.start_audio = lambda pid, force=False: False
        self.app.start_translate = lambda pid, force=False: False
        self.app.start_glossary = lambda pid, force=False: False
        self.pid = self.app.store.import_text("", TEXT)["id"]
        translate.generate(self.app.store.paper_dir(self.pid), self.app.store.load(self.pid))
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        os.environ.pop("PAPER_READER_TRANSLATOR", None)
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_review_card_has_query_and_translation(self):
        res = {"found": True, "headword": "actuator", "query": "actuators", "source": "test",
               "entries": [{"word": "actuator", "mean": "作動装置"}]}
        self.app.vocab.record(res, self.pid, "1. Introduction", "Each of the actuators pushes the mirror.")
        q = json.loads(urllib.request.urlopen(self.base + "/api/review").read())
        ex = q["card"]["examples"][0]
        self.assertEqual((ex["query"], ex["ja"]), ("actuators", "訳:Each of the actuators pushes the mirror."))
        z = bundle.make_bundle(self.app.store, self.app.vocab, [self.pid], self.tmp / "share", self.app.state)
        with zipfile.ZipFile(z) as f:                                                 # スマホにも訳と引いた形が渡る
            w = json.loads(f.read("vocab.json"))["words"][0]["examples"][0]
        self.assertEqual((w["query"], w["ja"]), ("actuators", "訳:Each of the actuators pushes the mirror."))


if __name__ == "__main__":
    unittest.main()
