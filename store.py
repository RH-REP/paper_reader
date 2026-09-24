"""data/ の読み書き。論文1本 = data/papers/<sha8>/ の1フォルダ。

  data/papers/<sha8>/original.pdf    取り込んだ PDF のコピー（正本）
  data/papers/<sha8>/meta.json       題名・元のファイル名・取り込み日時・sha256・OCR したページ
  data/papers/<sha8>/sentences.json  章と文（original.pdf から作り直せるキャッシュ）
                                     AI などが手直ししたら最上位に "manual" が付き、自動の取り出し直しで上書きしない
  data/papers/<sha8>/sentences.orig.json   手直し前の自動取り出し（手直しする側が残す）
  data/papers/<sha8>/original.txt    貼り付けたテキスト（PDF の代わりの正本。このときは original.pdf が無い）

取り込みは元の PDF をコピーするだけで、元のファイルは動かさない。
画面での章の編集（edit_sections）も手直しとして "manual" を付ける。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
MAX_LEVEL = 3
MAX_TEXT = 2_000_000


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

    def import_text(self, title: str, text: str) -> dict:
        """貼り付けた英文を取り込む。同じ題名・中身が既にあれば、それをそのまま返す。"""
        text = text.strip()
        if not text:
            raise ValueError("テキストが空")
        if len(text) > MAX_TEXT:
            raise ValueError("テキストが長すぎる")
        sha = hashlib.sha256(f"{title.strip()}\n{text}".encode("utf-8")).hexdigest()
        pid = sha[:8]
        d = self.papers / pid
        if (d / "meta.json").exists():
            return {**json.loads((d / "meta.json").read_text(encoding="utf-8")), "already": True}
        d.mkdir(parents=True, exist_ok=True)
        (d / "original.txt").write_text(text + "\n", encoding="utf-8")
        meta = {"id": pid, "sha256": sha, "source": "text", "source_name": title.strip() or "貼り付けたテキスト",
                "given_title": title.strip(), "imported_at": datetime.now().isoformat(timespec="seconds")}
        return self._extract(d, meta)

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

    @staticmethod
    def _version(meta: dict):
        return f"text{extract.TEXT_VERSION}" if meta.get("source") == "text" else extract.EXTRACTOR_VERSION

    def _extract(self, d: Path, meta: dict, on_page=None) -> dict:
        if meta.get("source") == "text":
            paper = extract.build_from_text(meta.get("given_title", ""), (d / "original.txt").read_text(encoding="utf-8"))
        else:
            paper = extract.extract_file(d / "original.pdf", on_page)
        paper["id"] = meta["id"]
        _write_json(d / "sentences.json", paper)
        if (d / "original.pdf").exists():
            try:
                figures.extract(d, force=True)          # 図・表の画像（手直し済みの figures.json は触らない）
            except Exception as e:  # 画像が壊れているなど。文の取り出しは続ける
                print(f"図を取り出せなかった: {e}")
        meta.update({
            "title": paper["title"] or (meta["source_name"] if meta.get("source") == "text" else Path(meta["source_name"]).stem),
            "pages": paper["pages"], "ocr_pages": paper["ocr_pages"],
            "sections": len(paper["sections"]),
            "sentences": sum(len(s["sentences"]) for s in paper["sections"]),
            "extracted_at": datetime.now().isoformat(timespec="seconds"),
            "extractor_version": self._version(meta),
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
            if not manual and (meta.get("extractor_version") != self._version(meta) or raw is None):
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

    def edit_sections(self, pid: str, op: str, sec: int, sentence: int | None = None, title: str | None = None,
                      take: bool = False) -> dict:
        """画面からの章の編集。sec は章の並び順（0始まり）、sentence はその章の文の並び順（0始まり）。
          indent / outdent  その章と、その下にぶら下がる節（段がより深く続く章）を1段下げる／上げる（1〜3段）
          split             sentence の文から新しい章にする。take なら、その文を見出しにする（本文から外す）
          merge             見出しを文に戻して、前の章につなぐ
          rename            見出しを title にする
        手直しとして manual を付けて保存する（自動の取り出し直しで上書きしない）。直せないときは ValueError。"""
        d = self.paper_dir(pid)
        with self._lock:
            paper = self.load(pid)
            secs = paper["sections"]
            if not (isinstance(sec, int) and 0 <= sec < len(secs)):
                raise ValueError("章が見つからない")
            cur = secs[sec]
            if op in ("indent", "outdent"):
                end = sec + 1
                while end < len(secs) and secs[end]["level"] > cur["level"]:
                    end += 1
                step = 1 if op == "indent" else -1
                if op == "indent" and (sec == 0 or cur["level"] > secs[sec - 1]["level"]):
                    raise ValueError("前の章より2段以上深くはできない")
                if op == "indent" and max(s["level"] for s in secs[sec:end]) >= MAX_LEVEL:
                    raise ValueError(f"{MAX_LEVEL}段より深くはできない")
                if op == "outdent" and cur["level"] <= 1:
                    raise ValueError("これより上の段は無い")
                for s in secs[sec:end]:
                    s["level"] += step
            elif op == "split":
                sents = cur["sentences"]
                if not (isinstance(sentence, int) and 0 <= sentence < len(sents)):
                    raise ValueError("文が見つからない")
                if not take and sentence == 0:
                    raise ValueError("章の最初の文では分けられない（見出しにするなら take）")
                head = sents[sentence]["t"].strip() if take else (title or "").strip()
                if take:
                    head = (title or "").strip() or re.sub(r"[.:]\s*$", "", head)
                if not head:
                    raise ValueError("見出しが空")
                rest = sents[sentence + 1:] if take else sents[sentence:]
                cur["sentences"] = sents[:sentence]
                secs.insert(sec + 1, {"title": head, "kind": cur["kind"], "level": cur["level"],
                                      "page": cur.get("page", 1), "sentences": rest})
            elif op == "merge":
                if sec == 0:
                    raise ValueError("最初の章は前とつなげない")
                prev = secs[sec - 1]
                prev["sentences"] += [{"t": cur["title"]}] + cur["sentences"]
                del secs[sec]
            elif op == "rename":
                if not (title or "").strip():
                    raise ValueError("見出しが空")
                cur["title"] = title.strip()
            else:
                raise ValueError(f"知らない操作: {op}")
            for s in secs:                                # 番号・読み上げ用の見出しは normalize で付け直す
                for k in ("number", "speech_title", "id"):
                    s.pop(k, None)
            orig = d / "sentences.orig.json"
            if not orig.exists():
                shutil.copy2(d / "sentences.json", orig)   # 最初の手直しの前の形を残す
            now = datetime.now().isoformat(timespec="seconds")
            paper["manual"] = {**(paper.get("manual") or {}), "by": (paper.get("manual") or {}).get("by", "user"),
                               "at": (paper.get("manual") or {}).get("at", now), "edited_at": now,
                               "notes": (paper.get("manual") or {}).get("notes", "画面で章を編集")}
            paper, errs = normalize(paper)
            if errs:
                raise ValueError(" / ".join(errs[:3]))
            _write_json(d / "sentences.json", paper)
            return self.load(pid)
