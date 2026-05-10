#!/usr/bin/env python3
"""
医疗AI情报 CLI — 自包含版本
无外部依赖，仅使用 Python 标准库。
供 OpenClaw Skill 通过 exec 直接调用。

用法:
  python3 medai.py digest [--days N]
  python3 medai.py article <id>
  python3 medai.py search <keywords> [--days N]
  python3 medai.py render [--style card|classic|minimal|magazine] [--days N]
  python3 medai.py prefs [get|set <json>]
  python3 medai.py init                   # 初始化数据库
  python3 medai.py analyze <id>           # 深度分析（需要 DEEPSEEK_API_KEY）
  python3 medai.py summarize [--days N]   # 列出缺少摘要的文章
  python3 medai.py save-summary '<json>'  # 保存 agent 生成的摘要
"""

import sqlite3
import json
import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────────

DB_PATH = os.environ.get("MEDAI_DB", os.path.join(os.path.dirname(__file__), "data.db"))

# ── 数据库核心 ────────────────────────────────────────────────────

def get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            source_name TEXT,
            title       TEXT NOT NULL,
            content     TEXT,
            url         TEXT UNIQUE,
            published_at TEXT,
            fetched_at  TEXT DEFAULT (datetime('now')),
            tags        TEXT DEFAULT '[]',
            read_count  INTEGER DEFAULT 0,
            like_count  INTEGER DEFAULT 0,
            quality_score REAL DEFAULT 0,
            is_processed INTEGER DEFAULT 0,
            category    TEXT DEFAULT '',
            is_starred  INTEGER DEFAULT 0,
            ai_summary  TEXT DEFAULT '',
            deep_analysis TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id         INTEGER PRIMARY KEY DEFAULT 0,
            template_style  TEXT DEFAULT 'card',
            push_time       TEXT DEFAULT '08:00',
            content_format  TEXT DEFAULT 'mixed',
            comic_style     TEXT DEFAULT 'hand-drawn',
            interests       TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_articles_quality ON articles(quality_score);
        CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
    """)
    conn.commit()
    # 迁移：给已有表补字段
    try:
        conn.execute("ALTER TABLE user_preferences ADD COLUMN interests TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 字段已存在
    conn.close()
    print(json.dumps({"ok": True, "msg": "数据库初始化完成", "db": DB_PATH}))


# ── 关键词匹配 ────────────────────────────────────────────────────

_ZH_EN_MAP = {
    "影像": "imaging", "病理": "pathology", "药物": "drug",
    "基因": "genomic", "蛋白": "protein", "手术": "surgery",
    "诊断": "diagnosis", "预后": "prognosis", "治疗": "treatment",
    "心电": "ECG", "超声": "ultrasound", "CT": "CT", "MRI": "MRI",
    "大模型": "LLM", "语言模型": "language model",
    "扩散模型": "diffusion", "生成": "generation",
}


def _expand_keywords(kw_str: str) -> list:
    keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
    expanded = []
    for kw in keywords:
        expanded.append(kw)
        for zh, en in _ZH_EN_MAP.items():
            if zh in kw and en not in kw:
                expanded.append(en)
            if en.lower() in kw.lower() and zh not in kw:
                expanded.append(zh)
    return list(set(expanded))


def match_articles(keywords: str, days: int = 7) -> list:
    conn = get_conn()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        expanded = _expand_keywords(keywords)
        conditions = []
        params = []
        for kw in expanded:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])
        if not conditions:
            return []
        where = " OR ".join(conditions)
        sql = f"""SELECT id, title, content, source_name, url, published_at, quality_score
                  FROM articles WHERE ({where}) AND published_at >= ?
                  ORDER BY quality_score DESC LIMIT 10"""
        params.append(cutoff)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── 消息模板渲染 ──────────────────────────────────────────────────

CAT_EMOJI = {
    "顶刊论文": "📄", "大组动态": "🔬",
    "商业落地": "💰", "开源项目": "💻", "未分类": "📌",
}


def build_skill_message(articles: list, xhs_notes: list = None,
                        template_style: str = "card") -> str:
    date_str = datetime.now().strftime("%Y年%m月%d日")

    def _article_line(a, idx):
        title = a.get("title_zh") or a.get("title", "")
        score = a.get("score", a.get("quality_score", 0))
        source = a.get("source_name", "")
        summary = a.get("ai_summary", "")
        emoji = CAT_EMOJI.get(a.get("category", ""), "📌")
        if template_style == "minimal":
            return f"{idx}. {title} ({source} ★{score})"
        elif template_style == "magazine":
            return f"  {emoji} {title}\n     {source} · 评分 {score}\n     {summary}"
        elif template_style == "classic":
            return f"[{idx}] {title}\n    来源: {source} | 评分: {score}\n    {summary}"
        else:  # card
            stars = "⭐" * min(int(float(score) / 2), 5)
            return f"┌─ {emoji} {title}\n│  {source} {stars} {score}\n│  {summary}\n└{'─' * 30}"

    lines = []
    if template_style == "card":
        lines += [f"{'═' * 32}", f"  🏥 医疗AI每日情报  {date_str}", f"{'═' * 32}"]
    elif template_style == "magazine":
        lines += [f"━━━ 🏥 医疗AI情报 · {date_str} ━━━", ""]
    elif template_style == "classic":
        lines += [f"【医疗AI每日情报】{date_str}", "-" * 30]
    else:
        lines.append(f"📋 {date_str} 情报")

    idx = 0
    for a in articles:
        idx += 1
        lines.append(_article_line(a, idx))
    if idx == 0:
        lines.append("暂无匹配的高质量文章")

    if xhs_notes:
        if template_style == "card":
            lines += ["", "┌─────────────────────────────", "│  📕 小红书热门笔记", "├─────────────────────────────"]
        elif template_style == "magazine":
            lines += ["", "━━━ 📕 小红书精选 ━━━"]
        else:
            lines += ["", "📕 小红书热门:"]
        for note in xhs_notes[:3]:
            ntitle = note.get("title", "")
            likes = note.get("liked_count", "")
            if template_style == "card":
                lines.append(f"│  🔥 {ntitle}  ({likes}赞)")
            else:
                lines.append(f"  - {ntitle} ({likes}赞)")
        if template_style == "card":
            lines.append("└─────────────────────────────")

    lines += ["", "💬 回复论文序号，看漫画详解"]
    return "\n".join(lines)


# ── CLI 命令 ──────────────────────────────────────────────────────

def cmd_init(args):
    init_db()


def cmd_digest(args):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, COALESCE(ai_summary,'') as ai_summary, "
            "source_name, quality_score, url, COALESCE(category,'') as category "
            "FROM articles WHERE quality_score >= 5.5 AND category != '' "
            "AND fetched_at >= datetime('now', ?) ORDER BY quality_score DESC LIMIT 30",
            (f'-{args.days} days',)
        ).fetchall()
        articles = [dict(r) for r in rows]
    finally:
        conn.close()

    slim = {}
    for a in articles:
        cat = a["category"] or "未分类"
        if cat not in slim:
            slim[cat] = []
        slim[cat].append({
            "id": a["id"], "title": a["title"], "title_zh": "",
            "ai_summary": a["ai_summary"], "source_name": a["source_name"],
            "score": a["quality_score"], "url": a["url"], "category": cat,
        })
    print(json.dumps({"digest": slim, "xhs_notes": []}, ensure_ascii=False))


def cmd_article(args):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, title, content, source_name, url, quality_score, "
            "COALESCE(ai_summary,'') as ai_summary, "
            "COALESCE(deep_analysis,'') as deep_analysis, "
            "published_at, tags, category "
            "FROM articles WHERE id=?", (args.id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        print(json.dumps({"ok": False, "msg": "文章不存在"}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({"ok": True, "article": dict(row)}, ensure_ascii=False))


def cmd_search(args):
    articles = match_articles(args.keywords, days=args.days)
    safe = [
        {"id": a["id"], "title": a["title"], "source_name": a["source_name"],
         "url": a.get("url", ""), "quality_score": a.get("quality_score", 0),
         "content": (a.get("content") or "")[:200],
         "published_at": (a.get("published_at") or "")[:10]}
        for a in articles
    ]
    print(json.dumps({"articles": safe}, ensure_ascii=False))


def cmd_render(args):
    conn = get_conn()
    try:
        # 读取用户偏好（样式 + 兴趣方向）
        style = args.style
        interests = ""
        try:
            pref_row = conn.execute(
                "SELECT template_style, interests FROM user_preferences WHERE user_id=0"
            ).fetchone()
            if pref_row:
                if style == "card" and pref_row["template_style"]:
                    style = pref_row["template_style"]
                interests = pref_row["interests"] or ""
        except Exception:
            pass

        rows = conn.execute(
            "SELECT id, title, COALESCE(content,'') as content, "
            "COALESCE(ai_summary,'') as ai_summary, "
            "source_name, quality_score, url, COALESCE(category,'') as category "
            "FROM articles WHERE quality_score >= 5.5 AND category != '' "
            "AND fetched_at >= datetime('now', ?) ORDER BY quality_score DESC LIMIT 50",
            (f'-{args.days} days',)
        ).fetchall()
        articles = [dict(r) for r in rows]

        # 按兴趣方向过滤
        if interests and articles:
            expanded = _expand_keywords(interests)
            matched = [a for a in articles
                       if any(kw.lower() in (a["title"] + " " + a.get("content", "")).lower()
                              for kw in expanded)]
            if matched:
                articles = matched[:20]
            else:
                articles = articles[:20]  # 无匹配则回退全部
        else:
            articles = articles[:20]
    finally:
        conn.close()

    for a in articles:
        a.setdefault("title_zh", "")

    missing = [a["id"] for a in articles if not a.get("ai_summary")]

    msg = build_skill_message(articles, xhs_notes=[], template_style=style)
    result = {"ok": True, "message": msg, "style": style,
              "article_count": len(articles)}
    if missing:
        result["missing_summaries"] = missing
        result["hint"] = f"有 {len(missing)} 篇文章缺少摘要，请先执行 summarize 命令补全后再渲染"
    print(json.dumps(result, ensure_ascii=False))


def cmd_analyze(args):
    """获取文章信息，返回已有深度分析或待分析状态"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, title, content, source_name, COALESCE(deep_analysis,'') as deep_analysis "
            "FROM articles WHERE id=?", (args.id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        print(json.dumps({"ok": False, "msg": "文章不存在"}, ensure_ascii=False))
        sys.exit(1)

    article = dict(row)
    if article["deep_analysis"]:
        print(json.dumps({"ok": True, "analysis": article["deep_analysis"], "cached": True}, ensure_ascii=False))
    else:
        # 返回文章内容，由 agent 自己生成深度分析
        content = (article["content"] or "")[:3000]
        print(json.dumps({
            "ok": True, "cached": False,
            "id": article["id"],
            "title": article["title"],
            "source": article["source_name"],
            "content": content,
            "msg": "请根据内容生成7维度深度分析，然后用 save-analysis 保存"
        }, ensure_ascii=False))


def cmd_save_analysis(args):
    """将 agent 生成的深度分析保存到数据库"""
    conn = get_conn()
    try:
        conn.execute("UPDATE articles SET deep_analysis=? WHERE id=?", (args.analysis, args.id))
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"ok": True, "id": args.id}, ensure_ascii=False))


def cmd_summarize(args):
    """列出缺少 ai_summary 的文章（供 agent 自行生成摘要后回写）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, COALESCE(source_name,'') as source_name, quality_score "
            "FROM articles WHERE quality_score >= 5.5 AND category != '' "
            "AND (ai_summary IS NULL OR ai_summary = '') "
            "AND fetched_at >= datetime('now', ?) ORDER BY quality_score DESC LIMIT ?",
            (f'-{args.days} days', args.limit)
        ).fetchall()
        articles = [dict(r) for r in rows]
    finally:
        conn.close()

    print(json.dumps({"ok": True, "articles": articles, "count": len(articles)}, ensure_ascii=False))


def cmd_save_summary(args):
    """将 agent 生成的摘要写入数据库"""
    try:
        pairs = json.loads(args.json_str)  # [{"id": 1, "summary": "..."}]
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "msg": "JSON 格式错误，需要 [{\"id\":1,\"summary\":\"...\"}]", "example": [{"id":1,"summary":"..."}]}, ensure_ascii=False))
        sys.exit(1)

    conn = get_conn()
    try:
        updated = 0
        for item in pairs:
            aid = item.get("id")
            summary = item.get("summary", "")
            if aid and summary:
                conn.execute("UPDATE articles SET ai_summary=? WHERE id=?", (summary, aid))
                updated += 1
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"ok": True, "updated": updated}, ensure_ascii=False))


def cmd_score(args):
    """列出需要评分的文章（供 agent 评分后回写）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, COALESCE(content,'') as content, COALESCE(source_name,'') as source_name "
            "FROM articles WHERE quality_score = 0 "
            "ORDER BY fetched_at DESC LIMIT ?",
            (args.limit,)
        ).fetchall()
        articles = [{"id": r["id"], "title": r["title"],
                     "content": (r["content"] or "")[:300],
                     "source": r["source_name"]} for r in rows]
    finally:
        conn.close()
    print(json.dumps({"ok": True, "articles": articles, "count": len(articles)}, ensure_ascii=False))


def cmd_save_score(args):
    """将 agent 评分结果写入数据库"""
    try:
        pairs = json.loads(args.json_str)  # [{"id": 1, "score": 8.5, "category": "顶刊论文"}]
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "msg": "JSON 格式错误"}, ensure_ascii=False))
        sys.exit(1)

    conn = get_conn()
    try:
        updated = 0
        for item in pairs:
            aid = item.get("id")
            score = item.get("score", 0)
            category = item.get("category", "")
            if aid:
                conn.execute(
                    "UPDATE articles SET quality_score=?, category=?, is_processed=1 WHERE id=?",
                    (score, category, aid))
                updated += 1
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"ok": True, "updated": updated}, ensure_ascii=False))


def cmd_set_interests(args):
    """设置用户研究方向关键词（逗号分隔）"""
    keywords = args.keywords.strip()
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO user_preferences (user_id, interests) VALUES (0, ?)
            ON CONFLICT(user_id) DO UPDATE SET interests=excluded.interests
        """, (keywords,))
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"ok": True, "interests": keywords}, ensure_ascii=False))


def cmd_prefs(args):
    conn = get_conn()
    try:
        if args.action == "get":
            row = conn.execute("SELECT * FROM user_preferences WHERE user_id=0").fetchone()
            prefs = dict(row) if row else {"template_style": "card", "push_time": "08:00",
                                            "content_format": "mixed", "comic_style": "hand-drawn"}
            print(json.dumps({"ok": True, "preferences": prefs}, ensure_ascii=False))
        elif args.action == "set":
            p = json.loads(args.json_str)
            conn.execute("""
                INSERT INTO user_preferences (user_id, template_style, push_time, content_format, comic_style)
                VALUES (0, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    template_style=excluded.template_style,
                    push_time=excluded.push_time,
                    content_format=excluded.content_format,
                    comic_style=excluded.comic_style
            """, (p.get("template_style", "card"), p.get("push_time", "08:00"),
                  p.get("content_format", "mixed"), p.get("comic_style", "hand-drawn")))
            conn.commit()
            print(json.dumps({"ok": True}, ensure_ascii=False))
    finally:
        conn.close()


def cmd_svg2png(args):
    """将 SVG 文件转换为 PNG 图片（使用 rsvg-convert）"""
    import subprocess
    svg_path = args.svg_file
    if not os.path.exists(svg_path):
        print(json.dumps({"ok": False, "msg": f"文件不存在: {svg_path}"}, ensure_ascii=False))
        sys.exit(1)
    png_path = args.output or svg_path.rsplit(".", 1)[0] + ".png"
    try:
        result = subprocess.run(
            ["rsvg-convert", "-w", "1400", "-h", "2400", svg_path, "-o", png_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(json.dumps({"ok": False, "msg": f"转换失败: {result.stderr}"}, ensure_ascii=False))
            sys.exit(1)
        print(json.dumps({"ok": True, "png": png_path}, ensure_ascii=False))
    except FileNotFoundError:
        print(json.dumps({"ok": False, "msg": "需要安装: brew install librsvg"}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "msg": f"转换失败: {e}"}, ensure_ascii=False))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="医疗AI情报 CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="初始化数据库")

    p = sub.add_parser("digest", help="获取精简 digest")
    p.add_argument("--days", type=int, default=1)

    p = sub.add_parser("article", help="获取单篇文章")
    p.add_argument("id", type=int)

    p = sub.add_parser("search", help="关键词搜索")
    p.add_argument("keywords")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("render", help="渲染情报消息")
    p.add_argument("--style", default="card", choices=["card", "classic", "minimal", "magazine"])
    p.add_argument("--days", type=int, default=1)

    p = sub.add_parser("analyze", help="深度分析（需 DEEPSEEK_API_KEY）")
    p.add_argument("id", type=int)

    p = sub.add_parser("summarize", help="列出缺少摘要的文章")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("save-summary", help="保存 agent 生成的摘要")
    p.add_argument("json_str", help='JSON 数组，如 [{"id":1,"summary":"..."}]')

    p = sub.add_parser("save-analysis", help="保存 agent 生成的深度分析")
    p.add_argument("id", type=int)
    p.add_argument("analysis", help="深度分析文本")

    p = sub.add_parser("score", help="列出需要评分的文章")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("save-score", help="保存 agent 评分结果")
    p.add_argument("json_str", help='JSON 数组，如 [{"id":1,"score":8.5,"category":"顶刊论文"}]')

    p = sub.add_parser("set-interests", help="设置研究方向关键词")
    p.add_argument("keywords", help="逗号分隔的关键词，如 影像诊断,大模型,病理")

    p = sub.add_parser("prefs", help="用户偏好")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("json_str", nargs="?", default="{}")

    p = sub.add_parser("svg2png", help="SVG 转 PNG 图片")
    p.add_argument("svg_file", help="SVG 文件路径")
    p.add_argument("--output", "-o", help="输出 PNG 路径")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"init": cmd_init, "digest": cmd_digest, "article": cmd_article,
     "search": cmd_search, "render": cmd_render, "analyze": cmd_analyze,
     "prefs": cmd_prefs, "svg2png": cmd_svg2png,
     "summarize": cmd_summarize, "save-summary": cmd_save_summary,
     "save-analysis": cmd_save_analysis,
     "score": cmd_score, "save-score": cmd_save_score,
     "set-interests": cmd_set_interests}[args.command](args)


if __name__ == "__main__":
    main()
