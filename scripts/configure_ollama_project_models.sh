#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OLLAMA_DIR="${PROJECT_ROOT}/models/ollama"
DROP_IN_DIR="/etc/systemd/system/ollama.service.d"
DROP_IN_FILE="${DROP_IN_DIR}/override.conf"

mkdir -p "${OLLAMA_DIR}"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl was not found. Start Ollama manually with:"
  echo "  OLLAMA_MODELS=${OLLAMA_DIR} ollama serve"
  exit 0
fi

echo "Configuring Ollama to store models in:"
echo "  ${OLLAMA_DIR}"

sudo mkdir -p "${DROP_IN_DIR}"
sudo tee "${DROP_IN_FILE}" >/dev/null <<EOF
[Service]
Environment="OLLAMA_MODELS=${OLLAMA_DIR}"
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama

echo
echo "Ollama restarted. New models pulled by Ollama will be visible under:"
echo "  ${OLLAMA_DIR}"
echo
echo "Next:"
echo "  ollama rm gemma4:31b || true"
echo "  ollama pull gemma4:31b-it-q8_0"
