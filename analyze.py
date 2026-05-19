"""
AI分析模块（使用DeepSeek API）
1. 对外部文章打质量分（0-10），筛选值得参考的内容
2. 基于语料库分析，推荐标题候选
"""

import os
import re
import json
from openai import OpenAI
from db import (get_top_posts, get_recent_articles,
                update_quality_score, save_title_suggestions, stats)


def get_client():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("请设置环境变量 DEEPSEEK_API_KEY")
    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )


def chat(client, prompt: str, max_tokens: int = 2000) -> str:
    resp = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.choices[0].message.content


_TOP_JOURNAL_KEYS = ("arxiv", "nature", "lancet", "nejm", "jama",
                     "ieee", "npj", "medical image")
_COMMERCIAL_KEYS = ("stat news", "mit technology", "healthcare it",
                    "techcrunch", "venturebeat")


def _classify_by_source(source: str, source_name: str) -> str:
    """根据来源启发式归桶，不调用 LLM。"""
    if (source or "").lower() == "github":
        return "开源项目"
    name = (source_name or "").lower()
    if any(k in name for k in _TOP_JOURNAL_KEYS):
        return "顶刊论文"
    if any(k in name for k in _COMMERCIAL_KEYS):
        return "商业落地"
    return "大组动态"


def auto_classify_by_source(db_path: str = "corpus/corpus.db") -> int:
    """对所有 category 为空的文章按 source_name 启发式补分类，返回更新数。"""
    from db import get_conn
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, source, source_name FROM articles "
            "WHERE category IS NULL OR category = ''"
        ).fetchall()
        updated = 0
        for r in rows:
            cat = _classify_by_source(r["source"], r["source_name"])
            conn.execute("UPDATE articles SET category=? WHERE id=?", (cat, r["id"]))
            updated += 1
        conn.commit()
        return updated
    finally:
        conn.close()


_STOPWORDS = {
    'a', 'an', 'the', 'for', 'with', 'using', 'via', 'based', 'on', 'in',
    'of', 'by', 'from', 'to', 'and', 'or', 'is', 'are', 'we', 'our',
    'this', 'that', 'its', 'as', 'be', 'at', 'it', 'into', 'over',
    'under', 'new', 'towards', 'toward', 'through', 'between', 'among',
    'across', 'large', 'deep', 'learning', 'model', 'models', 'method',
    'approach', 'framework', 'network', 'networks', 'system', 'systems',
}

_ARXIV_CATEGORY_MAP = {
    'cs.AI':  'AI',
    'cs.CV':  'Computer Vision',
    'cs.LG':  'Machine Learning',
    'cs.CL':  'NLP',
    'cs.RO':  'Robotics',
    'cs.NE':  'Neural Networks',
    'eess.IV': 'Image & Video',
    'eess.SP': 'Signal Processing',
    'q-bio.QM': 'Quantitative Biology',
    'q-bio.GN': 'Genomics',
    'q-bio.NC': 'Neuroscience',
    'stat.ML': 'Statistics & ML',
    'physics.med-ph': 'Medical Physics',
}


def _build_arxiv_tags(source_name: str, title: str) -> str:
    """从 source_name 提取 arXiv 分类码，从标题提取关键词，返回 JSON 字符串。"""
    m = re.search(r'arXiv\s+([a-z\-]+\.[A-Z]+)', source_name)
    category_code = m.group(1) if m else ''
    category_label = _ARXIV_CATEGORY_MAP.get(category_code, category_code)
    words = re.findall(r'[A-Za-z][A-Za-z0-9\-]*', title)
    keywords = [w for w in words
                if len(w) >= 4 and w.lower() not in _STOPWORDS][:6]
    tags = ([category_label] if category_label else []) + keywords
    return json.dumps(tags, ensure_ascii=False)


def tag_and_skip_arxiv(db_path: str = "corpus/corpus.db") -> int:
    """对积压的 arXiv 文章打领域标签，标记已处理（跳过 LLM 质量评分）。
    新入库的 arXiv 文章已在 rss.py 中处理，此函数用于一次性清理历史积压。
    """
    from db import get_conn
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT id, source_name, title FROM articles
        WHERE is_processed = 0 AND source_name LIKE 'arXiv%'
    """).fetchall()

    if not rows:
        conn.close()
        return 0

    for r in rows:
        domain_tags = _build_arxiv_tags(r['source_name'], r['title'])
        conn.execute("""
            UPDATE articles
            SET is_processed = 1, quality_score = 7.0, domain_tags = ?
            WHERE id = ?
        """, (domain_tags, r['id']))

    conn.commit()
    conn.close()
    return len(rows)


def score_articles(limit: int = 20, db_path: str = "corpus/corpus.db"):
    """对未打分的文章进行多维度质量评分（arXiv 直接打标签跳过 LLM）"""
    # 先清理 arXiv 积压，不花 token
    n_arxiv = tag_and_skip_arxiv(db_path=db_path)
    if n_arxiv:
        print(f"✓ arXiv 文章打标签并跳过评分：{n_arxiv} 篇")

    from db import get_conn
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT id, title, content, source_name FROM articles
        WHERE is_processed = 0
        ORDER BY fetched_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    if not rows:
        print("没有待评分的文章")
    else:
        client = get_client()
        BATCH = 20
        all_scored = 0
        for batch_start in range(0, len(rows), BATCH):
            batch = rows[batch_start:batch_start + BATCH]
            articles_text = "\n\n".join([
                f"ID:{r['id']} | 来源:{r['source_name'] or '未知'} | 标题:{r['title']}\n"
                f"内容:{(r['content'] or '').strip()[:400]}"
                for r in batch
            ])
            print(f"正在评分第 {batch_start+1}-{batch_start+len(batch)} 篇...")
            _do_score_batch(client, articles_text, batch, db_path)
            all_scored += len(batch)
        print(f"\n✓ 共完成 {all_scored} 篇评分")

    n = auto_classify_by_source(db_path=db_path)
    if n:
        print(f"✓ 启发式补分类 {n} 篇（按 source_name 归桶）")
    return


def _do_score_batch(client, articles_text, rows, db_path):
    prompt = f"""你是一个学术内容质量评估助手。对以下文章从四个维度打分（每项1-10分）：

1. density（信息密度）：有无具体数据/实验结果/明确结论。泛泛而谈、只有观点无依据 → 低分
2. novelty（新颖性）：是否原创研究或首次报道。转述已有内容、综述/新闻稿 → 中低分
3. credibility（来源可信度）：结合"来源"字段判断。顶刊/arXiv/知名机构/知名媒体 → 高分；来源未知/营销号 → 低分
4. completeness（内容完整度）：摘要是否足以判断内容价值。内容极短（<50字）或缺失 → 低分

overall 按以下权重计算（自行计算，保留一位小数）：
  overall = density×0.35 + novelty×0.30 + credibility×0.25 + completeness×0.10

只输出JSON，不要其他任何文字：
{{"scores": [
  {{"id": 1, "scores": {{"density": 8, "novelty": 7, "credibility": 9, "completeness": 6}}, "overall": 7.6, "reason": "理由（15字以内）"}},
  ...
]}}

文章列表（格式：ID | 来源 | 标题 / 内容摘要）：
{articles_text}"""

    try:
        text = chat(client, prompt, max_tokens=2500)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        for item in result["scores"]:
            detail = json.dumps(item.get("scores", {}), ensure_ascii=False)
            overall = float(item.get("overall", 0))
            update_quality_score(item["id"], overall, score_detail=detail, db_path=db_path)
            dim = item.get("scores", {})
            print(f"  [{item['id']}] {overall:.1f}分 "
                  f"(密度{dim.get('density','?')} 新颖{dim.get('novelty','?')} "
                  f"可信{dim.get('credibility','?')} 完整{dim.get('completeness','?')}) "
                  f"- {item.get('reason','')}")
        print(f"\n✓ 已完成 {len(result['scores'])} 篇评分")
    except Exception as e:
        print(f"解析评分结果失败: {e}")


def classify_and_digest(topic: str = None, days: int = 2,
                        db_path: str = "corpus/corpus.db"):
    """
    每日情报摘要：抓取近N天高分文章，分类为
    - 顶刊论文
    - 大组/机构动态
    - 商业落地
    并每类推荐3-5篇，附一句话摘要
    """
    from db import get_conn

    conn = get_conn(db_path)
    # GitHub项目单独处理，不参与AI分类
    conn.execute("""
        UPDATE articles SET category='开源项目'
        WHERE source='github' AND (category IS NULL OR category='')
    """)
    conn.commit()

    rows = conn.execute("""
        SELECT id, title, content, source_name, url, tags, quality_score
        FROM articles
        WHERE fetched_at >= datetime('now', ?)
          AND quality_score >= 5.5
          AND source != 'github'
        ORDER BY quality_score DESC
        LIMIT 60
    """, (f'-{days} days',)).fetchall()
    conn.close()

    articles = [dict(r) for r in rows]

    if not articles:
        print("⚠ 近期没有高质量文章，请先运行 fetch 和 score")
        return

    client = get_client()
    topic_hint = f"\n用户今天关注的方向：{topic}" if topic else ""

    articles_text = "\n\n".join([
        f"ID:{a['id']} 来源:{a['source_name']} 评分:{a['quality_score']}\n"
        f"标题:{a['title']}\n"
        f"内容:{(a['content'] or '').strip()}"
        for a in articles
    ])

    print(f"正在分析 {len(articles)} 篇文章，生成今日情报摘要...\n")

    prompt = f"""你是一个医疗AI领域的情报分析师。以下是今天抓取的文章列表，请帮我完成两件事：

1. 将每篇文章分类到以下四类之一：
   - "顶刊论文"：来自Nature/Lancet/NEJM/JAMA/arXiv等学术期刊的研究论文
   - "大组动态"：顶级高校、研究机构（斯坦福、MIT、Google DeepMind等）发布的成果或观点
   - "商业落地"：企业产品发布、医院部署案例、融资并购、监管审批等产业新闻
   - "开源项目"：来自GitHub的开源项目，包含star数和项目描述

2. 每类选出最值得关注的3-5篇，给出一句话中文摘要（25字以内，说清楚"谁做了什么，结论是什么"）
{topic_hint}

只输出JSON格式：
{{
  "顶刊论文": [
    {{"id": 1, "title": "原标题", "summary": "一句话摘要", "why": "为什么值得关注"}},
    ...
  ],
  "大组动态": [...],
  "商业落地": [...]
}}

文章列表：
{articles_text}"""

    try:
        text = chat(client, prompt, max_tokens=4000)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)

        # 更新数据库中的分类
        conn = get_conn(db_path)
        for category, items in result.items():
            for item in items:
                conn.execute("UPDATE articles SET category=? WHERE id=?",
                             (category, item["id"]))
        conn.commit()
        conn.close()

        # 格式化输出
        print("=" * 60)
        print(f"今日医疗AI情报摘要")
        print("=" * 60)

        icons = {"顶刊论文": "📄", "大组动态": "🏛", "商业落地": "🏢"}
        for category in ["顶刊论文", "大组动态", "商业落地"]:
            items = result.get(category, [])
            if not items:
                continue
            print(f"\n{icons.get(category, '•')} 【{category}】（{len(items)}篇）")
            print("-" * 40)
            for i, item in enumerate(items, 1):
                print(f"{i}. {item['title']}")
                print(f"   摘要: {item['summary']}")
                print(f"   亮点: {item['why']}")

        print("\n" + "=" * 60)
        total = sum(len(v) for v in result.values())
        print(f"共推荐 {total} 篇，运行 'python main.py titles' 可基于以上内容生成标题")

    except Exception as e:
        print(f"解析失败: {e}")


def recommend_titles(topic: str = None, db_path: str = "corpus/corpus.db") -> int:
    """基于语料库分析，推荐10个标题候选"""
    top_posts = get_top_posts(limit=15, db_path=db_path)
    recent = get_recent_articles(days=7, min_quality=6.0, limit=20, db_path=db_path)

    if not top_posts:
        print("⚠ 语料库中还没有自己的历史文章，建议先用 import-post 录入")

    seed_titles = "\n".join([
        f"- 【互动{p['engagement_score']:.0f}】{p['title']}"
        for p in top_posts
    ]) or "（暂无数据）"

    hot_titles = "\n".join([
        f"- 【{a['quality_score']:.1f}分】{a['title']} （{a['source_name']}）"
        for a in recent[:15]
    ]) or "（暂无数据）"

    topic_hint = f"\n今天想聚焦的话题方向：{topic}" if topic else ""

    client = get_client()
    print("正在分析语料库，生成标题建议...")

    prompt = f"""你是一个微信公众号内容策略师。

## 历史高互动文章（按互动分排序）：
{seed_titles}

## 最近7天热点文章：
{hot_titles}
{topic_hint}

## 任务
1. 分析历史文章有哪些共同的标题特征（结构、视角、切入点）
2. 结合近期热点，生成10个新标题候选

## 标题规范
- 有具体信息量，避免空洞表述
- 微信公众号风格：清晰、有信息价值
- 禁用套话：深度剖析、全面解读、重磅、颠覆等

## 输出格式（JSON）：
{{
  "analysis": "标题模式分析（100字内）",
  "titles": [
    {{"title": "标题1", "angle": "切入角度", "hook": "吸引点"}},
    ...
  ]
}}"""

    try:
        text = chat(client, prompt, max_tokens=3000)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        source_ids = [a["id"] for a in recent[:15]]
        suggestion_id = save_title_suggestions(
            topic=topic or "综合热点",
            titles=[t["title"] for t in result["titles"]],
            analysis=result["analysis"],
            source_ids=source_ids,
            db_path=db_path
        )

        print(f"\n=== 标题模式分析 ===")
        print(result["analysis"])
        print(f"\n=== 10个标题候选（建议ID={suggestion_id}）===")
        for i, t in enumerate(result["titles"], 1):
            print(f"\n{i}. {t['title']}")
            print(f"   角度: {t['angle']} | 钩子: {t['hook']}")

        return suggestion_id
    except Exception as e:
        print(f"解析结果失败: {e}")
        return -1


def expand_research_direction(direction: str, api_key: str = "") -> list:
    """将自然语言研究方向展开为 10-15 个英文检索关键词，覆盖核心技术、相关方法和上位概念。"""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key or not direction.strip():
        return []
    client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
    prompt = f"""将以下研究方向展开为10-15个英文学术检索关键词，覆盖核心技术、相关方法、应用场景和上位概念。
研究方向：{direction}
只输出JSON数组，例如：["keyword1", "keyword2", ...]，不要任何说明文字。"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat", timeout=30, max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception as e:
        print(f"⚠ 关键词展开失败: {e}")
        return []


def score_articles_for_profile(profile_id: int, direction: str,
                                expanded_keywords: list = None,
                                api_key: str = "", limit: int = 200,
                                db_path: str = "corpus/corpus.db",
                                days: int = 5) -> int:
    """为单个研究档案对尚未个性化评分的文章打分，返回已评分篇数。

    策略：用 expanded_keywords 在 title/content 上做 LIKE 预筛（绕过全局评分瓶颈），
    命中关键词的才送 LLM 精评。无关键词时退化为依赖 quality_score 的旧逻辑作兜底。
    """
    from db import get_conn, save_user_relevance

    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return 0

    keywords = [k.strip() for k in (expanded_keywords or []) if k and k.strip()][:15]

    conn = get_conn(db_path)
    if keywords:
        kw_clause = " OR ".join(
            ["(LOWER(a.title) LIKE ? OR LOWER(a.content) LIKE ?)" for _ in keywords]
        )
        params = []
        for kw in keywords:
            pat = f"%{kw.lower()}%"
            params.extend([pat, pat])
        params.extend([profile_id, f"-{days} days", limit])
        rows = conn.execute(f"""
            SELECT a.id, a.title, a.content
            FROM articles a
            WHERE ({kw_clause})
              AND a.id NOT IN (
                  SELECT article_id FROM user_article_relevance WHERE profile_id=?
              )
              AND a.fetched_at >= datetime('now', ?)
            ORDER BY a.fetched_at DESC
            LIMIT ?
        """, params).fetchall()
    else:
        rows = conn.execute("""
            SELECT a.id, a.title, a.content
            FROM articles a
            WHERE a.quality_score >= 5.0
              AND a.id NOT IN (
                  SELECT article_id FROM user_article_relevance WHERE profile_id=?
              )
            ORDER BY a.fetched_at DESC
            LIMIT ?
        """, (profile_id, limit)).fetchall()
    conn.close()

    articles = [dict(r) for r in rows]
    if not articles:
        return 0

    client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
    kw_hint = f"\n相关关键词：{', '.join((expanded_keywords or [])[:10])}" if expanded_keywords else ""

    BATCH = 25
    scored = 0
    for i in range(0, len(articles), BATCH):
        batch = articles[i:i + BATCH]
        lines = "\n".join(
            f'{a["id"]}|{a["title"]}|{(a.get("content") or "")[:150]}'
            for a in batch
        )
        prompt = f"""你是科研助手。根据用户研究方向，对以下论文逐篇给出个人相关性评分。

用户研究方向：{direction}{kw_hint}

候选论文（ID|标题|摘要片段）：
{lines}

评分标准（1-10）：9-10核心相关，7-8高度相关，4-6部分相关，1-3关联较弱。
每行输出一篇：ID|评分|理由（15字以内）
只输出数据行，不要任何其他文字。"""
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat", timeout=60, max_tokens=800,
                messages=[{"role": "user", "content": prompt}]
            )
            for line in resp.choices[0].message.content.strip().split("\n"):
                if "|" not in line:
                    continue
                parts = line.split("|", 2)
                try:
                    aid = int(parts[0].strip())
                    score = float(parts[1].strip())
                    reason = parts[2].strip() if len(parts) > 2 else ""
                    save_user_relevance(profile_id, aid, score, reason, db_path=db_path)
                    scored += 1
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            print(f"  ⚠ 批量评分失败 (batch {i}): {e}")

    return scored


def score_articles_for_all_users(db_path: str = "corpus/corpus.db"):
    """为所有活跃研究档案执行个性化评分。"""
    import base64
    from db import get_conn, get_active_subscriptions

    subs = get_active_subscriptions(db_path=db_path)
    email_to_key = {}
    for sub in subs:
        if sub.get("api_key"):
            try:
                email_to_key[sub["email"]] = base64.b64decode(sub["api_key"]).decode()
            except Exception:
                pass

    conn = get_conn(db_path)
    profiles = [dict(r) for r in conn.execute(
        "SELECT * FROM user_research_profiles WHERE active=1"
    ).fetchall()]
    conn.close()

    if not profiles:
        print("没有活跃的研究档案")
        return

    print(f"共 {len(profiles)} 个研究档案需要个性化评分...")
    total = 0
    for p in profiles:
        api_key = email_to_key.get(p["sub_email"], "")
        expanded = json.loads(p.get("expanded_keywords") or "[]")
        print(f"\n▶ [{p['name']}] {p['sub_email']} ({p['direction'][:40]}...)")
        n = score_articles_for_profile(
            p["id"], p["direction"], expanded, api_key, db_path=db_path
        )
        print(f"  → 完成 {n} 篇")
        total += n

    print(f"\n✓ 个性化评分完成，共 {total} 篇")


def analyze_trends(db_path: str = "corpus/corpus.db",
                   recent_days: int = 7,
                   baseline_days: int = 30,
                   top_n: int = 15) -> list:
    """
    统计近 recent_days 天 vs 之前 (baseline_days - recent_days) 天的关键词频率，
    计算增长率，返回上升最快的研究热点。纯统计，不消耗 token。
    """
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone
    from db import get_conn

    _DOMAIN_LABELS = {
        'NLP', 'AI', 'Computer Vision', 'Machine Learning', 'Robotics',
        'Neural Networks', 'Image & Video', 'Signal Processing',
        'Statistics & ML', 'Medical Physics', 'Genomics', 'Neuroscience',
        'Quantitative Biology', 'Information Retrieval',
        'Human-Computer Interaction',
    }
    _SKIP_WORDS = {
        'large', 'model', 'models', 'using', 'based', 'towards', 'novel',
        'efficient', 'study', 'analysis', 'approach', 'method', 'framework',
        'system', 'network', 'learning', 'deep', 'paper', 'benchmark',
        'evaluation', 'survey', 'review', 'first', 'multi', 'cross',
        # 过于宽泛的词
        'world', 'generation', 'generative', 'representation', 'understanding',
        'reasoning', 'training', 'language', 'vision', 'visual', 'image',
        'video', 'audio', 'text', 'tasks', 'data', 'dataset', 'results',
        'performance', 'knowledge', 'information', 'human', 'automated',
    }

    def _keywords(article: dict) -> list:
        """从 domain_tags 取关键词，没有则从标题提取。"""
        tags = []
        try:
            tags = json.loads(article.get('domain_tags') or '[]')
        except Exception:
            pass
        domain = tags[0] if tags else ''
        kws = [t for t in tags[1:]
               if len(t) >= 5
               and t.lower() not in _SKIP_WORDS
               and t not in _DOMAIN_LABELS]
        # 没有 domain_tags 时从标题提取关键词
        if not kws:
            title = article.get('title') or ''
            words = re.findall(r'[A-Za-z][A-Za-z0-9\-]{3,}', title)
            kws = [w for w in words
                   if w.lower() not in _SKIP_WORDS
                   and w not in _DOMAIN_LABELS][:6]
        return domain, kws

    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT id, title, domain_tags, quality_score, url, source_name, fetched_at
        FROM articles
        WHERE fetched_at >= datetime('now', ?)
          AND typeof(quality_score) IN ('real','integer')
          AND quality_score >= 5.0
        ORDER BY fetched_at DESC
    """, (f"-{baseline_days} days",)).fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=recent_days)
    baseline_start = now - timedelta(days=baseline_days)

    recent_kw: dict = defaultdict(lambda: {'count': 0, 'score': 0.0, 'articles': []})
    baseline_kw: dict = defaultdict(int)
    recent_domain: dict = defaultdict(lambda: {'count': 0, 'articles': []})
    baseline_domain: dict = defaultdict(int)

    for r in rows:
        raw = r['fetched_at'] or ''
        try:
            t = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        domain, kws = _keywords(dict(r))
        a = dict(r)
        score = float(r['quality_score']) if r['quality_score'] else 0.0

        if t >= cutoff:
            if domain:
                recent_domain[domain]['count'] += 1
                if len(recent_domain[domain]['articles']) < 3:
                    recent_domain[domain]['articles'].append(a)
            for kw in kws:
                recent_kw[kw]['count'] += 1
                recent_kw[kw]['score'] += score
                if len(recent_kw[kw]['articles']) < 3:
                    recent_kw[kw]['articles'].append(a)
        elif t >= baseline_start:
            if domain:
                baseline_domain[domain] += 1
            for kw in kws:
                baseline_kw[kw] += 1

    baseline_days_actual = baseline_days - recent_days
    scale = recent_days / max(baseline_days_actual, 1)

    def _trend_score(recent_count, baseline_count, avg_q):
        baseline_rate = baseline_count * scale
        growth = (recent_count - baseline_rate) / (baseline_rate + 2)
        return round(growth * (1 + avg_q / 10), 3)

    results = []

    # 关键词级热点（不单独展示大类 domain，太宽泛）
    for kw, data in recent_kw.items():
        cnt = data['count']
        if cnt < 3:
            continue
        avg_q = data['score'] / cnt
        ts = _trend_score(cnt, baseline_kw.get(kw, 0), avg_q)
        results.append({
            'type': 'keyword',
            'keyword': kw,
            'count': cnt,
            'trend_score': ts,
            'articles': data['articles'],
        })

    results.sort(key=lambda x: x['trend_score'], reverse=True)

    # 去重：同关键词只保留最高分那条
    seen = set()
    deduped = []
    for r in results:
        k = r['keyword'].lower()
        if k not in seen:
            seen.add(k)
            deduped.append(r)

    return deduped[:top_n]


def generate_trend_descriptions(db_path: str = "corpus/corpus.db") -> list:
    """
    对 analyze_trends() 的结果调用 DeepSeek 生成中文描述，
    结果存入 app_state['trend_descriptions']，有效期24小时。
    返回带描述的 trends 列表。
    """
    import time
    from db import get_conn

    # 读缓存
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT value FROM app_state WHERE key='trend_descriptions'"
    ).fetchone()
    conn.close()

    if row and row["value"]:
        try:
            cached = json.loads(row["value"])
            if time.time() - cached.get("ts", 0) < 86400:
                return cached["trends"]
        except Exception:
            pass

    # 重新计算热点
    trends = analyze_trends(db_path, top_n=10)
    if not trends:
        return []

    # 构建 prompt
    blocks = []
    for i, t in enumerate(trends, 1):
        titles = "\n".join(
            f"    - {a['title']}" for a in t.get("articles", [])[:3]
        )
        blocks.append(f"{i}. 关键词：{t['keyword']}（{t['count']}篇）\n{titles}")
    prompt = f"""你是医疗AI领域的科研助手。以下是近7天上升最快的研究热点关键词及代表论文标题。

请为每个热点写一段简短描述（2句话，50字以内），说明：
1. 这个方向最近在研究什么
2. 为什么值得关注

只输出JSON，格式：
{{"descriptions": [{{"keyword": "关键词", "desc": "描述文字"}}]}}

热点列表：
{chr(10).join(blocks)}"""

    try:
        client = get_client()
        text = chat(client, prompt, max_tokens=1500)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        desc_map = {d["keyword"]: d["desc"] for d in result.get("descriptions", [])}
        for t in trends:
            t["desc"] = desc_map.get(t["keyword"], "")
    except Exception as e:
        print(f"⚠ 热点描述生成失败: {e}")
        for t in trends:
            t["desc"] = ""

    # 存缓存
    payload = json.dumps({"ts": time.time(), "trends": trends}, ensure_ascii=False)
    conn = get_conn(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO app_state (key, value) VALUES ('trend_descriptions', ?)",
        (payload,)
    )
    conn.commit()
    conn.close()

    return trends


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI分析模块")
    parser.add_argument("--score", action="store_true", help="对未评分文章打分")
    parser.add_argument("--titles", action="store_true", help="推荐标题候选")
    parser.add_argument("--topic", type=str, help="指定今日话题方向")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if args.score:
        score_articles(limit=args.limit)
    elif args.titles:
        recommend_titles(topic=args.topic)
    else:
        s = stats()
        print("=== 语料库统计 ===")
        for k, v in s.items():
            print(f"  {k}: {v} 条")
