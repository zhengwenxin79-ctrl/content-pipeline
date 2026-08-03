#!/bin/bash
set -euo pipefail

cd /opt/content-pipeline

LOCAL=$(git rev-parse HEAD)
git fetch origin main -q
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0
fi

git pull origin main -q
pkill -f "python3 bench_server.py" || true
sleep 1

HOST=${HOST:-0.0.0.0}
BENCH_PORT=${BENCH_PORT:-8765}
BENCH_OUTPUT_DIR=${BENCH_OUTPUT_DIR:-bench_analysis_outputs}

nohup env HOST="$HOST" BENCH_PORT="$BENCH_PORT" BENCH_OUTPUT_DIR="$BENCH_OUTPUT_DIR" \
  python3 bench_server.py > bench_server.log 2>&1 &

echo "bench deployed at $(date)"
