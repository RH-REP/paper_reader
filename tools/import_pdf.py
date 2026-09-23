#!/usr/bin/env python3
"""画面を使わずに PDF を取り込む。元の PDF はコピーするだけで動かさない。

  python3 tools/import_pdf.py --config config.json <論文.pdf> [...]
  python3 tools/import_pdf.py --config config.json --reextract <id>
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from server import load_config  # noqa: E402
from store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*", type=Path)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--reextract", metavar="ID")
    a = ap.parse_args()
    store = Store(load_config(a.config)["data_root"])
    metas = [store.reextract(a.reextract)] if a.reextract else [store.import_pdf(p) for p in a.pdfs]
    if not metas:
        ap.error("PDF か --reextract を指定する")
    for m in metas:
        tag = "既にある" if m.get("already") else "取り込んだ"
        print(f"{tag}: {m['id']}  {m['title']}  {m['pages']}ページ  {m['sections']}章  {m['sentences']}文"
              + (f"  OCR: {m['ocr_pages']}" if m["ocr_pages"] else ""))


if __name__ == "__main__":
    main()
