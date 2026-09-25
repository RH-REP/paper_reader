#!/usr/bin/env python3
"""自動の取り出し（extract.py）を、手直し済みの sentences.json（正解）と比べて点数にする。

  .venv/bin/python tools/eval_extract.py <論文のフォルダ>... [--json 結果.json]

論文のフォルダには original.pdf と、手直し済み（manual 付き）の sentences.json があること。
  見出し   正解の見出し（文言を小文字・空白を詰めてそろえる）を、自動の見出しがどれだけ当てたか（適合率・再現率・F1）
  段       当たった見出しのうち、段（level）も同じ割合
  文       正解の文（本文と要旨。後付けは除く）と、空白を詰めて完全に同じ文の割合（F1）
  要旨     正解の要旨の文のうち、自動の要旨に入った割合
正解そのものも AI の判断を含むので、100% を目指すものではない（同じ PDF で規則を変えたときの上がり下がりを見る）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import extract  # noqa: E402


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def head_key(t: str) -> str:
    t = norm(t).rstrip(":.")
    return re.sub(r"^(\d+(?:\.\d+)*)\.?\s+", r"\1 ", t)          # 「2.1. 題」と「2.1 題」を同じに


def f1(p: float, r: float) -> float:
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def score(auto: dict, truth: dict) -> dict:
    ta = [s for s in truth["sections"]]
    aa = [s for s in auto["sections"]]
    th = {head_key(s["title"]): s for s in ta if s.get("kind") != "front"}
    ah = {head_key(s["title"]): s for s in aa if s.get("kind") != "front"}
    hit = set(th) & set(ah)
    hp, hr = len(hit) / max(1, len(ah)), len(hit) / max(1, len(th))
    lv = sum(th[k].get("level") == ah[k].get("level") for k in hit) / max(1, len(hit))
    body = lambda secs: [norm(x["t"]) for s in secs if s.get("kind") != "back" for x in s["sentences"]]
    ts, as_ = body(ta), body(aa)
    tset, aset = set(ts), set(as_)
    sp, sr = len(tset & aset) / max(1, len(aset)), len(tset & aset) / max(1, len(tset))
    tabs = [norm(x["t"]) for s in ta if s.get("kind") == "front" for x in s["sentences"]]
    aabs = {norm(x["t"]) for s in aa if s.get("kind") == "front" for x in s["sentences"]}
    ab = sum(t in aabs for t in tabs) / max(1, len(tabs))
    return {"heading_p": round(hp, 3), "heading_r": round(hr, 3), "heading_f1": round(f1(hp, hr), 3),
            "level": round(lv, 3), "sentence_p": round(sp, 3), "sentence_r": round(sr, 3), "sentence_f1": round(f1(sp, sr), 3),
            "abstract": round(ab, 3), "n_truth_headings": len(th), "n_auto_headings": len(ah),
            "missing_headings": sorted(set(th) - hit)[:12], "extra_headings": sorted(set(ah) - hit)[:12]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    out = {}
    for d in a.folders:
        truth = json.loads((d / "sentences.json").read_text(encoding="utf-8"))
        if not truth.get("manual"):
            print(f"{d.name}: 手直し済みでないので飛ばす")
            continue
        auto = extract.extract_file(d / "original.pdf")
        out[d.name] = s = score(auto, truth)
        print(f"{d.name}  見出し F1 {s['heading_f1']:.2f}（P {s['heading_p']:.2f} R {s['heading_r']:.2f}）"
              f"  段 {s['level']:.2f}  文 F1 {s['sentence_f1']:.2f}  要旨 {s['abstract']:.2f}")
        if s["missing_headings"]:
            print("   抜け:", s["missing_headings"])
        if s["extra_headings"]:
            print("   余分:", s["extra_headings"])
    if out:
        keys = ["heading_f1", "level", "sentence_f1", "abstract"]
        mean = {k: round(sum(v[k] for v in out.values()) / len(out), 3) for k in keys}
        print("平均", "  ".join(f"{k} {v:.2f}" for k, v in mean.items()))
        out["_mean"] = mean
    if a.json:
        a.json.write_text(json.dumps({"extractor_version": extract.EXTRACTOR_VERSION, "papers": out}, ensure_ascii=False, indent=1),
                          encoding="utf-8")


if __name__ == "__main__":
    main()
