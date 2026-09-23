#!/usr/bin/env python3
"""スマホ版（pwa/）の辞書を作る。EJDict-hand（CC0）を頭文字ごとの JSON（pwa/dict/a.json〜z.json）にする。

  python3 tools/build_pwa_dict.py                       # GitHub から EJDict を取って作る（GitHub Actions で使う）
  python3 tools/build_pwa_dict.py --sqlite <ejdict.sqlite>   # tools/build_dict.py で作った手元の辞書から作る

各ファイル: {"<小文字の見出し語>": [["見出し語", "意味"], ...], ...}
"""
import argparse
import json
import sqlite3
import string
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))


def rows_from_github():
    from build_dict import RAW, fetch, latest_commit
    ref = latest_commit()
    for ch in string.ascii_lowercase:
        for line in fetch(RAW.format(ref=ref, name=f"{ch}.txt")).decode("utf-8").splitlines():
            if "\t" in line:
                heads, mean = line.split("\t", 1)
                for h in heads.split(","):
                    if h.strip():
                        yield h.strip(), mean.strip()
    print(f"EJDict commit {ref}")


def rows_from_sqlite(path: Path):
    con = sqlite3.connect(path)
    yield from con.execute("SELECT word, mean FROM words")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", type=Path)
    ap.add_argument("--out", type=Path, default=HERE / "pwa" / "dict")
    a = ap.parse_args()
    shards: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    n = 0
    for word, mean in (rows_from_sqlite(a.sqlite) if a.sqlite else rows_from_github()):
        low = word.lower()
        if low[:1] in string.ascii_lowercase:
            shards[low[0]][low].append([word, mean])
            n += 1
    a.out.mkdir(parents=True, exist_ok=True)
    for ch in string.ascii_lowercase:
        (a.out / f"{ch}.json").write_text(json.dumps(shards.get(ch, {}), ensure_ascii=False, separators=(",", ":")),
                                          encoding="utf-8")
    (a.out / "LICENSE.txt").write_text("EJDict-hand: Public Domain (CC0). https://github.com/kujirahand/EJDict\n",
                                       encoding="utf-8")
    print(f"{a.out}: {n} 語")


if __name__ == "__main__":
    main()
