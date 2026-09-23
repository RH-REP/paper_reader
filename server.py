#!/usr/bin/env python3
"""paper_reader のローカルサーバー。

画面（index.html, assets/）を配り、論文の取り込み・章と文・読み上げ音声・単語検索の API を出す。
データの置き場は config.json の data_root（config.json からの相対か絶対パス。既定 data/）。
127.0.0.1 だけで待つ。

  GET  /api/health
  GET  /api/config                      声・速さ・使える声・辞書の有無
  POST /api/config                      {"voice": ..., "rate": ...} を config.json に保存
  GET  /api/papers                      取り込んだ論文の一覧（meta.json）
  GET  /api/papers/<id>                 章と文（sentences.json）＋ audio（音声の作成状態）
  POST /api/papers/<id>/reextract       章と文を作り直し、音声も作り直す
  POST /api/papers/<id>/audio           音声を今の声・速さで作り直す
  POST /api/papers/<id>/reveal          持ち出し用の音声フォルダ（export/）を Finder で開く
  GET  /api/papers/<id>/audio/<item>.m4a    1文ずつの音声
  GET  /api/papers/<id>/export/<file>.m4a   章ごとの音声
  POST /api/import                      本文 = PDF のバイト列、ヘッダー X-Filename = 元のファイル名（URL エンコード）
  GET  /api/lookup?w=<語>&paper=&sec=&sentence=   単語を引き、見つかれば単語帳に保存
  GET  /api/word_audio?w=<語>           単語の発音
  GET  /api/vocab                       単語帳
  POST /api/vocab/<n>/delete            単語帳から消す
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audio  # noqa: E402
from lookup import Dictionary  # noqa: E402
from store import Store  # noqa: E402
from vocab import Vocab  # noqa: E402

STATIC = {"/": "index.html", "/index.html": "index.html"}
MAX_UPLOAD = 200 * 1024 * 1024
DEFAULTS = {"data_root": "data", "voice": audio.DEFAULT_VOICE, "rate": 175, "online_dict": True}


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
        self.vocab = Vocab(self.cfg["data_root"])
        self.lock = threading.Lock()
        self.jobs: dict[str, threading.Thread] = {}

    def save_config(self, voice=None, rate=None):
        if voice is not None:
            self.cfg["voice"] = voice
        if rate is not None:
            self.cfg["rate"] = int(rate)
        if self.config_path:
            raw = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}
            raw.update(voice=self.cfg["voice"], rate=self.cfg["rate"])
            self.config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def start_audio(self, pid: str, force=False) -> bool:
        """その論文の音声づくりを裏で始める。すでに走っていれば何もしない。"""
        with self.lock:
            t = self.jobs.get(pid)
            if t and t.is_alive():
                return False
            d = self.store.paper_dir(pid)
            paper = self.store.load(pid)
            st = audio.status(d)
            # 作り終えたもの・失敗したもの（直さずにやり直しても同じ）は、明示の作り直しまで触らない
            if not force and st.get("state") in ("done", "error") \
                    and st.get("extractor_version") == paper.get("extractor_version"):
                return False
            t = threading.Thread(target=audio.generate, args=(d, paper, self.cfg["voice"], self.cfg["rate"]),
                                 daemon=True, name=f"audio-{pid}")
            self.jobs[pid] = t
            t.start()
            return True

    def resume_pending(self):
        """起動時: 音声がまだ無い・途中で止まった論文の音声づくりを始める。"""
        for m in self.store.list():
            st = audio.status(self.store.paper_dir(m["id"]))
            if st.get("state") in ("none", "running"):
                self.start_audio(m["id"], force=True)


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
                               "voices": audio.voices(), "dict": app.dict.available(),
                               "dict_meta": json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None})
        if path == "/api/papers":
            return self._json(app.store.list())
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})", path)
        if m:
            d = self._paper_dir(m.group(1))
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            paper = app.store.load(m.group(1))
            app.start_audio(m.group(1))                  # 無い・古いときだけ始まる
            return self._json({**paper, "audio": audio.status(d)})
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/audio", path)
        if m:
            d = self._paper_dir(m.group(1))
            return self._json(audio.status(d)) if d else self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/(audio|export)/([A-Za-z0-9_]+\.m4a)", path)
        if m:
            d = self._paper_dir(m.group(1))
            return self._file(d / m.group(2) / m.group(3), "audio/mp4") if d else self.send_error(HTTPStatus.NOT_FOUND)
        if path == "/api/lookup":
            res = app.dict.lookup(q.get("w", ""))
            res["vocab_id"] = app.vocab.record(res, q.get("paper"), q.get("sec"), q.get("sentence"))
            return self._json(res)
        if path == "/api/word_audio":
            try:
                p = audio.word_audio(app.store.root / "words", q.get("w", ""), app.cfg["voice"], app.cfg["rate"])
            except Exception as e:
                return self._json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._file(p, "audio/mp4") if p else self.send_error(HTTPStatus.BAD_REQUEST)
        if path == "/api/vocab":
            return self._json(app.vocab.list())
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
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/(reextract|audio|reveal)", path)
        if m:
            pid, act = m.groups()
            d = self._paper_dir(pid)
            if not d:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            if act == "reextract":
                meta = app.store.reextract(pid)
                app.start_audio(pid, force=True)
                return self._json(meta)
            if act == "audio":
                started = app.start_audio(pid, force=True)
                return self._json({"started": started, **audio.status(d)})
            (d / "export").mkdir(exist_ok=True)
            subprocess.run(["open", str(d / "export")], check=False)
            return self._json({"opened": str(d / "export")})
        m = re.fullmatch(r"/api/vocab/(\d+)/delete", path)
        if m:
            return self._json({"deleted": app.vocab.delete(int(m.group(1)))})
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
            try:
                meta = app.store.import_pdf(tmp, name)
            except Exception as e:  # 壊れた PDF など
                return self._json({"error": f"取り込めなかった: {e}"}, HTTPStatus.UNPROCESSABLE_ENTITY)
            finally:
                tmp.unlink(missing_ok=True)
            app.start_audio(meta["id"])                  # 取り込んだらすぐ音声をまとめて作る
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
