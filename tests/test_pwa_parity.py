"""スマホ版の復習計算（pwa/js/srs.js）が Mac 版（py-fsrs）と同じ結果になるかを、ランダムな答えの列で確かめる。
Node.js があるときだけ走る。

  .venv/bin/python -m unittest tests.test_pwa_parity -v
"""
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import srs  # noqa: E402

JS = """
import { replay, preview } from %(srs)s;
import { readFileSync } from "node:fs";
const cases = JSON.parse(readFileSync(%(cases)s, "utf8"));
const out = cases.map((c) => { const k = replay(c.logs);
  return { due: k.due, state: k.state, s: k.stability, d: k.difficulty, p: preview(k, new Date(k.due)) }; });
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js が無い")
class ParityTest(unittest.TestCase):
    def test_random_sequences_match(self):
        rnd = random.Random(11)
        cases, expect = [], []
        for _ in range(400):
            t = srs.parse("2026-09-24T01:00:00+00:00")
            logs, card = [], None
            for _ in range(rnd.randint(1, 14)):
                r = rnd.choices([1, 2, 3, 4], [2, 2, 5, 1])[0]
                logs.append({"rating": r, "reviewed_at": t.isoformat()})
                card = srs.answer(card, r, t)
                t += (card.due - t) * rnd.choice([0.2, 1, 1, 1.6, 4]) + timedelta(seconds=rnd.randint(0, 7200))
            cases.append({"logs": logs})
            expect.append({"due": int(card.due.timestamp() * 1000), "state": card.state.value,
                           "s": card.stability, "d": card.difficulty, "p": srs.preview(card, card.due)})
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "cases.json").write_text(json.dumps(cases))
            (tmp / "run.mjs").write_text(JS % {"srs": json.dumps((HERE / "pwa" / "js" / "srs.js").as_uri()),
                                               "cases": json.dumps(str(tmp / "cases.json"))})
            got = json.loads(subprocess.run(["node", str(tmp / "run.mjs")], capture_output=True, text=True, check=True).stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        for e, g in zip(expect, got):
            self.assertEqual((g["due"], g["state"]), (e["due"], e["state"]))
            self.assertAlmostEqual(g["s"], e["s"], places=9)
            self.assertAlmostEqual(g["d"], e["d"], places=9)
            self.assertEqual({int(k): v for k, v in g["p"].items()}, e["p"])


if __name__ == "__main__":
    unittest.main()
