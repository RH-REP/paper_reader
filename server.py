#!/usr/bin/env python3
"""paper_reader のローカルサーバー。

画面（index.html, assets/）を配り、論文の取り込みと章・文の読み出しの API を出す。
データの置き場は config.json の data_root（config.json からの相対か絶対パス。既定 data/）。
127.0.0.1 だけで待つ。

  GET  /api/health
  GET  /api/papers                  取り込んだ論文の一覧（meta.json）
  GET  /api/papers/<id>             章と文（sentences.json）
  POST /api/papers/<id>/reextract   章と文を作り直す
  POST /api/import                  本文 = PDF のバイト列、ヘッダー X-Filename = 元のファイル名（URL エンコード）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from store import Store  # noqa: E402

STATIC = {"/": "index.html", "/index.html": "index.html"}
MAX_UPLOAD = 200 * 1024 * 1024


def load_config(path: Path | None) -> dict:
    cfg = {"data_root": "data"}
    if path and path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    base = path.parent if path else HERE
    root = Path(cfg["data_root"]).expanduser()
    cfg["data_root"] = root if root.is_absolute() else (base / root).resolve()
    return cfg


class Handler(SimpleHTTPRequestHandler):
    store: Store

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

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self._json({"ok": True, "data_root": str(self.store.root)})
        if path == "/api/papers":
            return self._json(self.store.list())
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})", path)
        if m:
            try:
                return self._json(self.store.load(m.group(1)))
            except KeyError:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        # 画面だけを配る。data/ など他のファイルは出さない
        if path in STATIC or path.startswith("/assets/"):
            self.path = "/" + STATIC.get(path, path.lstrip("/"))
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlsplit(self.path).path
        m = re.fullmatch(r"/api/papers/([0-9a-f]{8})/reextract", path)
        if m:
            try:
                return self._json(self.store.reextract(m.group(1)))
            except KeyError:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
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
                return self._json(self.store.import_pdf(tmp, name))
            except Exception as e:  # 壊れた PDF など
                return self._json({"error": f"取り込めなかった: {e}"}, HTTPStatus.UNPROCESSABLE_ENTITY)
            finally:
                tmp.unlink(missing_ok=True)
        self.send_error(HTTPStatus.NOT_FOUND)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--config", type=Path, default=None)
    a = ap.parse_args()
    cfg = load_config(a.config)
    Handler.store = Store(cfg["data_root"])
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"paper_reader: http://127.0.0.1:{a.port}/  data_root={cfg['data_root']}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
