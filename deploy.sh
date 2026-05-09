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
sleep 1
nohup python3 server.py > server.log 2>&1 &
echo "deployed at $(date)"
