"""論文のフォルダ分け。data/folders.json に、フォルダの並びと、論文ごとの入れ先を持つ。

  {"folders": ["論文", "ニュース", "study"], "papers": {"<論文の id>": "論文", ...}}

どのフォルダにも入っていない論文は「未分類」として画面に出す（取り込んだばかりの論文もここ）。
"""
import json
import threading
from pathlib import Path

DEFAULT = ["論文", "ニュース", "study"]
UNSORTED = "未分類"            # 画面での呼び名。フォルダの名前には使えない


class Folders:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def get(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        folders = [f for f in d.get("folders", DEFAULT) if isinstance(f, str) and f.strip()]
        papers = {k: v for k, v in (d.get("papers") or {}).items() if v in folders}   # 消したフォルダの論文は未分類へ
        return {"folders": folders, "papers": papers}

    def _save(self, d: dict) -> dict:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        return d

    @staticmethod
    def _name(name) -> str:
        name = name.strip() if isinstance(name, str) else ""
        if not name or name == UNSORTED or len(name) > 40:
            raise ValueError(f"フォルダの名前が使えない: {name!r}")
        return name

    def edit(self, op: str, name=None, to=None) -> dict:
        """add（足す）・rename（名前を変える。中の論文もついてくる）・delete（消す。中の論文は未分類へ）・up / down（並びを動かす）。"""
        with self._lock:
            d = self.get()
            fs = d["folders"]
            if op == "add":
                name = self._name(name)
                if name in fs:
                    raise ValueError(f"同じ名前のフォルダがある: {name}")
                fs.append(name)
            elif name not in fs:
                raise ValueError(f"フォルダが無い: {name!r}")
            elif op == "rename":
                to = self._name(to)
                if to != name and to in fs:
                    raise ValueError(f"同じ名前のフォルダがある: {to}")
                fs[fs.index(name)] = to
                d["papers"] = {k: (to if v == name else v) for k, v in d["papers"].items()}
            elif op == "delete":
                fs.remove(name)
                d["papers"] = {k: v for k, v in d["papers"].items() if v != name}
            elif op in ("up", "down"):
                i = fs.index(name)
                j = i - 1 if op == "up" else i + 1
                if 0 <= j < len(fs):
                    fs[i], fs[j] = fs[j], fs[i]
            else:
                raise ValueError(f"できない操作: {op}")
            return self._save(d)

    def put(self, pid: str, folder: str | None) -> dict:
        """論文をフォルダに入れる。folder が空なら未分類に戻す。"""
        with self._lock:
            d = self.get()
            if folder:
                if folder not in d["folders"]:
                    raise ValueError(f"フォルダが無い: {folder!r}")
                d["papers"][pid] = folder
            else:
                d["papers"].pop(pid, None)
            return self._save(d)
