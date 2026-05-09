#!/usr/bin/env bash
# Bootstrap a Pulse install on Ubuntu (or any Debian-derivative with apt).
#
# Default mode (no flags): "dev" install — uses the current user, sets up the
# venv inside whatever repo this script lives in, picks the system's default
# python3 (3.10 on Jammy, 3.11 on Bookworm, 3.12 on Noble — anything ≥ 3.10
# satisfies pyproject.toml). Good for laptop / single-user VMs.
#
# `--system` mode — creates a 'pulse' system user, expects the repo at
# /home/pulse/pulse-agent (override with PULSE_REPO_DIR), and prepares the
# systemd path. Good for production.
#
# Override the python interpreter explicitly with PULSE_PYTHON, e.g.
#   PULSE_PYTHON=python3.11 bash scripts/bootstrap.sh
#
# Idempotent: safe to re-run.
set -euo pipefail

MODE="dev"
[[ "${1:-}" == "--system" ]] && MODE="system"

# --- Pick a python: explicit override → 3.12 → 3.11 → 3.10 → system python3 -
pick_python() {
    if [[ -n "${PULSE_PYTHON:-}" ]] && command -v "$PULSE_PYTHON" >/dev/null 2>&1; then
        echo "$PULSE_PYTHON"; return
    fi
    for v in python3.12 python3.11 python3.10 python3; do
        if command -v "$v" >/dev/null 2>&1; then
            echo "$v"; return
        fi
    done
    echo "ERROR: no python3.10+ found on PATH" >&2
    return 1
}

# --- Pick repo dir + install user from mode -------------------------------
if [[ "$MODE" == "system" ]]; then
    REPO_DIR="${PULSE_REPO_DIR:-/home/pulse/pulse-agent}"
    INSTALL_USER="pulse"
else
    REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    INSTALL_USER="$(id -un)"
fi

echo "==> mode=$MODE  repo=$REPO_DIR  user=$INSTALL_USER"

echo "==> apt deps (unversioned — resolves to whichever python3 your distro ships)"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-dev git curl build-essential

PYTHON="$(pick_python)"
echo "==> selected python: $PYTHON ($("$PYTHON" --version 2>&1))"

echo "==> Node.js LTS 20.x (for the claude CLI subprocess)"
if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
sudo npm install -g @anthropic-ai/claude-code

# --- venv + pip ------------------------------------------------------------
if [[ "$MODE" == "system" ]]; then
    echo "==> 'pulse' system user"
    if ! id -u pulse >/dev/null 2>&1; then
        sudo useradd -m -s /bin/bash pulse
    fi
    sudo mkdir -p "$(dirname "$REPO_DIR")"
    sudo chown -R pulse:pulse "$(dirname "$REPO_DIR")"
    if [ ! -d "$REPO_DIR" ]; then
        echo "  NOTE: clone the repo to $REPO_DIR as user pulse, then re-run this script."
        exit 0
    fi
    sudo -u pulse "$PYTHON" -m venv "$REPO_DIR/.venv"
    sudo -u pulse "$REPO_DIR/.venv/bin/pip" install --upgrade pip
    sudo -u pulse "$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"
else
    "$PYTHON" -m venv "$REPO_DIR/.venv"
    "$REPO_DIR/.venv/bin/pip" install --upgrade pip
    "$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"
fi

# --- generate a tailored systemd unit -------------------------------------
# The repo ships a template with User=pulse and WorkingDirectory=
# /home/pulse/pulse-agent. We materialise a substituted copy at
# $REPO_DIR/systemd/pulse.service.generated so the user can `sudo cp` it
# directly without sed-patching by hand.
GENERATED_UNIT="$REPO_DIR/systemd/pulse.service.generated"
sed -e "s|User=pulse|User=$INSTALL_USER|g" \
    -e "s|/home/pulse/pulse-agent|$REPO_DIR|g" \
    "$REPO_DIR/systemd/pulse.service" > "$GENERATED_UNIT"
echo "==> wrote tailored systemd unit: $GENERATED_UNIT"

# --- next-step instructions -----------------------------------------------
cat <<EOF

Next steps:
  1. claude setup-token              # one-time; paste sk-ant-oat01-...
  2. cp $REPO_DIR/.env.example $REPO_DIR/.env  &&  put the OAuth token into it
  3. $REPO_DIR/.venv/bin/python -m scripts.seed --force
  4. $REPO_DIR/.venv/bin/python -m pulse.data_engine.ml_train

To run interactively (foreground):
  $REPO_DIR/.venv/bin/python -m pulse.server      # → http://127.0.0.1:8080

To install as a systemd service (background, auto-restart):
  sudo cp $GENERATED_UNIT /etc/systemd/system/pulse.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now pulse
  sudo systemctl status pulse --no-pager
EOF
