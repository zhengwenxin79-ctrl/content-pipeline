#!/bin/bash
cd "$(dirname "$0")"
export DEEPSEEK_API_KEY="sk-498fec10a4c142f8b794c1566ef80a59"

# 如果已经在运行就直接打开浏览器
if lsof -i :8888 -t > /dev/null 2>&1; then
  echo "服务已在运行，打开浏览器..."
  open http://localhost:8888
  exit 0
fi

echo "启动医疗AI情报服务..."
.venv/bin/python server.py &
sleep 1
open http://localhost:8888
echo "服务已启动，浏览器将自动打开"
echo "关闭此窗口不会停止服务（后台运行）"
