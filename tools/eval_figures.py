#!/usr/bin/env python3
"""図・表・数式の自動の切り抜き（figures.py）を、手直し済みの figures.json（正解）と比べる。

  .venv/bin/python tools/eval_figures.py <論文のフォルダ>... [--json 結果.json]

  再現率  正解の図・表・数式（番号のあるもの）のうち、自動で同じ種類・番号が付いたものの割合
  余分    自動で切り抜いたが、番号が付かなかった画像の数（ロゴや、1枚の図が分かれた切れ端など）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import figures  # noqa: E402


def key(it: dict) -> str | None:
    m = re.match(r"^\s*(Fig(?:ure)?s?\.?|Table|Tab\.|Eq(?:uation)?\.?)\s*\(?([A-Z]?-?\d+)", it.get("label") or "", re.I)
    if not m:
        return None
    k = "table" if m.group(1).lower().startswith("tab") else "equation" if m.group(1).lower().startswith("eq") else "figure"
    return f"{k}:{m.group(2).replace('-', '')}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    out = {}
    for d in a.folders:
        truth = json.loads((d / "figures.json").read_text(encoding="utf-8")) if (d / "figures.json").exists() else {}
        if not truth.get("manual"):
            continue
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copy2(d / "original.pdf", tmp / "original.pdf")
            auto = figures.extract(tmp, force=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        tk = {key(i) for i in truth["items"]} - {None}
        ak = [key(i) for i in auto["items"]]
        found = tk & set(ak)
        out[d.name] = r = {"truth": len(tk), "auto": len(ak), "recall": round(len(found) / max(1, len(tk)), 3),
                           "unlabeled": sum(k is None for k in ak), "missing": sorted(tk - found)}
        print(f"{d.name}  再現率 {r['recall']:.2f}（{len(found)}/{len(tk)}）  自動 {r['auto']} 枚・番号なし {r['unlabeled']}  抜け {r['missing'][:10]}")
    if out:
        out["_mean"] = {"recall": round(sum(v["recall"] for v in out.values()) / len(out), 3),
                        "unlabeled": sum(v["unlabeled"] for v in out.values())}
        print("平均 再現率", out["_mean"]["recall"], "番号なし 計", out["_mean"]["unlabeled"])
    if a.json:
        a.json.write_text(json.dumps({"version": figures.VERSION, "papers": out}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
