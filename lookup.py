"""英単語を引く。

引く順:
  1. EJDict（<data_root>/dict/ejdict.sqlite。英和）をそのまま
  2. 活用を戻して EJDict（satellites → satellite、measured → measure、flown はそのまま載っている）
  3. 派生語を戻して EJDict（deformable → deform、actuator → actuate）。「近い語」として返す
  4. Free Dictionary API（英英。ネットが要る。つながらなければ飛ばす）
どれでも見つからなければ found=False で、画面は Weblio へのリンクだけ出す。
"""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.dictionaryapi.dev/api/v2/entries/en/{}"


def normalize(word: str) -> str:
    w = word.strip().strip(".,;:!?()[]{}\"“”‘’'").lower()
    w = re.sub(r"['’]s$", "", w)
    return w


def inflection_candidates(w: str) -> list[str]:
    """活用形 → 原形の候補（順に試す）。"""
    c = []
    def add(x):
        if len(x) >= 2 and x not in c and x != w:
            c.append(x)
    if w.endswith("ies"):
        add(w[:-3] + "y")
    if w.endswith("es"):
        add(w[:-2])
    if w.endswith("s") and not w.endswith("ss"):
        add(w[:-1])
    if w.endswith("ied"):
        add(w[:-3] + "y")
    if w.endswith("ed"):
        add(w[:-2]); add(w[:-1])
        if len(w) > 4 and w[-3] == w[-4]:
            add(w[:-3])                                  # stopped → stop
    if w.endswith("ing"):
        add(w[:-3]); add(w[:-3] + "e")
        if len(w) > 5 and w[-4] == w[-5]:
            add(w[:-4])                                  # running → run
    if w.endswith("er"):
        add(w[:-2]); add(w[:-1])
    if w.endswith("est"):
        add(w[:-3]); add(w[:-2])
    if w.endswith("ly"):
        add(w[:-2])
    return c


DERIVATIONS = [("ability", ""), ("ability", "e"), ("able", ""), ("able", "e"), ("ible", ""), ("ible", "e"),
               ("ator", "ate"), ("ators", "ate"), ("or", ""), ("ors", ""), ("ation", "ate"), ("ation", "e"),
               ("ations", "ate"), ("ation", ""), ("ment", ""), ("ments", ""), ("ness", ""), ("ity", ""),
               ("ity", "e"), ("ization", "ize"), ("ized", "ize"), ("ically", "ic"), ("ically", "ical")]


def derivation_candidates(w: str) -> list[str]:
    out = []
    for suf, rep in DERIVATIONS:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            x = w[: -len(suf)] + rep
            if x not in out and x != w:
                out.append(x)
    return out


class Dictionary:
    def __init__(self, data_root: Path, online: bool = True):
        self.path = Path(data_root) / "dict" / "ejdict.sqlite"
        self.online = online

    def available(self) -> bool:
        return self.path.exists()

    def _ej(self, con, w: str) -> list[dict]:
        rows = con.execute("SELECT word, mean FROM words WHERE lower = ? LIMIT 5", (w,)).fetchall()
        return [{"word": r[0], "mean": r[1]} for r in rows]

    def _api(self, w: str) -> list[dict]:
        if not self.online:
            return []
        try:
            req = urllib.request.Request(API.format(urllib.parse.quote(w)), headers={"User-Agent": "paper_reader"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
        except Exception:
            return []
        out = []
        for e in data[:1]:
            parts = []
            for m in e.get("meanings", [])[:3]:
                defs = [d["definition"] for d in m.get("definitions", [])[:2]]
                if defs:
                    parts.append(f"({m.get('partOfSpeech', '')}) " + " / ".join(defs))
            if parts:
                out.append({"word": e.get("word", w), "mean": " / ".join(parts)})
        return out

    def lookup(self, word: str) -> dict:
        q = normalize(word)
        res = {"query": word, "normalized": q, "found": False, "source": None, "entries": [], "note": None,
               "weblio": f"https://ejje.weblio.jp/content/{urllib.parse.quote(q)}"}
        if not q or not re.fullmatch(r"[a-z][a-z\-']*", q):
            return res
        if self.available():
            con = sqlite3.connect(self.path)
            try:
                for cand, note in [(q, None)] + [(c, "原形") for c in inflection_candidates(q)] \
                        + [(c, "近い語") for c in derivation_candidates(q)]:
                    hits = self._ej(con, cand)
                    if hits:
                        return {**res, "found": True, "source": "EJDict", "headword": hits[0]["word"],
                                "entries": hits, "note": note}
                if "-" in q:                               # micro-electromechanical → 後半
                    for part in reversed(q.split("-")):
                        hits = self._ej(con, part)
                        if hits:
                            return {**res, "found": True, "source": "EJDict", "headword": hits[0]["word"],
                                    "entries": hits, "note": "部分"}
            finally:
                con.close()
        hits = self._api(q)
        if hits:
            return {**res, "found": True, "source": "Free Dictionary API（英英）", "headword": hits[0]["word"],
                    "entries": hits}
        return res
