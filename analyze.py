"""
AI分析模块（使用DeepSeek API）
1. 对外部文章打质量分（0-10），筛选值得参考的内容
2. 基于语料库分析，推荐标题候选
"""

import os
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


def score_articles(limit: int = 20, db_path: str = "corpus/corpus.db"):
    """对未打分的外部文章进行质量评分，重点筛选对公众号选题有参考价值的内容"""
    from db import get_conn
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT id, title, content FROM articles
        WHERE is_processed = 0
        ORDER BY fetched_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    if not rows:
        print("没有待评分的文章")
        return

    client = get_client()
    # 每批最多15篇，避免JSON截断
    BATCH = 15
    all_scored = 0
    for batch_start in range(0, len(rows), BATCH):
        batch = rows[batch_start:batch_start + BATCH]
        articles_text = "\n\n".join([
            f"ID:{r['id']} 标题:{r['title']}\n内容:{(r['content'] or '').strip()}"
            for r in batch
        ])
        print(f"正在评分第 {batch_start+1}-{batch_start+len(batch)} 篇...")
        _do_score_batch(client, articles_text, batch, db_path)
        all_scored += len(batch)
    print(f"\n✓ 共完成 {all_scored} 篇评分")
    return


def _do_score_batch(client, articles_text, rows, db_path):
    prompt = f"""你是一个内容筛选助手。帮我评估以下文章对"医疗+AI"方向微信公众号内容创作的参考价值。

核心筛选原则：文章必须同时涉及"医疗/健康/生命科学"AND"人工智能/机器学习/深度学习"，缺一不可。

评分标准（0-10分）：
- 8-10分：明确的医疗AI应用案例、临床验证、产品落地或行业洞察，信息具体
- 5-7分：有医疗AI相关性，但需要较多加工
- 0-3分：以下任一情况直接给0-3分：
  * 纯医学研究（无AI成分，如药物试验、纯临床研究）
  * 纯AI研究（无医疗场景，如通用LLM、推荐系统）
  * 政治/政策/非技术内容
  * 信息量太低

请对每篇文章打分，只输出JSON，格式：
{{"scores": [{{"id": 1, "score": 7.5, "reason": "简短理由"}}, ...]}}

文章列表：
{articles_text}"""

    try:
        text = chat(client, prompt, max_tokens=2000)
        # DeepSeek有时在JSON外包一层markdown代码块，去掉
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        for item in result["scores"]:
            update_quality_score(item["id"], item["score"], db_path=db_path)
            print(f"  文章{item['id']}: {item['score']}分 - {item['reason']}")
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
          AND quality_score >= 6.5
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
