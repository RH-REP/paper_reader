#!/usr/bin/env python3
"""手直しした sentences.json を確かめる（AI に手直しを頼むプロンプトから呼ぶ）。

  python3 tools/check_paper.py <論文のフォルダ（data/papers/<id>）>

エラー（app が読めない）があれば終了コード 1。警告は、PDF どおりなら残ってよいもの。
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import figures  # noqa: E402
import quality  # noqa: E402
from store import normalize  # noqa: E402

GARBLED = re.compile(r"[ðÞ�]|1⁄4|1⁄2")


def check(folder: Path) -> int:
    p = folder / "sentences.json"
    if not p.exists():
        print(f"エラー: {p} が無い")
        return 1
    try:
        paper = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"エラー: JSON として読めない: {e}")
        return 1
    errors, warns = [], []
    if not isinstance(paper.get("manual"), dict):
        errors.append('最上位に "manual": {"by": "ai", "at": ..., "notes": ...} が無い（無いと自動の取り出し直しで上書きされる）')
    paper, errs = normalize(paper)
    errors += errs
    if errors:
        for e in errors:
            print("エラー:", e)
        return 1

    secs = paper["sections"]
    print(f"題名: {paper.get('title', '')}")
    print(f"章・節 {len(secs)} / 文 {sum(len(s['sentences']) for s in secs)}")
    for s in secs:
        print(f"  {'  ' * (s['level'] - 1)}[{s['kind']}] {s['title']}  （{len(s['sentences'])}文）")
    if not any(s["kind"] == "body" for s in secs):
        warns.append('kind "body" の章が1つも無い')
    kinds = [s["kind"] for s in secs]
    if "back" in kinds and "body" in kinds[kinds.index("back"):]:
        warns.append('kind "back" のあとに "body" がある（後付けの後ろに本文？）')
    for s in secs:
        if s["kind"] == "body" and not s["sentences"] and s["level"] >= 2:
            warns.append(f"節「{s['title']}」に文が無い")
        for k, x in enumerate(s["sentences"], 1):
            where = f"「{s['title'][:24]}」{k}文目"
            t, sp = x["t"], x["s"]
            for w in re.findall(r"[A-Za-z]{25,}", t):
                warns.append(f"{where}: 空白の抜け？ {w[:40]}")
            if len(t) < 15:
                warns.append(f"{where}: 短すぎる文 {t!r}")
            if t[:1].islower():
                warns.append(f"{where}: 小文字で始まる（文の途中で切れている？） {t[:40]!r}")
            if GARBLED.search(sp):
                warns.append(f"{where}: 読み上げる文 s に文字化け {GARBLED.search(sp).group(0)!r}")
            if re.search(r"\bet al\.,? \d{4}", sp) or re.search(r"\[\d+(?:[-–,]\s*\d+)*\]", sp):
                warns.append(f"{where}: 読み上げる文 s に引用が残っている")
            if re.match(r"^(Fig\.|Figure|Table)\s*\d+[.:|]", t):
                warns.append(f"{where}: 図表の説明が混ざっている？ {t[:40]!r}")
    ferrs, fwarns = figures.check(folder)
    fst = figures.status(folder)
    # 構造の点検（章の並び・番号の飛び・文字の重なり・見えない文字・要旨に紛れた文献など）。PDF どおりなら残してよい
    for q in quality.inspect(paper, fst.get("items", [])):
        warns.insert(0, f"{'[重要] ' if q['level'] == 'major' else ''}{q['where'] + ': ' if q['where'] else ''}{q['msg']}")
    print(f"図・表・数式 {len(fst.get('items', []))} 件" + ("（AI 手直し済み）" if fst.get("manual") else "（自動のまま。manual が無い）"))
    for it in fst.get("items", []):
        print(f"  p.{it.get('page')} [{it.get('kind')}] {it.get('label') or '-'}  {it.get('file')}")
    for e in ferrs:
        print("エラー:", e)
    warns += fwarns
    if ferrs:
        return 1
    for w in warns[:80]:
        print("警告:", w)
    if len(warns) > 80:
        print(f"警告: ほか {len(warns) - 80} 件")
    print(f"エラー 0 件、警告 {len(warns)} 件")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(check(Path(sys.argv[1])))
