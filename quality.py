"""取り出した章と文の点検（自動の取り出しの直後に使い、AI の手直しを勧めるかを決める。tools/check_paper.py でも使う）。

  inspect(paper, figures_items) -> [{"level": "major"|"minor", "code", "where", "msg"}]
    major  そのまま聞くと内容を取り違えるおそれがあるもの（章の並び・見出しの抜け・文字の重なり・見えない文字など）
    minor  直すと良いが、聞くのに大きく困らないもの（短すぎる文・長すぎる文・空白の抜け など）
  summary(issues) -> {"major", "minor", "recommend", "top"}   recommend = major が1つでもあれば AI の手直しを勧める

2026-09-24 の5本の点検で、自動の取り出しが見逃していた形（ABSTRACT の位置、小節の抜け、短冊の重なった文字、
要旨に紛れた文献、Symbol の私用領域の文字）を、並びと文字の種類だけで見分ける。
"""
from __future__ import annotations

import re

NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
PUA = re.compile(r"[-]")
GARBLED = re.compile(r"[ðÞ�]|1⁄4|1⁄2|[܀-෿]")
REF_ABBR = re.compile(r"\b(Opt|Soc|Proc|Phys|Astron|Appl|Rev|Lett|Eng|Instrum|J|Vol|vol|pp)\.")
WORD = re.compile(r"[A-Za-z]+")
REF_KEY = re.compile(r"\b(Fig(?:ure)?s?\.?|Table)\s*(\d+)", re.I)


def _nums(title: str):
    m = NUMBERED.match(title)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _doubled_counter(paper: dict):
    """「Actua ator」「hexapo od」のような、短冊の境目で重なった文字を見分ける関数を返す。
    隣り合う2語で、前の語の終わりと後ろの語の頭が同じ文字、つないで1字減らすと論文のほかの所に出る語になり、
    どちらかの語はほとんど出てこない（ふつうの「the error」は外れる）。"""
    from collections import Counter
    vocab = Counter(w.lower() for s in paper.get("sections", []) for t in [s["title"]] + [x["t"] for x in s["sentences"]]
                    for w in WORD.findall(t))

    def count(text: str) -> int:
        ws = list(WORD.finditer(text))
        n = 0
        for a, b in zip(ws, ws[1:]):
            x, y = a.group(0), b.group(0)
            if b.start() - a.end() != 1 or len(x) < 2 or len(y) < 2 or x[-1].lower() != y[0].lower():
                continue
            joined = (x + y[1:]).lower()
            if vocab[joined] >= 1 and min(vocab[x.lower()], vocab[y.lower()]) <= 2:
                n += 1
        return n
    return count


def inspect(paper: dict, figure_items: list[dict] | None = None) -> list[dict]:
    out: list[dict] = []
    doubled_in = _doubled_counter(paper)

    def add(level, code, where, msg):
        out.append({"level": level, "code": code, "where": where, "msg": msg})

    secs = paper.get("sections", [])
    body = [s for s in secs if s.get("kind") == "body"]
    n_sent = sum(len(s["sentences"]) for s in body)
    # ---- 章の並び ----
    for i, s in enumerate(secs):
        if re.match(r"^abstract\b", s["title"], re.I) and any(x.get("kind") == "body" for x in secs[:i]):
            add("major", "abstract_order", s["title"], "本文の章のあとに「Abstract」の章がある（読む順番が入れ替わっている）")
    prev = None
    for s in body:
        n = _nums(s["title"])
        if not n:
            continue
        if prev:
            same_depth = len(n) == len(prev)
            if n <= prev or (same_depth and n[:-1] == prev[:-1] and n[-1] > prev[-1] + 1) or (len(n) == 1 and n[0] > prev[0] + 1):
                add("major", "numbering", s["title"], f"章の番号が {'.'.join(map(str, prev))} の次に {'.'.join(map(str, n))}（見出しの抜けか順番の入れ替わり？）")
        prev = n
    if len(body) <= 3 and n_sent >= 120:
        add("major", "few_sections", "", f"本文が {len(body)} 章しかないのに {n_sent} 文ある（小節の見出しを拾えていない？）")
    for s in body:
        if len(s["sentences"]) > 70:
            add("minor", "long_section", s["title"], f"{len(s['sentences'])} 文ある長い章（小節の見出しの拾い漏れ？）")
    # ---- 見出し・文の文字 ----
    for s in secs:
        if doubled_in(s["title"]):
            add("major", "doubled_heading", s["title"], "見出しの語が割れて文字が重なっている（例: Actua ator）")
    doubled = sum(doubled_in(x["t"]) > 0 for s in body for x in s["sentences"])
    if n_sent and doubled / n_sent > 0.05:
        add("major", "doubled_text", "", f"{doubled} 文で語が割れて文字が重なっている（短冊に分けて描かれた PDF？）")
    pua = sum(bool(PUA.search(x["t"])) for s in secs for x in s["sentences"])
    if pua:
        add("major", "pua", "", f"{pua} 文に見えない文字（私用領域の文字。μ や箇条書きの点が化けている）")
    garbled = sum(bool(GARBLED.search(x["t"])) for s in secs for x in s["sentences"])
    if garbled >= 3:
        add("major", "garbled", "", f"{garbled} 文に数式の文字化け（ð Þ 1⁄4 など）")
    elif garbled:
        add("minor", "garbled", "", f"{garbled} 文に数式の文字化け")
    # ---- 要旨 ----
    front = [s for s in secs if s.get("kind") == "front"]
    abs_text = " ".join(x["t"] for s in front for x in s["sentences"])
    if front and len(REF_ABBR.findall(abs_text)) >= 4:
        add("major", "abstract_refs", "Abstract", "要旨に参考文献の切れ端（Opt. / Soc. / Proc. など）が混ざっている")
    n_abs = sum(len(s["sentences"]) for s in front)
    if not front or n_abs == 0:
        add("minor", "no_abstract", "", "要旨が無い")
    elif n_abs > 20:
        add("major", "abstract_long", "Abstract", f"要旨が {n_abs} 文ある（本文か文献が要旨に入っている？）")
    # ---- 文の長さ ----
    long_ = [x for s in body for x in s["sentences"] if len(x["t"]) > 450]
    if len(long_) >= 3:
        add("minor", "long_sentences", "", f"450 字を超える文が {len(long_)} 個（2文がつながっている？）")
    short = [x for s in body for x in s["sentences"] if len(x["t"]) < 15]
    if len(short) >= 5:
        add("minor", "short_sentences", "", f"15 字未満の文が {len(short)} 個（細切れ？）")
    glued = [w for s in body for x in s["sentences"] for w in re.findall(r"[A-Za-z]{25,}", x["t"])]
    if glued:
        add("minor", "glued", "", f"空白の抜けらしい語が {len(glued)} 個（例: {glued[0][:30]}）")
    # ---- 図 ----
    if figure_items is not None:
        have = set()
        for it in figure_items:
            m = REF_KEY.match(it.get("label") or "")
            if m:
                have.add(("table" if m.group(1).lower().startswith("tab") else "figure", m.group(2)))
        cited = set()
        for s in body:
            for x in s["sentences"]:
                for m in REF_KEY.finditer(x["t"]):
                    cited.add(("table" if m.group(1).lower().startswith("tab") else "figure", m.group(2)))
        missing = sorted(cited - have, key=lambda k: (k[0], int(k[1])))
        if missing:
            add("minor", "missing_figures", "", "本文で参照しているのに一覧に無い図・表: "
                + ", ".join(("Table " if k == "table" else "Fig. ") + n for k, n in missing[:10]))
        unl = sum(1 for it in figure_items if it.get("kind") == "image")
        if unl:
            add("minor", "unlabeled_images", "", f"種類の分からない画像が {unl} 枚")
    return out


def summary(issues: list[dict]) -> dict:
    major = [i for i in issues if i["level"] == "major"]
    return {"major": len(major), "minor": len(issues) - len(major), "recommend": bool(major),
            "top": (major + [i for i in issues if i["level"] == "minor"])[:8]}
