#!/bin/bash
# 双击这个文件就能运行每日情报pipeline

cd "$(dirname "$0")"

export DEEPSEEK_API_KEY="sk-498fec10a4c142f8b794c1566ef80a59"

echo "========================================"
echo "  医疗AI 每日情报 Pipeline"
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
