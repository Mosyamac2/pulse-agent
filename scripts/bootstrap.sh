#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu VM for Pulse.
# Idempotent — safe to re-run.
set -euo pipefail

REPO_DIR="${PULSE_REPO_DIR:-/home/pulse/pulse-agent}"
PYTHON_VERSION="3.11"

echo "==> apt deps"
sudo apt-get update -y
sudo apt-get install -y \
    "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-venv" "python${PYTHON_VERSION}-dev" \
    git curl build-essential

echo "==> Node.js (for the claude CLI subprocess)"
if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
sudo npm install -g @anthropic-ai/claude-code

echo "==> 'pulse' user"
if ! id -u pulse >/dev/null 2>&1; then
    sudo useradd -m -s /bin/bash pulse
fi

echo "==> repo dir + venv"
sudo mkdir -p "$(dirname "$REPO_DIR")"
sudo chown -R pulse:pulse "$(dirname "$REPO_DIR")"
if [ ! -d "$REPO_DIR" ]; then
    echo "  NOTE: clone the repo to $REPO_DIR before re-running this script."
    exit 0
fi

sudo -u pulse "python${PYTHON_VERSION}" -m venv "$REPO_DIR/.venv"
sudo -u pulse "$REPO_DIR/.venv/bin/pip" install --upgrade pip
sudo -u pulse "$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo
echo "Next steps (as user 'pulse'):"
echo "  1. claude setup-token   # paste sk-ant-oat01-..."
echo "  2. cp $REPO_DIR/.env.example $REPO_DIR/.env  &&  edit token in .env"
echo "  3. $REPO_DIR/.venv/bin/python -m scripts.seed --force"
echo "  4. $REPO_DIR/.venv/bin/python -m pulse.data_engine.ml_train"
echo "  5. sudo cp $REPO_DIR/systemd/pulse.service /etc/systemd/system/"
echo "  6. sudo systemctl daemon-reload && sudo systemctl enable --now pulse"
echo
echo "Then check http://VM_IP:8080 in a browser."
