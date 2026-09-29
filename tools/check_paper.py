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
import audio  # noqa: E402
import pronounce  # noqa: E402
import quality  # noqa: E402
from store import normalize  # noqa: E402

GARBLED = re.compile(r"[ðÞ�]|1⁄4|1⁄2")


def quiz_check(quiz: dict, paper: dict) -> tuple[list[str], list[str]]:
    """quiz.json の形と中身。(エラー, 警告)"""
    errs, warns = [], []
    titles = {sec["title"] for sec in paper["sections"]}
    texts = {x["t"] for sec in paper["sections"] for x in sec["sentences"]}
    if not isinstance(quiz.get("chapters"), list):
        return ["quiz.json に chapters（配列）が無い"], []
    answers = []
    for i, ch in enumerate(quiz["chapters"]):
        where = f"quiz.json chapters[{i}]"
        if ch.get("chapter") not in titles:
            errs.append(f"{where}.chapter が sentences.json の章の title と一致しない: {ch.get('chapter')!r}")
        for j, q in enumerate(ch.get("questions") or []):
            w = f"{where}.questions[{j}]"
            if not isinstance(q.get("q"), str) or not q["q"].strip():
                errs.append(f"{w}.q が無い")
            if not (isinstance(q.get("choices"), list) and len(q["choices"]) == 4 and all(isinstance(c, str) and c.strip() for c in q["choices"])):
                errs.append(f"{w}.choices は4つの文字列")
            if q.get("answer") not in (0, 1, 2, 3):
                errs.append(f"{w}.answer は 0〜3")
            else:
                answers.append(q["answer"])
            if not q.get("explain"):
                warns.append(f"{w}: explain（日本語の解説）が無い")
            ev = q.get("evidence") or []
            if not ev:
                warns.append(f"{w}: evidence（根拠の文）が無い")
            for e in ev:
                if e not in texts:
                    warns.append(f"{w}: evidence の文が本文に一字一句同じでは見つからない: {str(e)[:60]!r}")
    if len(answers) >= 6 and max(answers.count(k) for k in range(4)) > 0.6 * len(answers):
        warns.append("正解の位置が偏っている（同じ番号が6割を超える）")
    return errs, warns


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
    # 読み上げで読み違えそうな所（論文ごとの読み方・自分の辞書・組み込みの直しのあとの、音声に渡す文で数える）
    audio.PRONOUNCER = pronounce.Pronouncer(folder.parent.parent / "pronunciations.json")
    risks = quality.speech_risks([it["text"] for it in audio.items(paper)])
    print("読み上げの気になる所: " + "、".join(f"{quality.RISKS[k][1].split('（')[0]} {v['count']}" for k, v in risks.items()))
    for k, v in risks.items():
        if v["count"]:
            print(f"  {k}: 例 {', '.join(v['examples'][:8])}")
    # 理解クイズ（quiz.json）
    qp = folder / "quiz.json"
    if qp.exists():
        try:
            quiz = json.loads(qp.read_text(encoding="utf-8"))
        except ValueError as e:
            print("エラー: quiz.json が JSON として読めない:", e)
            return 1
        qerrs, qwarns = quiz_check(quiz, paper)
        n_q = sum(len(c.get("questions", [])) for c in quiz.get("chapters", []))
        print(f"理解クイズ {len(quiz.get('chapters', []))} 章・{n_q} 問")
        for e in qerrs:
            print("エラー:", e)
        if qerrs:
            return 1
        warns += qwarns
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
