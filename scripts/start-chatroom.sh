#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHAT="$ROOT/chatroom"
PY="python3"
HOST="127.0.0.1"
PORT="8787"

if [ -f "$ROOT/config.json" ]; then
  PY_VAL=$(grep -o '"python"[^,]*' "$ROOT/config.json" | head -1 | sed 's/.*: *"//; s/"//')
  HOST_VAL=$(grep -o '"host"[^,]*' "$ROOT/config.json" | head -1 | sed 's/.*: *"//; s/"//')
  PORT_VAL=$(grep -o '"port"[^,]*' "$ROOT/config.json" | head -1 | sed 's/.*: *//')
  [ -n "$PY_VAL" ] && PY="$PY_VAL"
  [ -n "$HOST_VAL" ] && HOST="$HOST_VAL"
  [ -n "$PORT_VAL" ] && PORT="$PORT_VAL"
fi

if ! command -v "$PY" >/dev/null 2>&1; then PY="python3"; fi
cd "$CHAT"
nohup "$PY" chatroom.py server --host "$HOST" --port "$PORT" >/dev/null 2>&1 &
echo "chatroom started at http://$HOST:$PORT/"
