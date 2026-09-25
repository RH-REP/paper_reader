"""「AI に手直しを頼む」を、この Mac の Claude Code（claude -p）に裏で頼む。

  data/papers/<id>/ai_fix.json   状態 {"state": "running"|"done"|"error"|"stopped", "started_at", "finished_at",
                                      "tools": 操作の回数, "last": 最後にしていること, "result": 最後の報告, "cost_usd"}
  data/papers/<id>/ai_fix.log    claude の出力そのもの（stream-json）

- 依頼文は画面の「AI に手直しを頼む（プロンプトをコピー）」と同じもの（ai_fix_prompt.md）に、自動で進めるための一言を足す
- claude には、その論文のフォルダの中の読み書きと、この app の Python（切り抜き・確認のコマンド）・cp・date・tesseract だけを許す
- 論文の文章は Claude（Anthropic）に送られる。使うかは画面で人が決める（ボタンを押したときだけ走る）
- 同時に走らせるのは1本だけ（費用と Mac の負荷のため）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

TIMEOUT = 40 * 60
AUTO_NOTE = ("\n\n---\nこの依頼は paper_reader の画面から自動で実行されています。人に質問せず最後まで進めてください。"
             "PDF を読む・切り抜くには、上に書いた Python（{python}）を使ってください（pymupdf と pysbd が入っています）。"
             "パッケージを入れない（pip などは使えません）。"
             "確認のコマンドでエラーが 0 件になったら終わりです。最後の報告は日本語で 10 行以内にしてください。")
CANDIDATES = ["~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]


def find_cli() -> str | None:
    p = shutil.which("claude")
    if p:
        return p
    for c in CANDIDATES:
        c = os.path.expanduser(c)
        if os.access(c, os.X_OK):
            return c
    return None


def status(paper_dir: Path) -> dict:
    p = paper_dir / "ai_fix.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"state": "none"}
    except ValueError:
        return {"state": "none"}


def _write(paper_dir: Path, st: dict):
    p = paper_dir / "ai_fix.json"
    tmp = p.with_name(f"ai_fix.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _describe(block: dict) -> str:
    """claude の操作を短く（画面の「今していること」）。"""
    name, inp = block.get("name", ""), block.get("input") or {}
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        return "確認のコマンド" if "check_paper" in cmd else "図の切り抜き" if "get_pixmap" in cmd else f"コマンド: {cmd[:60]}"
    if name in ("Read", "Write", "Edit"):
        f = Path(str(inp.get("file_path", ""))).name
        return {"Read": "読んでいる", "Write": "書いている", "Edit": "直している"}[name] + f": {f}"
    return name


class AiFixer:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.pid: str | None = None
        self.stopping: set = set()

    def running(self) -> str | None:
        with self.lock:
            return self.pid if self.proc and self.proc.poll() is None else None

    def start(self, pid: str, paper_dir: Path, prompt: str, python: str, on_done=None) -> dict:
        cli = find_cli()
        if not cli:
            raise RuntimeError("この Mac に Claude Code（claude コマンド）が見つからない")
        # この app の Python（.venv）で PDF が読めることを先に確かめる。読めない Python を渡すと、
        # claude がパッケージを入れようとする（2026-09-25 の試しで、ユーザーの Python に pymupdf を入れてしまった）
        ok = os.path.isabs(python) and subprocess.run([python, "-c", "import pymupdf, pysbd"], capture_output=True).returncode == 0
        if not ok:
            raise RuntimeError("この app の Python（.venv）に pymupdf / pysbd が無い（start.command で作り直してください）")
        with self.lock:
            if self.proc and self.proc.poll() is None:
                raise RuntimeError(f"ほかの論文（{self.pid}）を手直ししている最中")
            tools = ["Read", "Write", "Edit", "Glob", "Grep", f"Bash({python}:*)", "Bash(cp:*)", "Bash(date:*)",
                     "Bash(tesseract:*)", "Bash(ls:*)", "Bash(mkdir:*)"]
            # --setting-sources local: 人の対話用の指示（~/.claude/CLAUDE.md や上の階層の CLAUDE.md・AGENTS.md）を読ませない。
            # 読ませると「まず理解を書いて質問があれば止まる」などの対話向けの決まりで、無人の手直しが止まる（2026-09-25 に確かめた）
            deny = [f"Bash({python} -m pip:*)", "Bash(python3 -m pip:*)", "Bash(python -m pip:*)", "Bash(pip:*)",
                    "Bash(pip3:*)", "Bash(brew:*)", "Bash(curl:*)", "WebFetch", "WebSearch"]
            cmd = [cli, "-p", prompt + AUTO_NOTE.replace("{python}", python), "--output-format", "stream-json", "--verbose",
                   "--setting-sources", "local", "--permission-mode", "acceptEdits",
                   "--allowedTools", *tools, "--disallowedTools", *deny]
            log = open(paper_dir / "ai_fix.log", "w", encoding="utf-8")
            env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":" + os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin"}
            self.proc = subprocess.Popen(cmd, cwd=paper_dir, stdout=subprocess.PIPE, stderr=log, text=True, env=env)
            self.pid = pid
            st = {"state": "running", "started_at": datetime.now().isoformat(timespec="seconds"), "tools": 0,
                  "last": "始めています", "result": "", "cost_usd": None}
            _write(paper_dir, st)
            self.stopping.discard(pid)
            threading.Thread(target=self._watch, args=(pid, self.proc, paper_dir, st, log, on_done), daemon=True,
                             name=f"aifix-{pid}").start()
            return st

    def _watch(self, pid, proc, paper_dir: Path, st: dict, log, on_done):
        t0, last_write = time.time(), 0.0
        try:
            for line in proc.stdout:
                log.write(line)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "assistant":
                    for b in (ev.get("message") or {}).get("content", []):
                        if b.get("type") == "tool_use":
                            st["tools"] += 1
                            st["last"] = _describe(b)
                        elif b.get("type") == "text" and b.get("text", "").strip():
                            st["last"] = b["text"].strip().splitlines()[0][:120]
                elif ev.get("type") == "result":
                    st["result"] = str(ev.get("result") or "")[:4000]
                    st["cost_usd"] = ev.get("total_cost_usd")
                    st["error"] = bool(ev.get("is_error"))
                if time.time() - last_write > 1.0:
                    _write(paper_dir, st)
                    last_write = time.time()
                if time.time() - t0 > TIMEOUT:
                    proc.kill()
                    st["timeout"] = True
            proc.wait()
        finally:
            log.close()
            if proc.stdout:
                proc.stdout.close()
            if pid in self.stopping:
                st["state"] = "stopped"
            else:
                st["state"] = "done" if proc.returncode == 0 and not st.get("error") and not st.get("timeout") else "error"
            st["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _write(paper_dir, st)
            with self.lock:
                if self.proc is proc:
                    self.proc, self.pid = None, None
            if on_done:
                on_done(st)

    def stop(self, pid: str, paper_dir: Path) -> bool:
        with self.lock:
            if not (self.proc and self.proc.poll() is None and self.pid == pid):
                return False
            self.stopping.add(pid)
            self.proc.terminate()
            return True
