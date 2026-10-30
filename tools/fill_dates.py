#!/usr/bin/env python3
"""取り込み済みの論文に、並べ替え用の日付を足す（無いものだけ。元のファイルは読むだけで動かさない）。

  python3 tools/fill_dates.py --config config.json [探すフォルダ ...]

- pdf_created: data/papers/<id>/original.pdf の中の作成日（無ければ更新日）
- downloaded_at: 探すフォルダの中から、大きさと sha256 が同じ PDF を探し、そのファイルの日付（更新日時）
  見つからない論文は書かない（画面では「不明」として並べ替えの最後に回る）
"""
import argparse
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from server import load_config  # noqa: E402
from store import Store, _write_json, pdf_created  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_originals(dirs: list[Path], want: dict[int, set[str]]) -> dict[str, Path]:
    """{sha256: 見つかったファイル}。大きさが合うものだけ sha256 を計算する。同じ中身が複数あれば日付の古いほう。"""
    found: dict[str, Path] = {}
    for root in dirs:
        for dp, dns, fns in os.walk(root):
            dns[:] = [x for x in dns if not x.startswith(".")]
            for fn in fns:
                if not fn.lower().endswith(".pdf"):
                    continue
                p = Path(dp) / fn
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if size not in want:
                    continue
                h = sha256(p)
                if h in want[size] and (h not in found or p.stat().st_mtime < found[h].stat().st_mtime):
                    found[h] = p
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="*", type=Path)
    ap.add_argument("--config", type=Path, default=None)
    a = ap.parse_args()
    store = Store(load_config(a.config)["data_root"])
    metas = [m for m in store.list() if m.get("source") != "text"]
    want: dict[int, set[str]] = {}
    for m in metas:
        pdf = store.papers / m["id"] / "original.pdf"
        if not m.get("downloaded_at") and pdf.exists():
            want.setdefault(pdf.stat().st_size, set()).add(m["sha256"])
    found = find_originals(a.dirs, want) if a.dirs and want else {}
    for m in metas:
        d = store.papers / m["id"]
        changed = []
        if not m.get("pdf_created") and (d / "original.pdf").exists():
            c = pdf_created(d / "original.pdf")
            if c:
                m["pdf_created"] = c
                changed.append(f"作成 {c[:10]}")
        if not m.get("downloaded_at") and m["sha256"] in found:
            m["downloaded_at"] = datetime.fromtimestamp(found[m["sha256"]].stat().st_mtime).isoformat(timespec="seconds")
            changed.append(f"ダウンロード {m['downloaded_at'][:10]}")
        if changed:
            _write_json(d / "meta.json", m)
        print(f"{m['id']}  {m.get('title', '')[:50]:50}  {'・'.join(changed) or '（変更なし）'}"
              + ("" if m.get("downloaded_at") else "  ダウンロード日: 不明"))


if __name__ == "__main__":
    main()
