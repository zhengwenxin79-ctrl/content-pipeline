"""
BYOK（Bring Your Own Key）+ 免费配额模块

- 用户可选填自己的 DeepSeek / DashScope API key
- key 用 Fernet 对称加密存 SQLite
- 没有自己 key 的用户走免费配额（每日 N 次）
"""

import os
import time
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── 主密钥管理 ─────────────────────────────────────────────────────────────────

_KEY_PATH = os.path.join(os.path.dirname(__file__), "corpus", "master.key")


def _load_fernet():
    from cryptography.fernet import Fernet
    if os.path.exists(_KEY_PATH):
        return Fernet(open(_KEY_PATH, "rb").read().strip())
    os.makedirs(os.path.dirname(_KEY_PATH), exist_ok=True)
    k = Fernet.generate_key()
    with open(_KEY_PATH, "wb") as f:
        f.write(k)
    os.chmod(_KEY_PATH, 0o600)
    print(f"[byok] 主密钥已生成：{_KEY_PATH}  ⚠️ 请妥善备份，丢失后已存 key 无法解密")
    return Fernet(k)


_fernet = None


def _f():
    global _fernet
    if _fernet is None:
        _fernet = _load_fernet()
    return _fernet


def _encrypt(plain: str) -> str:
    return _f().encrypt(plain.encode()).decode()


def _decrypt(cipher: str) -> str:
    return _f().decrypt(cipher.encode()).decode()


# ── 配额配置 ──────────────────────────────────────────────────────────────────

QUOTA_LIMITS: dict[str, int] = {
    "animation": 3,   # 每日免费动画解析次数
}


class QuotaExceeded(Exception):
    """用户免费配额用完时抛出"""
    pass


# ── Key CRUD ──────────────────────────────────────────────────────────────────

def save_user_key(user_id: int, provider: str, raw_key: str, db_path: str):
    """加密保存用户的 API key。provider: 'deepseek' | 'dashscope'"""
    hint = f"{raw_key[:6]}...{raw_key[-4:]}"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO user_api_keys(user_id, provider, encrypted_key, key_hint, created_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
            encrypted_key = excluded.encrypted_key,
            key_hint      = excluded.key_hint,
            created_at    = excluded.created_at
    """, (user_id, provider, _encrypt(raw_key), hint, int(time.time())))
    conn.commit()
    conn.close()


def get_user_key(user_id: int, provider: str, db_path: str) -> Optional[str]:
    """返回解密后的 key，不存在返回 None。"""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT encrypted_key FROM user_api_keys WHERE user_id=? AND provider=?",
        (user_id, provider)
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return _decrypt(row[0])
    except Exception:
        return None


def list_user_keys(user_id: int, db_path: str) -> dict:
    """返回 {'deepseek': 'sk-...xxxx', 'dashscope': 'sk-...yyyy'}（只含 hint，不含明文）"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT provider, key_hint FROM user_api_keys WHERE user_id=?",
        (user_id,)
    ).fetchall()
    conn.close()
    return {p: h for p, h in rows}


def delete_user_key(user_id: int, provider: str, db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "DELETE FROM user_api_keys WHERE user_id=? AND provider=?",
        (user_id, provider)
    )
    conn.commit()
    conn.close()


# ── 配额管理 ──────────────────────────────────────────────────────────────────

def _today_cst() -> str:
    """返回北京时间当日日期字符串 YYYY-MM-DD"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def get_quota_status(user_id: int, feature: str, has_own_key: bool,
                     db_path: str) -> dict:
    """返回 {used, limit, has_own_key, unlimited}"""
    limit = QUOTA_LIMITS.get(feature, 0)
    if has_own_key:
        return {"used": 0, "limit": limit, "has_own_key": True, "unlimited": True}
    today = _today_cst()
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT count FROM user_quota_usage WHERE user_id=? AND feature=? AND date=?",
        (user_id, feature, today)
    ).fetchone()
    conn.close()
    return {"used": row[0] if row else 0, "limit": limit,
            "has_own_key": False, "unlimited": False}


def check_and_consume(user_id: int, feature: str, has_own_key: bool,
                      db_path: str):
    """
    检查配额并消耗一次。
    - has_own_key=True：直接放行，不消耗免费配额
    - 超限时抛出 QuotaExceeded
    """
    if has_own_key:
        return
    limit = QUOTA_LIMITS.get(feature, 0)
    today = _today_cst()
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT count FROM user_quota_usage WHERE user_id=? AND feature=? AND date=?",
        (user_id, feature, today)
    ).fetchone()
    used = row[0] if row else 0
    if used >= limit:
        conn.close()
        raise QuotaExceeded(
            f"今日免费动画解析已用完（{used}/{limit} 次），"
            f"可在「个人设置 → API Keys」填入自己的 key 解除限制"
        )
    conn.execute("""
        INSERT INTO user_quota_usage(user_id, feature, date, count) VALUES(?, ?, ?, 1)
        ON CONFLICT(user_id, feature, date) DO UPDATE SET count = count + 1
    """, (user_id, feature, today))
    conn.commit()
    conn.close()


# ── Key 验证 ──────────────────────────────────────────────────────────────────

def validate_key(provider: str, raw_key: str) -> tuple[bool, str]:
    """
    用最便宜的 API 调用验证 key 是否有效。
    返回 (ok: bool, error_msg: str)
    """
    try:
        if provider == "deepseek":
            from openai import OpenAI, AuthenticationError
            c = OpenAI(api_key=raw_key, base_url="https://api.deepseek.com/v1")
            c.models.list()
        elif provider == "dashscope":
            from openai import OpenAI, AuthenticationError
            c = OpenAI(api_key=raw_key,
                       base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
            c.models.list()
        else:
            return False, f"未知 provider: {provider}"
        return True, ""
    except Exception as e:
        msg = str(e)
        if "401" in msg or "invalid" in msg.lower() or "auth" in msg.lower():
            return False, "Key 无效，请检查是否复制完整"
        return False, f"验证失败：{msg[:100]}"
