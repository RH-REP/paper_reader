"""同じ Wi‑Fi のスマホにデータを写すための、一時的な受け渡しページ。

- Mac の画面で「スマホに送る」を押したときだけ、LAN 側のアドレス（例 192.168.1.23:8810）で待つ
- アドレスには推測できない合言葉（token）が入る。合言葉の無いアクセスは 404
- EXPIRE_MIN 分たつか「止める」で閉じる
- 出すもの: 受け渡しページ（スマホ向け）、書き出した zip、スマホからの記録ファイルの受け取り
メインのサーバー（127.0.0.1）は LAN に出さない。
"""
from __future__ import annotations

import html
import io
import json
import secrets
import socket
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import segno

EXPIRE_MIN = 10
PORTS = range(8810, 8820)
MAX_PROGRESS = 20 * 1024 * 1024


def lan_ip() -> str | None:
    for iface in ("en0", "en1"):
        r = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True)
        ip = r.stdout.strip()
        if ip:
            return ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))                       # 送らない。経路から自分のアドレスを知るだけ
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def qr_svg(text: str) -> str:
    buf = io.BytesIO()
    # omitsize: 幅・高さを書かず viewBox だけにする（表示の枠に合わせて縮み、端が切れない）
    # make_qr: 短い文字列でも Micro QR にしない（スマホのカメラで読めないことがある）
    segno.make_qr(text, error="m").save(buf, kind="svg", border=2, dark="#000", light="#fff", xmldecl=False, omitsize=True)
    return buf.getvalue().decode("utf-8")


PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>paper_reader 受け渡し</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;margin:0;padding:18px;background:#f6f5f1;color:#1d2330}}
h1{{font-size:19px}} a.b,button{{display:block;text-align:center;font:inherit;padding:14px;border-radius:12px;border:0;
background:#2f6fbd;color:#fff;text-decoration:none;margin:12px 0;width:100%}} .m{{color:#667;font-size:14px}}
ol{{padding-left:20px}} input{{font:inherit;margin:8px 0}}</style></head><body>
<h1>paper_reader 受け渡し</h1>
<p class="m">この画面は {expire} まで開いています（Mac の画面で止めるとすぐ閉じます）。</p>
<h2>① Mac → このスマホ</h2>
<a class="b" href="{zip_url}" download>データ（{zip_name}、{zip_mb} MB）を保存</a>
<ol class="m"><li>上のボタンで保存（「ダウンロード」フォルダに入る）</li>
<li>paper_reader の app（<a href="{pwa}">{pwa}</a>）を開く</li>
<li>「データ」→「Mac のデータを読み込む」で、保存した zip を選ぶ</li></ol>
<h2>② このスマホ → Mac（復習の記録）</h2>
<ol class="m"><li>app の「データ」→「記録を書き出す」で JSON を保存</li><li>下で選んで送る</li></ol>
<input type="file" id="f" accept=".json,application/json"><button id="s">記録を Mac に送る</button>
<p id="r" class="m"></p>
<script>
document.getElementById('s').onclick=async()=>{{const f=document.getElementById('f').files[0];const r=document.getElementById('r');
if(!f){{r.textContent='ファイルを選んでください';return}}r.textContent='送っています…';
try{{const x=await fetch('{upload_url}',{{method:'POST',body:f}});const j=await x.json();
r.textContent=x.ok?`送りました（引いた語 ${{j.lookups}} 件、答え ${{j.reviews}} 件を追加）`:('失敗: '+j.error)}}catch(e){{r.textContent='失敗: '+e}}}};
</script></body></html>"""


class ShareServer:
    """1度に1つだけ。start で開き、stop か期限で閉じる。"""

    def __init__(self, on_progress):
        self.on_progress = on_progress                    # bytes → 合わせた件数の dict
        self.lock = threading.Lock()
        self.srv = None
        self.info = None

    def status(self) -> dict:
        with self.lock:
            if self.info and time.time() > self.info["expires"]:
                self._stop_locked()
            return dict(self.info, active=True) if self.info else {"active": False}

    def start(self, zip_path: Path, pwa_url: str) -> dict:
        with self.lock:
            self._stop_locked()
            ip = lan_ip()
            if not ip:
                raise RuntimeError("Wi‑Fi（LAN）のアドレスが見つからない。Mac が Wi‑Fi につながっているか確かめてください")
            token = secrets.token_urlsafe(12)
            handler = self._handler(token, zip_path, pwa_url)
            for port in PORTS:
                try:
                    srv = ThreadingHTTPServer((ip, port), handler)
                    break
                except OSError:
                    continue
            else:
                raise RuntimeError("8810-8819 に空きポートが無い")
            self.srv = srv
            threading.Thread(target=srv.serve_forever, daemon=True, name="share").start()
            url = f"http://{ip}:{port}/{token}/"
            expires = time.time() + EXPIRE_MIN * 60
            self.info = {"url": url, "qr": qr_svg(url), "expires": expires,
                         "expires_at": time.strftime("%H:%M", time.localtime(expires)),
                         "zip": zip_path.name, "zip_mb": round(zip_path.stat().st_size / 1e6, 1), "received": []}
            timer = threading.Timer(EXPIRE_MIN * 60 + 1, self.status)   # 期限で閉じる
            timer.daemon = True
            timer.start()
            return dict(self.info, active=True)

    def stop(self):
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        if self.srv:
            srv, self.srv = self.srv, None
            threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
        self.info = None

    def _handler(self, token: str, zip_path: Path, pwa_url: str):
        share = self
        base = f"/{token}/"

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body: bytes, ctype, extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _alive(self):
                return share.info is not None and time.time() <= share.info["expires"]

            def do_GET(self):
                if not self._alive() or not self.path.startswith(base):
                    return self.send_error(HTTPStatus.NOT_FOUND)
                rest = self.path[len(base):]
                if rest == "":
                    page = PAGE.format(expire=html.escape(share.info["expires_at"]), zip_url=base + "bundle.zip",
                                       zip_name=html.escape(zip_path.name), zip_mb=share.info["zip_mb"],
                                       pwa=html.escape(pwa_url), upload_url=base + "progress")
                    return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                if rest == "bundle.zip" and zip_path.exists():
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(zip_path.stat().st_size))
                    self.send_header("Content-Disposition", f'attachment; filename="{zip_path.name}"')
                    self.end_headers()
                    with zip_path.open("rb") as f:
                        while chunk := f.read(1 << 16):
                            self.wfile.write(chunk)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self):
                if not self._alive() or self.path != base + "progress":
                    return self.send_error(HTTPStatus.NOT_FOUND)
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_PROGRESS:
                    return self._send(400, b'{"error":"size"}', "application/json")
                try:
                    res = share.on_progress(self.rfile.read(n))
                except Exception as e:
                    return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False).encode(), "application/json")
                with share.lock:
                    if share.info:
                        share.info["received"].append(res)
                return self._send(200, json.dumps(res).encode(), "application/json")

        return H
