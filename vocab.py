"""単語帳（<data_root>/vocab.sqlite）。辞書で引いた語のうち「登録」したものを入れ、FSRS で復習する。

  words     見出し語ごとに1行。意味・出典・最初に引いた日時・引いた回数・復習の状態（card は reviews から計算した結果）
  lookups   引いた記録。どの論文のどの文で引いたか（audio_ref = "<論文id>/<音声id>" があればその文の音声が鳴らせる）
  reviews   答えの記録。uid で重複を除くので、スマホ（PWA）の記録を何度読み込んでも二重にならない
            復習の状態の正本はこの表。words.card / due は reviews を計算し直した結果の置き場
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import srs
from fsrs import Card

SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
  id INTEGER PRIMARY KEY,
  headword TEXT NOT NULL UNIQUE,
  meaning TEXT NOT NULL,
  source TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  lookups INTEGER NOT NULL DEFAULT 1,
  card TEXT,
  due TEXT
);
CREATE TABLE IF NOT EXISTS lookups (
  id INTEGER PRIMARY KEY,
  word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
  query TEXT NOT NULL,
  paper_id TEXT,
  section TEXT,
  sentence TEXT,
  looked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  uid TEXT PRIMARY KEY,
  headword TEXT NOT NULL,
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
  reviewed_at TEXT NOT NULL,
  device TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_word ON reviews(headword, reviewed_at);
"""
MIGRATIONS = [("lookups", "uid", "TEXT"), ("lookups", "audio_ref", "TEXT")]


def _now_local() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Vocab:
    def __init__(self, data_root: Path, device: str = "mac"):
        self.path = Path(data_root) / "vocab.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.device = device
        with self._con() as con:
            con.executescript(SCHEMA)
            for table, col, typ in MIGRATIONS:
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS lookups_uid ON lookups(uid)")

    def _con(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    # ---- 引いた語 ----
    def _upsert_word(self, con, head, meaning, source, when) -> int:
        row = con.execute("SELECT id FROM words WHERE headword = ?", (head,)).fetchone()
        if row:
            con.execute("UPDATE words SET lookups = lookups + 1, last_seen = max(last_seen, ?) WHERE id = ?", (when, row["id"]))
            return row["id"]
        return con.execute("INSERT INTO words (headword, meaning, source, first_seen, last_seen) VALUES (?,?,?,?,?)",
                           (head, meaning, source, when, when)).lastrowid

    def record(self, result: dict, paper_id=None, section=None, sentence=None, audio_ref=None) -> int | None:
        """辞書で見つかった単語を登録する。同じ見出し語は1行にまとめ、登録した回数と例文を増やす。"""
        if not result.get("found"):
            return None
        now = _now_local()
        meaning = "\n".join(e["mean"] for e in result["entries"][:2])
        with self._con() as con:
            wid = self._upsert_word(con, result["headword"], meaning, result.get("source"), now)
            con.execute("INSERT INTO lookups (word_id, query, paper_id, section, sentence, looked_at, uid, audio_ref) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (wid, result["query"], paper_id, section, sentence, now, uuid.uuid4().hex, audio_ref))
        return wid

    def has(self, head: str) -> bool:
        with self._con() as con:
            return con.execute("SELECT 1 FROM words WHERE headword = ?", (head,)).fetchone() is not None

    def list(self, limit: int = 500) -> list[dict]:
        with self._con() as con:
            rows = con.execute("""
              SELECT w.*, (SELECT sentence FROM lookups l WHERE l.word_id = w.id ORDER BY l.id DESC LIMIT 1) AS example
              FROM words w ORDER BY w.last_seen DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def delete(self, wid: int) -> bool:
        with self._con() as con:
            row = con.execute("SELECT headword FROM words WHERE id = ?", (wid,)).fetchone()
            if not row:
                return False
            con.execute("DELETE FROM reviews WHERE headword = ?", (row["headword"],))
            return con.execute("DELETE FROM words WHERE id = ?", (wid,)).rowcount > 0

    # ---- 復習 ----
    def _recompute(self, con, head: str):
        logs = [dict(r) for r in con.execute("SELECT rating, reviewed_at FROM reviews WHERE headword = ?", (head,))]
        card = srs.replay(logs)
        con.execute("UPDATE words SET card = ?, due = ? WHERE headword = ?",
                    (card.to_json() if card else None, card.due.isoformat() if card else None, head))

    def _card(self, row) -> Card | None:
        return Card.from_json(row["card"]) if row["card"] else None

    def _new_today(self, con, now) -> int:
        """今日（この Mac の日付）初めて答えた語の数。"""
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = con.execute("SELECT headword, min(reviewed_at) AS first FROM reviews GROUP BY headword").fetchall()
        return sum(1 for r in rows if srs.parse(r["first"]) >= start)

    def queue(self, now=None, new_per_day: int = srs.NEW_PER_DAY) -> dict:
        """期限の来た語（覚えている途中・復習）→ 今日の枠が残っていれば新しい語、の順。"""
        now = now or srs.now_utc()
        with self._con() as con:
            rows = con.execute("SELECT * FROM words").fetchall()
            new_left = max(0, new_per_day - self._new_today(con, now))
        due, new, later = [], [], []
        for r in rows:
            c = self._card(r)
            if c is None:
                new.append(r)
            elif c.due <= now:
                due.append((c.due, r, c))
            else:
                later.append(c.due)
        due.sort(key=lambda x: x[0])
        new.sort(key=lambda r: r["first_seen"])
        learning = sum(1 for _, _, c in due if c.state.name in ("Learning", "Relearning"))
        counts = {"new": min(len(new), new_left), "learning": learning, "review": len(due) - learning,
                  "total": len(rows), "new_waiting": len(new)}
        nxt = None
        if due:
            nxt = due[0][1]
        elif new and new_left:
            nxt = new[0]
        res = {"counts": counts, "card": None,
               "next_due": min(later).isoformat() if later else None}
        if nxt is not None:
            res["card"] = self.card_view(nxt["headword"], now)
        return res

    def card_view(self, head: str, now=None) -> dict | None:
        now = now or srs.now_utc()
        with self._con() as con:
            r = con.execute("SELECT * FROM words WHERE headword = ?", (head,)).fetchone()
            if not r:
                return None
            ex = con.execute("SELECT sentence, section, paper_id, audio_ref FROM lookups WHERE word_id = ? "
                             "AND sentence IS NOT NULL ORDER BY id DESC LIMIT 3", (r["id"],)).fetchall()
            reviews = con.execute("SELECT count(*) FROM reviews WHERE headword = ?", (head,)).fetchone()[0]
        c = self._card(r)
        return {"headword": head, "meaning": r["meaning"], "source": r["source"], "id": r["id"],
                "state": c.state.name if c else "New", "reviews": reviews,
                "examples": [dict(e) for e in ex], "intervals": srs.preview(c, now)}

    def answer(self, head: str, rating: int, at=None) -> dict:
        at = at or srs.now_utc()
        with self._con() as con:
            if not con.execute("SELECT 1 FROM words WHERE headword = ?", (head,)).fetchone():
                raise KeyError(head)
            con.execute("INSERT INTO reviews (uid, headword, rating, reviewed_at, device) VALUES (?,?,?,?,?)",
                        (uuid.uuid4().hex, head, int(rating), at.isoformat(), self.device))
            self._recompute(con, head)
        return {"ok": True}

    def undo(self) -> str | None:
        """この機器で最後に答えた1件を取り消す。"""
        with self._con() as con:
            r = con.execute("SELECT uid, headword FROM reviews WHERE device = ? ORDER BY reviewed_at DESC LIMIT 1",
                            (self.device,)).fetchone()
            if not r:
                return None
            con.execute("DELETE FROM reviews WHERE uid = ?", (r["uid"],))
            self._recompute(con, r["headword"])
            return r["headword"]

    # ---- スマホとの受け渡し ----
    def export(self) -> dict:
        """スマホに渡す単語帳（語・意味・例文・答えの記録すべて）。"""
        with self._con() as con:
            words = []
            for r in con.execute("SELECT * FROM words ORDER BY first_seen"):
                ex = con.execute("SELECT sentence, section, paper_id, audio_ref FROM lookups WHERE word_id = ? "
                                 "AND sentence IS NOT NULL ORDER BY id DESC LIMIT 3", (r["id"],)).fetchall()
                words.append({"headword": r["headword"], "meaning": r["meaning"], "source": r["source"],
                              "first_seen": r["first_seen"], "examples": [dict(e) for e in ex]})
            reviews = [dict(r) for r in con.execute("SELECT uid, headword, rating, reviewed_at, device FROM reviews")]
        return {"words": words, "reviews": reviews}

    def merge(self, progress: dict) -> dict:
        """スマホから来た記録（引いた語・答え）を合わせる。uid が同じものは入れない。"""
        added_lookups = added_reviews = 0
        touched = set()
        with self._con() as con:
            for lk in progress.get("lookups", []):
                if con.execute("SELECT 1 FROM lookups WHERE uid = ?", (lk["uid"],)).fetchone():
                    continue
                when = srs.parse(lk["looked_at"]).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
                wid = self._upsert_word(con, lk["headword"], lk["meaning"], lk.get("source"), when)   # Mac の書式（現地時刻）にそろえる
                con.execute("INSERT INTO lookups (word_id, query, paper_id, section, sentence, looked_at, uid, audio_ref) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (wid, lk.get("query") or lk["headword"], lk.get("paper_id"), lk.get("section"),
                             lk.get("sentence"), when, lk["uid"], lk.get("audio_ref")))
                added_lookups += 1
            for rv in progress.get("reviews", []):
                if not con.execute("SELECT 1 FROM words WHERE headword = ?", (rv["headword"],)).fetchone():
                    continue                                   # 消した語の記録は入れない
                cur = con.execute("INSERT OR IGNORE INTO reviews (uid, headword, rating, reviewed_at, device) "
                                  "VALUES (?,?,?,?,?)", (rv["uid"], rv["headword"], int(rv["rating"]),
                                                         rv["reviewed_at"], rv.get("device", "phone")))
                if cur.rowcount:
                    added_reviews += 1
                    touched.add(rv["headword"])
            for h in touched:
                self._recompute(con, h)
        return {"lookups": added_lookups, "reviews": added_reviews}
