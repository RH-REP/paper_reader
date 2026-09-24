"""論文ごとの専門用語の一覧（data/papers/<id>/glossary.json）。

  拾うもの
    word     辞書（EJDict）に無い語・派生語でしか引けない語で、2回以上出るもの（deformable、actuator、wavefront）
    acronym  2回以上出る略語（DM、MEMS）。本文の「deformable mirror (DM)」から元の語を探す
    phrase   3回以上出る2〜3語の句（adaptive optics、influence function）。専門の語を含むか、4回以上出るもの
  訳は Mac 内蔵の翻訳（translate.py と同じ仕組み、端末内）。略語は元の語を訳す
  固有名詞（大文字で始まる形でしか出ない語）と後付け（References など）の中の語は拾わない

画面では本文の中の専門の語に点線が付き、押すと辞書と同じカードが出る（辞書に無い語はこの一覧の訳を出し、単語帳にも登録できる）。
文が変わると（中身の目印）作り直す。
"""
from __future__ import annotations

import hashlib
import os
import threading
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import translate

VERSION = 2   # 2: 訳しても同じ語（XAO → XAO）は訳なし
MAX_WORDS, MAX_ACRONYMS, MAX_PHRASES = 40, 25, 25
STOP = set("""a about above after again against all almost along also although always am among an and another any
are around as at be because been before being below between both but by can could did do does done down during each
either else enough even ever every few first for from further had has have having he her here hers him his how however
i if in into is it its itself just last least less like made make many may me might more most much must my near need
neither never next no nor not now of off often on once one only or other others our out over own per rather same second
several shall she should since so some such than that the their them then there these they this those though three through
thus to too two under until up upon us use used uses using very via was we well were what when where whether which while
who whom whose why will with within without would yet you your also shown show shows table figure fig figs section eq
equation respectively et al can't cannot new high low large small different based order due case cases result results
paper work value values number time times data given total well mean using obtained described important possible
""".split())
L = "A-Za-zÀ-ÖØ-öø-ÿ"
TOKEN = re.compile(rf"[{L}][{L}\-]*[{L}]|[{L}]")                  # Maréchal を Mar と chal に割らない
UNIT_WORDS = {"rad", "arcsec", "arcmin", "mrad", "nm", "mm", "cm", "khz", "hz", "mas"}
ACRONYM = re.compile(r"^[A-Z][A-Z0-9]{1,6}$")


def items_hash(paper: dict) -> str:
    return hashlib.sha1((f"v{VERSION}" + translate.items_hash(paper)).encode()).hexdigest()


def status(paper_dir: Path) -> dict:
    p = paper_dir / "glossary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"state": "none", "terms": []}


def is_current(paper_dir: Path, paper: dict) -> bool:
    return status(paper_dir).get("items_hash") == items_hash(paper)


def _sentences(paper: dict):
    for sec in paper["sections"]:
        if sec.get("kind") == "back":
            continue
        for s in sec["sentences"]:
            yield sec["title"], s["t"]


def _singular(w: str, known: set[str]) -> str:
    if w.endswith("ies") and w[:-3] + "y" in known:
        return w[:-3] + "y"
    if w.endswith("es") and w[:-2] in known and w[-3] in "sxz":
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and w[:-1] in known:
        return w[:-1]
    return w


def _expansion(acr: str, texts: list[str]) -> str | None:
    """「deformable mirror (DM)」「micro-electro-mechanical systems (MEMS)」から元の語を探す。"""
    letters = acr.rstrip("s").lower()
    for t in texts:
        for m in re.finditer(r"\(\s*" + re.escape(acr) + r"s?\s*\)", t):
            words = re.findall(r"[A-Za-z][A-Za-z\-]*", t[:m.start()])[-(len(letters) + 3):]
            for i in range(len(words)):
                cand = words[i:]
                initials = "".join(p[0] for w in cand for p in w.lower().split("-") if p)
                if cand and cand[0][0].lower() == letters[0] and _subseq(letters, initials) and len(cand) <= len(letters) + 1 \
                        and cand[0].lower() not in STOP and len(cand[0]) > 1:
                    return " ".join(cand)
    return None


def _subseq(small: str, big: str) -> bool:
    it = iter(big)
    return all(c in it for c in small) and big[-1:] == small[-1:]


def candidates(paper: dict, dictionary) -> list[dict]:
    """訳を付ける前の専門用語の候補（回数の多い順）。dictionary は lookup.Dictionary（ネットを使わないもの）。"""
    sents = list(_sentences(paper))
    texts = [t for _, t in sents]
    words, lower, first = Counter(), Counter(), {}
    acr = Counter()
    phrases = Counter()
    for sec, t in sents:
        toks = TOKEN.findall(t)
        for i, w in enumerate(toks):
            if ACRONYM.match(w.rstrip("s")) and len(w.rstrip("s")) >= 2 and not w.rstrip("s").isdigit():
                a = w[:-1] if w.endswith("s") and ACRONYM.match(w[:-1]) else w
                acr[a] += 1
                first.setdefault(("acronym", a), (sec, t))
                continue
            lw = w.lower()
            words[lw] += 1
            if w[0].islower():
                lower[lw] += 1                           # 小文字で書かれた回数（大文字でしか出ない語は固有名詞とみなす）
            first.setdefault(("word", lw), (sec, t))
        low = [w.lower() for w in toks]
        for n in (2, 3):
            for i in range(len(low) - n + 1):
                g = low[i:i + n]
                if all(len(x) >= 3 and x not in STOP and x.isalpha() for x in g):
                    phrases[" ".join(g)] += 1
                    first.setdefault(("phrase", " ".join(g)), (sec, t))
    known = set(words)
    merged, lower_seen = Counter(), Counter()
    for w, c in words.items():
        s = _singular(w, known)
        merged[s] += c
        lower_seen[s] += lower[w]
        if ("word", s) not in first:
            first[("word", s)] = first[("word", w)]
    tech = {}
    for w, c in merged.items():
        if c < (3 if len(w) <= 5 else 2) or len(w) < 4 or w in STOP or lower_seen[w] == 0:
            continue
        r = dictionary.lookup(w)
        if not r["found"] or r.get("note") in ("近い語", "部分"):
            tech[w] = c
    out = []
    for w, c in sorted(tech.items(), key=lambda x: -x[1])[:MAX_WORDS]:
        out.append({"term": w, "kind": "word", "count": c})
    for a, c in sorted(acr.items(), key=lambda x: -x[1]):
        if c >= 2 and len([o for o in out if o["kind"] == "acronym"]) < MAX_ACRONYMS:
            out.append({"term": a, "kind": "acronym", "count": c, "expansion": _expansion(a, texts)})
    folded = Counter()                                   # 「deformable mirrors」と「deformable mirror」を1つに
    for p, c in phrases.items():
        head, _, last = p.rpartition(" ")
        if head.split()[0] in UNIT_WORDS:
            continue                                     # 「rad fitting error」のような単位から始まる句は外す
        key = f"{head} {_singular(last, known)}"
        folded[key] += c
        if ("phrase", key) not in first:
            first[("phrase", key)] = first[("phrase", p)]
    phrases = folded
    tri = {p for p, c in phrases.items() if c >= 3 and p.count(" ") == 2}
    ph = []
    for p, c in sorted(phrases.items(), key=lambda x: (-x[1], -len(x[0]))):
        if c < 3 or not (c >= 4 or any(x in tech for x in p.split())):
            continue
        if p.count(" ") == 1 and any(p in t and phrases[t] == c for t in tri):
            continue                                     # 同じ回数の3語の句に含まれる2語の句は外す
        ph.append({"term": p, "kind": "phrase", "count": c})
        if len(ph) >= MAX_PHRASES:
            break
    out += ph
    for o in out:
        sec, t = first.get((o["kind"], o["term"]), ("", ""))
        o["section"], o["sentence"] = sec, t
    return out


def generate(paper_dir: Path, paper: dict, dictionary) -> dict:
    st = {"version": VERSION, "state": "running", "items_hash": items_hash(paper), "terms": [],
          "started_at": datetime.now().isoformat(timespec="seconds")}
    terms = candidates(paper, dictionary)
    src = [(o.get("expansion") or o["term"]) for o in terms]
    ja: list[str] = [""] * len(src)
    try:
        exe = translate.translator()
        for i in range(0, len(src), translate.CHUNK):
            res = translate.call(exe, src[i:i + translate.CHUNK])
            if res.get("status") != "installed":
                st.update(note="訳を作れなかった（翻訳データ・翻訳の仕組み）。語の一覧だけ作った", raw=res.get("raw"),
                          state="no_translation")
                break
            ja[i:i + translate.CHUNK] = res["translations"]
    except Exception as e:  # swiftc が無いなど
        st["note"] = f"訳を作れなかった: {str(e)[:200]}"
    for o, j, x in zip(terms, ja, src):
        o["ja"] = "" if j.strip().lower() == x.strip().lower() else j   # 訳しても同じ（XAO → XAO）なら訳なし
    st.update(state=st["state"] if st["state"] == "no_translation" else "done", terms=terms,
              finished_at=datetime.now().isoformat(timespec="seconds"))
    p = paper_dir / "glossary.json"
    tmp = p.with_name(f"glossary.{os.getpid()}.{threading.get_ident()}.tmp")   # 同時に書いてもぶつからない
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return st
