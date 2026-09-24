"""文ごとの日本語訳を、macOS 内蔵の翻訳（Translation フレームワーク、端末内）で作る。

  data/papers/<id>/translation.json
    {"state": "done" | "running" | "need_install" | "unsupported" | "error",
     "engine": "apple", "target": "ja", "total", "done", "items": {"<sec>_<k>": "訳", ...}}

- 文（画面に出す原文 t）を CHUNK 文ずつ tools/mac_translate.swift に渡す。翻訳はこの Mac の中だけで行う
- 英語・日本語の言語データが入っていなければ state = need_install（システム設定から入れて「作り直す」）
- tools/mac_translate.swift は初回に swiftc で <このフォルダ>/.bin/mac_translate に作る（Xcode のコマンドラインツールが要る）
- 環境変数 PAPER_READER_TRANSLATOR があれば、その実行ファイルを代わりに使う（テスト用）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "tools" / "mac_translate.swift"
BINARY = HERE / ".bin" / "mac_translate"
CHUNK = 40
INSTALL_HINT = ("システム設定 → 一般 → 言語と地域 → 「翻訳言語…」で「英語」と「日本語」をダウンロードしてから、"
                "「訳を作り直す」を押してください")


def translator() -> Path:
    env = os.environ.get("PAPER_READER_TRANSLATOR")
    if env:
        return Path(env)
    if not BINARY.exists() or BINARY.stat().st_mtime < SOURCE.stat().st_mtime:
        swiftc = shutil.which("swiftc")
        if not swiftc:
            raise RuntimeError("swiftc が無い（xcode-select --install で Xcode のコマンドラインツールを入れる）")
        BINARY.parent.mkdir(exist_ok=True)
        subprocess.run([swiftc, "-O", str(SOURCE), "-o", str(BINARY)], check=True, capture_output=True)
    return BINARY


def items(paper: dict) -> list[tuple[str, str]]:
    return [(f"{sec['id']}_{k}", s["t"]) for sec in paper["sections"] for k, s in enumerate(sec["sentences"], 1) if s["t"]]


def items_hash(paper: dict) -> str:
    import hashlib
    return hashlib.sha1(json.dumps(items(paper), ensure_ascii=False).encode()).hexdigest()


def is_current(paper_dir: Path, paper: dict) -> bool:
    """今ある訳が、今の文と合っているか（目印の無い古いものは、取り出し方の版が同じなら合っているとみなす）。"""
    st = status(paper_dir)
    h = items_hash(paper)
    if st.get("items_hash"):
        return st["items_hash"] == h
    if st.get("state") not in (None, "none", "running") and st.get("extractor_version") == paper.get("extractor_version") \
            and not paper.get("manual"):
        st["items_hash"] = h
        _write(paper_dir, st)
        return True
    return False


def status(paper_dir: Path) -> dict:
    p = paper_dir / "translation.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"state": "none"}


def _write(paper_dir: Path, st: dict):
    p = paper_dir / "translation.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _call(exe: Path, texts: list[str]) -> dict:
    r = subprocess.run([str(exe)], input=json.dumps({"source": "en", "target": "ja", "texts": texts}),
                       capture_output=True, text=True, timeout=600)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"status": "error", "error": (r.stderr or r.stdout or f"exit {r.returncode}").strip()[:500]}


def generate(paper_dir: Path, paper: dict) -> dict:
    its = items(paper)
    st = {"state": "running", "engine": "apple", "target": "ja", "total": len(its), "done": 0, "items": {},
          "extractor_version": paper.get("extractor_version"), "items_hash": items_hash(paper),
          "started_at": datetime.now().isoformat(timespec="seconds")}
    _write(paper_dir, st)
    try:
        exe = translator()
        for i in range(0, len(its), CHUNK):
            chunk = its[i:i + CHUNK]
            res = _call(exe, [t for _, t in chunk])
            if res.get("status") == "supported":
                st.update(state="need_install", error=INSTALL_HINT, items={}, done=0)
                return st
            if res.get("status") != "installed":
                st.update(state="unsupported" if res.get("status") == "unsupported" else "error",
                          error=res.get("error") or "この Mac では英語→日本語の翻訳が使えない")
                return st
            for (key, _), ja in zip(chunk, res["translations"]):
                st["items"][key] = ja
            st["done"] = len(st["items"])
            _write(paper_dir, st)
        st.update(state="done", finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # swiftc が無い・時間切れなど
        st.update(state="error", error=str(e)[:500])
    finally:
        _write(paper_dir, st)
    return st
