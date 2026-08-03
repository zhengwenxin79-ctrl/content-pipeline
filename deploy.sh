#!/bin/bash
# 只在有新 commit 时才重启，避免打断正在运行的动画任务
cd /opt/content-pipeline

LOCAL=$(git rev-parse HEAD)
git fetch origin main -q
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # 无更新，不重启
fi

git pull origin main -q
pkill -f "python3 server.py" || true
pkill -f "python3 bench_server.py" || true
sleep 1
nohup python3 server.py > server.log 2>&1 &

if [ "${ENABLE_BENCH_SERVER:-1}" = "1" ]; then
  HOST=${HOST:-0.0.0.0}
  BENCH_PORT=${BENCH_PORT:-8765}
  BENCH_OUTPUT_DIR=${BENCH_OUTPUT_DIR:-bench_analysis_outputs}

  nohup env HOST="$HOST" BENCH_PORT="$BENCH_PORT" BENCH_OUTPUT_DIR="$BENCH_OUTPUT_DIR" \
    python3 bench_server.py > bench_server.log 2>&1 &
fi

echo "deployed at $(date)"
