"""聞いた位置（しおり）と文の印（★）。<data_root>/state.sqlite。

  positions  論文ごとに最後に聞いた文。文は中身（t）で持つので、章の編集や AI の手直しで番号がずれても戻れる
  marks      印を付けた文（uid ごと）。メモ付き。消したものも deleted = 1 で残し、スマホとの受け渡しで消えたことを伝える

どちらも更新日時（updated_at、UTC の ISO 8601）の新しいほうを正とする。Mac とスマホで同じ規則で合わせるので、
同じ記録を何度読み込んでも結果は変わらない。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  paper_id TEXT PRIMARY KEY,
  sentence TEXT NOT NULL,
  section TEXT,
  item_id TEXT,
  offset REAL NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  device TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marks (
  uid TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL,
  sentence TEXT NOT NULL,
  section TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  device TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS marks_paper ON marks(paper_id, deleted);
"""
POSITION_KEYS = ("paper_id", "sentence", "section", "item_id", "offset", "done", "total", "updated_at", "device")
MARK_KEYS = ("uid", "paper_id", "sentence", "section", "note", "created_at", "updated_at", "deleted", "device")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _key(ts: str) -> datetime:
    """書式の違う日時（Z 付き・+09:00 付き・秒まで）を比べられるようにする。"""
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.astimezone()


class State:
    def __init__(self, data_root: Path, device: str = "mac"):
        self.path = Path(data_root) / "state.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.device = device
        with self._con() as con:
            con.executescript(SCHEMA)

    def _con(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    # ---- しおり ----
    def positions(self) -> dict[str, dict]:
        with self._con() as con:
            return {r["paper_id"]: dict(r) for r in con.execute("SELECT * FROM positions")}

    def set_position(self, pos: dict) -> dict:
        """{"paper_id", "sentence", "section", "item_id", "offset", "done", "total"}。updated_at が無ければ今。"""
        p = {"section": None, "item_id": None, "offset": 0.0, "done": 0, "total": 0, "device": self.device,
             "updated_at": now_utc(), **{k: v for k, v in pos.items() if k in POSITION_KEYS and v is not None}}
        if not (isinstance(p.get("paper_id"), str) and isinstance(p.get("sentence"), str) and p["sentence"]):
            raise ValueError("paper_id と sentence が要る")
        with self._con() as con:
            self._put_position(con, p)
        return p

    def _put_position(self, con, p: dict) -> bool:
        cur = con.execute("SELECT updated_at FROM positions WHERE paper_id = ?", (p["paper_id"],)).fetchone()
        if cur and _key(cur["updated_at"]) >= _key(p["updated_at"]):
            return False                                   # 手元のほうが新しい
        con.execute(f"INSERT OR REPLACE INTO positions ({','.join(POSITION_KEYS)}) VALUES ({','.join('?' * len(POSITION_KEYS))})",
                    [p.get(k) for k in POSITION_KEYS])
        return True

    # ---- 印 ----
    def marks(self, paper_id: str | None = None, include_deleted: bool = False) -> list[dict]:
        q, args = "SELECT * FROM marks WHERE 1=1", []
        if paper_id:
            q += " AND paper_id = ?"
            args.append(paper_id)
        if not include_deleted:
            q += " AND deleted = 0"
        with self._con() as con:
            return [dict(r) for r in con.execute(q + " ORDER BY created_at", args)]

    def toggle_mark(self, paper_id: str, sentence: str, section: str | None = None) -> dict | None:
        """その文に印が無ければ付け、あれば外す。付けた印を返す（外したら None）。"""
        with self._con() as con:
            cur = con.execute("SELECT * FROM marks WHERE paper_id = ? AND sentence = ? AND deleted = 0",
                              (paper_id, sentence)).fetchone()
            now = now_utc()
            if cur:
                con.execute("UPDATE marks SET deleted = 1, updated_at = ?, device = ? WHERE uid = ?", (now, self.device, cur["uid"]))
                return None
            m = {"uid": uuid.uuid4().hex, "paper_id": paper_id, "sentence": sentence, "section": section, "note": "",
                 "created_at": now, "updated_at": now, "deleted": 0, "device": self.device}
            con.execute(f"INSERT INTO marks ({','.join(MARK_KEYS)}) VALUES ({','.join('?' * len(MARK_KEYS))})",
                        [m[k] for k in MARK_KEYS])
            return m

    def set_note(self, uid: str, note: str) -> dict:
        with self._con() as con:
            if not con.execute("SELECT 1 FROM marks WHERE uid = ?", (uid,)).fetchone():
                raise KeyError(uid)
            con.execute("UPDATE marks SET note = ?, updated_at = ?, device = ? WHERE uid = ?",
                        (note.strip()[:2000], now_utc(), self.device, uid))
            return dict(con.execute("SELECT * FROM marks WHERE uid = ?", (uid,)).fetchone())

    # ---- スマホとの受け渡し ----
    def export(self) -> dict:
        return {"positions": list(self.positions().values()), "marks": self.marks(include_deleted=True)}

    def merge(self, data: dict) -> dict:
        """スマホから来たしおりと印を合わせる（新しいほうが勝つ）。"""
        n_pos = n_marks = 0
        with self._con() as con:
            for p in data.get("positions") or []:
                if isinstance(p, dict) and p.get("paper_id") and p.get("sentence") and p.get("updated_at"):
                    n_pos += self._put_position(con, {"section": None, "item_id": None, "offset": 0, "done": 0, "total": 0,
                                                      "device": "phone", **p})
            for m in data.get("marks") or []:
                if not (isinstance(m, dict) and m.get("uid") and m.get("paper_id") and m.get("sentence") and m.get("updated_at")):
                    continue
                cur = con.execute("SELECT updated_at FROM marks WHERE uid = ?", (m["uid"],)).fetchone()
                if cur and _key(cur["updated_at"]) >= _key(m["updated_at"]):
                    continue
                row = {"section": None, "note": "", "created_at": m["updated_at"], "deleted": 0, "device": "phone", **m}
                row["deleted"] = int(bool(row["deleted"]))
                con.execute(f"INSERT OR REPLACE INTO marks ({','.join(MARK_KEYS)}) VALUES ({','.join('?' * len(MARK_KEYS))})",
                            [row.get(k) for k in MARK_KEYS])
                n_marks += 1
        return {"positions": n_pos, "marks": n_marks}
