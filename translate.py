"""文ごとの日本語訳を、macOS 内蔵の翻訳（Translation フレームワーク、端末内）で作る。

  data/papers/<id>/translation.json
    {"state": "done" | "running" | "need_install" | "unsupported" | "error",
     "engine": "apple", "target": "ja", "total", "done", "items": {"<sec>_<k>": "訳", ...}}

  data/papers/<id>/translation_cache.json   {"原文": "訳"}。章を編集して文の番号がずれても、同じ文は訳し直さない
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


def _read_cache(paper_dir: Path) -> dict:
    p = paper_dir / "translation_cache.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        return {}


def _write_cache(paper_dir: Path, cache: dict):
    p = paper_dir / "translation_cache.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def remember(paper_dir: Path, paper: dict):
    """今の訳を原文ごとの控えに入れておく（章を編集する前に呼ぶ。控えが無い前の訳も使い回せるように）。"""
    st = status(paper_dir)
    if st.get("state") != "done" or not is_current(paper_dir, paper):
        return
    cache = _read_cache(paper_dir)
    cache.update({t: st["items"][k] for k, t in items(paper) if k in st.get("items", {})})
    _write_cache(paper_dir, cache)


def view(paper_dir: Path, paper: dict) -> tuple[dict, dict]:
    """画面に出す (状態, {"<sec>_<k>": 訳})。文が変わって作り直す前でも、同じ文の訳は控えから出す。"""
    st = status(paper_dir)
    ja = st.pop("items", {})
    if st.get("state") in (None, "none") or is_current(paper_dir, paper):
        return st, ja
    cache = _read_cache(paper_dir)
    its = items(paper)
    ja = {k: cache[t] for k, t in its if t in cache}
    return {**st, "state": "running", "done": len(ja), "total": len(its)}, ja


RETRIES, RETRY_WAIT = 2, 3.0


def call(exe: Path, texts: list[str]) -> dict:
    """訳す。「使える」（installed）以外が返ったら、少し待って2回まで呼び直す。
    翻訳データが入っているのに、たまに1回だけ未導入（supported）やエラーが返ることがあるため（2026-09-24 に2回見た）。
    3回とも同じなら、その答えを返す（本当に未導入なら need_install になる）。最後の答えの中身は "raw" に残す。"""
    import time
    res = {}
    for i in range(RETRIES + 1):
        res = _call(exe, texts)
        if res.get("status") == "installed":
            if i:
                res["retried"] = i
            return res
        if i < RETRIES:
            time.sleep(RETRY_WAIT)
    res["raw"] = {k: v for k, v in res.items() if k != "translations"}
    return res


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
    cache = _read_cache(paper_dir)
    st["items"] = {key: cache[t] for key, t in its if t in cache}
    st["done"] = len(st["items"])
    _write(paper_dir, st)                                # 控えにあった訳は最初からすぐ画面に出す
    try:
        todo = [(k, t) for k, t in its if k not in st["items"]]
        exe = translator() if todo else None
        for i in range(0, len(todo), CHUNK):
            chunk = todo[i:i + CHUNK]
            res = call(exe, [t for _, t in chunk])
            st["retried"] = st.get("retried", 0) + res.get("retried", 0)
            if res.get("status") == "supported":
                st.update(state="need_install", error=INSTALL_HINT, items={}, done=0, raw=res.get("raw"))
                return st
            if res.get("status") != "installed":
                st.update(state="unsupported" if res.get("status") == "unsupported" else "error",
                          error=res.get("error") or "この Mac では英語→日本語の翻訳が使えない")
                return st
            for (key, t), ja in zip(chunk, res["translations"]):
                st["items"][key] = cache[t] = ja
            st["done"] = len(st["items"])
            _write(paper_dir, st)
        st.update(state="done", finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # swiftc が無い・時間切れなど
        st.update(state="error", error=str(e)[:500])
    finally:
        _write(paper_dir, st)
        # 訳し終えたら今の文に使わない訳は捨てる。途中で止まったら、訳せた分を控えに残す
        _write_cache(paper_dir, {t: cache[t] for _, t in its if t in cache} if st["state"] == "done" else cache)
    return st
