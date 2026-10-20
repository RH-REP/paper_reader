"""改善のコメント（画面の !○）を GitHub の Issue に送る。ほかの app にもそのまま写せる形にしてある。

  send(repo, account, report) -> {"number", "url"}
    report = {"screen", "comment", "app", "version", "context": {...}, "screenshot": PNG のバイト列 or None,
              "log": システムの記録の末尾（場所は伏せてから渡す）}

- 鍵は持たない。この Mac の gh にログインしている account（例 RH-REP）の token を、その都度 `gh auth token -u` で借りる
- 画面の写真は、コードの入らない枝 feedback-assets の feedback/<日時>_<画面>.png に上げ、Issue に載せる
  （枝が無ければ、README だけの枝として作る。Pages や main の履歴には混ざらない）
- Issue にはラベル feedback を付ける（無ければ作る）
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime

API = "https://api.github.com"
ASSET_BRANCH = "feedback-assets"
LABEL = "feedback"


class FeedbackError(RuntimeError):
    pass


def token_for(account: str) -> str:
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    try:
        r = subprocess.run([gh, "auth", "token", "-u", account], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FeedbackError(f"gh を動かせない: {e}") from e
    if r.returncode != 0 or not r.stdout.strip():
        raise FeedbackError(f"gh に {account} でログインしていない（gh auth login で {account} を足す）")
    return r.stdout.strip()


def _api(token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "paper_reader-feedback"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _ensure_asset_branch(token: str, repo: str):
    st, _ = _api(token, "GET", f"/repos/{repo}/branches/{ASSET_BRANCH}")
    if st == 200:
        return
    st, tree = _api(token, "POST", f"/repos/{repo}/git/trees", {"tree": [{
        "path": "README.md", "mode": "100644", "type": "blob",
        "content": "画面の !○ から送った改善のコメントの写真だけを置く枝（コードは入れない）。\n"}]})
    if st != 201:
        raise FeedbackError(f"写真の置き場（{ASSET_BRANCH}）を作れない: {st} {tree.get('message', '')}")
    st, commit = _api(token, "POST", f"/repos/{repo}/git/commits",
                      {"message": "写真の置き場を作る", "tree": tree["sha"], "parents": []})
    if st != 201:
        raise FeedbackError(f"写真の置き場を作れない: {st} {commit.get('message', '')}")
    st, ref = _api(token, "POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{ASSET_BRANCH}", "sha": commit["sha"]})
    if st not in (201, 422):                            # 422 = 同時に作られた
        raise FeedbackError(f"写真の置き場を作れない: {st} {ref.get('message', '')}")


def _upload_png(token: str, repo: str, name: str, png: bytes) -> str:
    _ensure_asset_branch(token, repo)
    path = f"feedback/{name}"
    st, res = _api(token, "PUT", f"/repos/{repo}/contents/{path}",
                   {"message": f"画面の写真 {name}", "branch": ASSET_BRANCH, "content": base64.b64encode(png).decode()})
    if st not in (200, 201):
        raise FeedbackError(f"写真を上げられない: {st} {res.get('message', '')}")
    return f"https://raw.githubusercontent.com/{repo}/{ASSET_BRANCH}/{path}"


def _ensure_label(token: str, repo: str):
    _api(token, "POST", f"/repos/{repo}/labels", {"name": LABEL, "color": "d4c5f9", "description": "画面の !○ から送った改善のコメント"})


def issue_body(report: dict, image_url: str | None = None) -> str:
    """Issue の本文（Markdown）。スマホ版も同じ形を自分で作る（assets/feedback.js の issueBody）。"""
    c = report.get("context") or {}
    lines = [f"**画面**: `{report.get('screen', '?')}`",
             f"**app**: {report.get('app', '')} ・ 版 {report.get('version', '')} ・ {c.get('device', '')}",
             f"**時刻**: {report.get('at') or datetime.now().astimezone().isoformat(timespec='seconds')}"]
    if c.get("paper"):
        lines.append(f"**論文・記事**: {c['paper']}")
    if c.get("sentence"):
        lines.append(f"**今の文**: > {c['sentence']}")
    if c.get("extra"):
        lines.append(f"**そのときの状態**: {c['extra']}")
    lines += ["", "### コメント", "", report.get("comment", "").strip() or "（なし）"]
    if image_url:
        lines += ["", "### 画面の写真", "", f"![画面の写真]({image_url})"]
    if report.get("log"):
        lines += ["", "<details><summary>システムの記録（末尾。この Mac の場所は伏せてある）</summary>", "", "```",
                  report["log"].rstrip()[-12000:], "```", "</details>"]
    lines += ["", "<!-- 画面の !○ から送った -->"]
    return "\n".join(lines)


def issue_title(report: dict) -> str:
    first = (report.get("comment") or "").strip().splitlines()[0] if (report.get("comment") or "").strip() else "（コメントなし）"
    return f"[{report.get('screen', '?')}] {first[:70]}"


def send(repo: str, account: str, report: dict, token: str | None = None) -> dict:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise FeedbackError(f"送り先の repo が正しくない: {repo!r}（config.json の feedback_repo）")
    token = token or token_for(account)
    image_url = None
    if report.get("screenshot"):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", report.get("screen", "screen"))[:40]
        image_url = _upload_png(token, repo, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe}.png", report["screenshot"])
    _ensure_label(token, repo)
    st, res = _api(token, "POST", f"/repos/{repo}/issues",
                   {"title": issue_title(report), "body": issue_body(report, image_url), "labels": [LABEL]})
    if st != 201:
        raise FeedbackError(f"Issue を作れない: {st} {res.get('message', '')}")
    return {"number": res["number"], "url": res["html_url"], "image": image_url}
