#!/usr/bin/env python3
"""画面を使わずに PDF を取り込み、読み上げ音声もまとめて作る。元の PDF はコピーするだけで動かさない。

  python3 tools/import_pdf.py --config config.json <論文.pdf> [...]
  python3 tools/import_pdf.py --config config.json --reextract <id>     # 章と文・音声を作り直す
  python3 tools/import_pdf.py --config config.json --audio <id>         # 音声だけ作り直す
  --no-audio を付けると音声を作らない（あとでサーバーを起動したときに作られる）
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import audio  # noqa: E402
from server import load_config  # noqa: E402
from store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*", type=Path)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--reextract", metavar="ID")
    ap.add_argument("--audio", metavar="ID")
    ap.add_argument("--no-audio", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    store = Store(cfg["data_root"])
    if a.audio:
        metas = [m for m in store.list() if m["id"] == a.audio]
    elif a.reextract:
        metas = [store.reextract(a.reextract)]
    else:
        metas = [store.import_pdf(p) for p in a.pdfs]
    if not metas:
        ap.error("PDF か --reextract / --audio を指定する")
    for m in metas:
        if not a.audio:
            tag = "既にある" if m.get("already") else "取り込んだ"
            print(f"{tag}: {m['id']}  {m['title']}  {m['pages']}ページ  {m['sections']}章  {m['sentences']}文"
                  + (f"  OCR: {m['ocr_pages']}" if m["ocr_pages"] else ""))
        if a.no_audio or (m.get("already") and not a.audio):
            continue
        d = store.paper_dir(m["id"])
        st = audio.generate(d, store.load(m["id"]), cfg["voice"], cfg["rate"],
                            on_progress=lambda n, t: print(f"  音声 {n}/{t}", end="\r", flush=True))
        print()
        if st["state"] == "done":
            print(f"  音声: {st['total']} 本（声 {st['voice']}、速さ {st['rate']}）、持ち出し用 {d / 'export'}")
        else:
            print(f"  音声を作れなかった: {st.get('error')}")


if __name__ == "__main__":
    main()
