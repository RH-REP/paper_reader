"""単語帳（<data_root>/vocab.sqlite）。引いた単語を自動で入れる。

  words       見出し語ごとに1行。意味・出典・最初に引いた日時・引いた回数・復習の状態（card, due は次の段階で FSRS が使う）
  lookups     引いた記録。どの論文のどの文で引いたか
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

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
"""


class Vocab:
    def __init__(self, data_root: Path):
        self.path = Path(data_root) / "vocab.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.executescript(SCHEMA)

    def _con(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def record(self, result: dict, paper_id=None, section=None, sentence=None) -> int | None:
        """辞書で見つかった単語を保存する。同じ見出し語は1行にまとめ、引いた回数を増やす。"""
        if not result.get("found"):
            return None
        now = datetime.now().isoformat(timespec="seconds")
        head = result["headword"]
        meaning = "\n".join(e["mean"] for e in result["entries"][:2])
        with self._con() as con:
            row = con.execute("SELECT id FROM words WHERE headword = ?", (head,)).fetchone()
            if row:
                wid = row["id"]
                con.execute("UPDATE words SET lookups = lookups + 1, last_seen = ? WHERE id = ?", (now, wid))
            else:
                wid = con.execute(
                    "INSERT INTO words (headword, meaning, source, first_seen, last_seen, due) VALUES (?,?,?,?,?,?)",
                    (head, meaning, result.get("source"), now, now, now)).lastrowid
            con.execute("INSERT INTO lookups (word_id, query, paper_id, section, sentence, looked_at) VALUES (?,?,?,?,?,?)",
                        (wid, result["query"], paper_id, section, sentence, now))
        return wid

    def list(self, limit: int = 200) -> list[dict]:
        with self._con() as con:
            rows = con.execute("""
              SELECT w.*, (SELECT sentence FROM lookups l WHERE l.word_id = w.id ORDER BY l.id DESC LIMIT 1) AS example
              FROM words w ORDER BY w.last_seen DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def delete(self, wid: int) -> bool:
        with self._con() as con:
            return con.execute("DELETE FROM words WHERE id = ?", (wid,)).rowcount > 0
