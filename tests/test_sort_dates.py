"""論文の一覧の並べ替え: PDF の作成日・元のファイルの日付を覚える（store・tools/fill_dates.py）と、並べ方（shared.js）。

  .venv/bin/python -m unittest tests.test_sort_dates -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import store  # noqa: E402
from test_extract import make_ao_style  # noqa: E402


def make_pdf(path, created="D:20240827101530+09'00'"):
    import pymupdf
    make_ao_style(path)
    doc = pymupdf.open(path)
    doc.set_metadata({"creationDate": created} if created else {})
    out = path.with_name("m_" + path.name)
    doc.save(out)
    return out


class DatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pdf_created_and_file_date_kept(self):
        pdf = make_pdf(self.tmp / "a.pdf")
        os.utime(pdf, (1700000000, 1700000000))
        s = store.Store(self.tmp / "data")
        m = s.import_pdf(pdf)                                         # 道具から: 元のファイルの日付
        self.assertEqual(m["pdf_created"], "2024-08-27T10:15:30")
        self.assertEqual(m["downloaded_at"], datetime.fromtimestamp(1700000000).isoformat(timespec="seconds"))
        pdf2 = make_pdf(self.tmp / "b.pdf", created=None)
        m2 = s.import_pdf(pdf2, file_mtime=None)                      # 画面から日付が来なかった: 書かない
        self.assertNotIn("downloaded_at", m2)

    def test_fill_dates_finds_original_by_hash(self):
        pdf = make_pdf(self.tmp / "a.pdf")
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"data_root": str(self.tmp / "data")}))
        s = store.Store(self.tmp / "data")
        m = s.import_pdf(pdf, file_mtime=None)
        meta_p = s.papers / m["id"] / "meta.json"
        meta = json.loads(meta_p.read_text())
        meta.pop("pdf_created")                                       # 前からある論文（日付なし）にする
        meta_p.write_text(json.dumps(meta))
        src = self.tmp / "dl"
        src.mkdir()
        shutil.copy2(pdf, src / "renamed.pdf")
        os.utime(src / "renamed.pdf", (1600000000, 1600000000))
        (src / "other.pdf").write_bytes(b"%PDF-1.4 not the same")
        r = subprocess.run([sys.executable, str(HERE / "tools" / "fill_dates.py"), "--config", str(cfg), str(src)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = json.loads(meta_p.read_text())
        self.assertEqual(meta["pdf_created"], "2024-08-27T10:15:30")
        self.assertEqual(meta["downloaded_at"], datetime.fromtimestamp(1600000000).isoformat(timespec="seconds"))


@unittest.skipUnless(shutil.which("node"), "node が無い")
class SortJsTest(unittest.TestCase):
    def test_sort_papers(self):
        js = f'import * as S from "{(HERE / "assets" / "shared.js").as_posix()}";\n' + r"""
const ms = [
  { id: "a", title: "beta", pages: 10, imported_at: "2026-10-01", pdf_created: "2020-01-01", downloaded_at: "2026-09-01" },
  { id: "b", title: "Alpha", pages: 3, imported_at: "2026-10-03", pdf_created: "2024-05-01" },
  { id: "c", title: "gamma", pages: 30, imported_at: "2026-10-02", downloaded_at: "2026-09-20" },
];
const ids = (k, d) => S.sortPapers(ms, k, d).map((m) => m.id).join("");
console.log(JSON.stringify({ imp: ids("imported", true), cr: ids("created", true), crAsc: ids("created", false),
  dl: ids("downloaded", true), title: ids("title", false), pages: ids("pages", true),
  label: S.sortLabel(ms[2], "created"), label2: S.sortLabel(ms[0], "downloaded") }));"""
        p = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        o = json.loads(p.stdout)
        self.assertEqual(o["imp"], "bca")
        self.assertEqual(o["cr"], "bac")                 # 作成日の無い c は最後
        self.assertEqual(o["crAsc"], "abc")              # 古い順でも分からないものは最後
        self.assertEqual(o["dl"], "cab")
        self.assertEqual(o["title"], "bac")              # 大文字小文字を区別しない
        self.assertEqual(o["pages"], "cab")
        self.assertEqual(o["label"], "作成 不明")
        self.assertEqual(o["label2"], "ダウンロード 2026-09-01")


if __name__ == "__main__":
    unittest.main()
