#!/usr/bin/env python3
"""paper_reader のローカルサーバー。

画面（index.html, assets/）を配り、論文の取り込み・章と文・読み上げ音声・単語検索の API を出す。
データの置き場は config.json の data_root（config.json からの相対か絶対パス。既定 data/）。
127.0.0.1 だけで待つ。

  GET  /api/health
  GET  /api/config                      声・速さ・使える声・辞書の有無
  POST /api/config                      {"voice": ..., "rate": ...} を config.json に保存
  GET  /api/papers                      取り込んだ論文の一覧（meta.json ＋ しおり position ＋ 印の数 marks）
  GET  /api/papers/<id>                 章と文（sentences.json）＋ audio（音声の作成状態）
  POST /api/papers/<id>/reextract       章と文を作り直し、音声も作り直す
  POST /api/papers/<id>/sections        章の編集 {"op": indent|outdent|split|merge|rename, "sec", "sentence", "title", "take"}
                                        文が変わった分だけ音声・訳を作り直す（同じ文は控えから写す）
  POST /api/papers/<id>/audio           音声を今の声・速さで作り直す
  POST /api/papers/<id>/translate       文ごとの日本語訳を作り直す（macOS 内蔵の翻訳。端末内）
  GET  /api/papers/<id>/ai_prompt       AI に手直しを頼むプロンプト（フォルダの場所 ＋ ai_fix_prompt.md）
  GET  /api/papers/<id>/ai_fix          この Mac の Claude Code に頼んだ手直しの状態（available = claude があるか）
  POST /api/papers/<id>/ai_fix          {"action": "start"|"stop"} 手直しを頼む／止める（1本ずつ）
  GET  /api/papers/<id>/figures         図・表・数式の一覧（figures.json）
  GET  /api/papers/<id>/glossary        専門用語の一覧（glossary.json。辞書に無い語・略語と元の語・よく出る句と訳）
  GET  /api/papers/<id>/figures/<file>.png  図・表・数式の画像
  POST /api/papers/<id>/reveal          持ち出し用の音声フォルダ（export/）を Finder で開く
  GET  /api/papers/<id>/audio/<item>.m4a    1文ずつの音声
  GET  /api/papers/<id>/export/<file>.m4a   章ごとの音声
  POST /api/import                      本文 = PDF のバイト列、ヘッダー X-Filename = 元のファイル名（URL エンコード）
  GET  /api/pronounce                   読み方の辞書 {"rules": [{"from", "to", "case"}]}
  POST /api/pronounce                   読み方の辞書を保存（変わった文だけ音声が作り直される）
  POST /api/pronounce/preview           {"text"} 直した読みと試聴の音声（/api/preview/<名前>.m4a）
  POST /api/position                    しおり {"paper_id", "sentence", "section", "item_id", "offset", "done", "total"}
  POST /api/marks/toggle                {"paper_id", "sentence", "section"} 文の印を付ける／外す
  POST /api/marks/<uid>/note            {"note"} 印のメモ
  GET  /api/marks/export?paper=<id>     印を付けた文の Markdown（paper なしで全部の論文）
  POST /api/import_text                 {"title", "text"} 貼り付けた英文を取り込む
  GET  /api/import                      取り込み中の進み具合（何ページ目か。プログレスバー用）
  GET  /api/lookup?w=<語>                単語を引く（単語帳には入れない。registered = 登録済みか）。辞書に無い語は論文の用語集の訳
  POST /api/vocab/add                   {"w", "paper", "sec", "sentence"} で引き直して単語帳に登録する
  GET  /api/word_audio?w=<語>           単語の発音
  GET  /api/vocab                       単語帳
  POST /api/vocab/<n>/delete            単語帳から消す
  GET  /api/review                      次に復習する語（表・裏・4つのボタンの間隔）と残りの数
  POST /api/review/answer               {"headword", "rating": 1-4}（もう一度／難しい／正解／簡単）
  POST /api/review/undo                 この Mac で最後に答えた1件を取り消す
  GET  /api/bundle?papers=<id,id>       スマホ用の zip を作って渡す（ファイルで写すとき）
  POST /api/progress                    本文 = スマホから書き出した記録（JSON）。単語帳に合わせる
  GET  /api/share                       受け渡しページの状態
  GET  /api/qr?t=<文字列>                QR コード（SVG）
  POST /api/share/start                 {"papers": [id...]} で zip を作り、同じ Wi‑Fi 向けの受け渡しページを開く（10分）
  POST /api/share/stop                  受け渡しページを閉じる
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aifix  # noqa: E402
import audio  # noqa: E402
import bundle  # noqa: E402
import figures  # noqa: E402
import glossary  # noqa: E402
import pronounce  # noqa: E402
import quality  # noqa: E402
import translate  # noqa: E402
from lookup import Dictionary  # noqa: E402
from share import ShareServer  # noqa: E402
from state import State  # noqa: E402
from store import Store  # noqa: E402
from vocab import Vocab  # noqa: E402

STATIC = {"/": "index.html", "/index.html": "index.html"}
MAX_UPLOAD = 200 * 1024 * 1024
DEFAULTS = {"data_root": "data", "voice": audio.DEFAULT_VOICE, "rate": 175, "online_dict": True,
            "pwa_url": "https://rh-rep.github.io/paper_reader/"}


def load_config(path: Path | None) -> dict:
    cfg = dict(DEFAULTS)
    if path and path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    base = path.parent if path else HERE
    root = Path(cfg["data_root"]).expanduser()
    cfg["data_root"] = root if root.is_absolute() else (base / root).resolve()
    return cfg


class App:
    """サーバー全体で1つ。設定・保存先・音声づくりのジョブを持つ。"""

    def __init__(self, config_path: Path | None):
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self.store = Store(self.cfg["data_root"])
        self.dict = Dictionary(self.cfg["data_root"], online=self.cfg["online_dict"])
        self.localdict = Dictionary(self.cfg["data_root"], online=False)    # 用語集づくり用（ネットを使わない）
        self._gloss = {"sig": None, "index": {}}
        self.aifix = aifix.AiFixer()
        self.vocab = Vocab(self.cfg["data_root"])
        self.state = State(self.cfg["data_root"])
        audio.PRONOUNCER = self.pron = pronounce.Pronouncer(self.store.root / "pronunciations.json")
        self.lock = threading.Lock()
        self.jobs: dict[str, threading.Thread] = {}
        self.redo: set[str] = set()
        self.share = ShareServer(self.merge_progress)
        self.importing = {"active": False}              # 取り込み中の進み具合

    def merge_progress(self, data: bytes) -> dict:
        prog = bundle.read_progress(data)
        return {**self.vocab.merge(prog), **{f"state_{k}": v for k, v in self.state.merge(prog).items()}}

    def make_bundle(self, ids) -> Path:
        ids = [i for i in ids if re.fullmatch(r"[0-9a-f]{8}", i or "")]
        for i in ids:
            self.store.paper_dir(i)                       # 無い id は KeyError
        return bundle.make_bundle(self.store, self.vocab, ids, self.store.root / "share", self.state)

    def fix_refs(self, q: dict) -> dict:
        """復習カードの例文の音声を、今の文の並びから引き直す（取り出し方を変えると文の番号がずれるため）。
        例文の日本語訳（ja）も付ける（「空欄を埋める」モードで、訳を見て英文の空欄を埋める）。"""
        for e in ((q.get("card") or {}).get("examples") or []):
            e["audio_ref"] = self.audio_ref(e.get("paper_id"), e.get("sentence"))
            e["ja"] = self.sentence_ja(e.get("paper_id"), e.get("sentence"))
        return q

    def sentence_ja(self, pid, sentence) -> str | None:
        """論文の文の日本語訳（文の中身で引く）。"""
        return bundle.sentence_ja(self.store, pid, sentence)

    def audio_ref(self, pid, sentence) -> str | None:
        """引いた文の音声（<論文id>/<音声id>）。見つからなければ None。"""
        if not (pid and sentence):
            return None
        try:
            paper = self.store.load(pid)
        except KeyError:
            return None
        for sec in paper["sections"]:
            for k, s in enumerate(sec["sentences"], 1):
                if s["t"] == sentence and s["s"]:
                    return f"{pid}/{sec['id']}_{k}"
        return None

    def marks_markdown(self, pid: str | None) -> str:
        """印を付けた文を Markdown に（論文ごと・章ごと。英文・訳・メモ・出典）。pid が None なら全部の論文。"""
        metas = {m["id"]: m for m in self.store.list()}
        by_paper: dict[str, list] = {}
        for m in self.state.marks(pid):
            if m["paper_id"] in metas:
                by_paper.setdefault(m["paper_id"], []).append(m)
        out = [f"# 印を付けた文（paper_reader、{datetime.now().strftime('%Y-%m-%d %H:%M')} 書き出し）", ""]
        for p_id, marks in by_paper.items():
            meta = metas[p_id]
            d = self.store.paper_dir(p_id)
            paper = self.store.load(p_id)
            _, ja = translate.view(d, paper)
            order, where = {}, {}
            for sec in paper["sections"]:                  # 論文の中の順に並べ、今の章の名前を付ける
                for k, s in enumerate(sec["sentences"], 1):
                    order.setdefault(s["t"], len(order))
                    where.setdefault(s["t"], (sec["title"], ja.get(f"{sec['id']}_{k}", "")))
            src = meta.get("source_name", "")
            kind = "貼り付けたテキスト" if meta.get("source") == "text" else f"{meta.get('pages', '?')}ページ"
            out += [f"## {meta.get('title') or p_id}", "",
                    f"出典: {src}（{kind}）", ""]
            last_sec = None
            for m in sorted(marks, key=lambda m: order.get(m["sentence"], 1e9)):
                sec_title, j = where.get(m["sentence"], (m.get("section") or "（今の文には見つからない）", ""))
                if sec_title != last_sec:
                    out += [f"### {sec_title}", ""]
                    last_sec = sec_title
                out.append(f"> {m['sentence']}")
                if j:
                    out += [">", f"> {j}"]
                out.append("")
                if m["note"]:
                    out += [f"メモ: {m['note']}", ""]
        if len(out) == 2:
            out.append("（印を付けた文はありません）")
        return "\n".join(out).rstrip() + "\n"

    def python(self) -> str:
        py = HERE / ".venv" / "bin" / "python"
        return str(py if py.exists() else "python3")

    def ai_prompt(self, pid: str) -> tuple[Path, str]:
        """AI に手直しを頼む依頼文（フォルダの場所 ＋ ai_fix_prompt.md）。"""
        d = self.store.paper_dir(pid)
        py = self.python()
        check = f"{shlex.quote(py)} {shlex.quote(str(HERE / 'tools' / 'check_paper.py'))} {shlex.quote(str(d))}"
        fixed = (HERE / "ai_fix_prompt.md").read_text(encoding="utf-8").replace("{check}", check) \
            .replace("{python}", shlex.quote(py)).replace("{folder}", str(d))
        return d, f"{d}\n\n{fixed}"

    def ai_available(self) -> bool:
        """この Mac の Claude Code に頼めるか（claude があり、この app の .venv がある）。"""
        return bool(aifix.find_cli()) and (HERE / ".venv" / "bin" / "python").exists()

    def start_ai_fix(self, pid: str) -> dict:
        d, prompt = self.ai_prompt(pid)

        def done(st):                                # 手直しが終わったら音声・訳・用語集を今の文で作り直す
            try:
                self.store.load(pid)
                self.start_audio(pid)
                self.start_translate(pid)
                self.start_glossary(pid)
            except (ValueError, KeyError) as e:
                st["result"] = f"{st.get('result', '')}\n（app が読めない: {e}）"
        return self.aifix.start(pid, d, prompt, self.python(), done)

    def quality(self, pid: str, d: Path, paper: dict) -> dict:
        return quality.summary(quality.inspect(paper, figures.status(d).get("items", []) if (d / "original.pdf").exists() else None))

    def preview(self, text: str) -> dict:
        """読み方の試聴。直した読み（spoken）と、その音声のファイル名。data/preview/ に最近の 40 本だけ残す。"""
        spoken = self.pron.apply(text)
        d = self.store.root / "preview"
        d.mkdir(exist_ok=True)
        voice = audio.resolve_voice(self.cfg["voice"])
        name = audio._cache_key(voice, self.cfg["rate"], spoken) + ".m4a"
        if not (d / name).exists():
            wav = d / (name + ".wav")
            audio._say(spoken, wav, voice, self.cfg["rate"])
            audio._to_m4a(wav, d / name)
            wav.unlink(missing_ok=True)
        for old in sorted(d.glob("*.m4a"), key=lambda p: p.stat().st_mtime)[:-40]:
            old.unlink(missing_ok=True)
        return {"text": text, "spoken": spoken, "url": f"/api/preview/{name}"}

    def save_config(self, voice=None, rate=None):
        if voice is not None:
            self.cfg["voice"] = voice
        if rate is not None:
            self.cfg["rate"] = int(rate)
        if self.config_path:
            raw = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}
            raw.update(voice=self.cfg["voice"], rate=self.cfg["rate"])
            self.config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _loop(self, key: str, pid: str, run, current):
        """仕事を走らせ、終わったときに文が変わっていれば（作っている最中に章を編集したなど）今の文でもう一度走らせる。"""
        while True:
            run(self.store.paper_dir(pid), self.store.load(pid))
            with self.lock:
                d = self.store.paper_dir(pid)
                if key not in self.redo and current(d, self.store.load(pid)):
                    self.jobs.pop(key, None)
                    return
                self.redo.discard(key)

    def _start(self, key: str, pid: str, force: bool, run, current, done_states) -> bool:
        with self.lock:
            t = self.jobs.get(key)
            if t and t.is_alive():
                if force:
                    self.redo.add(key)                   # 走っている仕事が終わったら、もう一度走らせる
                return False
            d = self.store.paper_dir(pid)
            paper = self.store.load(pid)
            # 作り終えたもの・失敗したものは、文が変わる（手直し・章の編集・取り出し直し）か明示の作り直しまで触らない
            if not force and self._status(key, d).get("state") in done_states and current(d, paper):
                return False
            t = threading.Thread(target=self._loop, args=(key, pid, run, current), daemon=True, name=key)
            self.jobs[key] = t
            t.start()
            return True

    @staticmethod
    def _status(key: str, d: Path) -> dict:
        return translate.status(d) if key.startswith("tr-") else audio.status(d)

    def start_audio(self, pid: str, force=False) -> bool:
        """その論文の音声づくりを裏で始める。すでに走っていれば、終わったあとに今の文と合うか確かめ直す。"""
        return self._start(f"audio-{pid}", pid, force,
                           lambda d, p: audio.generate(d, p, self.cfg["voice"], self.cfg["rate"]),
                           audio.is_current, ("done", "error"))

    def start_translate(self, pid: str, force=False) -> bool:
        """その論文の日本語訳づくりを裏で始める（音声とは別の仕事として並べて走る）。"""
        return self._start(f"tr-{pid}", pid, force, translate.generate,
                           translate.is_current,
                           ("done", "need_install", "unsupported", "error"))

    def start_glossary(self, pid: str, force=False) -> bool:
        """専門用語の一覧づくり（訳の仕組みを使うので、訳のあとに走ることが多い）。"""
        return self._start(f"gl-{pid}", pid, force, lambda d, p: glossary.generate(d, p, self.localdict),
                           glossary.is_current, ("done", "no_translation"))

    @staticmethod
    def _term_key(w: str) -> str:
        w = w.lower()
        return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w   # actuators → actuator、DMs → dm

    def glossary_index(self) -> dict:
        """全部の論文の用語集をまとめた索引 {語の鍵: {論文id: 項目}}。glossary.json が変わったときだけ作り直す。"""
        files = sorted(self.store.papers.glob("*/glossary.json"))
        sig = tuple((str(f), f.stat().st_mtime) for f in files)
        if sig != self._gloss["sig"]:
            idx: dict[str, dict] = {}
            titles = {m["id"]: m.get("title", "") for m in self.store.list()}
            for f in files:
                pid = f.parent.name
                for t in json.loads(f.read_text(encoding="utf-8")).get("terms", []):
                    if not t.get("ja") or t["kind"] == "phrase":
                        continue
                    per = idx.setdefault(self._term_key(t["term"]), {})
                    if pid not in per or per[pid]["count"] < t["count"]:
                        per[pid] = {**t, "paper": titles.get(pid, ""), "paper_id": pid}
            self._gloss = {"sig": sig, "index": idx}
        return self._gloss["index"]

    def lookup(self, word: str, pid: str | None = None) -> dict:
        """辞書で引き、無い語（派生語・部分でしか引けない語も）は論文の用語集の訳を出す。読んでいる論文の用語集を先に使う。"""
        res = self.dict.lookup(word)
        if res["found"] and res.get("note") not in ("近い語", "部分"):
            return res
        per = self.glossary_index().get(self._term_key(res.get("normalized") or word))
        if not per:
            return res
        t = per.get(pid) or max(per.values(), key=lambda x: x["count"])
        mean = t["ja"] + (f"（{t['expansion']}）" if t.get("expansion") else "")
        return {**res, "found": True, "source": f"用語集（端末内の翻訳・{t['paper'][:40]}）", "headword": t["term"],
                "entries": [{"word": t["term"], "mean": mean}] + (res["entries"] if res["found"] else []),
                "note": "専門用語"}

    def resume_pending(self):
        """起動時: 音声・訳がまだ無い・途中で止まった論文の仕事を始める。"""
        for m in self.store.list():
            d = self.store.paper_dir(m["id"])
            if audio.status(d).get("state") in ("none", "running"):
                self.start_audio(m["id"], force=True)
            if translate.status(d).get("state") in ("none", "running"):
                self.start_translate(m["id"], force=True)
            if glossary.status(d).get("state") in ("none", "running"):
                self.start_glossary(m["id"], force=True)


class Handler(SimpleHTTPRequestHandler):
    app: App

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def end_headers(self):
        # update.command で画面を差し替えたら、次に開いたときに必ず新しいものを読む
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str):
        """Range に応える（Safari の <audio> は Range が無いと再生しない）。"""
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        size = path.stat().st_size
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        partial = bool(m) and (m.group(1) or m.group(2))
        if partial:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as f:
            f.seek(start)
            self.wfile.write(f.read(end - start + 1))

    def _paper_dir(self, pid):
        try:
            return self.app.store.paper_dir(pid)
        except KeyError:
            return None

    @staticmethod
    def _audio_view(d: Path, paper: dict) -> dict:
        """音声の状態。文が変わって作り直しが始まる直前も「作っている」として返す（古い番号の音声を鳴らさない）。"""
        au = audio.ensure_durations(d, paper)
        if au.get("state") in ("done", "error") and not audio.is_current(d, paper):
            au = {**au, "state": "running", "done": 0, "total": len(audio.items(paper)), "phase": None}
        return au

    def _paper_payload(self, pid: str, d: Path, paper: dict) -> dict:
        self.app.start_audio(pid)                        # 無い・古い（文が変わった）ときだけ始まる
        self.app.start_translate(pid)
        gl = glossary.status(d)
        # 訳の仕組みが一時的に使えず訳なしで作った用語集は、訳が使えるようになっていれば作り直す
        self.app.start_glossary(pid, force=gl.get("state") == "no_translation" and translate.status(d).get("state") == "done")
        tr, ja = translate.view(d, paper)
        au = self._audio_view(d, paper)
        gl = glossary.status(d)
        return {**paper, "quality": self.app.quality(pid, d, paper),
                "ai_fix": {**aifix.status(d), "available": self.app.ai_available(), "busy": self.app.aifix.running()},
                "audio": au, "ja": ja, "translation": tr, "has_pdf": (d / "original.pdf").exists(),
                "glossary": {"state": gl.get("state"), "current": glossary.is_current(d, paper), "terms": gl.get("terms", [])},
                "marks": self.app.state.marks(pid), "position": self.app.state.positions().get(pid)}

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlsplit(self.path)
        path, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        app = self.app
        if path == "/api/health":
            return self._json({"ok": True, "data_root": str(app.store.root)})
        if path == "/api/config":
            meta = app.dict.path.parent / "meta.json"
            return self._json({"voice": audio.resolve_voice(app.cfg["voice"]), "rate": app.cfg["rate"],
                               "pwa_url": app.cfg["pwa_url"],
                               "voices": audio.voices(), "dict": app.dict.available(),
                               "dict_meta": json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None})
        if path == "/api/papers":
            pos = app.state.positions()
            counts: dict[str, int] = {}
            for mk in app.state.marks():
                counts[mk["paper_id"]] = counts.get(mk["paper_id"], 0) + 1
            return self._json([{**m, "position": pos.get(m["id"]), "marks": counts.get(m["id"], 0)} for m in app.store.list()])
        if path == "/api/marks/export":
            pid = q.get("paper") or None
            if pid and not self._paper_dir(pid):
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            body = app.marks_markdown(pid).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            try:
                paper = app.store.load(m.group(1))
            except ValueError as e:                      # 手直しした sentences.json が壊れている
                return self._json({"error": str(e)}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return self._json(self._paper_payload(m.group(1), d, paper))
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/figures", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            if not (d / "original.pdf").exists():       # 貼り付けたテキストには図が無い
                return self._json({"version": 0, "items": []})
            try:
                return self._json(figures.extract(d))   # 無い・古いときだけ作る（前に取り込んだ論文もここで作られる）
            except Exception as e:
                return self._json({"version": 0, "items": [], "error": str(e)})
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/figures/([A-Za-z0-9_\-]+\.png)", path)
        if m:
            d = self._paper_dir(m.group(1))
            return self._file(d / "figures" / m.group(2), "image/png") if d else self.send_error(HTTPStatus.NOT_FOUND)
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/ai_prompt", path)
        if m:
            if not self._paper_dir(m.group(1)):
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            d, prompt = app.ai_prompt(m.group(1))
            return self._json({"folder": str(d), "prompt": prompt})
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/ai_fix", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self._json({**aifix.status(d), "available": app.ai_available(), "busy": app.aifix.running()})
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/glossary", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            gl = glossary.status(d)
            try:
                gl["current"] = glossary.is_current(d, app.store.load(m.group(1)))
            except ValueError:
                gl["current"] = False
            return self._json(gl)
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/translation", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            try:
                tr, ja = translate.view(d, app.store.load(m.group(1)))
            except ValueError as e:
                return self._json({"error": str(e)}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return self._json({**tr, "ja": ja})
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/audio", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            try:
                return self._json(self._audio_view(d, app.store.load(m.group(1))))
            except ValueError:
                return self._json(audio.status(d))
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/(audio|export)/([A-Za-z0-9_]+\.m4a)", path)
        if m:
            d = self._paper_dir(m.group(1))
            return self._file(d / m.group(2) / m.group(3), "audio/mp4") if d else self.send_error(HTTPStatus.NOT_FOUND)
        if path == "/api/lookup":
            res = app.lookup(q.get("w", ""), q.get("paper"))
            res["registered"] = bool(res["found"]) and app.vocab.has(res["headword"])
            return self._json(res)
        if path == "/api/word_audio":
            try:
                p = audio.word_audio(app.store.root / "words", q.get("w", ""), app.cfg["voice"], app.cfg["rate"])
            except Exception as e:
                return self._json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._file(p, "audio/mp4") if p else self.send_error(HTTPStatus.BAD_REQUEST)
        if path == "/api/vocab":
            return self._json(app.vocab.list())
        if path == "/api/review":
            return self._json(app.fix_refs(app.vocab.queue()))
        if path == "/api/pronounce":
            return self._json({"rules": app.pron.rules()})
        m = re.fullmatch(r"/api/preview/([0-9a-f]{20}\.m4a)", path)
        if m:
            return self._file(app.store.root / "preview" / m.group(1), "audio/mp4")
        if path == "/api/share":
            return self._json(app.share.status())
        if path == "/api/import":
            return self._json(app.importing)
        if path == "/api/qr":
            from share import qr_svg
            t = q.get("t", "")[:500]
            if not t:
                return self._json({"error": "t が空"}, HTTPStatus.BAD_REQUEST)
            body = qr_svg(t).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/bundle":
            try:
                z = app.make_bundle([x for x in q.get("papers", "").split(",") if x])
            except KeyError:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(z.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{z.name}"')
            self.end_headers()
            with z.open("rb") as f:
                while chunk := f.read(1 << 16):
                    self.wfile.write(chunk)
            return
        # 画面だけを配る。data/ など他のファイルは出さない
        if path in STATIC or path.startswith("/assets/"):
            self.path = "/" + STATIC.get(path, path.lstrip("/"))
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def _body_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def do_POST(self):
        path = urlsplit(self.path).path
        app = self.app
        if path == "/api/config":
            b = self._body_json()
            names = {v["name"] for v in audio.voices()}
            voice = b.get("voice") if b.get("voice") in names else None
            rate = b.get("rate")
            rate = int(rate) if isinstance(rate, (int, float)) and 90 <= rate <= 360 else None
            app.save_config(voice, rate)
            return self._json({"voice": app.cfg["voice"], "rate": app.cfg["rate"]})
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/(reextract|audio|reveal|translate)", path)
        if m:
            pid, act = m.groups()
            d = self._paper_dir(pid)
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            if act == "reextract":
                meta = app.store.reextract(pid)
                app.start_audio(pid, force=True)
                app.start_translate(pid, force=True)
                return self._json(meta)
            if act == "translate":
                return self._json({"started": app.start_translate(pid, force=True)})
            if act == "audio":
                started = app.start_audio(pid, force=True)
                return self._json({"started": started, **audio.status(d)})
            (d / "export").mkdir(exist_ok=True)
            subprocess.run(["open", str(d / "export")], check=False)
            return self._json({"opened": str(d / "export")})
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/sections", path)
        if m:
            pid = m.group(1)
            d = self._paper_dir(pid)
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            b = self._body_json()
            try:
                translate.remember(d, app.store.load(pid))   # 番号がずれる前の訳を原文ごとに控える
                paper = app.store.edit_sections(pid, str(b.get("op", "")), b.get("sec"), b.get("sentence"),
                                                b.get("title"), bool(b.get("take")))
            except ValueError as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return self._json(self._paper_payload(pid, d, paper))
        if path == "/api/import_text":
            b = self._body_json()
            if not isinstance(b.get("text"), str) or not isinstance(b.get("title", ""), str):
                return self._json({"error": "text（文字列）が要る"}, HTTPStatus.BAD_REQUEST)
            try:
                meta = app.store.import_text(b.get("title", ""), b["text"])
            except ValueError as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            app.start_audio(meta["id"])
            app.start_translate(meta["id"])
            app.start_glossary(meta["id"])
            return self._json(meta)
        if path == "/api/pronounce":
            b = self._body_json()
            if not isinstance(b.get("rules"), list):
                return self._json({"error": "rules（配列）が要る"}, HTTPStatus.BAD_REQUEST)
            return self._json({"rules": app.pron.save(b["rules"])})
        if path == "/api/pronounce/preview":
            t = str(self._body_json().get("text", "")).strip()[:400]
            if not t:
                return self._json({"error": "text が空"}, HTTPStatus.BAD_REQUEST)
            try:
                return self._json(app.preview(t))
            except Exception as e:  # say / afconvert が無いなど
                return self._json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/ai_fix", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            act = self._body_json().get("action")
            try:
                if act == "start":
                    return self._json(app.start_ai_fix(m.group(1)))
                if act == "stop":
                    return self._json({"stopped": app.aifix.stop(m.group(1), d)})
            except RuntimeError as e:
                return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
            return self._json({"error": "action は start か stop"}, HTTPStatus.BAD_REQUEST)
        if path == "/api/position":
            b = self._body_json()
            if not self._paper_dir(str(b.get("paper_id", ""))):
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            try:
                return self._json(app.state.set_position({k: b.get(k) for k in ("paper_id", "sentence", "section", "item_id",
                                                                                 "offset", "done", "total")}))
            except (ValueError, TypeError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/marks/toggle":
            b = self._body_json()
            pid = str(b.get("paper_id", ""))
            if not self._paper_dir(pid) or not isinstance(b.get("sentence"), str) or not b["sentence"].strip():
                return self._json({"error": "paper_id と sentence が要る"}, HTTPStatus.BAD_REQUEST)
            mark = app.state.toggle_mark(pid, b["sentence"], b.get("section"))
            return self._json({"mark": mark, "marks": app.state.marks(pid)})
        m = re.fullmatch(r"/api/marks/([0-9a-f]{32})/note", path)
        if m:
            try:
                mark = app.state.set_note(m.group(1), str(self._body_json().get("note", "")))
            except KeyError:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self._json(mark)
        m = re.fullmatch(r"/api/vocab/(\d+)/delete", path)
        if m:
            return self._json({"deleted": app.vocab.delete(int(m.group(1)))})
        if path == "/api/vocab/add":
            b = self._body_json()
            res = app.lookup(str(b.get("w", "")), b.get("paper"))  # 画面から来た意味は使わず、ここで引き直す
            if not res["found"]:
                return self._json({"error": "辞書に無い語は登録できない"}, HTTPStatus.BAD_REQUEST)
            res["vocab_id"] = app.vocab.record(res, b.get("paper"), b.get("sec"), b.get("sentence"),
                                               app.audio_ref(b.get("paper"), b.get("sentence")))
            res["registered"] = True
            return self._json(res)
        if path == "/api/review/answer":
            b = self._body_json()
            if b.get("rating") not in (1, 2, 3, 4) or not isinstance(b.get("headword"), str):
                return self._json({"error": "headword と rating(1-4) が要る"}, HTTPStatus.BAD_REQUEST)
            try:
                app.vocab.answer(b["headword"], b["rating"])
            except KeyError:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self._json(app.fix_refs(app.vocab.queue()))
        if path == "/api/review/undo":
            undone = app.vocab.undo()
            q = app.vocab.queue()
            if undone:
                q["card"] = app.vocab.card_view(undone)       # 取り消した語をもう一度出す
            return self._json({**app.fix_refs(q), "undone": undone})
        if path == "/api/progress":
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 20 * 1024 * 1024:
                return self._json({"error": "空か大きすぎる"}, HTTPStatus.BAD_REQUEST)
            try:
                return self._json(app.merge_progress(self.rfile.read(n)))
            except (ValueError, KeyError, TypeError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/share/start":
            try:
                z = app.make_bundle(self._body_json().get("papers") or [])
                return self._json(app.share.start(z, app.cfg["pwa_url"]))
            except KeyError:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except RuntimeError as e:
                return self._json({"error": str(e)}, HTTPStatus.SERVICE_UNAVAILABLE)
        if path == "/api/share/stop":
            app.share.stop()
            return self._json({"active": False})
        if path == "/api/import":
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_UPLOAD:
                return self._json({"error": "PDF が空か大きすぎる"}, HTTPStatus.BAD_REQUEST)
            name = Path(unquote(self.headers.get("X-Filename") or "upload.pdf")).name
            data = self.rfile.read(n)
            if not data.startswith(b"%PDF"):
                return self._json({"error": "PDF ではない"}, HTTPStatus.BAD_REQUEST)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(data)
                tmp = Path(f.name)
            prog = {"active": True, "name": name, "page": 0, "pages": 0, "ocr": 0,
                    "started_at": datetime.now().isoformat(timespec="seconds")}
            app.importing = prog

            def on_page(n, total, ocr):
                prog.update(page=n, pages=total, ocr=prog["ocr"] + (1 if ocr else 0))

            try:
                meta = app.store.import_pdf(tmp, name, on_page)
            except Exception as e:  # 壊れた PDF など
                return self._json({"error": f"取り込めなかった: {e}"}, HTTPStatus.UNPROCESSABLE_ENTITY)
            finally:
                tmp.unlink(missing_ok=True)
                app.importing = {"active": False}
            app.start_audio(meta["id"])                  # 取り込んだらすぐ音声と訳をまとめて作る
            app.start_translate(meta["id"])
            app.start_glossary(meta["id"])
            return self._json(meta)
        self.send_error(HTTPStatus.NOT_FOUND)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--config", type=Path, default=None)
    a = ap.parse_args()
    app = App(a.config)
    Handler.app = app
    app.resume_pending()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"paper_reader: http://127.0.0.1:{a.port}/  data_root={app.cfg['data_root']}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
