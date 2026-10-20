#!/bin/zsh
# paper_reader の起動口。コードはこのフォルダ（開発側 repo の clone。update.command で pull して
# 取り込む。直すのは開発側）。設定（config.json）・取り込んだ論文（data/）もこのフォルダ。
# 初回は .venv を作って pymupdf / pysbd を入れる（ネットが要る）。
set -euo pipefail

export PATH="$HOME/.pyenv/shims:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

USE_DIR="${0:A:h}"
CONFIG_FILE="$USE_DIR/config.json"
mkdir -p "$USE_DIR/log"
LOG_FILE="$USE_DIR/log/terminal.log"            # 記録は log/ 1か所（server.log・クラッシュのレポートも log/）
VENV="$USE_DIR/.venv"

if [[ ! -f "$USE_DIR/server.py" ]]; then
  echo "server.py がありません: $USE_DIR"
  echo "update.command を実行して開発側からコードを取り込んで（pull して）ください。"
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  cp "$USE_DIR/config.example.json" "$CONFIG_FILE"
  echo "config.json を作りました（data_root: data）。"
fi

BASE_PY="$HOME/.pyenv/versions/3.11.8/bin/python3"
[[ -x "$BASE_PY" ]] || BASE_PY="$(command -v python3)"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "初回の準備: $VENV を作って依存を入れます。"
  "$BASE_PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q -r "$USE_DIR/requirements.txt"
fi
PYTHON_BIN="$VENV/bin/python"
for d in /opt/homebrew/share/tessdata /usr/local/share/tessdata; do
  if [[ -f "$d/eng.traineddata" ]]; then export TESSDATA_PREFIX="$d"; break; fi
done

PORT=""
URL=""
HEALTH_URL=""
set_port_urls() {
  PORT="$1"
  URL="http://127.0.0.1:$PORT/"
  HEALTH_URL="http://127.0.0.1:$PORT/api/health"
}
port_is_listening() { lsof -ti tcp:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

select_port() {
  local candidate
  for candidate in {8796..8805}; do
    set_port_urls "$candidate"
    if curl -fsS "$HEALTH_URL" 2>/dev/null | grep -q data_root; then
      echo "既に起動している paper_reader を見つけました（ポート $PORT）。再起動します。"
      return
    fi
    if ! port_is_listening "$PORT"; then
      echo "ポート $PORT を使います。"
      return
    fi
  done
  echo "8796-8805 に空きポートがありません。"
  exit 1
}

stop_existing_server() {
  local pids pid
  pids="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "$pids" ]] && return
  for pid in ${(f)pids}; do kill "$pid" 2>/dev/null || true; done
  for _ in {1..30}; do
    curl -fsS "$HEALTH_URL" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
}

select_port
if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then stop_existing_server; fi

cd "$USE_DIR"
nohup "$PYTHON_BIN" "$USE_DIR/server.py" --port "$PORT" --config "$CONFIG_FILE" >>"$LOG_FILE" 2>&1 &

for _ in {1..40}; do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    open "$URL"
    echo "起動しました: $URL"
    echo "ログ: $LOG_FILE"
    exit 0
  fi
  sleep 0.25
done

echo "サーバーが応答しませんでした。ログを確認してください: $LOG_FILE"
tail -n 20 "$LOG_FILE" 2>/dev/null || true
exit 1
