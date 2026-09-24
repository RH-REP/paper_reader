"""data/ の読み書き。論文1本 = data/papers/<sha8>/ の1フォルダ。

  data/papers/<sha8>/original.pdf    取り込んだ PDF のコピー（正本）
  data/papers/<sha8>/meta.json       題名・元のファイル名・取り込み日時・sha256・OCR したページ
  data/papers/<sha8>/sentences.json  章と文（original.pdf から作り直せるキャッシュ）
                                     AI などが手直ししたら最上位に "manual" が付き、自動の取り出し直しで上書きしない
  data/papers/<sha8>/sentences.orig.json   手直し前の自動取り出し（手直しする側が残す）

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
import figures


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


KINDS = ("front", "body", "back")


def normalize(paper: dict) -> tuple[dict, list[str]]:
    """手直しされた sentences.json を確かめ、足りない項目（id・番号・読み上げ用の見出し・読み上げ用の文など）を補う。
    返り値の2つ目は直せない誤り（空なら使える）。"""
    errs: list[str] = []
    if not isinstance(paper, dict) or not isinstance(paper.get("sections"), list):
        return paper, ["最上位に sections（配列）が要る"]
    for i, sec in enumerate(paper["sections"]):
        where = f"sections[{i}]"
        if not isinstance(sec, dict):
            errs.append(f"{where} がオブジェクトでない")
            continue
        if not isinstance(sec.get("title"), str) or not sec["title"].strip():
            errs.append(f"{where}.title が無い")
            continue
        if sec.get("kind") not in KINDS:
            errs.append(f"{where}.kind は front / body / back のどれか（今: {sec.get('kind')!r}）")
        if not isinstance(sec.get("sentences"), list):
            errs.append(f"{where}.sentences（配列）が無い")
            continue
        m = extract.NUMBERED.match(sec["title"])
        sec.setdefault("number", m.group(1) if m else "")
        if not isinstance(sec.get("level"), int) or sec["level"] < 1:
            sec["level"] = m.group(1).count(".") + 1 if m else 1
        sec.setdefault("page", 1)
        sec["id"] = f"s{i}"                               # 音声のファイル名になるので、並びどおりに付け直す
        sec["speech_title"] = extract._heading_speech(sec["title"])
        for k, s in enumerate(sec["sentences"]):
            if isinstance(s, str):
                s = sec["sentences"][k] = {"t": s}
            if not isinstance(s, dict) or not isinstance(s.get("t"), str):
                errs.append(f"{where}.sentences[{k}] に t（文字列）が無い")
                continue
            if not isinstance(s.get("s"), str):
                s["s"] = extract.speech_text(s["t"])
    paper.setdefault("title", "")
    paper.setdefault("ocr_pages", [])
    return paper, errs


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
        """PDF から取り出し直す（明示の操作）。手直し済みなら sentences.manual.json に退避してから上書きする。"""
        d = self.paper_dir(pid)
        with self._lock:
            cur = d / "sentences.json"
            if cur.exists() and '"manual"' in cur.read_text(encoding="utf-8"):
                shutil.copy2(cur, d / "sentences.manual.json")
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            meta.pop("manual", None)
            return self._extract(d, meta)

    def _extract(self, d: Path, meta: dict, on_page=None) -> dict:
        paper = extract.extract_file(d / "original.pdf", on_page)
        paper["id"] = meta["id"]
        _write_json(d / "sentences.json", paper)
        try:
            figures.extract(d, force=True)              # 図・表の画像（手直し済みの figures.json は触らない）
        except Exception as e:  # 画像が壊れているなど。文の取り出しは続ける
            print(f"図を取り出せなかった: {e}")
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
            path = d / "sentences.json"
            raw = path.read_text(encoding="utf-8") if path.exists() else None
            manual = raw is not None and '"manual"' in raw
            if not manual and (meta.get("extractor_version") != extract.EXTRACTOR_VERSION or raw is None):
                self._extract(d, meta)                  # 取り出し方を変えたら作り直す（手直し済みは上書きしない）
                raw = path.read_text(encoding="utf-8")
            try:
                paper = json.loads(raw)
            except ValueError as e:
                raise ValueError(f"sentences.json が JSON として読めない（{e}）。sentences.orig.json に戻すか直してください") from e
            paper, errs = normalize(paper)
            if errs:
                raise ValueError("sentences.json の形が正しくない: " + " / ".join(errs[:5]))
            paper["id"] = pid
            if paper.get("manual"):
                counts = {"sections": len(paper["sections"]),
                          "sentences": sum(len(s["sentences"]) for s in paper["sections"]), "manual": paper["manual"]}
                if any(meta.get(k) != v for k, v in counts.items()):
                    meta.update(counts)                  # 一覧の章・文の数を手直し後に合わせる
                    _write_json(d / "meta.json", meta)
        return paper
