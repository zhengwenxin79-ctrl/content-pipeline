"""
语料库数据库操作模块
SQLite存储，三张核心表：articles（文章）、my_posts（自己的历史文章）、titles（推荐标题）
"""

import sqlite3
import json
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path


def get_conn(db_path: str = "corpus/corpus.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # WAL: 允许并发读 + 单写者不阻塞读者；busy_timeout: 写锁竞争时等待而非立即报错
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: str = "corpus/corpus.db"):
    """初始化数据库表结构"""
    conn = get_conn(db_path)
    conn.executescript("""
        -- 外部抓取的文章（竞品/热点）
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,          -- 来源：rss/wechat/manual
            source_name TEXT,                   -- 公众号名/网站名
            title       TEXT NOT NULL,
            content     TEXT,
            url         TEXT UNIQUE,
            published_at TEXT,
            fetched_at  TEXT DEFAULT (datetime('now')),
            tags        TEXT,                   -- JSON数组
            read_count  INTEGER DEFAULT 0,      -- 阅读量（若可获取）
            like_count  INTEGER DEFAULT 0,
            quality_score REAL DEFAULT 0.0,     -- 0-10分，AI打分
            is_processed INTEGER DEFAULT 0      -- 是否已分析过
        );

        -- 自己历史发布的文章（语料库核心种子）
        CREATE TABLE IF NOT EXISTS my_posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            platform    TEXT DEFAULT 'xiaohongshu',
            published_at TEXT,
            imported_at TEXT DEFAULT (datetime('now')),
            read_count  INTEGER DEFAULT 0,
            like_count  INTEGER DEFAULT 0,
            collect_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            engagement_score REAL DEFAULT 0.0,  -- 综合互动率，用于权重
            tags        TEXT,                   -- JSON数组
            notes       TEXT                    -- 备注（哪类话题、什么角度）
        );

        -- Claude推荐的标题候选
        CREATE TABLE IF NOT EXISTS title_suggestions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT DEFAULT (datetime('now')),
            topic       TEXT,                   -- 基于什么话题
            titles      TEXT,                   -- JSON数组，5-10个候选
            analysis    TEXT,                   -- Claude的分析说明
            source_articles TEXT,               -- 参考的文章ids（JSON）
            status      TEXT DEFAULT 'pending', -- pending/selected/rejected
            selected_title TEXT                 -- 最终选用的标题
        );

        -- 生成的文章草稿
        CREATE TABLE IF NOT EXISTS drafts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT DEFAULT (datetime('now')),
            title       TEXT,
            content     TEXT,
            title_suggestion_id INTEGER,
            model_used  TEXT,                   -- claude/gemini
            review_status TEXT DEFAULT 'draft', -- draft/reviewing/approved/rejected
            review_notes TEXT,
            final_content TEXT                  -- 审核后定稿
        );

        -- 关键词订阅表
        CREATE TABLE IF NOT EXISTS subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL UNIQUE,
            keywords    TEXT NOT NULL,            -- 逗号分隔，如 "心电图,AI诊断,ECG"
            api_key     TEXT,                     -- 用户自己的DeepSeek API Key（base64编码）
            active      INTEGER DEFAULT 1,        -- 1=启用 0=暂停
            created_at  TEXT DEFAULT (datetime('now')),
            last_sent_at TEXT                     -- 上次推送时间
        );

        CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
        CREATE INDEX IF NOT EXISTS idx_articles_quality ON articles(quality_score DESC);
        CREATE INDEX IF NOT EXISTS idx_my_posts_engagement ON my_posts(engagement_score DESC);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_active ON subscriptions(active);

        -- 研究档案（每用户可有多个）
        CREATE TABLE IF NOT EXISTS user_research_profiles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sub_email   TEXT NOT NULL,
            name        TEXT NOT NULL DEFAULT '我的研究方向',
            direction   TEXT NOT NULL,
            expanded_keywords TEXT DEFAULT '[]',
            direction_hash TEXT NOT NULL DEFAULT '',
            active      INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_profiles_email ON user_research_profiles(sub_email);

        -- 用户账号表
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

        -- 持久化 session 表
        CREATE TABLE IF NOT EXISTS sessions (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            email       TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            expires_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

        -- 用户自定义 RSS 源
        CREATE TABLE IF NOT EXISTS user_feeds (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            url         TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            last_fetched_at TEXT,
            UNIQUE(user_id, url)
        );
        CREATE INDEX IF NOT EXISTS idx_user_feeds_user ON user_feeds(user_id);

        -- 从用户 RSS 源抓到的文章
        CREATE TABLE IF NOT EXISTS user_articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            feed_id     INTEGER NOT NULL,
            title       TEXT NOT NULL,
            url         TEXT,
            content     TEXT,
            summary     TEXT,
            published_at TEXT,
            fetched_at  TEXT DEFAULT (datetime('now')),
            is_read     INTEGER DEFAULT 0,
            UNIQUE(user_id, url)
        );
        CREATE INDEX IF NOT EXISTS idx_user_articles_user ON user_articles(user_id);
        CREATE INDEX IF NOT EXISTS idx_user_articles_feed ON user_articles(feed_id);
    """)
    conn.commit()

    # 迁移：为旧数据库补充后来新增的列和表（ALTER TABLE 对已有列会报错，用 try 忽略）
    migrations = [
        "ALTER TABLE articles ADD COLUMN category TEXT",
        "ALTER TABLE drafts ADD COLUMN draft_v1 TEXT",
        "ALTER TABLE drafts ADD COLUMN draft_v2 TEXT",
        "ALTER TABLE drafts ADD COLUMN review_json TEXT",
        "ALTER TABLE drafts ADD COLUMN best_draft TEXT",
        "ALTER TABLE drafts ADD COLUMN source_article_ids TEXT",
        "ALTER TABLE drafts ADD COLUMN generate_type TEXT",
        "ALTER TABLE articles ADD COLUMN is_starred INTEGER DEFAULT 0",
        "ALTER TABLE subscriptions ADD COLUMN research_direction TEXT DEFAULT ''",
        "ALTER TABLE articles ADD COLUMN ai_summary TEXT DEFAULT ''",
        "ALTER TABLE articles ADD COLUMN deep_analysis TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN reset_token TEXT",
        "ALTER TABLE users ADD COLUMN reset_token_expires TEXT",
        """CREATE TABLE IF NOT EXISTS app_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS invite_tokens (
            token       TEXT PRIMARY KEY,
            domain_tags TEXT DEFAULT '',
            created_by  TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            used_count  INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS article_animations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id      INTEGER NOT NULL,
            image_index     INTEGER DEFAULT 0,
            image_hash      TEXT NOT NULL,
            graph_json      TEXT,
            animation_html  TEXT,
            status          TEXT DEFAULT 'pending',
            error_msg       TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(article_id, image_hash)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_animations_article ON article_animations(article_id)",
        """CREATE TABLE IF NOT EXISTS user_article_relevance (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id      INTEGER NOT NULL,
            article_id      INTEGER NOT NULL,
            relevance_score REAL DEFAULT 0.0,
            recommend_reason TEXT DEFAULT '',
            scored_at       TEXT DEFAULT (datetime('now')),
            UNIQUE(profile_id, article_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_relevance_profile ON user_article_relevance(profile_id)",
        """CREATE TABLE IF NOT EXISTS user_api_keys (
            user_id      INTEGER NOT NULL,
            provider     TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            key_hint     TEXT NOT NULL,
            created_at   INTEGER NOT NULL,
            last_used_at INTEGER,
            PRIMARY KEY (user_id, provider)
        )""",
        """CREATE TABLE IF NOT EXISTS user_quota_usage (
            user_id  INTEGER NOT NULL,
            feature  TEXT NOT NULL,
            date     TEXT NOT NULL,
            count    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, feature, date)
        )""",
        """CREATE TABLE IF NOT EXISTS user_preferences (
            user_id         INTEGER PRIMARY KEY,
            template_style  TEXT DEFAULT 'card',
            push_time       TEXT DEFAULT '08:00',
            content_format  TEXT DEFAULT 'mixed',
            comic_style     TEXT DEFAULT 'hand-drawn',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # 列/表已存在，忽略
    conn.commit()
    conn.close()
    print("✓ 数据库初始化完成")


def add_article(source: str, title: str, content: str = None,
                url: str = None, source_name: str = None,
                published_at: str = None, tags: list = None,
                db_path: str = "corpus/corpus.db"):
    """添加外部文章，URL或标题重复则跳过"""
    conn = get_conn(db_path)
    try:
        # URL去重（已有UNIQUE约束）
        if url:
            exists = conn.execute(
                "SELECT id FROM articles WHERE url = ?", (url,)
            ).fetchone()
            if exists:
                return None
        # 标题去重（同来源同标题视为重复）
        if title:
            exists = conn.execute(
                "SELECT id FROM articles WHERE title = ? AND source_name = ?",
                (title, source_name)
            ).fetchone()
            if exists:
                return None
        cursor = conn.execute("""
            INSERT OR IGNORE INTO articles
                (source, source_name, title, content, url, published_at, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source, source_name, title, content, url,
              published_at, json.dumps(tags or [], ensure_ascii=False)))
        conn.commit()
        if cursor.rowcount == 0:
            return None
        return cursor.lastrowid
    finally:
        conn.close()


def add_my_post(title: str, content: str, published_at: str = None,
                read_count: int = 0, like_count: int = 0,
                collect_count: int = 0, comment_count: int = 0,
                tags: list = None, notes: str = None,
                db_path: str = "corpus/corpus.db") -> int:
    """添加自己的历史文章"""
    # 计算互动分（阅读量权重低，互动权重高）
    engagement = (like_count * 3 + collect_count * 5 + comment_count * 2 +
                  read_count * 0.01)
    conn = get_conn(db_path)
    try:
        cursor = conn.execute("""
            INSERT INTO my_posts
                (title, content, published_at, read_count, like_count,
                 collect_count, comment_count, engagement_score, tags, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, content, published_at, read_count, like_count,
              collect_count, comment_count, engagement,
              json.dumps(tags or [], ensure_ascii=False), notes))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_top_posts(limit: int = 20, db_path: str = "corpus/corpus.db") -> list:
    """获取互动率最高的自己的文章，作为风格种子"""
    conn = get_conn(db_path)
    try:
        rows = conn.execute("""
            SELECT * FROM my_posts
            ORDER BY engagement_score DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_articles(days: int = 7, min_quality: float = 0.0,
                        limit: int = 50,
                        db_path: str = "corpus/corpus.db") -> list:
    """获取最近N天的外部文章"""
    conn = get_conn(db_path)
    try:
        rows = conn.execute("""
            SELECT * FROM articles
            WHERE fetched_at >= datetime('now', ?)
              AND quality_score >= ?
            ORDER BY quality_score DESC, fetched_at DESC
            LIMIT ?
        """, (f'-{days} days', min_quality, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_quality_score(article_id: int, score: float,
                         db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        conn.execute("UPDATE articles SET quality_score=?, is_processed=1 WHERE id=?",
                     (score, article_id))
        conn.commit()
    finally:
        conn.close()


def save_title_suggestions(topic: str, titles: list, analysis: str,
                            source_ids: list = None,
                            db_path: str = "corpus/corpus.db") -> int:
    conn = get_conn(db_path)
    try:
        cursor = conn.execute("""
            INSERT INTO title_suggestions (topic, titles, analysis, source_articles)
            VALUES (?, ?, ?, ?)
        """, (topic, json.dumps(titles, ensure_ascii=False),
              analysis, json.dumps(source_ids or [])))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def add_subscription(email: str, keywords: str, api_key: str = None,
                     research_direction: str = "",
                     db_path: str = "corpus/corpus.db") -> dict:
    """新增订阅，邮箱已存在则返回错误"""
    import base64
    encoded_key = base64.b64encode(api_key.encode()).decode() if api_key else None
    conn = get_conn(db_path)
    try:
        exists = conn.execute(
            "SELECT id, active FROM subscriptions WHERE email = ?", (email,)
        ).fetchone()
        if exists:
            if exists["active"] == 0:
                conn.execute(
                    "UPDATE subscriptions SET keywords=?, api_key=?, active=1, research_direction=? WHERE email=?",
                    (keywords, encoded_key, research_direction, email)
                )
                conn.commit()
                return {"ok": True, "msg": "已重新激活订阅"}
            return {"ok": False, "msg": "该邮箱已订阅，如需修改请使用更新功能"}
        conn.execute(
            "INSERT INTO subscriptions (email, keywords, api_key, research_direction) VALUES (?, ?, ?, ?)",
            (email, keywords, encoded_key, research_direction)
        )
        conn.commit()
        return {"ok": True, "msg": "订阅成功"}
    finally:
        conn.close()


def update_subscription(email: str, keywords: str, api_key: str = None,
                        research_direction: str = None,
                        db_path: str = "corpus/corpus.db") -> dict:
    """修改订阅关键词和研究方向"""
    import base64
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM subscriptions WHERE email = ? AND active = 1", (email,)
        ).fetchone()
        if not row:
            return {"ok": False, "msg": "未找到该邮箱的有效订阅"}
        if api_key:
            encoded_key = base64.b64encode(api_key.encode()).decode()
            conn.execute(
                "UPDATE subscriptions SET keywords=?, api_key=?, research_direction=COALESCE(?,research_direction) WHERE email=?",
                (keywords, encoded_key, research_direction, email)
            )
        else:
            conn.execute(
                "UPDATE subscriptions SET keywords=?, research_direction=COALESCE(?,research_direction) WHERE email=?",
                (keywords, research_direction, email)
            )
        conn.commit()
        return {"ok": True, "msg": "订阅已更新"}
    finally:
        conn.close()


def cancel_subscription(email: str, db_path: str = "corpus/corpus.db") -> dict:
    """退订（软删除）"""
    conn = get_conn(db_path)
    try:
        result = conn.execute(
            "UPDATE subscriptions SET active=0 WHERE email=? AND active=1", (email,)
        )
        conn.commit()
        if result.rowcount == 0:
            return {"ok": False, "msg": "未找到该邮箱的有效订阅"}
        return {"ok": True, "msg": "已退订"}
    finally:
        conn.close()


def get_active_subscriptions(db_path: str = "corpus/corpus.db") -> list:
    """获取所有启用的订阅"""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE active = 1"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_last_sent(email: str, db_path: str = "corpus/corpus.db"):
    """更新最后推送时间"""
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE subscriptions SET last_sent_at = datetime('now') WHERE email = ?",
            (email,)
        )
        conn.commit()
    finally:
        conn.close()


def stats(db_path: str = "corpus/corpus.db") -> dict:
    """语料库统计"""
    conn = get_conn(db_path)
    try:
        result = {}
        for table in ["articles", "my_posts", "title_suggestions", "drafts"]:
            row = conn.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
            result[table] = row["n"]
        # 今日新增
        today = conn.execute(
            "SELECT COUNT(*) as n FROM articles WHERE fetched_at >= datetime('now', '-1 days')"
        ).fetchone()["n"]
        result["today"] = today
        # 覆盖来源数
        sources = conn.execute(
            "SELECT COUNT(DISTINCT source_name) as n FROM articles"
        ).fetchone()["n"]
        result["sources"] = sources
        # 医疗相关（有category或来自医疗专属源）
        medical = conn.execute(
            """SELECT COUNT(*) as n FROM articles
               WHERE source_name IN (
                 'Nature Medicine','Nature Biomedical Engineering',
                 'The Lancet Digital Health','NEJM AI','npj Digital Medicine',
                 'JAMA Network Open','Medical Image Analysis',
                 'IEEE Transactions on Medical Imaging',
                 'IEEE Journal of Biomedical and Health Informatics',
                 'STAT News','Healthcare IT News',
                 'arXiv q-bio.QM (生物医学定量方法)',
                 'arXiv eess.IV (医学影像/MICCAI方向)'
               )
               OR category != ''"""
        ).fetchone()["n"]
        result["medical"] = medical
        return result
    finally:
        conn.close()


def _hash_password(password: str) -> str:
    salt = os.environ.get("AUTH_SALT", "medai-salt-2026")
    return hashlib.sha256((salt + password).encode()).hexdigest()


def register_user(email: str, password: str,
                  db_path: str = "corpus/corpus.db") -> dict:
    conn = get_conn(db_path)
    try:
        exists = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if exists:
            return {"ok": False, "msg": "该邮箱已注册"}
        conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, _hash_password(password))
        )
        conn.commit()
        row = conn.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
        return {"ok": True, "user": {"id": row["id"], "email": row["email"]}}
    finally:
        conn.close()


def login_user(email: str, password: str,
               db_path: str = "corpus/corpus.db") -> dict:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, email FROM users WHERE email = ? AND password_hash = ?",
            (email, _hash_password(password))
        ).fetchone()
        if not row:
            return {"ok": False, "msg": "邮箱或密码错误"}
        return {"ok": True, "user": {"id": row["id"], "email": row["email"]}}
    finally:
        conn.close()


def create_password_reset_token(email: str,
                                ttl_minutes: int = 30,
                                db_path: str = "corpus/corpus.db") -> str:
    """为邮箱生成一次性重置 token 并写库。邮箱不存在时返回空串（调用方不要泄露这一信息）。"""
    import secrets
    from datetime import datetime, timezone, timedelta

    conn = get_conn(db_path)
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            return ""
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE users SET reset_token = ?, reset_token_expires = ? WHERE id = ?",
            (token, expires, row["id"])
        )
        conn.commit()
        return token
    finally:
        conn.close()


def reset_password_with_token(token: str, new_password: str,
                              db_path: str = "corpus/corpus.db") -> dict:
    """校验 token+过期时间，通过则更新密码并失效 token。"""
    from datetime import datetime, timezone

    if not token or len(new_password) < 6:
        return {"ok": False, "msg": "参数无效（密码至少 6 位）"}
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, email, reset_token_expires FROM users WHERE reset_token = ?",
            (token,)
        ).fetchone()
        if not row:
            return {"ok": False, "msg": "链接无效或已被使用"}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if (row["reset_token_expires"] or "") < now:
            return {"ok": False, "msg": "链接已过期，请重新申请"}
        conn.execute(
            "UPDATE users SET password_hash=?, reset_token=NULL, reset_token_expires=NULL WHERE id=?",
            (_hash_password(new_password), row["id"])
        )
        conn.commit()
        return {"ok": True, "email": row["email"]}
    finally:
        conn.close()


# ── user_feeds ────────────────────────────────────────

def add_user_feed(user_id: int, name: str, url: str,
                  db_path: str = "corpus/corpus.db") -> dict:
    conn = get_conn(db_path)
    try:
        exists = conn.execute(
            "SELECT id FROM user_feeds WHERE user_id=? AND url=?", (user_id, url)
        ).fetchone()
        if exists:
            return {"ok": False, "msg": "该 RSS 源已添加"}
        cursor = conn.execute(
            "INSERT INTO user_feeds (user_id, name, url) VALUES (?,?,?)",
            (user_id, name, url)
        )
        conn.commit()
        return {"ok": True, "id": cursor.lastrowid}
    finally:
        conn.close()


def get_user_feeds(user_id: int, db_path: str = "corpus/corpus.db") -> list:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM user_feeds WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_user_feed(feed_id: int, user_id: int,
                     db_path: str = "corpus/corpus.db") -> dict:
    conn = get_conn(db_path)
    try:
        conn.execute(
            "DELETE FROM user_feeds WHERE id=? AND user_id=?", (feed_id, user_id)
        )
        conn.execute(
            "DELETE FROM user_articles WHERE feed_id=? AND user_id=?", (feed_id, user_id)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def get_all_user_feeds(db_path: str = "corpus/corpus.db") -> list:
    """定时任务用：获取全部用户的所有 RSS 源"""
    conn = get_conn(db_path)
    try:
        rows = conn.execute("SELECT * FROM user_feeds").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_feed_fetched(feed_id: int, db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE user_feeds SET last_fetched_at=datetime('now') WHERE id=?",
            (feed_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ── user_articles ─────────────────────────────────────

def save_user_article(user_id: int, feed_id: int, title: str,
                      url: str, content: str = None, summary: str = None,
                      published_at: str = None,
                      db_path: str = "corpus/corpus.db") -> bool:
    """保存一篇用户文章，已存在则跳过，返回是否是新文章"""
    conn = get_conn(db_path)
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO user_articles
                (user_id, feed_id, title, url, content, summary, published_at)
            VALUES (?,?,?,?,?,?,?)
        """, (user_id, feed_id, title, url, content, summary, published_at))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_user_articles(user_id: int, feed_id: int = None, limit: int = 100,
                      db_path: str = "corpus/corpus.db") -> list:
    conn = get_conn(db_path)
    try:
        if feed_id:
            rows = conn.execute("""
                SELECT a.*, f.name as feed_name FROM user_articles a
                JOIN user_feeds f ON f.id = a.feed_id
                WHERE a.user_id=? AND a.feed_id=?
                ORDER BY a.fetched_at DESC LIMIT ?
            """, (user_id, feed_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT a.*, f.name as feed_name FROM user_articles a
                JOIN user_feeds f ON f.id = a.feed_id
                WHERE a.user_id=?
                ORDER BY a.fetched_at DESC LIMIT ?
            """, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_user_article_read(article_id: int, user_id: int,
                           db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE user_articles SET is_read=1 WHERE id=? AND user_id=?",
            (article_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


# ── Session 持久化 ────────────────────────────────────────────

def save_session(token: str, user_id: int, email: str,
                 days: int = 30, db_path: str = "corpus/corpus.db"):
    expires = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token, user_id, email, expires_at) VALUES (?,?,?,?)",
            (token, user_id, email, expires)
        )
        conn.commit()
    finally:
        conn.close()


def load_session(token: str, db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT user_id, email FROM sessions WHERE token=? AND expires_at > datetime('now')",
            (token,)
        ).fetchone()
        return {"id": row["user_id"], "email": row["email"]} if row else None
    finally:
        conn.close()


def delete_session(token: str, db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
    finally:
        conn.close()


def cleanup_sessions(db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
        conn.commit()
    finally:
        conn.close()


# ── 邀请链接 ──────────────────────────────────────────────────

def create_invite_token(domain_tags: str, created_by: str,
                        db_path: str = "corpus/corpus.db") -> str:
    import secrets
    token = secrets.token_urlsafe(8)
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO invite_tokens (token, domain_tags, created_by) VALUES (?,?,?)",
            (token, domain_tags, created_by)
        )
        conn.commit()
    finally:
        conn.close()
    return token


def get_invite_token(token: str, db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT token, domain_tags, used_count FROM invite_tokens WHERE token=?",
            (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def use_invite_token(token: str, db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE invite_tokens SET used_count = used_count + 1 WHERE token=?",
            (token,)
        )
        conn.commit()
    finally:
        conn.close()


def list_invite_tokens(created_by: str, db_path: str = "corpus/corpus.db") -> list:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT token, domain_tags, used_count, created_at FROM invite_tokens WHERE created_by=? ORDER BY created_at DESC",
            (created_by,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── article_animations ────────────────────────────────

def save_animation(article_id: int, image_index: int, image_hash: str,
                   graph_json: dict, animation_html: str,
                   status: str, error_msg: str = None,
                   db_path: str = "corpus/corpus.db") -> int:
    """插入或更新一条动画记录，返回 row id。"""
    conn = get_conn(db_path)
    try:
        cur = conn.execute("""
            INSERT INTO article_animations
                (article_id, image_index, image_hash, graph_json, animation_html, status, error_msg)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(article_id, image_hash) DO UPDATE SET
                graph_json=excluded.graph_json,
                animation_html=excluded.animation_html,
                status=excluded.status,
                error_msg=excluded.error_msg,
                created_at=datetime('now')
        """, (
            article_id, image_index, image_hash,
            json.dumps(graph_json or {}, ensure_ascii=False),
            animation_html, status, error_msg,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_animations_for_article(article_id: int,
                                db_path: str = "corpus/corpus.db") -> list:
    """返回某篇文章所有已生成（status='done'）的动画列表。"""
    conn = get_conn(db_path)
    try:
        rows = conn.execute("""
            SELECT id, image_index, image_hash, graph_json, status, error_msg, created_at
            FROM article_animations
            WHERE article_id=? AND status='done'
            ORDER BY image_index
        """, (article_id,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                g = json.loads(d.get("graph_json") or "{}")
                d["title"] = g.get("title", f"图 {d['image_index']+1}")
            except Exception:
                d["title"] = f"图 {d['image_index']+1}"
            result.append(d)
        return result
    finally:
        conn.close()


def get_animation_html(animation_id: int,
                       db_path: str = "corpus/corpus.db"):
    """返回指定动画的 HTML 内容，不存在则返回 None。"""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT animation_html FROM article_animations WHERE id=?",
            (animation_id,)
        ).fetchone()
        return row["animation_html"] if row else None
    finally:
        conn.close()


# ── user_research_profiles ────────────────────────────

def _direction_hash(direction: str) -> str:
    return hashlib.md5(direction.strip().encode()).hexdigest()[:8]


def create_research_profile(email: str, name: str, direction: str,
                             expanded_keywords: list = None,
                             db_path: str = "corpus/corpus.db") -> int:
    conn = get_conn(db_path)
    try:
        cursor = conn.execute("""
            INSERT INTO user_research_profiles
                (sub_email, name, direction, expanded_keywords, direction_hash)
            VALUES (?,?,?,?,?)
        """, (email, name, direction,
              json.dumps(expanded_keywords or [], ensure_ascii=False),
              _direction_hash(direction)))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_research_profile(profile_id: int, email: str,
                             name: str = None, direction: str = None,
                             expanded_keywords: list = None,
                             db_path: str = "corpus/corpus.db") -> dict:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM user_research_profiles WHERE id=? AND sub_email=?",
            (profile_id, email)
        ).fetchone()
        if not row:
            return {"ok": False, "msg": "档案不存在"}

        updates, params = [], []
        if name is not None:
            updates.append("name=?"); params.append(name)
        if direction is not None:
            updates.append("direction=?"); params.append(direction)
            updates.append("direction_hash=?"); params.append(_direction_hash(direction))
            conn.execute("DELETE FROM user_article_relevance WHERE profile_id=?",
                         (profile_id,))
        if expanded_keywords is not None:
            updates.append("expanded_keywords=?")
            params.append(json.dumps(expanded_keywords, ensure_ascii=False))
        if updates:
            updates.append("updated_at=datetime('now')")
            params.extend([profile_id, email])
            conn.execute(
                f"UPDATE user_research_profiles SET {', '.join(updates)} WHERE id=? AND sub_email=?",
                params
            )
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def delete_research_profile(profile_id: int, email: str,
                             db_path: str = "corpus/corpus.db") -> dict:
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE user_research_profiles SET active=0, updated_at=datetime('now') WHERE id=? AND sub_email=?",
            (profile_id, email)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def get_research_profiles(email: str, db_path: str = "corpus/corpus.db") -> list:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM user_research_profiles WHERE sub_email=? AND active=1 ORDER BY created_at",
            (email,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_user_relevance(profile_id: int, article_id: int,
                        relevance_score: float, recommend_reason: str = "",
                        db_path: str = "corpus/corpus.db"):
    conn = get_conn(db_path)
    try:
        conn.execute("""
            INSERT INTO user_article_relevance
                (profile_id, article_id, relevance_score, recommend_reason)
            VALUES (?,?,?,?)
            ON CONFLICT(profile_id, article_id) DO UPDATE SET
                relevance_score=excluded.relevance_score,
                recommend_reason=excluded.recommend_reason,
                scored_at=datetime('now')
        """, (profile_id, article_id, relevance_score, recommend_reason))
        conn.commit()
    finally:
        conn.close()


def get_user_relevance_batch(profile_ids: list, article_ids: list,
                              db_path: str = "corpus/corpus.db") -> dict:
    """返回 {article_id: {"score", "reason", "profile_id"}}，多 profile 取最高分"""
    if not profile_ids or not article_ids:
        return {}
    conn = get_conn(db_path)
    try:
        p_ph = ",".join("?" * len(profile_ids))
        a_ph = ",".join("?" * len(article_ids))
        rows = conn.execute(f"""
            SELECT profile_id, article_id, relevance_score, recommend_reason
            FROM user_article_relevance
            WHERE profile_id IN ({p_ph}) AND article_id IN ({a_ph})
        """, list(profile_ids) + list(article_ids)).fetchall()
        result = {}
        for r in rows:
            aid = r["article_id"]
            if aid not in result or r["relevance_score"] > result[aid]["score"]:
                result[aid] = {
                    "score": r["relevance_score"],
                    "reason": r["recommend_reason"],
                    "profile_id": r["profile_id"],
                }
        return result
    finally:
        conn.close()


def migrate_research_directions(db_path: str = "corpus/corpus.db"):
    """将 subscriptions.research_direction 迁移为 user_research_profiles（幂等）"""
    conn = get_conn(db_path)
    try:
        subs = conn.execute(
            "SELECT email, research_direction FROM subscriptions "
            "WHERE research_direction IS NOT NULL AND research_direction != ''"
        ).fetchall()
        migrated = 0
        for sub in subs:
            exists = conn.execute(
                "SELECT id FROM user_research_profiles WHERE sub_email=?",
                (sub["email"],)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO user_research_profiles (sub_email, name, direction, direction_hash) "
                    "VALUES (?,?,?,?)",
                    (sub["email"], "我的研究方向", sub["research_direction"],
                     _direction_hash(sub["research_direction"]))
                )
                migrated += 1
        conn.commit()
        if migrated:
            print(f"✓ 迁移了 {migrated} 条旧研究方向到档案表")
    finally:
        conn.close()


# ── user_preferences (OpenClaw Skill) ─────────────────

def get_user_preferences(user_id: int, db_path: str = "corpus/corpus.db") -> dict:
    """获取用户偏好设置，不存在则返回默认值"""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT template_style, push_time, content_format, comic_style FROM user_preferences WHERE user_id=?",
            (user_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {
            "template_style": "card",
            "push_time": "08:00",
            "content_format": "mixed",
            "comic_style": "hand-drawn",
        }
    finally:
        conn.close()


def save_user_preferences(user_id: int, prefs: dict, db_path: str = "corpus/corpus.db"):
    """保存用户偏好设置（upsert）"""
    conn = get_conn(db_path)
    try:
        conn.execute("""
            INSERT INTO user_preferences (user_id, template_style, push_time, content_format, comic_style)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                template_style=excluded.template_style,
                push_time=excluded.push_time,
                content_format=excluded.content_format,
                comic_style=excluded.comic_style
        """, (
            user_id,
            prefs.get("template_style", "card"),
            prefs.get("push_time", "08:00"),
            prefs.get("content_format", "mixed"),
            prefs.get("comic_style", "hand-drawn"),
        ))
        conn.commit()
    finally:
        conn.close()
