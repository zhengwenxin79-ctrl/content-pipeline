"""
语料库数据库操作模块
SQLite存储，三张核心表：articles（文章）、my_posts（自己的历史文章）、titles（推荐标题）
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path


def get_conn(db_path: str = "corpus/corpus.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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

        CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
        CREATE INDEX IF NOT EXISTS idx_articles_quality ON articles(quality_score DESC);
        CREATE INDEX IF NOT EXISTS idx_my_posts_engagement ON my_posts(engagement_score DESC);
    """)
    conn.commit()
    conn.close()
    print("✓ 数据库初始化完成")


def add_article(source: str, title: str, content: str = None,
                url: str = None, source_name: str = None,
                published_at: str = None, tags: list = None,
                db_path: str = "corpus/corpus.db"):
    """添加外部文章，URL重复则跳过"""
    conn = get_conn(db_path)
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO articles
                (source, source_name, title, content, url, published_at, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source, source_name, title, content, url,
              published_at, json.dumps(tags or [], ensure_ascii=False)))
        conn.commit()
        if cursor.rowcount == 0:
            return None  # 已存在
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


def stats(db_path: str = "corpus/corpus.db") -> dict:
    """语料库统计"""
    conn = get_conn(db_path)
    try:
        result = {}
        for table in ["articles", "my_posts", "title_suggestions", "drafts"]:
            row = conn.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
            result[table] = row["n"]
        return result
    finally:
        conn.close()
