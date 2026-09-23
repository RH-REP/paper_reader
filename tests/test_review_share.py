"""復習（FSRS）・スマホとの受け渡し（zip / 記録の合わせ込み / Wi‑Fi の受け渡しページ）のテスト。

  .venv/bin/python -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
import bundle  # noqa: E402
import make_sample_pdf  # noqa: E402
import share  # noqa: E402
import srs  # noqa: E402
from store import Store  # noqa: E402
from vocab import Vocab  # noqa: E402

T0 = srs.parse("2026-09-24T01:00:00+00:00")


def found(head, mean="意味"):
    return {"found": True, "headword": head, "query": head, "source": "EJDict", "entries": [{"word": head, "mean": mean}]}


class SrsTest(unittest.TestCase):
    def test_new_card_intervals_match_anki_defaults(self):
        self.assertEqual(srs.preview(None, T0), {1: "1分", 2: "6分", 3: "10分", 4: "8日"})

    def test_replay_is_order_independent_of_input(self):
        logs = [{"rating": 3, "reviewed_at": "2026-09-24T01:00:00+00:00"},
                {"rating": 3, "reviewed_at": "2026-09-24T01:10:00+00:00"},
                {"rating": 1, "reviewed_at": "2026-09-26T09:00:00+00:00"}]
        a, b = srs.replay(logs), srs.replay(list(reversed(logs)))
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(a.state.name, "Relearning")

    def test_human(self):
        self.assertEqual([srs.human(timedelta(minutes=10)), srs.human(timedelta(days=3)),
                          srs.human(timedelta(days=90)), srs.human(timedelta(days=730))], ["10分", "3日", "3か月", "2年"])


class VocabReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.v = Vocab(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_queue_answer_undo(self):
        for w in ["alpha", "beta"]:
            self.v.record(found(w), "abcd1234", "1 INTRO", f"The {w} sentence.", "abcd1234/s1_1")
        q = self.v.queue(T0)
        self.assertEqual(q["card"]["headword"], "alpha")          # 先に引いた語から
        self.assertEqual(q["counts"], {"new": 2, "learning": 0, "review": 0, "total": 2, "new_waiting": 2})
        self.assertEqual(q["card"]["examples"][0]["audio_ref"], "abcd1234/s1_1")
        self.v.answer("alpha", 3, T0)                              # 正解 → 10分後
        q = self.v.queue(T0 + timedelta(minutes=1))
        self.assertEqual(q["card"]["headword"], "beta")            # alpha はまだ期限前
        self.assertEqual(q["counts"]["learning"], 0)
        q = self.v.queue(T0 + timedelta(minutes=11))
        self.assertEqual(q["card"]["headword"], "alpha")           # 期限の来た語が新しい語より先
        self.assertEqual(q["counts"]["learning"], 1)
        self.assertEqual(self.v.undo(), "alpha")
        self.assertEqual(self.v.queue(T0)["card"]["state"], "New")

    def test_new_per_day_limit(self):
        for i in range(3):
            self.v.record(found(f"w{i}"))
        self.assertEqual(self.v.queue(new_per_day=2)["counts"]["new"], 2)
        self.v.answer("w0", 4)                                      # 今日1語目
        q = self.v.queue(new_per_day=2)
        self.assertEqual(q["counts"]["new"], 1)
        self.v.answer("w1", 4)
        self.assertIsNone(self.v.queue(new_per_day=2)["card"])      # 枠を使い切った

    def test_delete_removes_reviews(self):
        wid = self.v.record(found("gamma"))
        self.v.answer("gamma", 3, T0)
        self.v.delete(wid)
        self.assertEqual(self.v.export(), {"words": [], "reviews": []})

    def test_merge_phone_progress_is_idempotent(self):
        self.v.record(found("alpha"))
        self.v.answer("alpha", 3, T0)
        prog = {"kind": bundle.PROGRESS_KIND, "version": 1, "device": "android",
                "lookups": [{"uid": "L1", "headword": "delta", "meaning": "デルタ", "source": "EJDict",
                             "looked_at": "2026-09-24T12:00:00", "sentence": "A delta."}],
                "reviews": [{"uid": "R1", "headword": "alpha", "rating": 3, "reviewed_at": "2026-09-24T01:10:00+00:00", "device": "android"},
                            {"uid": "R2", "headword": "delta", "rating": 4, "reviewed_at": "2026-09-24T12:01:00+00:00", "device": "android"},
                            {"uid": "R3", "headword": "gone", "rating": 3, "reviewed_at": "2026-09-24T12:01:00+00:00", "device": "android"}]}
        data = json.dumps(prog).encode()
        self.assertEqual(self.v.merge(bundle.read_progress(data)), {"lookups": 1, "reviews": 2})
        self.assertEqual(self.v.merge(bundle.read_progress(data)), {"lookups": 0, "reviews": 0})
        ex = self.v.export()
        self.assertEqual(sorted(w["headword"] for w in ex["words"]), ["alpha", "delta"])
        # alpha は Mac の正解＋スマホの正解 → 覚えている途中を抜けて復習に
        self.assertEqual(self.v.card_view("alpha")["state"], "Review")
        self.assertEqual(self.v.card_view("delta")["reviews"], 1)

    def test_read_progress_rejects_bad_files(self):
        for bad in [b"[]", b'{"kind": "other"}', json.dumps({"kind": bundle.PROGRESS_KIND, "version": 1,
                                                               "reviews": [{"uid": "x", "headword": "a", "rating": 9, "reviewed_at": "t"}]}).encode()]:
            with self.assertRaises(ValueError):
                bundle.read_progress(bad)


class BundleShareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = Store(cls.tmp / "data")
        cls.meta = cls.store.import_pdf(make_sample_pdf.make(cls.tmp / "s.pdf"))
        cls.v = Vocab(cls.tmp / "data")
        cls.v.record(found("lens"), cls.meta["id"], "1 INTRODUCTION", "x")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bundle_contents(self):
        z = bundle.make_bundle(self.store, self.v, [self.meta["id"]], self.tmp / "share")
        with zipfile.ZipFile(z) as f:
            names = set(f.namelist())
            man = json.loads(f.read("manifest.json"))
            voc = json.loads(f.read("vocab.json"))
        pid = self.meta["id"]
        self.assertEqual(man["kind"], bundle.BUNDLE_KIND)
        self.assertEqual(man["papers"], [pid])
        self.assertIn(f"papers/{pid}/sentences.json", names)
        self.assertEqual(voc["words"][0]["headword"], "lens")
        # 2回目は前の zip を消して作り直す
        z2 = bundle.make_bundle(self.store, self.v, [], self.tmp / "share")
        self.assertEqual([p.name for p in (self.tmp / "share").glob("*.zip")], [z2.name])

    def test_share_page_token_and_upload(self):
        if not share.lan_ip():
            self.skipTest("LAN のアドレスが無い")
        z = bundle.make_bundle(self.store, self.v, [self.meta["id"]], self.tmp / "share2")
        got = []
        srv = share.ShareServer(lambda b: got.append(b) or {"lookups": 0, "reviews": 0})
        try:
            info = srv.start(z, "https://example.invalid/pwa/")
            self.assertIn("<svg", info["qr"])
            page = urllib.request.urlopen(info["url"], timeout=5).read().decode()
            self.assertIn("paper_reader 受け渡し", page)
            self.assertEqual(urllib.request.urlopen(info["url"] + "bundle.zip", timeout=5).read(), z.read_bytes())
            wrong = info["url"].rsplit("/", 2)[0] + "/wrongtoken/"
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(wrong, timeout=5)
            r = urllib.request.urlopen(urllib.request.Request(info["url"] + "progress", data=b"{}", method="POST"), timeout=5)
            self.assertEqual(r.status, 200)
            self.assertEqual(got, [b"{}"])
        finally:
            srv.stop()
        self.assertFalse(srv.status()["active"])


if __name__ == "__main__":
    unittest.main()
