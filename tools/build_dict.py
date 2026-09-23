#!/usr/bin/env python3
"""EJDict-hand（英和辞書、CC0）を取ってきて <data_root>/dict/ejdict.sqlite を作る。

  python3 tools/build_dict.py --config config.json            # GitHub から src/a.txt〜z.txt（約4.4MB）を取る
  python3 tools/build_dict.py --config config.json --src DIR  # 手元の a.txt〜z.txt から作る

出典: https://github.com/kujirahand/EJDict （Public Domain / CC0）
各行は「見出し語[,見出し語...] TAB 意味」。
"""
import argparse
import json
import sqlite3
import string
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from server import load_config  # noqa: E402

REPO = "kujirahand/EJDict"
RAW = f"https://raw.githubusercontent.com/{REPO}/{{ref}}/src/{{name}}"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "paper_reader"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def latest_commit() -> str:
    j = json.loads(fetch(f"https://api.github.com/repos/{REPO}/commits/master"))
    return j["sha"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--src", type=Path, help="a.txt〜z.txt のあるフォルダ（無ければ GitHub から取る）")
    a = ap.parse_args()
    out_dir = Path(load_config(a.config)["data_root"]) / "dict"
    out_dir.mkdir(parents=True, exist_ok=True)

    ref = None if a.src else latest_commit()
    rows = []
    for ch in string.ascii_lowercase:
        name = f"{ch}.txt"
        data = (a.src / name).read_bytes() if a.src else fetch(RAW.format(ref=ref, name=name))
        for line in data.decode("utf-8").splitlines():
            if "\t" not in line:
                continue
            heads, mean = line.split("\t", 1)
            for h in heads.split(","):
                h = h.strip()
                if h:
                    rows.append((h, h.lower(), mean.strip()))
        print(f"{name}: 累計 {len(rows)} 語", end="\r")

    tmp = out_dir / "ejdict.sqlite.tmp"
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE words (word TEXT, lower TEXT, mean TEXT)")
    con.executemany("INSERT INTO words VALUES (?,?,?)", rows)
    con.execute("CREATE INDEX words_lower ON words(lower)")
    con.commit()
    con.close()
    tmp.replace(out_dir / "ejdict.sqlite")
    meta = {"source": f"https://github.com/{REPO}", "license": "CC0 (Public Domain)", "commit": ref,
            "local_src": str(a.src) if a.src else None, "entries": len(rows),
            "built_at": datetime.now().isoformat(timespec="seconds")}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n作った: {out_dir / 'ejdict.sqlite'}（{len(rows)} 語）")


if __name__ == "__main__":
    main()
