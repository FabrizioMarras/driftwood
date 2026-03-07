#!/usr/bin/env bash

set -euo pipefail

# Always run relative to project root (location of this script).
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Activate local virtual environment.
source .venv/bin/activate

mkdir -p logs

SCHEDULER_PID=""

cleanup() {
  echo "Stopping..."
  if [[ -n "${SCHEDULER_PID}" ]] && kill -0 "${SCHEDULER_PID}" 2>/dev/null; then
    kill "${SCHEDULER_PID}" 2>/dev/null || true
    wait "${SCHEDULER_PID}" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

echo "Starting Driftwood scheduler..."
python3 scheduler.py > logs/scheduler.log 2>&1 &
SCHEDULER_PID=$!

echo "Starting Driftwood dashboard..."
streamlit run dashboard/app.py
