#!/usr/bin/env bash
# setup_frontend.sh — the STRUCT console: the whole pipeline in one page (Design,
# Overview, Viewers, Services). This is the thing a person opens.
#
#     python ui/app.py   ->   :8600
#
# Stdlib-only (no venv, no deps), so this just (re)starts it and prints the URLs. The
# page builds iframe hosts from location.hostname, so the same server works from the box,
# a LAN address, or a Tailscale/SSH tunnel — open whichever reaches this machine.
#
#   ./setup_frontend.sh            (re)start the console + print URLs
#   ./setup_frontend.sh --status   just print URLs / reachability, start nothing
#
# Env: REPO (auto)  PORT (8600)
set -uo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
PORT="${PORT:-8600}"
LOGDIR="$REPO/setup/logs"; mkdir -p "$LOGDIR"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ip()  { hostname -I 2>/dev/null | awk '{print $1}'; }

urls() {
  local i; i=$(ip)
  echo "   http://localhost:$PORT/"
  [ -n "$i" ] && echo "   http://$i:$PORT/            (LAN)"
  echo "   ssh -L $PORT:localhost:$PORT spark   ->   http://localhost:$PORT/   (tunnel)"
}

if [ "${1:-}" = "--status" ]; then
  if curl -sf -m 3 "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then say "console UP:"; urls
  else echo "console DOWN on :$PORT — run ./setup_frontend.sh"; exit 1; fi
  exit 0
fi

say "restarting console on :$PORT"
pkill -f "ui/app.py" 2>/dev/null && sleep 1 || true
( cd "$REPO" && nohup python3 ui/app.py --host 0.0.0.0 --port "$PORT" \
    > "$LOGDIR/console.log" 2>&1 & disown )

for _ in $(seq 1 20); do
  curl -sf -m 3 "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && { say "console UP — open:"; urls; exit 0; }
  sleep 1
done
echo "console did not come up — tail $LOGDIR/console.log" >&2
tail -20 "$LOGDIR/console.log" 2>/dev/null | sed 's/^/   /' >&2
exit 1
