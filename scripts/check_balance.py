#!/usr/bin/env python3
"""
检查 DeepSeek 和 DashScope(Qwen) API 余额。
用法:
  python3 scripts/check_balance.py          # 单次检查
  python3 scripts/check_balance.py --watch  # 每小时刷新

余额低于阈值时打印醒目警告。可集成进 cron：
  0 9 * * * cd /opt/content-pipeline && python3 scripts/check_balance.py >> logs/balance.log 2>&1
"""
import os
import sys
import json
import time
import urllib.request
from pathlib import Path


def load_env():
    env = Path(__file__).parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def check_deepseek() -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return {"ok": False, "msg": "未配置 DEEPSEEK_API_KEY"}
    req = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        infos = data.get("balance_infos", [{}])
        balance = float(infos[0].get("total_balance", 0)) if infos else 0.0
        currency = infos[0].get("currency", "CNY") if infos else "CNY"
        return {"ok": True, "balance": balance, "currency": currency}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def check_dashscope() -> dict:
    # DashScope 无公开余额 API，返回指引
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        return {"ok": False, "msg": "未配置 DASHSCOPE_API_KEY"}
    return {"ok": None, "msg": "请登录阿里云控制台查看：https://dashscope.console.aliyun.com"}


WARN_THRESHOLD = 10.0   # 低于此金额（CNY）触发警告


def report():
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"  API 余额检查  {ts}")
    print(f"{'='*50}")

    # DeepSeek
    ds = check_deepseek()
    if ds["ok"] is True:
        b = ds["balance"]
        warn = "  ⚠️  余额不足！请充值" if b < WARN_THRESHOLD else ""
        level = "🔴" if b < WARN_THRESHOLD else ("🟡" if b < 30 else "🟢")
        print(f"  {level} DeepSeek  ¥{b:.2f} {ds['currency']}{warn}")
    else:
        print(f"  ❌ DeepSeek  {ds['msg']}")

    # DashScope
    sc = check_dashscope()
    if sc["ok"] is None:
        print(f"  ℹ️  DashScope  {sc['msg']}")
    else:
        print(f"  ❌ DashScope  {sc['msg']}")

    print()

    # 返回是否有余额不足警告
    return ds.get("ok") is True and ds.get("balance", 999) < WARN_THRESHOLD


if __name__ == "__main__":
    load_env()
    watch = "--watch" in sys.argv

    if watch:
        print("监控模式启动，每小时检查一次。Ctrl+C 退出。")
        while True:
            try:
                report()
                time.sleep(3600)
            except KeyboardInterrupt:
                print("\n已退出监控。")
                break
    else:
        low = report()
        sys.exit(1 if low else 0)
