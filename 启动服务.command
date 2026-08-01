#!/bin/bash
cd "$(dirname "$0")"

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
else
  echo "未找到 .env；请先复制 .env.example 并填入必要配置。"
fi

# 如果已经在运行就直接打开浏览器
if lsof -i :8888 -t > /dev/null 2>&1; then
  echo "服务已在运行，打开浏览器..."
  open http://localhost:8888
  exit 0
fi

echo "启动 AI+X 交叉研究雷达..."
.venv/bin/python server.py &
sleep 1
open http://localhost:8888
echo "服务已启动，浏览器将自动打开"
echo "关闭此窗口不会停止服务（后台运行）"
