"""data/ の読み書き。論文1本 = data/papers/<sha8>/ の1フォルダ。

  data/papers/<sha8>/original.pdf    取り込んだ PDF のコピー（正本）
  data/papers/<sha8>/meta.json       題名・元のファイル名・取り込み日時・sha256・OCR したページ
  data/papers/<sha8>/sentences.json  章と文（original.pdf から作り直せるキャッシュ）

取り込みは元の PDF をコピーするだけで、元のファイルは動かさない。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

import extract


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, obj) -> None:
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")   # 同時に書いてもぶつからない
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class Store:
    def __init__(self, data_root: Path):
        self.root = Path(data_root)
        self.papers = self.root / "papers"
        self.papers.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()                   # 同じ論文を同時に作り直さない

    def paper_dir(self, pid: str) -> Path:
        if not (len(pid) == 8 and all(c in "0123456789abcdef" for c in pid)):
            raise KeyError(pid)
        d = self.papers / pid
        if not d.is_dir():
            raise KeyError(pid)
        return d

    def import_pdf(self, src: Path, source_name: str | None = None, on_page=None) -> dict:
        """PDF をコピーして取り込む。同じ中身が既にあれば、それをそのまま返す。"""
        src = Path(src)
        sha = _sha256(src)
        pid = sha[:8]
        d = self.papers / pid
        if (d / "meta.json").exists():
            return {**json.loads((d / "meta.json").read_text(encoding="utf-8")), "already": True}
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d / "original.pdf")
        meta = {"id": pid, "sha256": sha, "source_name": source_name or src.name,
                "imported_at": datetime.now().isoformat(timespec="seconds")}
        return self._extract(d, meta, on_page)

    def reextract(self, pid: str) -> dict:
        d = self.paper_dir(pid)
        with self._lock:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            return self._extract(d, meta)

    def _extract(self, d: Path, meta: dict, on_page=None) -> dict:
        paper = extract.extract_file(d / "original.pdf", on_page)
        paper["id"] = meta["id"]
        _write_json(d / "sentences.json", paper)
        meta.update({
            "title": paper["title"] or Path(meta["source_name"]).stem,
            "pages": paper["pages"], "ocr_pages": paper["ocr_pages"],
            "sections": len(paper["sections"]),
            "sentences": sum(len(s["sentences"]) for s in paper["sections"]),
            "extracted_at": datetime.now().isoformat(timespec="seconds"),
            "extractor_version": extract.EXTRACTOR_VERSION,
        })
        _write_json(d / "meta.json", meta)
        return meta

    def list(self) -> list[dict]:
        out = []
        for m in self.papers.glob("*/meta.json"):
            try:
                out.append(json.loads(m.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda m: m.get("imported_at", ""), reverse=True)

    def load(self, pid: str) -> dict:
        d = self.paper_dir(pid)
        with self._lock:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            if meta.get("extractor_version") != extract.EXTRACTOR_VERSION or not (d / "sentences.json").exists():
                self._extract(d, meta)                  # 取り出し方を変えたら作り直す
        return json.loads((d / "sentences.json").read_text(encoding="utf-8"))
