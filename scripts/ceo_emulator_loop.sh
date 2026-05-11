#!/usr/bin/env bash
# CEO emulator overnight loop — calls full_iteration every 5 minutes.
# Each iteration: ask → auto-vote → maybe general-feedback → maybe_evolve.
#
# Launch (overnight, detached):
#   nohup bash scripts/ceo_emulator_loop.sh \
#       > data/ceo_emulation/loop.out 2>&1 &
#   disown
#
# Stop:
#   pkill -f ceo_emulator_loop.sh
#
# Inspect:
#   tail -f data/ceo_emulation/loop.out
#   .venv/bin/python scripts/ceo_emulation.py status
#
# Exit cleanly on SIGTERM/SIGINT.
set -u
trap 'echo "[$(date -Iseconds)] STOP signal received, exiting loop"; exit 0' INT TERM

ROOT="/home/mosyamac/pulse-agent"
PYTHON="$ROOT/.venv/bin/python"
SCRIPT="$ROOT/scripts/ceo_emulation.py"
SLEEP_SECONDS="${SLEEP_SECONDS:-60}"

cd "$ROOT" || exit 1

iteration=0
while true; do
  iteration=$((iteration + 1))
  echo "[$(date -Iseconds)] ── loop iteration #$iteration ──"
  # full_iteration may take 1–8 min depending on tool calls. timeout=900 inside.
  if "$PYTHON" "$SCRIPT" full_iteration; then
    echo "[$(date -Iseconds)] ── done OK ──"
  else
    echo "[$(date -Iseconds)] ── full_iteration returned non-zero, see errors.jsonl ──"
  fi
  echo "[$(date -Iseconds)] sleeping ${SLEEP_SECONDS}s before next iteration"
  sleep "$SLEEP_SECONDS"
done
