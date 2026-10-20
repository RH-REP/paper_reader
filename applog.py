"""paper_reader の記録（log/ 1か所）。改善・不具合の相談では、このフォルダを写して渡せば足りるようにする。

log/
  README.txt          中身の説明（起動のたびに書き直す）
  server.log          サーバーの出来事（起動の記録・API の失敗・音声や訳の仕事の失敗）。2 MB で server.log.1〜3 へ回す
  terminal.log        端末への出力（start.command が向ける。import の失敗などもここに出る）
  faulthandler.log    Python ごと落ちたときの場所（空なら何も起きていない）
  crash/<日時>_<部分>/report.md   例外で止まったとき（スレッド・API の処理も）。新しい 20 件だけ残す

入れないもの: 合言葉・トークン、論文・音声・単語帳の中身（データの置き場の名前だけ書く）。
改善のコメント（画面の !○）は server.log の末尾を Issue に付ける。そのときこの Mac の場所は伏せる（scrub）。
"""
from __future__ import annotations

import faulthandler
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 3
KEEP_CRASHES = 20

README = """paper_reader の記録（改善・不具合の相談では、このフォルダごと写して渡す。論文・音声・単語帳の中身は入っていない）

server.log(.1〜.3)  サーバーの出来事。起動のたびに版・Python・設定の要点を書く
terminal.log        端末への出力（起動の失敗などもここに出る）
faulthandler.log    Python ごと落ちたときの場所
crash/              例外で止まったときのレポート（report.md）
"""


def code_version() -> str:
    """コードの版（.git があればコミット。git を呼ばずにファイルから読む）。"""
    try:
        head = (HERE / ".git" / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        p = HERE / ".git" / ref
        if p.exists():
            return p.read_text().strip()[:7]
        for line in (HERE / ".git" / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:7]
    except OSError:
        pass
    return "unknown"


def scrub(text: str, data_root: str | Path | None = None) -> str:
    """外に出す文から、この Mac の場所を伏せる（データの置き場 → <data>、ホーム → ~、ユーザー名 → <user>）。"""
    if data_root:
        text = text.replace(str(data_root), "<data>")
    home = str(Path.home())
    text = text.replace(home, "~")
    user = os.environ.get("USER") or Path.home().name
    if user and len(user) >= 2:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<user>", text)
    return text


def _rotate(path: Path) -> None:
    try:
        if path.stat().st_size < LOG_MAX_BYTES:
            return
    except FileNotFoundError:
        return
    for i in range(LOG_BACKUPS, 0, -1):
        src = path.with_name(f"{path.name}.{i - 1}") if i > 1 else path
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{i}"))


class AppLog:
    """出来事を端末（terminal.log に向く）と log/server.log の両方に書く。"""

    def __init__(self, log_dir: Path, echo: bool = True):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "crash").mkdir(exist_ok=True)
        self.file = self.dir / "server.log"
        self.echo = echo
        self.lock = threading.Lock()
        self._fh_fault = None
        tmp = self.dir / "README.txt.tmp"
        tmp.write_text(README, encoding="utf-8")
        os.replace(tmp, self.dir / "README.txt")

    def __call__(self, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        if self.echo:
            print(line, file=sys.stderr, flush=True)
        with self.lock:
            try:
                _rotate(self.file)
                with open(self.file, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass                                    # 記録が書けなくても app は止めない

    def start_info(self, argv: list[str], settings: dict) -> None:
        self("==== 起動 ====")
        self(f"版: {code_version()}  Python: {sys.version.split()[0]}  OS: {platform.platform()}")
        self(f"引数: {' '.join(argv)}  pid: {os.getpid()}")
        self("設定: " + ", ".join(f"{k}={v}" for k, v in settings.items()))

    def tail(self, lines: int = 80) -> str:
        """server.log の末尾（改善のコメントに付ける）。"""
        try:
            return "".join(self.file.read_text(encoding="utf-8", errors="replace").splitlines(True)[-lines:])
        except OSError:
            return ""

    def install_hooks(self) -> None:
        """落ちたときにレポートを書く（前のフックは呼び続ける）。"""
        if self._fh_fault is not None:
            return
        prev_sys, prev_thr = sys.excepthook, threading.excepthook

        def on_sys(tp, val, tb):
            if not issubclass(tp, KeyboardInterrupt):
                self.crash("main", "".join(traceback.format_exception(tp, val, tb)))
            prev_sys(tp, val, tb)

        def on_thread(args):
            if args.exc_type is not SystemExit:
                name = args.thread.name if args.thread else "thread"
                self.crash(name, "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
            prev_thr(args)

        sys.excepthook, threading.excepthook = on_sys, on_thread
        self._fh_fault = open(self.dir / "faulthandler.log", "a", encoding="utf-8")
        faulthandler.enable(file=self._fh_fault, all_threads=True)

    def crash(self, part: str, tb_text: str) -> Path | None:
        self(f"例外（{part}）: {tb_text.strip().splitlines()[-1] if tb_text.strip() else ''}")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in part)[:40]
        d = self.dir / "crash" / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}"
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / "report.md").write_text(
                f"# 例外: {part}\n\n- 時刻: {time.strftime('%Y-%m-%d %H:%M:%S')}\n- 版: {code_version()}\n- pid: {os.getpid()}\n\n"
                f"## トレースバック\n```\n{tb_text}```\n\n## server.log の末尾（100 行）\n```\n{self.tail(100)}```\n",
                encoding="utf-8")
            for p in sorted(x for x in (self.dir / "crash").iterdir() if x.is_dir())[:-KEEP_CRASHES]:
                shutil.rmtree(p, ignore_errors=True)
            return d
        except OSError:
            return None
