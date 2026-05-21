#!/usr/bin/env bash
set -euo pipefail

BASE_URL="https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL="meta/llama-3.3-70b-instruct"

echo "NVIDIA + Aider quick start"
echo

if ! command -v aider >/dev/null 2>&1; then
  echo "Error: 'aider' command not found."
  echo "Try this once: export PATH=\"\$HOME/.local/bin:\$PATH\""
  exit 1
fi

read -rsp "Paste your NVIDIA API key (input hidden): " NVIDIA_KEY
echo

if [[ -z "${NVIDIA_KEY}" ]]; then
  echo "No key provided. Aborting."
  exit 1
fi

export OPENAI_API_KEY="${NVIDIA_KEY}"
export OPENAI_BASE_URL="${BASE_URL}"

echo "Checking available NVIDIA models..."
MODEL_JSON="$(curl -sS "${BASE_URL}/models" -H "Authorization: Bearer ${OPENAI_API_KEY}" || true)"

if [[ "${MODEL_JSON}" != *"\"data\""* ]]; then
  echo "Could not read model list. Check your key or network."
  echo "Raw response:"
  echo "${MODEL_JSON}"
  exit 1
fi

echo
echo "Suggested model: ${DEFAULT_MODEL}"
read -rp "Press Enter to use it, or type another model id: " CHOSEN_MODEL
CHOSEN_MODEL="${CHOSEN_MODEL:-${DEFAULT_MODEL}}"

echo
echo "Starting Aider with model: ${CHOSEN_MODEL}"
echo "Tip: once inside aider, run: /add ."
echo

exec aider --model "openai/${CHOSEN_MODEL}"
