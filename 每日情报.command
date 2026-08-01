#!/bin/bash
# 双击这个文件就能运行每日情报pipeline

cd "$(dirname "$0")"

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
else
  echo "未找到 .env；请先复制 .env.example 并填入必要配置。"
  exit 1
fi

echo "========================================"
echo "  AI+X 交叉研究雷达 Pipeline"
echo "========================================"
echo ""

echo "[1/3] 抓取最新文章..."
.venv/bin/python main.py fetch

echo ""
echo "[2/3] AI评分筛选..."
.venv/bin/python main.py score --limit 50

echo ""
echo "[3/3] 生成今日情报摘要..."
.venv/bin/python main.py digest

echo ""
echo "========================================"
echo "  完成！按任意键关闭窗口"
echo "========================================"
read -n 1
