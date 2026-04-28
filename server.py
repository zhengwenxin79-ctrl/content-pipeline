"""
本地Web服务器 - 医疗AI每日情报
运行: python server.py
访问: http://localhost:8888
"""

import os
import json
import secrets
import threading
import subprocess
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from db import get_conn, stats, save_session, load_session, delete_session


def _make_session(user: dict) -> str:
    token = secrets.token_hex(32)
    save_session(token, user["id"], user["email"], days=30, db_path=DB_PATH)
    return token


def _get_session(handler):
    cookie = handler.headers.get("Cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("session="):
            token = part[len("session="):]
            return load_session(token, db_path=DB_PATH)
    return None

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_MODELS_BASE_URL = "https://models.inference.ai.azure.com"
DB_PATH = os.environ.get("DB_PATH", "corpus/corpus.db")
XHS_COOKIE = os.environ.get("XHS_COOKIE", "")


def fetch_user_feeds_once():
    """抓取所有用户的自定义 RSS 源，保存新文章，然后 6 小时后再次执行"""
    try:
        import feedparser
        from db import get_all_user_feeds, save_user_article, update_feed_fetched
        feeds = get_all_user_feeds(DB_PATH)
        for feed in feeds:
            try:
                parsed = feedparser.parse(feed["url"])
                for entry in parsed.entries[:30]:
                    title = entry.get("title", "").strip()
                    url = entry.get("link", "").strip()
                    content = entry.get("summary", "") or entry.get("content", [{}])[0].get("value", "")
                    published = entry.get("published", "") or entry.get("updated", "")
                    if title and url:
                        save_user_article(
                            user_id=feed["user_id"],
                            feed_id=feed["id"],
                            title=title,
                            url=url,
                            content=content[:2000] if content else None,
                            published_at=published,
                            db_path=DB_PATH
                        )
                update_feed_fetched(feed["id"], DB_PATH)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        t = threading.Timer(6 * 3600, fetch_user_feeds_once)
        t.daemon = True
        t.start()

# 全局任务状态
task_status = {"running": False, "log": [], "step": ""}

# 写作推荐缓存（避免每次刷新都调用API）
_recommend_cache = {"data": None, "digest_hash": ""}

# 公众号文章固定尾部——关注引导
ARTICLE_FOOTER = """

---

**关注这个公众号，你会持续收到：**

✅ **医疗AI前沿论文**精准拆解，只讲对转行和从业有用的部分

✅ **AI产品经理**真实学习路径、竞品分析、避坑指南

✅ **从生物科研到产品**的转行全记录，真实不美化

✅ **医疗AI行业动态**，政策、融资、落地案例一线速递

> 写给想用AI转行、或在医疗/生物领域做产品的你

点赞、在看、留言，都是对我创作最好的支持 👋"""

# 文章生成任务池 {task_id: {"status": "running"|"done"|"error", "result": ...}}
import uuid
_gen_tasks = {}

# 公众号分析缓存
_wechat_analysis_cache = {}


def get_wechat_articles() -> list:
    """获取所有公众号文章"""
    conn = get_conn(DB_PATH)
    rows = conn.execute("""
        SELECT id, title, content, source_name, url, quality_score, fetched_at
        FROM articles
        WHERE source = 'wechat_wewe'
        ORDER BY fetched_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def analyze_wechat_article(article_id: int) -> dict:
    """用DeepSeek深度分析一篇公众号文章的亮点和可学习之处"""
    if article_id in _wechat_analysis_cache:
        return _wechat_analysis_cache[article_id]

    conn = get_conn(DB_PATH)
    row = conn.execute(
        "SELECT title, content FROM articles WHERE id=?", (article_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"error": "文章不存在"}

    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    content_preview = (row["content"] or "")[:2000]

    prompt = f"""你是一个微信公众号内容分析师，专注于医疗AI方向。请深度分析以下这篇文章为什么能获得较高关注，从编辑和内容创作角度提炼可学习的经验。

文章标题：{row["title"]}

文章内容（节选）：
{content_preview}

请从以下维度分析，输出JSON：
{{
  "why_good": "这篇文章整体为什么好（2-3句话）",
  "pain_points": [
    {{"point": "踩中的痛点名称", "explanation": "具体说明这个痛点是什么，为什么读者会关心"}}
  ],
  "title_analysis": "标题的亮点分析：用了什么技巧，为什么能吸引点击",
  "structure_highlights": "文章结构上值得学习的地方",
  "writing_techniques": ["写作技巧1", "写作技巧2", "写作技巧3"],
  "learnable": "最值得你模仿学习的一点，具体到操作层面"
}}"""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
        timeout=90,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        _wechat_analysis_cache[article_id] = result
        return result
    except Exception as e:
        return {"error": str(e)}




def _extract_key_points(client, articles_text: str) -> str:
    """素材萃取：在生成初稿前，先把原始素材压缩成结构化要点。
    这一步解决"模型逐篇复述素材"的问题，让写作聚焦于跨材料的核心洞察。"""
    extract_prompt = f"""你是一个医疗AI领域的信息分析师。请从以下多篇素材中提取结构化要点，用于后续公众号文章写作。

要求：
1. 跨材料找出一个最值得写的核心发现/趋势/事件（一句话概括）
2. 提取 3-5 个关键事实（必须有具体数字、机构名、产品名等可核实信息）
3. 识别一个"读者会关心的决策点"（如果你是医疗AI产品经理，这意味着什么？）
4. 标注哪些信息来自哪篇素材（用【文章N】标注）

素材：
{articles_text}

输出格式：
【核心发现】一句话

【关键事实】
1. xxx（来源：【文章N】）
2. xxx（来源：【文章N】）
...

【决策参考】
对目标读者（医疗AI产品经理/医院信息化负责人）的具体意义

【写作建议】
推荐的文章切入角度（一句话）"""

    try:
        r = client.chat.completions.create(
            model="deepseek-chat", timeout=60, max_tokens=1200,
            temperature=0.3,
            messages=[{"role": "user", "content": extract_prompt}]
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠ 素材萃取失败，使用原始素材: {e}")
        return ""


def _build_draft_prompt(articles_text: str, extracted_points: str = "") -> tuple:
    """返回 (system_message, user_message) 元组，拆分角色定位和写作任务。"""
    system_msg = """你是医疗AI公众号主编，对标「Medical AI」「丁香园」「量子位医疗」等高阅读量账号的写作水准。
目标读者：医疗AI产品经理/产品总监、医疗科技公司决策层（创始人/BD）、医院信息化负责人——
他们看文章是为了做决定，不是为了学知识。让他们觉得"这个信息今天就能用上"，才会转发。
写作视角：比读者早一步看清落地坑的产品人，帮读者把事情想清楚，不是旁观者综述。

## 你的禁用词清单（遇到就替换为更具体的表述）
"下半场""上半场""深水区""新范式""新赛道""赋能""重塑""颠覆""生态闭环""闭环""破局""内卷""弯道超车""降维打击""护城河""数智化""智慧医疗""数字化转型""AI赋能""最后一公里""全链条""底层逻辑""顶层设计"

## 你的写作铁律
- 不用"近年来""随着AI发展""值得注意的是""首先其次最后"开头或过渡
- 不编造数据，原文没有的数字用相对表述替代，不用占位符（X%、待补充）
- 引用不写"发表于《某期刊》某卷某期"格式，用"据该研究""研究发现"替代"""

    source_section = f"""
## 素材萃取要点（优先基于此写作）
{extracted_points}

## 原始参考材料（需要具体数据时查阅）
{articles_text}""" if extracted_points else f"""
## 参考材料
{articles_text}"""

    user_msg = f"""请基于以下材料写一篇微信公众号文章。

{source_section}

## 本次写作任务
1. **字数1000-1500字**，宁短勿长。
2. **开头前3句**必须命中以下之一：具体数字+场景、一个尖锐问题、一个反直觉事实。
3. **全文只有一个核心判断**，围绕它展开。
4. **小标题用结论句**，2-3个小标题，体现递进关系。
5. 每个论点用材料中可核实的事实支撑。
6. **结尾**：给出一个明确判断或行动建议。
7. 每段2-4句，一个意思说完就换段。

## 标题要求（必须给出3个备选）
- **判断型**：直接给出有争议的观点（如"AI读片准确率超过专家，但医院为什么还不用"）
- **数字型**：用核心数据勾起好奇（如"一个模型让误诊率下降37%，它是怎么做到的"）
- **场景型**：代入具体人物情境（如"一个急诊科医生用了AI辅助诊断，然后他被投诉了"）

直接输出：
【备选标题】
- 判断型：xxx
- 数字型：xxx
- 场景型：xxx

【正文】
（直接写正文）

参考文献：
（仅列出正文中实际引用到的材料，格式：文章标题 / 来源名称 / 发布时间 / 链接）"""

    return system_msg, user_msg

def _build_review_prompt(draft: str) -> str:
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日")
    return f"""你是独立编辑，对以下医疗AI公众号文章进行严格审核。
只输出JSON批注，不要重写文章，不要输出JSON以外的任何内容。

今天日期：{today}（审核时以此为准，不要将今天或更早的日期判断为"未来日期"）

文章：
{draft}

## 评分锚点（校准你的评分尺度）
- 9-10分：读完让人想立刻转发给同事，有明确可执行的洞察，数据扎实
- 7-8分：专业可靠，有价值，但缺少让人"哇"的亮点或不够聚焦
- 5-6分：信息准确但像综述，读完不知道"所以呢"，缺乏判断
- 3-4分：空洞、套话多、或逻辑混乱
- 1-2分：有事实错误、编造数据、或完全跑题

## 审核要求
1. 每条issue包含：原文定位 + 问题说明 + 修改建议，输出3-5条，按优先级排序。
2. 至少1条issue检查事实可信度：是否有无法核实的数据、编造引用、绝对化表述。
3. 检查"决策价值"：读者读完能得到什么具体判断或行动参考？如果只是"介绍了什么"而没有"所以你应该怎么做"，作为high priority issue。
4. 检查段落逻辑衔接：相邻两段之间是否有明确的因果、递进或转折关系？如果前后两段各说各的、缺少连接逻辑，作为medium priority issue指出，并给出具体衔接改法。
5. 如有冗余句子，在cut_candidates中指出。

输出JSON：
```json
{{
  "scores": {{
    "title": 0-10,
    "hook": 0-10,
    "depth": 0-10,
    "readability": 0-10,
    "credibility": 0-10,
    "decision_value": 0-10,
    "overall": 0-10
  }},
  "issues": [
    {{
      "priority": "high|medium|low",
      "location": "引用原文短句",
      "problem": "具体问题说明",
      "suggestion": "明确改法"
    }}
  ],
  "strengths": ["亮点1", "亮点2"],
  "cut_candidates": ["可删减或压缩的位置"],
  "title_suggestion": "更好的标题建议（如有）",
  "key_fix": {{
    "location": "最关键问题所在原文片段",
    "reason": "为什么这是最重要的",
    "suggestion": "如何修改"
  }}
}}
```"""

def _parse_review(review_text: str) -> dict:
    try:
        clean = review_text
        if "```json" in clean:
            s = clean.find("```json")
            e = clean.find("```", s + 6)
            clean = clean[s+7:e].strip()
        elif clean.strip().startswith("{"):
            clean = clean.strip()
        return json.loads(clean)
    except Exception:
        return {}


def _build_polish_prompt(draft_v1: str, review_data: dict) -> str:
    issues = review_data.get("issues", [])
    issues_text = "\n".join([
        f"- [{i.get('priority','').upper()}] 「{i.get('location','')}」→ 问题：{i.get('problem','')} → 改法：{i.get('suggestion','')}"
        if isinstance(i, dict) else f"- {i}"
        for i in issues
    ])
    cut_text = "\n".join([f"- {c}" for c in review_data.get("cut_candidates", [])])
    key_fix = review_data.get("key_fix", {})
    key_fix_text = ""
    if isinstance(key_fix, dict) and key_fix:
        key_fix_text = f"最关键修改：「{key_fix.get('location','')}」→ {key_fix.get('suggestion','')}（原因：{key_fix.get('reason','')}）"
    title_suggest = review_data.get("title_suggestion", "")

    return f"""你是微信公众号终稿编辑。对文章做定向修改，只改审核指出的问题，其余不动。

原文：
{draft_v1}

审核意见：
{issues_text}
{f"可删减位置：{cut_text}" if cut_text else ""}
{f"标题建议：{title_suggest}" if title_suggest else ""}
{key_fix_text}

## 修改原则（只有3条，严格遵守）
1. **只改审核提到的问题**，优先落实 high priority。没提到的地方一个字不动。
2. **保持原文的语气、节奏和结构**。你是在打磨，不是重写。
3. **保持1000-1500字**，偏长就压缩，不扩写。

## 附加任务
- 给出3个备选标题（判断型/数字型/场景型），每个≤20字，口语化
- 在2-3处最有判断力的句子前加 ★ 标记
- 参考文献原样保留

## 排版规范
- 小标题用结论句，加粗，每个小标题下优先用列表承载核心信息
- 每条列表项必须包含具体数字、机构名、案例或可核实的事实，禁止纯概念性列表（只有名词没有数据）
- 关键数字和结论句加粗，每段最多1处
- 段落之间空行，保持视觉节奏

输出格式：
【备选标题】
- 判断型：xxx
- 数字型：xxx
- 场景型：xxx

【正文】
（直接输出终稿，末尾保留参考文献，无需解释）"""

def _extract_title(text: str, fallback: str = "") -> str:
    if "【备选标题】" in text:
        header = text.split("【正文】")[0] if "【正文】" in text else text
        for line in header.split("\n"):
            line = line.strip()
            if line.startswith("- 判断型："):
                return line[len("- 判断型："):].strip()
            elif line.startswith("-") and "：" in line:
                return line.split("：", 1)[1].strip()
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("标题："):
            return line[3:].strip()
        elif line.startswith("# "):
            return line[2:].strip()
    return fallback


def generate_wechat_article(article_ids: list, style_hint: str = "") -> dict:
    """
    并行生成3篇初稿，各自GPT审核打分，选分最高的做第3轮润色。
    三篇初稿全部暂存，成稿另存。
    """
    if not article_ids:
        return {"error": "未选择文章"}

    conn = get_conn(DB_PATH)
    placeholders = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"SELECT id, title, content, source_name, url, category, published_at FROM articles WHERE id IN ({placeholders})",
        article_ids
    ).fetchall()
    conn.close()

    if not rows:
        return {"error": "未找到文章"}

    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    gpt_client = OpenAI(api_key=GITHUB_TOKEN, base_url=GITHUB_MODELS_BASE_URL)

    selected_rows = [dict(r) for r in rows]
    selected_ids = [r["id"] for r in selected_rows]

    articles_text = "\n\n".join([
        f"【文章{i+1}】{r['title']}\n来源：{r['source_name']} | 发布时间：{(r.get('published_at') or '')[:10] or '未知'} | 链接：{r.get('url') or '无'}\n内容：{(r['content'] or '').strip()}"
        for i, r in enumerate(selected_rows)
    ])

    # ── 新增：素材萃取 ──────────────────────────────────────
    print("▶ 正在萃取素材要点...")
    extracted_points = _extract_key_points(client, articles_text)
    if extracted_points:
        print(f"✓ 素材萃取完成（{len(extracted_points)}字）")

    system_msg, user_msg = _build_draft_prompt(articles_text, extracted_points)

    # ── 第一轮：并行生成3篇初稿 ──────────────────────────────
    drafts = [None, None, None]
    def gen_draft(idx):
        try:
            r = client.chat.completions.create(
                model="deepseek-chat", timeout=120, max_tokens=3500,
                temperature=0.75,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ]
            )
            drafts[idx] = r.choices[0].message.content.strip()
        except Exception as e:
            drafts[idx] = f"[生成失败: {e}]"

    threads = [threading.Thread(target=gen_draft, args=(i,)) for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()

    # ── 第二轮：并行对3篇审核打分 ────────────────────────────
    reviews = [None, None, None]
    review_texts = [None, None, None]
    def do_review(idx):
        draft = drafts[idx]
        if not draft or draft.startswith("[生成失败"):
            reviews[idx] = {}
            review_texts[idx] = ""
            return
        prompt = _build_review_prompt(draft)
        try:
            r = gpt_client.chat.completions.create(
                model="gpt-4.1", timeout=60, max_tokens=1200,
                messages=[{"role": "user", "content": prompt}]
            )
            review_texts[idx] = r.choices[0].message.content.strip()
        except Exception:
            r = client.chat.completions.create(
                model="deepseek-chat", timeout=60, max_tokens=1200,
                messages=[{"role": "user", "content": prompt}]
            )
            review_texts[idx] = r.choices[0].message.content.strip()
        reviews[idx] = _parse_review(review_texts[idx])

    threads2 = [threading.Thread(target=do_review, args=(i,)) for i in range(3)]
    for t in threads2: t.start()
    for t in threads2: t.join()

    # ── 选分最高的初稿 ───────────────────────────────────────
    def get_overall(idx):
        return reviews[idx].get("scores", {}).get("overall", 0) if reviews[idx] else 0

    best_idx = max(range(3), key=get_overall)
    best_score = get_overall(best_idx)

    # ── 质量门槛：最优初稿 < 7.0 则补生成2篇 ────────────────
    QUALITY_THRESHOLD = 7.0
    if best_score < QUALITY_THRESHOLD:
        print(f"⚠ 最优初稿仅 {best_score} 分，低于门槛 {QUALITY_THRESHOLD}，补生成2篇...")
        extra_drafts = [None, None]
        extra_reviews = [None, None]
        extra_review_texts = [None, None]

        def gen_extra(idx):
            try:
                r = client.chat.completions.create(
                    model="deepseek-chat", timeout=120, max_tokens=3500,
                    temperature=0.75,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg}
                    ]
                )
                extra_drafts[idx] = r.choices[0].message.content.strip()
            except Exception as e:
                extra_drafts[idx] = f"[生成失败: {e}]"

        def review_extra(idx):
            d = extra_drafts[idx]
            if not d or d.startswith("[生成失败"):
                extra_reviews[idx] = {}
                extra_review_texts[idx] = ""
                return
            prompt = _build_review_prompt(d)
            try:
                r = gpt_client.chat.completions.create(
                    model="gpt-4.1", timeout=60, max_tokens=1200,
                    messages=[{"role": "user", "content": prompt}]
                )
                extra_review_texts[idx] = r.choices[0].message.content.strip()
            except Exception:
                r = client.chat.completions.create(
                    model="deepseek-chat", timeout=60, max_tokens=1200,
                    messages=[{"role": "user", "content": prompt}]
                )
                extra_review_texts[idx] = r.choices[0].message.content.strip()
            extra_reviews[idx] = _parse_review(extra_review_texts[idx])

        gen_threads = [threading.Thread(target=gen_extra, args=(i,)) for i in range(2)]
        for t in gen_threads: t.start()
        for t in gen_threads: t.join()

        review_threads = [threading.Thread(target=review_extra, args=(i,)) for i in range(2)]
        for t in review_threads: t.start()
        for t in review_threads: t.join()

        # 合并5篇候选，重新选最高分
        all_drafts = drafts + extra_drafts
        all_reviews = reviews + extra_reviews
        all_review_texts = review_texts + extra_review_texts

        def get_overall_all(idx):
            rv = all_reviews[idx]
            return rv.get("scores", {}).get("overall", 0) if rv else 0

        best_idx = max(range(len(all_drafts)), key=get_overall_all)
        best_score = get_overall_all(best_idx)
        print(f"✓ 补生成后最优分：{best_score}（共{len(all_drafts)}篇候选）")

        drafts = all_drafts
        reviews = all_reviews
        review_texts = all_review_texts

    draft_v1 = drafts[best_idx]
    review_data = reviews[best_idx]
    review_text = review_texts[best_idx] or ""

    # 三篇（或五篇）初稿各自暂存（标注轮次和分数）
    for i, (d, rv) in enumerate(zip(drafts, reviews)):
        if d and not d.startswith("[生成失败"):
            score = rv.get("scores", {}).get("overall", 0) if rv else 0
            t = _extract_title(d, f"初稿{i+1}")
            save_draft(
                title=f"[初稿{i+1} · {score}分] {t}",
                content=d,
                draft_v1=d, draft_v2="",
                review_json=rv or {},
                source_article_ids=selected_ids,
                generate_type="draft_candidate"
            )

    # ── 第三轮：对最优初稿润色 ───────────────────────────────
    polish_prompt = _build_polish_prompt(draft_v1, review_data)
    polish_client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        timeout=90,  # 连接级别超时，确保90s后真正断开
    )
    try:
        resp3 = polish_client.chat.completions.create(
            model="deepseek-chat", max_tokens=4000,
            temperature=0.3,
            messages=[{"role": "user", "content": polish_prompt}]
        )
        final_article = resp3.choices[0].message.content.strip() + ARTICLE_FOOTER
        print("✓ 第三轮润色完成")
    except Exception as e:
        print(f"⚠ 第三轮润色超时或失败（{e}），使用最优初稿作为终稿")
        final_article = draft_v1 + ARTICLE_FOOTER
    draft_v2 = f"[GPT-4.1审核批注]\n{review_text}"
    title = _extract_title(final_article)

    # ── 终稿评分 ────────────────────────────────────────────────
    final_review_data = {}
    try:
        final_review_prompt = _build_review_prompt(final_article)
        try:
            fr = gpt_client.chat.completions.create(
                model="gpt-4.1", timeout=60, max_tokens=800,
                messages=[{"role": "user", "content": final_review_prompt}]
            )
            final_review_text = fr.choices[0].message.content.strip()
        except Exception:
            fr = client.chat.completions.create(
                model="deepseek-chat", timeout=60, max_tokens=800,
                messages=[{"role": "user", "content": final_review_prompt}]
            )
            final_review_text = fr.choices[0].message.content.strip()
        final_review_data = _parse_review(final_review_text)
    except Exception:
        pass

    # 候选初稿分数汇总（供前端展示）
    candidates_summary = [
        {
            "index": i + 1,
            "overall": reviews[i].get("scores", {}).get("overall", 0) if reviews[i] else 0,
            "title": _extract_title(drafts[i], f"初稿{i+1}") if drafts[i] else "",
            "selected": i == best_idx
        }
        for i in range(3)
    ]

    return {
        "title": title,
        "content": final_article,
        "draft_v1": draft_v1,
        "draft_v2": draft_v2,
        "review": review_data,
        "final_review": final_review_data,
        "source_article_ids": article_ids,
        "candidates_summary": candidates_summary,
        "related_articles": [],
        "rounds": 3
    }


def generate_article_from_recommendation(rec_data: dict) -> dict:
    """
    根据写作推荐的选题卡片生成完整微信公众号文章，三轮自我审核。
    """
    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    working_title = rec_data.get("working_title", "")
    core_question = rec_data.get("core_question", "")
    core_viewpoint = rec_data.get("core_viewpoint", "")
    writing_angle = rec_data.get("writing_angle", "")
    risks = rec_data.get("risks", "")
    outline = rec_data.get("outline", [])
    titles = rec_data.get("titles", [])
    source = rec_data.get("source", "")
    source_url = rec_data.get("source_url", "")
    source_content = rec_data.get("source_content", "")

    outline_text = "\n".join([f"- {o}" for o in outline])
    titles_text = "\n".join([f"- {t}" for t in titles])
    hook = rec_data.get("hook", "")

    # 构建原文材料块，包含完整内容和链接
    source_block = ""
    if source or source_url or source_content:
        source_block = f"""
原文材料（这是文章的真实来源，参考文献必须引用此条）：
- 标题：{source}
- 链接：{source_url or '无'}
- 内容：{source_content or '（无完整内容，请基于标题和选题卡片信息写作）'}
"""

    # ── 第一轮：DeepSeek 按选题卡片生成初稿 ─────────────────
    draft_prompt = f"""你是医疗AI公众号主编，对标「Medical AI」「量子位医疗」「丁香园」的写作水准。
目标读者：医疗AI产品经理/产品总监、医疗科技公司决策层、医院信息化负责人——
他们看文章是为了做决定，不是为了学知识。让他们觉得"这个信息今天就能用上"，才会转发。
写作视角：比读者早一步看清落地坑的产品人，帮读者把事情想清楚。

请根据以下选题卡片和原文材料写一篇公众号初稿。
{source_block}
选题卡片：
- 传播钩子（最吸引人的那一点）：{hook}
- 核心问题：{core_question}
- 核心观点（必须在文中明确表达）：{core_viewpoint}
- 写作切入点：{writing_angle}
- 必须避免：{risks}

参考大纲（严格按此逻辑展开）：
{outline_text}

标题候选（从中选一个最好的，或基于它们重新拟定）：
{titles_text}

写作要求：
1. **字数1000-1500字**，宁短勿长，每个字都要有信息量。
2. **开头第一句**：必须是一个具体数字、一个临床场景或一个尖锐问题——不准用"近年来""随着AI发展""在医疗领域"等废话开头。
3. 全文只有一个核心判断，围绕它展开，不要面面俱到。
4. 小标题用结论句（不用"背景介绍""未来展望"这类空洞标题）。
5. 每个论点只能用原文材料中明确出现的事实、数据、结论——原文里有什么就用什么，没有的用"据该研究""研究发现"等审慎表述，绝对不能编造数字或引用细节。
6. 【数据缺口处理】原文中没有的具体数字，不得用占位符（X%、待补充、具体数据见原文等）代替；改用相对表述（"显著优于基线""在少样本条件下取得"）或直接跳过该数字，不影响核心判断的表达。
6. 语气克制有力，有自己的判断，不浮夸，不写"首先其次最后""值得注意的是"。
7. **结尾**：给出一个明确判断或对读者有用的行动建议，不喊口号。
8. 【排版】每段2-4句，单句可独立成段；用短段制造节奏感。
9. 【禁用词】必须替换："下半场""上半场""深水区""新范式""新赛道""赋能""重塑""颠覆""生态闭环""破局""内卷""降维打击""护城河""数智化""智慧医疗""数字化转型""AI赋能""最后一公里""底层逻辑""顶层设计"。

直接输出：
标题：[标题]

[正文]

参考文献：
（格式：文章标题 / 来源名称 / 链接。链接必须使用原文材料中提供的链接，有链接填链接，无则写"暂无链接"。不得编造或省略。）"""

    resp1 = client.chat.completions.create(
        model="deepseek-chat",
        timeout=90,
        max_tokens=2000,
        messages=[{"role": "user", "content": draft_prompt}]
    )
    draft_v1 = resp1.choices[0].message.content.strip()

    # ── 第二轮：GPT-4.1 独立审核（只输出批注，不重写）────────
    gemini_client = OpenAI(api_key=GITHUB_TOKEN, base_url=GITHUB_MODELS_BASE_URL)
    review_prompt = f"""你是独立编辑，对以下医疗AI公众号文章进行严格审核。
只输出JSON批注，不要重写文章，不要输出JSON以外的任何内容。评分要严格，不要虚高。

文章：
{draft_v1}

审核要求：
1. 每条issue必须包含：原文定位（引用原文短句）+ 问题说明 + 修改建议。
2. issues输出3-5条，按优先级排序。
3. 至少1条issue检查事实可信度：是否有无法核实的数据、编造的引用细节、过度夸大的结论、绝对化表述。
4. 如有冗余段落或无新信息的句子，在cut_candidates中指出。
5. 不要重写全文。
6. 检查"决策价值"：目标读者（医疗AI产品经理/医院信息化负责人）读完，能得到什么具体判断或行动参考？如果全文只是"介绍了什么技术/发生了什么事"而没有"所以你应该/可以怎么做"，作为high priority issue输出。

输出JSON：
```json
{{
  "scores": {{
    "title": 0-10,
    "hook": 0-10,
    "depth": 0-10,
    "readability": 0-10,
    "credibility": 0-10,
    "viral": 0-10,
    "decision_value": 0-10,
    "overall": 0-10
  }},
  "issues": [
    {{
      "priority": "high|medium|low",
      "location": "引用原文短句",
      "problem": "具体问题说明",
      "suggestion": "明确改法"
    }}
  ],
  "strengths": ["亮点1", "亮点2"],
  "cut_candidates": ["可删减或压缩的位置"],
  "title_suggestion": "更好的标题建议（如有）",
  "key_fix": {{
    "location": "最关键问题所在原文片段",
    "reason": "为什么这是最重要的",
    "suggestion": "如何修改"
  }}
}}
```"""

    try:
        resp2 = gemini_client.chat.completions.create(
            model="gpt-4.1",
            timeout=60,
            max_tokens=1200,
            messages=[{"role": "user", "content": review_prompt}]
        )
        review_text = resp2.choices[0].message.content.strip()
    except Exception:
        resp2 = client.chat.completions.create(
            model="deepseek-chat",
            timeout=60,
            max_tokens=1200,
            messages=[{"role": "user", "content": review_prompt}]
        )
        review_text = resp2.choices[0].message.content.strip()

    review_data = {}
    try:
        json_start = review_text.find("```json")
        json_end = review_text.find("```", json_start + 6)
        if json_start != -1 and json_end != -1:
            review_data = json.loads(review_text[json_start+7:json_end].strip())
    except Exception:
        pass

    # ── 第三轮：DeepSeek 按批注定向修改 ──────────────────────
    issues = review_data.get("issues", [])
    issues_text = "\n".join([
        f"- [{i.get('priority','').upper()}] 「{i.get('location','')}」→ 问题：{i.get('problem','')} → 改法：{i.get('suggestion','')}"
        if isinstance(i, dict) else f"- {i}"
        for i in issues
    ])
    cut_text = "\n".join([f"- {c}" for c in review_data.get("cut_candidates", [])])
    key_fix = review_data.get("key_fix", {})
    key_fix_text = ""
    if isinstance(key_fix, dict) and key_fix:
        key_fix_text = f"最关键修改：「{key_fix.get('location','')}」→ {key_fix.get('suggestion','')}（原因：{key_fix.get('reason','')}）"
    title_suggest = review_data.get("title_suggestion", "")

    polish_prompt = f"""你是微信公众号终稿编辑。请基于审核意见对文章做定向修改，输出最终稿。

原文：
{draft_v1}

审核意见：
{issues_text}
{f"可删减位置：{cut_text}" if cut_text else ""}
{f"标题建议：{title_suggest}" if title_suggest else ""}
{key_fix_text}

修改要求：
1. 针对审核意见逐一修改，优先落实high优先级问题。
2. 【重要】保留原文的核心结构、段落节奏和语气风格；只做局部改写，不要重写全文，不要把有个性的表达磨平。
3. 审核意见未提到的地方，不要动。
4. 若有可删减位置，优先压缩而不是改写。
5. 【排版】每段2-4句，超长段落拆开；同一段不塞多个意思。
6. 【禁用词】替换以下词语为更具体的表达："下半场""上半场""深水区""新范式""新赛道""赋能""重塑""颠覆""生态闭环""闭环""破局""内卷""弯道超车""降维打击""护城河""数智化""智慧医疗""数字化转型""AI赋能""最后一公里""全链条""底层逻辑""顶层设计"。
7. 给出3个备选标题，风格真正不同：判断型（给出强观点）、悬念型（制造好奇或反差）、场景型（代入具体人物或情境）；口语化，有点击欲，不堆术语。
8. 在2-3处关键句前加 ★ 标记，选最有判断力的句子，不硬造。
9. 保持全文1000-1500字，偏长优先压缩。
10. 【参考文献】原文末尾的参考文献部分必须原样保留，不得修改来源名称、时间或链接；若某条有原文链接但未写出，补上。

输出格式：
【备选标题】
- 专业判断式：xxx
- 问题导向式：xxx
- 传播点击式：xxx

【正文】
（直接输出终稿，末尾保留参考文献，无需解释）"""

    polish_client2 = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        timeout=90,
    )
    try:
        resp3 = polish_client2.chat.completions.create(
            model="deepseek-chat",
            max_tokens=2200,
            temperature=0.3,
            messages=[{"role": "user", "content": polish_prompt}]
        )
        final_article = resp3.choices[0].message.content.strip() + ARTICLE_FOOTER
        print("✓ 第三轮润色完成")
    except Exception as e:
        print(f"⚠ 第三轮润色超时或失败（{e}），使用最优初稿作为终稿")
        final_article = draft_v1 + ARTICLE_FOOTER
    draft_v2 = f"[GPT-4.1审核批注]\n{review_text}"

    # 提取标题：新格式用【正文】分隔，从【备选标题】中取第一个
    title = working_title
    if "【备选标题】" in final_article:
        header_part = final_article.split("【正文】")[0] if "【正文】" in final_article else ""
        for line in header_part.split("\n"):
            line = line.strip()
            if line.startswith("- 专业判断式："):
                title = line[len("- 专业判断式："):].strip()
                break
            elif line.startswith("-") and "：" in line:
                title = line.split("：", 1)[1].strip()
                break
    elif "【正文】" in final_article:
        pass  # keep working_title
    else:
        for line in final_article.split("\n"):
            line = line.strip()
            if line.startswith("标题："):
                title = line[3:].strip()
                break
            elif line.startswith("# "):
                title = line[2:].strip()
                break

    return {
        "title": title,
        "content": final_article,
        "draft_v1": draft_v1,
        "draft_v2": draft_v2,
        "review": review_data,
        "rounds": 3
    }


def get_app_state(key: str) -> str:
    conn = get_conn(DB_PATH)
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_app_state(key: str, value: str):
    conn = get_conn(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


def get_starred_articles() -> list:
    conn = get_conn(DB_PATH)
    rows = conn.execute("""
        SELECT id, title, content, source, source_name, url, category,
               quality_score, published_at, fetched_at, is_starred
        FROM articles
        WHERE is_starred = 1
        ORDER BY fetched_at DESC
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["summary"] = (d["content"] or "")[:120]
        d["date"] = (d["published_at"] or d["fetched_at"] or "")[:10]
        d["title_zh"] = ""
        result.append(d)
    return result


def set_starred(article_id: int, starred: bool):
    conn = get_conn(DB_PATH)
    conn.execute("UPDATE articles SET is_starred=? WHERE id=?", (1 if starred else 0, article_id))
    conn.commit()
    conn.close()


def save_draft(title: str, content: str, draft_v1: str, draft_v2: str,
               review_json: dict, source_article_ids: list, generate_type: str) -> int:
    """保存生成的草稿到数据库，返回草稿ID"""
    cst = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn(DB_PATH)
    cur = conn.execute("""
        INSERT INTO drafts (title, content, draft_v1, draft_v2, review_json,
                            source_article_ids, generate_type, model_used, review_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
    """, (
        title, content, draft_v1, draft_v2,
        json.dumps(review_json, ensure_ascii=False),
        json.dumps(source_article_ids, ensure_ascii=False),
        generate_type, "deepseek+gpt4.1", cst
    ))
    conn.commit()
    draft_id = cur.lastrowid
    conn.close()
    return draft_id


def get_drafts() -> list:
    """获取所有草稿，按时间倒序"""
    conn = get_conn(DB_PATH)
    rows = conn.execute("""
        SELECT id, title, content, draft_v1, draft_v2, review_json,
               source_article_ids, generate_type, review_status, created_at
        FROM drafts
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["review_json"] = json.loads(d["review_json"] or "{}")
        except Exception:
            d["review_json"] = {}
        try:
            d["source_article_ids"] = json.loads(d["source_article_ids"] or "[]")
        except Exception:
            d["source_article_ids"] = []
        result.append(d)
    return result


def delete_draft(draft_id: int) -> bool:
    """删除指定草稿"""
    conn = get_conn(DB_PATH)
    conn.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    conn.commit()
    conn.close()
    return True


def get_writing_recommendations(days: int = 3, topic: str = "") -> list:
    """
    基于今日收集的文章，按照 daily-wechat-research-writer 的评分框架
    推荐2-3个公众号写作方向，每个包含选题卡片
    """
    conn = get_conn(DB_PATH)
    rows = conn.execute("""
        SELECT id, title, content, source_name, category, quality_score, url
        FROM articles
        WHERE fetched_at >= datetime('now', ?)
          AND quality_score >= 6.5
          AND source != 'github'
        ORDER BY quality_score DESC
        LIMIT 30
    """, (f'-{days} days',)).fetchall()
    conn.close()

    articles = [dict(r) for r in rows]
    if not articles:
        return []

    # 检查缓存（用文章ID列表 + topic 做hash，topic变化则重新分析）
    digest_hash = str(sorted([a["id"] for a in articles])) + "|" + topic
    if _recommend_cache["data"] and _recommend_cache["digest_hash"] == digest_hash:
        return _recommend_cache["data"]

    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    articles_text = "\n\n".join([
        f"[{a['category']}] {a['title']}\n来源:{a['source_name']} 评分:{a['quality_score']} 链接:{a.get('url') or '无'}\n{(a['content'] or '').strip()}"
        for a in articles
    ])

    topic_hint = f"\n\n⚠️ 今日关注方向：「{topic}」——请优先从文章列表中挑选与此方向相关的素材；若无相关素材则说明并推荐次优选题。" if topic else ""

    prompt = f"""你是医疗AI公众号「Medical AI」风格的资深主编。以下是今天收集到的文章列表，请推荐2-3个最有传播潜力的选题。{topic_hint}

## 爆款选题的核心标准（优先满足）
1. **反常识**：读者以为A，实际上是B——能让人产生"原来如此"的顿悟感
2. **临床冲击**：直接影响医生/患者的真实场景，有紧迫感
3. **数据震撼**：有一个让人意外的核心数字（准确率、效率提升、成本节省）
4. **行业决策**：医院管理者/投资人/产品经理看完会做出不同判断

## 传播力评估要素
- 这个选题，一个医生会不会转发给同事？（专业共鸣）
- 这个选题，行业媒体会不会引用？（信息价值）
- 这个标题，陌生人会不会点开？（好奇心钩子）

## 写作通道
- Lane A（研究解析）：顶刊/大机构成果，角度是"这个突破对临床意味着什么，现在能用吗"
- Lane B（产品落地）：产品/工具/案例，角度是"这解决了什么真实痛点，推广门槛在哪"

## 选题规则
- 只选真正值得写的2-3个，宁缺毋滥
- 每个选题必须能明确回答：读者看完会有什么收获？
- 如果今日素材不够好，直接说，不要强行凑数

只输出JSON，不要输出其他内容：
{{
  "recommendations": [
    {{
      "rank": 1,
      "lane": "Lane A 或 Lane B",
      "working_title": "参考标题（体现核心判断，非中性描述）",
      "source": "来源文章标题",
      "hook": "这个选题最吸引人的那一点（一句话，具体到数字或场景）",
      "core_question": "这篇文章要回答的核心问题",
      "core_viewpoint": "核心观点（一句话，有立场，不中性）",
      "why_write": "为什么今天值得写，读者会有什么收获",
      "writing_angle": "具体切入点：从哪个场景/数字/角色切入",
      "risks": "最容易踩的坑：编造数据/空喊口号/变成论文摘要",
      "titles": [
        "判断型标题（有争议感，20字内）",
        "数字型标题（用核心数据，20字内）",
        "场景型标题（代入具体人物，20字内）"
      ],
      "outline": [
        "开头：用什么钩子开场（具体到第一句话的方向）",
        "第一节标题：结论句",
        "第二节标题：结论句",
        "第三节标题：结论句",
        "结尾：给读者留下的判断或行动建议"
      ]
    }}
  ],
  "editor_note": "今日素材整体质量评价，以及最值得关注的信号（一句话）"
}}

今日文章列表：
{articles_text}"""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
        timeout=90,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        recs = result.get("recommendations", [])
        # 构建标题->文章的映射，用于注入 URL 和完整内容
        article_map = {a["title"]: a for a in articles}
        for r in recs:
            r["editor_note"] = result.get("editor_note", "")
            # 尝试匹配 source 字段对应的原文章，注入 URL 和完整内容
            source_title = r.get("source", "")
            matched = article_map.get(source_title)
            if not matched:
                # 模糊匹配：source 可能是截断的标题
                for title, art in article_map.items():
                    if source_title[:30] in title or title[:30] in source_title:
                        matched = art
                        break
            if matched:
                r["source_url"] = matched.get("url") or ""
                r["source_content"] = (matched.get("content") or "").strip()
                r["source_published_at"] = (matched.get("url") or "")  # url已在matched
            else:
                r["source_url"] = ""
                r["source_content"] = ""
        _recommend_cache["data"] = recs
        _recommend_cache["digest_hash"] = digest_hash
        return recs
    except Exception as e:
        return [{"error": str(e)}]


def run_pipeline(topic=""):
    """后台运行完整pipeline"""
    import concurrent.futures
    global task_status
    task_status["running"] = True
    task_status["log"] = []
    task_status["step"] = "抓取文章"

    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = DEEPSEEK_API_KEY
    python_exe = ".venv/bin/python" if os.path.exists(".venv/bin/python") else "python"

    def run_step(cmd, env=env):
        return subprocess.run(
            [python_exe] + cmd[1:],
            capture_output=True, text=True, env=env, cwd=os.getcwd()
        )

    db_args = ["--db", DB_PATH]

    # ── 第一步：并行抓取所有 RSS 源 ──────────────────────────
    task_status["step"] = "抓取文章"
    task_status["log"].append("▶ 并行抓取所有 RSS 源...")
    try:
        import yaml, feedparser as _fp
        from scrapers.rss import fetch_rss, load_config, build_wewe_sources, fetch_github
        from pathlib import Path
        config = load_config("config.yaml")
        cfg_sources = config.get("rss_sources", [])
        from scrapers.rss import DEFAULT_SOURCES
        rss_sources = cfg_sources if cfg_sources else DEFAULT_SOURCES
        wewe_sources = build_wewe_sources(config)
        all_sources = rss_sources + wewe_sources

        def _fetch_one(src):
            try:
                n = fetch_rss(src, db_path=DB_PATH)
                return f"  ✓ {src['name']}: +{n} 篇"
            except Exception as e:
                return f"  ✗ {src['name']}: {e}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_fetch_one, s): s for s in all_sources}
            for f in concurrent.futures.as_completed(futures):
                task_status["log"].append(f.result())

        # GitHub 单独串行（有限速考虑）
        task_status["log"].append("  抓取 GitHub 项目...")
        fetch_github(db_path=DB_PATH)
        task_status["log"].append("  ✓ GitHub 抓取完成")
    except Exception as e:
        task_status["log"].append(f"  抓取异常: {e}，降级到子进程模式...")
        r = run_step(["python", "main.py"] + db_args + ["fetch"])
        task_status["log"].extend((r.stdout or "").strip().split("\n"))

    # ── 第二步：AI 评分 ──────────────────────────────────────
    task_status["step"] = "AI评分"
    task_status["log"].append("▶ AI评分...")
    r = run_step(["python", "main.py"] + db_args + ["score", "--limit", "60"])
    if r.stdout:
        task_status["log"].extend(r.stdout.strip().split("\n"))
    if r.returncode != 0 and r.stderr:
        task_status["log"].append(f"评分错误: {r.stderr[:300]}")

    # ── 第三步：生成今日摘要 ─────────────────────────────────
    task_status["step"] = "生成摘要"
    task_status["log"].append("▶ 生成今日情报摘要...")
    digest_cmd = ["python", "main.py"] + db_args + ["digest"]
    if topic:
        digest_cmd += ["--topic", topic]
    r = run_step(digest_cmd)
    if r.stdout:
        task_status["log"].extend(r.stdout.strip().split("\n"))
    if r.returncode != 0 and r.stderr:
        task_status["log"].append(f"摘要错误: {r.stderr[:300]}")

    task_status["running"] = False
    task_status["step"] = "完成"
    from datetime import date
    set_app_state("last_fetch_date", str(date.today()))
    _recommend_cache["data"] = None
    _recommend_cache["digest_hash"] = ""


def is_english(text: str) -> bool:
    """简单判断标题是否为英文（非中文字符占多数）"""
    if not text:
        return False
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return chinese < len(text) * 0.2


def translate_titles(articles: list) -> dict:
    """
    批量翻译，返回 {id: 中文翻译} 字典
    - 普通英文文章：翻译标题
    - GitHub项目：翻译第一行描述（content首行），标题是 owner/repo 无需翻译
    """
    to_translate = []
    for a in articles:
        if a.get("source") == "github" or a["title"].startswith("[GitHub]"):
            # 取content第一行作为描述
            desc = (a.get("summary") or "").split("\n")[0].strip()
            if desc and is_english(desc):
                to_translate.append({"id": a["id"], "text": desc, "is_github": True})
        elif is_english(a["title"]):
            to_translate.append({"id": a["id"], "text": a["title"], "is_github": False})

    if not to_translate:
        return {}

    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    items_text = "\n".join([f'{t["id"]}|{t["text"]}' for t in to_translate])
    prompt = f"""将以下英文文本翻译成中文，保持简洁准确，每行格式为 ID|中文翻译，只输出翻译结果，不要其他内容：

{items_text}"""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
        timeout=90,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        lines = resp.choices[0].message.content.strip().split("\n")
        result = {}
        for line in lines:
            if "|" in line:
                parts = line.split("|", 1)
                try:
                    result[int(parts[0].strip())] = parts[1].strip()
                except ValueError:
                    pass
        return result
    except Exception:
        return {}


MEDICAL_KEYWORDS = [
    "medical", "clinical", "hospital", "patient", "disease", "diagnosis",
    "radiology", "patholog", "cancer", "tumor", "drug", "EHR", "EMR",
    "health", "biomedical", "surgery", "treatment", "therapy", "imaging",
    "genomic", "phenotype", "trial", "FDA", "physician", "nurse",
    "X-ray", "chest", "MRI", "CT scan", "ultrasound", "retinal", "fundus",
    "dermatol", "ophthalmol", "cardio", "cardiac", "ECG", "EEG",
    "wound", "lesion", "biopsy", "tissue", "organ", "brain", "lung",
    "疾病", "诊断", "医疗", "临床", "患者", "影像", "病理", "肿瘤",
    "药物", "基因", "手术", "治疗", "医院", "健康",
]

def _is_medical(title: str, content: str, source_name: str) -> bool:
    """判断文章是否与医疗/健康相关"""
    # 来自医疗专属期刊/媒体的直接通过
    medical_sources = {
        "Nature Medicine", "Nature Biomedical Engineering", "The Lancet Digital Health",
        "NEJM AI", "npj Digital Medicine", "JAMA Network Open",
        "Medical Image Analysis", "IEEE Transactions on Medical Imaging",
        "IEEE Journal of Biomedical and Health Informatics",
        "STAT News", "Healthcare IT News", "arXiv q-bio.QM (生物医学定量方法)",
        "arXiv eess.IV (医学影像/MICCAI方向)",
    }
    if source_name in medical_sources:
        return True
    text = (title + " " + (content or "")[:300]).lower()
    return any(kw.lower() in text for kw in MEDICAL_KEYWORDS)


def get_digest_data(days=2):
    """从数据库读取分类后的文章，过滤为医疗相关，附带日期和英文标题翻译"""
    conn = get_conn(DB_PATH)
    rows = conn.execute("""
        SELECT id, title, content, source, source_name, url, category, quality_score,
               published_at, fetched_at
        FROM articles
        WHERE fetched_at >= datetime('now', ?)
          AND quality_score >= 5.5
          AND category != ''
        ORDER BY quality_score DESC
    """, (f'-{days} days',)).fetchall()
    conn.close()

    # 医疗相关过滤
    rows = [r for r in rows if _is_medical(r["title"], r["content"], r["source_name"])]

    result = {"顶刊论文": [], "大组动态": [], "商业落地": [], "开源项目": [], "未分类": []}
    all_articles = []
    for r in rows:
        # 优先用发布时间，没有就用抓取时间
        date_str = r["published_at"] or r["fetched_at"] or ""
        date_display = date_str[:10] if date_str else ""

        cat = r["category"] if r["category"] in result else "未分类"
        article = {
            "id": r["id"],
            "title": r["title"],
            "summary": (r["content"] or "")[:120],
            "source": r["source_name"],
            "source_type": r["source"],
            "url": r["url"] or "",
            "score": r["quality_score"],
            "date": date_display,
            "title_zh": "",  # 待填充
        }
        result[cat].append(article)
        all_articles.append(article)

    # 批量翻译英文标题
    translations = translate_titles(all_articles)
    for cat_list in result.values():
        for a in cat_list:
            if a["id"] in translations:
                a["title_zh"] = translations[a["id"]]

    return result


HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>医疗AI 每日情报</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
         background: #f0f4f8; color: #1a202c; min-height: 100vh; }

  .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; padding: 24px 32px; }
  .header h1 { font-size: 22px; font-weight: 700; }
  .header p  { font-size: 13px; opacity: 0.8; margin-top: 4px; }

  .toolbar { background: white; padding: 16px 32px; display: flex;
             align-items: center; gap: 12px; border-bottom: 1px solid #e2e8f0;
             flex-wrap: wrap; }
  .toolbar input { flex: 1; min-width: 180px; padding: 8px 14px; border: 1px solid #cbd5e0;
                   border-radius: 8px; font-size: 14px; outline: none; }
  .toolbar input:focus { border-color: #667eea; }
  .btn { padding: 9px 20px; border-radius: 8px; border: none; cursor: pointer;
         font-size: 14px; font-weight: 600; transition: opacity .2s; }
  .btn:hover { opacity: .85; }
  .btn-primary { background: #667eea; color: white; }
  .btn-secondary { background: #e2e8f0; color: #4a5568; }
  .status-badge { font-size: 12px; padding: 4px 10px; border-radius: 12px;
                  background: #c6f6d5; color: #276749; }
  .status-badge.running { background: #fefcbf; color: #744210; }

  .main { padding: 24px 32px; max-width: 1100px; margin: 0 auto; }

  .stats-row { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .stat-card { background: white; border-radius: 12px; padding: 16px 20px;
               flex: 1; min-width: 140px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .stat-card .num { font-size: 28px; font-weight: 700; color: #667eea; }
  .stat-card .label { font-size: 12px; color: #718096; margin-top: 2px; }

  .section { margin-bottom: 28px; }
  .section-title { font-size: 16px; font-weight: 700; margin-bottom: 14px;
                   display: flex; align-items: center; gap: 8px; }
  .badge { font-size: 11px; background: #ebf4ff; color: #3182ce;
           padding: 2px 8px; border-radius: 10px; font-weight: 500; }

  .articles { display: grid; gap: 12px; }
  .article-card { background: white; border-radius: 12px; padding: 16px 20px;
                  box-shadow: 0 1px 4px rgba(0,0,0,.06);
                  border-left: 4px solid #e2e8f0; transition: box-shadow .2s; }
  .article-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,.1); }
  .article-card.journal { border-left-color: #4299e1; }
  .article-card.lab     { border-left-color: #9f7aea; }
  .article-card.biz     { border-left-color: #48bb78; }
  .article-card.github  { border-left-color: #ed8936; }

  .article-title { font-size: 15px; font-weight: 600; line-height: 1.5; }
  .article-title a { color: #2d3748; text-decoration: none; }
  .article-title a:hover { color: #667eea; }
  .article-title-zh { font-size: 13px; color: #667eea; margin-top: 3px; font-weight: 500; }
  .article-meta { font-size: 12px; color: #a0aec0; margin-top: 6px; }
  .article-summary { font-size: 13px; color: #4a5568; margin-top: 8px;
                     line-height: 1.6; display: -webkit-box;
                     -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .score-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
               margin-right: 4px; }

  .log-box { background: #1a202c; color: #a0aec0; border-radius: 12px;
             padding: 16px; font-family: monospace; font-size: 12px;
             max-height: 240px; overflow-y: auto; display: none; }
  .log-box.show { display: block; }
  .log-line { padding: 1px 0; }
  .log-line.ok { color: #68d391; }
  .log-line.err { color: #fc8181; }

  .empty { text-align: center; padding: 48px; color: #a0aec0; }
  .empty p { margin-top: 8px; font-size: 14px; }

  .rec-section { margin-top: 36px; border-top: 2px solid #e2e8f0; padding-top: 28px; }
  .rec-section-title { font-size: 18px; font-weight: 700; margin-bottom: 6px; }
  .rec-editor-note { font-size: 13px; color: #718096; margin-bottom: 20px; }
  .rec-cards { display: grid; gap: 20px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
  .rec-card { background: white; border-radius: 14px; padding: 20px 22px;
              box-shadow: 0 2px 8px rgba(0,0,0,.07); border-top: 4px solid #667eea; }
  .rec-rank { font-size: 11px; font-weight: 700; color: #667eea; text-transform: uppercase;
              letter-spacing: .05em; margin-bottom: 6px; }
  .rec-title { font-size: 15px; font-weight: 700; color: #2d3748; line-height: 1.5; margin-bottom: 10px; }
  .rec-lane { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 10px;
              background: #ebf8ff; color: #2b6cb0; font-weight: 600; margin-bottom: 10px; }
  .rec-field { margin-top: 10px; }
  .rec-label { font-size: 11px; color: #a0aec0; font-weight: 600; text-transform: uppercase;
               letter-spacing: .04em; margin-bottom: 3px; }
  .rec-value { font-size: 13px; color: #4a5568; line-height: 1.6; }
  .rec-titles { margin-top: 12px; }
  .rec-title-item { font-size: 13px; color: #553c9a; padding: 4px 0;
                    border-bottom: 1px dashed #e9d8fd; }
  .rec-title-item:last-child { border-bottom: none; }
  .rec-outline { margin-top: 10px; padding-left: 16px; }
  .rec-outline li { font-size: 13px; color: #4a5568; padding: 2px 0; }
  .rec-toggle { background: none; border: none; color: #667eea; font-size: 12px;
                cursor: pointer; padding: 6px 0; font-weight: 600; }
  .rec-detail { display: none; }
  .rec-detail.open { display: block; }

  /* Tab导航 */
  .tabs { background: white; border-bottom: 2px solid #e2e8f0;
          display: flex; padding: 0 32px; }
  .tab { padding: 14px 20px; font-size: 14px; font-weight: 600; color: #718096;
         cursor: pointer; border-bottom: 3px solid transparent; margin-bottom: -2px;
         transition: all .2s; }
  .tab:hover { color: #667eea; }
  .tab.active { color: #667eea; border-bottom-color: #667eea; }
  .page { display: none; }
  .page.active { display: block; }

  /* 公众号分析页 */
  .wx-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }
  .wx-card { background: white; border-radius: 14px; padding: 20px 22px;
             box-shadow: 0 2px 8px rgba(0,0,0,.06); cursor: pointer;
             transition: box-shadow .2s; border-left: 4px solid #48bb78; }
  .wx-card:hover { box-shadow: 0 6px 16px rgba(0,0,0,.1); }
  .wx-card-title { font-size: 15px; font-weight: 700; color: #2d3748; line-height: 1.5; }
  .wx-card-meta { font-size: 12px; color: #a0aec0; margin-top: 6px; }
  .wx-card-btn { margin-top: 12px; padding: 7px 14px; background: #ebf8ff;
                 color: #2b6cb0; border: none; border-radius: 8px; font-size: 13px;
                 font-weight: 600; cursor: pointer; }
  .wx-card-btn:hover { background: #bee3f8; }
  .wx-card-btn.loading { opacity: .6; pointer-events: none; }

  /* 分析弹窗 */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5);
                   z-index: 100; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: white; border-radius: 16px; width: 90%; max-width: 720px;
           max-height: 85vh; overflow-y: auto; padding: 28px 32px;
           box-shadow: 0 20px 60px rgba(0,0,0,.2); }
  .modal-close { float: right; background: none; border: none; font-size: 22px;
                 cursor: pointer; color: #a0aec0; line-height: 1; }
  .modal-close:hover { color: #2d3748; }
  .modal-title { font-size: 17px; font-weight: 700; color: #2d3748;
                 margin-bottom: 20px; padding-right: 32px; line-height: 1.5; }
  .analysis-block { margin-bottom: 20px; padding: 16px; border-radius: 10px;
                    background: #f7fafc; }
  .analysis-label { font-size: 11px; font-weight: 700; color: #667eea;
                    text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }
  .analysis-text { font-size: 14px; color: #2d3748; line-height: 1.7; }
  .pain-point { margin-bottom: 10px; padding: 10px 14px; background: white;
                border-radius: 8px; border-left: 3px solid #ed8936; }
  .pain-point-name { font-size: 13px; font-weight: 700; color: #c05621; }
  .pain-point-desc { font-size: 13px; color: #4a5568; margin-top: 3px; line-height: 1.6; }
  .technique-tag { display: inline-block; margin: 3px; padding: 4px 10px;
                   background: #ebf8ff; color: #2b6cb0; border-radius: 12px;
                   font-size: 12px; font-weight: 500; }
  .learnable-box { background: #fffff0; border: 1px solid #f6e05e; border-radius: 10px;
                   padding: 14px 16px; }
  .learnable-box .analysis-label { color: #b7791f; }
  .learnable-box .analysis-text { color: #744210; font-weight: 500; }

  /* 文章选择 & 生成按钮 */
  .article-select-wrap { display:flex; align-items:flex-start; gap:10px; }
  .article-select-wrap input[type=checkbox] { margin-top:3px; width:16px; height:16px; cursor:pointer; accent-color:#667eea; flex-shrink:0; }
  .article-card-body { flex:1; min-width:0; }
  .generate-bar { position: sticky; bottom: 0; background: white; border-top: 2px solid #667eea;
                  padding: 14px 32px; display:none; align-items:center; gap:12px;
                  box-shadow: 0 -4px 16px rgba(102,126,234,.15); z-index: 50; }
  .generate-bar.show { display:flex; }
  .generate-bar .sel-count { font-size:14px; font-weight:600; color:#667eea; }
  .generate-bar .sel-hint { font-size:13px; color:#718096; flex:1; }
  .btn-generate { background: linear-gradient(135deg,#667eea,#764ba2); color:white;
                  padding:10px 24px; border-radius:10px; border:none; cursor:pointer;
                  font-size:14px; font-weight:700; letter-spacing:.02em; }
  .btn-generate:hover { opacity:.9; }
  .btn-generate:disabled { opacity:.5; cursor:not-allowed; }

  /* 推荐卡片生成按钮 */
  .rec-gen-btn { width:100%; margin-top:14px; padding:10px 0;
                 background:linear-gradient(135deg,#667eea,#764ba2); color:white;
                 border:none; border-radius:10px; font-size:14px; font-weight:700;
                 cursor:pointer; letter-spacing:.02em; }
  .rec-gen-btn:hover { opacity:.9; }
  .rec-gen-btn:disabled { opacity:.5; cursor:not-allowed; }

  /* 生成结果弹窗内 */
  .gen-rounds { display:flex; gap:8px; margin-bottom:20px; flex-wrap:wrap; }
  .gen-round-tab { padding:6px 14px; border-radius:20px; font-size:12px; font-weight:600;
                   cursor:pointer; border:2px solid #e2e8f0; color:#718096; background:white; }
  .gen-round-tab.active { border-color:#667eea; color:#667eea; background:#ebf4ff; }
  .gen-article { white-space:pre-wrap; font-size:14px; line-height:1.9; color:#2d3748;
                 background:#f7fafc; border-radius:10px; padding:20px; max-height:60vh;
                 overflow-y:auto; }
  .gen-review { background:#fffaf0; border-radius:10px; padding:16px; margin-bottom:16px; }
  .gen-review-title { font-size:12px; font-weight:700; color:#b7791f; margin-bottom:8px; letter-spacing:.05em; }
  .gen-score-row { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:10px; }
  .gen-score-chip { font-size:12px; padding:3px 10px; border-radius:10px; background:#fef3c7; color:#92400e; font-weight:600; }
  .gen-issues { font-size:13px; color:#4a5568; line-height:1.7; }
  .gen-copy-btn { margin-top:12px; padding:8px 20px; background:#48bb78; color:white;
                  border:none; border-radius:8px; font-size:13px; font-weight:600;
                  cursor:pointer; }
  .gen-copy-btn:hover { background:#38a169; }

  /* 草稿箱 */
  .draft-card { background:white; border-radius:14px; padding:20px 24px;
                box-shadow:0 2px 8px rgba(0,0,0,.07); border-left:4px solid #667eea; }
  .draft-card-header { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
  .draft-title { font-size:15px; font-weight:700; color:#2d3748; line-height:1.5; flex:1; }
  .draft-meta { font-size:12px; color:#a0aec0; margin-top:5px; }
  .draft-actions { display:flex; gap:8px; margin-top:14px; flex-wrap:wrap; }
  .draft-btn { padding:7px 14px; border-radius:8px; font-size:13px; font-weight:600;
               border:none; cursor:pointer; }
  .draft-btn-view { background:#ebf4ff; color:#2b6cb0; }
  .draft-btn-view:hover { background:#bee3f8; }
  .draft-btn-copy { background:#c6f6d5; color:#276749; }
  .draft-btn-copy:hover { background:#9ae6b4; }
  .draft-btn-del { background:#fff5f5; color:#c53030; }
  .draft-btn-del:hover { background:#fed7d7; }
  .draft-btn-layout { background:#fef3c7; color:#92400e; }
  .draft-btn-layout:hover { background:#fde68a; }

  /* 排版弹窗 */
  #layoutModal { display:none; position:fixed; inset:0; background:rgba(0,0,0,.5);
    z-index:3000; align-items:center; justify-content:center; }
  #layoutModal.open { display:flex; }
  .layout-modal-box {
    background:#fff; border-radius:16px; width:96vw; max-width:1100px;
    height:88vh; display:flex; flex-direction:column;
    box-shadow:0 20px 60px rgba(0,0,0,.2); overflow:hidden; }
  .layout-modal-header {
    display:flex; align-items:center; justify-content:space-between;
    padding:16px 24px; border-bottom:1px solid #ebebeb; flex-shrink:0; }
  .layout-modal-header h3 { font-size:16px; font-weight:700; color:#111; }
  .layout-modal-body {
    flex:1; display:grid; grid-template-columns:1fr 1fr;
    gap:0; overflow:hidden; }
  .layout-panel { display:flex; flex-direction:column; overflow:hidden; }
  .layout-panel + .layout-panel { border-left:1px solid #ebebeb; }
  .layout-panel-head {
    padding:12px 20px; border-bottom:1px solid #ebebeb;
    font-size:12px; font-weight:600; color:#999;
    letter-spacing:.06em; flex-shrink:0;
    display:flex; align-items:center; justify-content:space-between; }
  .layout-panel-body { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:12px; }
  .layout-chips { display:flex; flex-wrap:wrap; gap:6px; }
  .layout-chip {
    font-size:12px; padding:5px 14px; border-radius:99px;
    border:1px solid #ebebeb; background:#f5f6f8;
    color:#555; cursor:pointer; transition:all .15s; }
  .layout-chip.on { background:#e6f4ff; border-color:#bae0ff; color:#1677ff; font-weight:500; }
  .layout-textarea {
    flex:1; min-height:200px; resize:none;
    border:1px solid #ebebeb; border-radius:10px;
    padding:14px; font-size:14px; line-height:1.85;
    color:#333; background:#f5f6f8;
    font-family:"PingFang SC","Microsoft YaHei",sans-serif;
    outline:none; transition:border .15s; }
  .layout-textarea:focus { border-color:#1677ff; background:#fff; }
  .layout-go-btn {
    padding:12px; border-radius:10px;
    background:#1677ff; color:#fff; border:none;
    font-size:14px; font-weight:600; cursor:pointer;
    font-family:inherit; transition:all .15s; flex-shrink:0; }
  .layout-go-btn:hover:not(:disabled) { background:#0958d9; }
  .layout-go-btn:disabled { opacity:.45; cursor:not-allowed; }
  .layout-preview-scroll { flex:1; overflow-y:auto; padding:20px; }
  .layout-foot {
    padding:12px 20px; border-top:1px solid #ebebeb;
    display:none; gap:8px; flex-shrink:0; }
  .layout-foot.show { display:flex; }
  .layout-copy-main {
    flex:1; padding:10px; border-radius:8px;
    background:#1677ff; color:#fff; border:none;
    font-size:13px; font-weight:600; cursor:pointer; font-family:inherit; }
  .layout-copy-main:hover { background:#0958d9; }
  .layout-copy-sec {
    padding:10px 16px; border-radius:8px;
    border:1px solid #ebebeb; background:#fff;
    color:#555; font-size:13px; cursor:pointer; font-family:inherit; }
  .layout-copy-sec:hover { border-color:#1677ff; color:#1677ff; }
  /* 排版预览样式（复用排版工具） */
  .wx-title { font-size:20px; font-weight:700; line-height:1.4; color:#111; margin-bottom:8px; }
  .wx-lead { font-size:13px; color:#999; line-height:1.7; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid #f0f0f0; }
  .wx-h2 { display:flex; align-items:center; gap:8px; margin:20px 0 10px; background:#f0f7ff; border-radius:4px; padding:9px 12px; }
  .wx-h2-bar { width:4px; height:18px; background:#1677ff; border-radius:2px; flex-shrink:0; }
  .wx-h2-text { font-size:15px; font-weight:700; color:#111; }
  .wx-p { font-size:14px; line-height:1.95; color:#333; margin-bottom:12px; text-indent:1em; }
  .wx-p strong { font-weight:700; color:#111; }
  .wx-highlight { background:#fffbe6; border-left:4px solid #faad14; padding:10px 13px; margin:12px 0; font-size:13.5px; color:#5c3d00; line-height:1.8; border-radius:0 4px 4px 0; }
  .wx-quote { background:#f7f8fa; border-left:4px solid #1677ff; padding:10px 13px; margin:10px 0; font-size:13.5px; color:#555; line-height:1.85; border-radius:0 4px 4px 0; font-style:italic; }
  .wx-divider { display:flex; align-items:center; gap:8px; margin:16px 0; }
  .wx-divider-line { flex:1; height:1px; background:#f0f0f0; }
  .wx-divider-dot { width:4px; height:4px; border-radius:50%; background:#ddd; }
  .wx-img-ph { border:1.5px dashed #91caff; border-radius:8px; padding:12px; margin:12px 0; text-align:center; background:#f5faff; font-size:12px; color:#1677ff; }
  .layout-tags { display:flex; flex-wrap:wrap; gap:5px; margin-bottom:12px; }
  .layout-tag { font-size:11px; padding:2px 8px; border-radius:3px; background:#e6f4ff; color:#1677ff; border:1px solid #bae0ff; }
  .layout-phone { width:320px; margin:0 auto; background:#fff; border-radius:18px; border:1px solid #e0e0e0; box-shadow:0 8px 32px rgba(0,0,0,.1); overflow:hidden; }
  .layout-phone-bar { background:#f9f9f9; padding:10px 16px; display:flex; align-items:center; border-bottom:1px solid #eee; font-size:13px; color:#666; justify-content:center; }
  .layout-phone-content { padding:18px 14px 36px; }
  .layout-err { font-size:12px; color:#c53030; background:#fff5f5; border:1px solid #feb2b2; border-radius:8px; padding:8px 12px; display:none; }
  .draft-preview { font-size:13px; color:#4a5568; line-height:1.7; margin-top:12px;
                   padding:12px; background:#f7fafc; border-radius:8px;
                   display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
  .draft-type-tag { display:inline-block; font-size:11px; padding:2px 8px; border-radius:10px;
                    background:#e9d8fd; color:#553c9a; font-weight:600; margin-left:8px; }
</style>
</head>
<body>

<div class="header" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
  <div>
    <h1>医疗AI 每日情报</h1>
    <p>每天追踪顶刊、arXiv 与行业动态，为科研与产业决策服务</p>
  </div>
  <div style="display:flex;align-items:center;gap:12px;flex-shrink:0">
    <a href="/studio" style="font-size:12px;color:rgba(255,255,255,0.7);text-decoration:none;border:1px solid rgba(255,255,255,0.3);padding:5px 12px;border-radius:6px" title="内容创作工具">✍️ 创作工具</a>
    <div id="authArea" style="display:flex;align-items:center;gap:10px"></div>
  </div>
</div>

<div class="tabs">
  <div class="tab active" id="tab-digest" onclick="switchTab('digest')">📊 今日情报</div>
  <div class="tab" id="tab-starred" onclick="switchTab('starred')">⭐ 收藏</div>
  <div class="tab" id="tab-myfeeds" onclick="switchTab('myfeeds')">📡 自定义订阅</div>
  <div class="tab" id="tab-subscribe" onclick="switchTab('subscribe')">📬 邮件推送</div>
</div>

<div class="toolbar" id="toolbar-digest">
  <div style="flex:1;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
    <span id="statusBadge" class="status-badge">就绪</span>
    <span id="lastUpdateLabel" style="font-size:12px;color:#a0aec0"></span>
  </div>
  <div id="adminTools" style="display:none;align-items:center;gap:8px;flex-wrap:wrap">
    <input type="text" id="topicInput" placeholder="关注方向（可选）" style="width:180px">
    <button class="btn btn-primary" onclick="runPipeline()">🔄 更新情报</button>
    <button class="btn btn-secondary" onclick="toggleLog()">📋 日志</button>
    <button class="btn btn-secondary" onclick="showInviteModal()" style="background:#48bb78">🔗 邀请链接</button>
  </div>

  <!-- 邀请链接弹窗 -->
  <div id="inviteModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:1000;align-items:center;justify-content:center">
    <div style="background:white;border-radius:14px;padding:28px;width:440px;max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,.18)">
      <div style="font-size:17px;font-weight:700;color:#2d3748;margin-bottom:16px">🔗 生成邀请链接</div>
      <div style="font-size:13px;color:#718096;margin-bottom:12px">选择预置领域，访客点链接后会自动选中这些标签</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px" id="inviteTagList"></div>
      <div style="display:flex;gap:10px;margin-top:4px">
        <button onclick="genInviteLink()" style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:9px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;flex:1">生成链接</button>
        <button onclick="document.getElementById('inviteModal').style.display='none'" style="background:#edf2f7;border:none;padding:9px 16px;border-radius:8px;font-size:14px;cursor:pointer">取消</button>
      </div>
      <div id="inviteResult" style="margin-top:14px;display:none">
        <div style="font-size:12px;color:#718096;margin-bottom:6px">链接已生成，点击复制：</div>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="inviteLinkInput" readonly style="flex:1;padding:8px 10px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:13px;background:#f7fafc">
          <button onclick="copyInviteLink()" style="background:#667eea;color:white;border:none;padding:8px 14px;border-radius:7px;font-size:13px;cursor:pointer">复制</button>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="page active" id="page-digest">
  <div class="main">
    <div class="stats-row" id="statsRow"></div>
    <div id="logBox" class="log-box"></div>
    <div id="content"></div>
    <div id="recSection" class="rec-section" style="display:none">
      <div class="rec-section-title">✍️ 今日写作推荐</div>
      <div id="recEditorNote" class="rec-editor-note"></div>
      <div id="recCards" class="rec-cards"></div>
    </div>
  </div>
</div>

<div class="page" id="page-wechat">
  <div class="main">
    <div style="margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
      <div>
        <div style="font-size:18px;font-weight:700;color:#2d3748">💬 公众号文章分析</div>
        <div style="font-size:13px;color:#718096;margin-top:4px">点击「深度分析」，用 AI 解析文章为什么好、踩中了哪些痛点</div>
      </div>
      <button class="btn btn-secondary" onclick="loadWechatArticles()">🔃 刷新</button>
    </div>
    <div id="wxGrid" class="wx-grid">
      <div class="empty"><div style="font-size:36px">⏳</div><p>加载中...</p></div>
    </div>
  </div>
</div>

<!-- 分析弹窗 -->
<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
  <div class="modal" id="analysisModal">
    <button class="modal-close" onclick="closeModal()">×</button>
    <div class="modal-title" id="modalTitle"></div>
    <div id="modalBody"></div>
  </div>
</div>

<div class="page" id="page-starred">
  <div class="main">
    <div style="margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
      <div>
        <div style="font-size:18px;font-weight:700;color:#2d3748">⭐ 收藏文章</div>
        <div style="font-size:13px;color:#718096;margin-top:4px">收藏的文章可直接选中生成公众号文章</div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <span id="starSelCount" style="font-size:13px;color:#667eea;font-weight:600;display:none">已选 0 篇</span>
        <button class="btn-generate" id="starGenBtn" style="display:none" onclick="generateFromStarred()">✍️ 生成公众号文章</button>
        <button class="btn btn-secondary" onclick="clearStarSelection()">清除选择</button>
        <button class="btn btn-secondary" onclick="loadStarred()">🔃 刷新</button>
      </div>
    </div>
    <div id="starredGrid" style="display:grid;gap:12px"></div>
  </div>
</div>

<div class="page" id="page-drafts">
  <div class="main">
    <div style="margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
      <div>
        <div style="font-size:18px;font-weight:700;color:#2d3748">📝 草稿箱</div>
        <div style="font-size:13px;color:#718096;margin-top:4px">暂存的公众号文章，可对比选择后复制使用</div>
      </div>
      <button class="btn btn-secondary" onclick="loadDrafts()">🔃 刷新</button>
    </div>
    <div id="draftsGrid" style="display:grid;gap:16px"></div>
  </div>
</div>

<!-- 订阅Tab -->
<div class="page" id="page-myfeeds">
  <div class="main">

    <!-- 引导说明 -->
    <div style="background:linear-gradient(135deg,#667eea15,#764ba215);border:1px solid #667eea30;border-radius:12px;padding:20px 24px;margin-bottom:24px">
      <div style="font-size:15px;font-weight:700;color:#2d3748;margin-bottom:10px">📡 订阅任意 RSS 源</div>
      <div style="font-size:13px;color:#4a5568;line-height:1.8">
        你可以把任何网站的 RSS 源添加到这里，系统每6小时自动抓取新文章。<br>
        <strong>想订阅微信公众号？</strong> 推荐使用
        <a href="https://werss.app" target="_blank" style="color:#667eea;font-weight:600">WeRSS.app</a>：
        搜索公众号名称 → 复制生成的 RSS 链接 → 粘贴到下方输入框，3步完成。
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <span style="font-size:12px;background:white;border:1px solid #e2e8f0;border-radius:20px;padding:3px 12px;color:#4a5568">arXiv: arxiv.org/rss/cs.AI</span>
        <span style="font-size:12px;background:white;border:1px solid #e2e8f0;border-radius:20px;padding:3px 12px;color:#4a5568">微信公众号: via WeRSS</span>
        <span style="font-size:12px;background:white;border:1px solid #e2e8f0;border-radius:20px;padding:3px 12px;color:#4a5568">任意博客 RSS/Atom URL</span>
      </div>
    </div>

    <!-- 添加新源 -->
    <div style="background:white;border-radius:12px;padding:24px;box-shadow:0 1px 4px rgba(0,0,0,0.08);margin-bottom:24px" id="myfeedsAddBox">
      <div style="font-size:15px;font-weight:600;color:#2d3748;margin-bottom:16px">添加 RSS 源</div>
      <div style="display:grid;gap:12px">
        <div style="display:grid;grid-template-columns:1fr 2fr;gap:12px">
          <div>
            <label style="font-size:13px;font-weight:500;color:#4a5568;display:block;margin-bottom:6px">源名称 *</label>
            <input id="feed-name" type="text" placeholder="如：STAT News"
              style="width:100%;box-sizing:border-box;padding:9px 12px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none"
              onfocus="this.style.borderColor='#667eea'" onblur="this.style.borderColor='#e2e8f0'">
          </div>
          <div>
            <label style="font-size:13px;font-weight:500;color:#4a5568;display:block;margin-bottom:6px">RSS URL *</label>
            <input id="feed-url" type="url" placeholder="https://..."
              style="width:100%;box-sizing:border-box;padding:9px 12px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none"
              onfocus="this.style.borderColor='#667eea'" onblur="this.style.borderColor='#e2e8f0'"
              onkeydown="if(event.key==='Enter')addFeed()">
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <button onclick="addFeed()" id="addFeedBtn"
            style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:9px 22px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
            + 添加并立即抓取
          </button>
          <div id="feed-msg" style="font-size:13px;display:none;padding:6px 12px;border-radius:6px"></div>
        </div>
      </div>
    </div>

    <!-- 未登录提示 -->
    <div id="myfeedsLoginHint" style="display:none;text-align:center;padding:48px;color:#a0aec0">
      <div style="font-size:36px;margin-bottom:12px">🔒</div>
      <p style="font-size:15px;margin-bottom:16px">登录后才能添加和查看你的订阅源</p>
      <button onclick="openAuthModal('login')"
        style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:10px 28px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
        登录 / 注册
      </button>
    </div>

    <!-- 订阅源列表 + 文章区 -->
    <div id="myfeedsContent" style="display:none;display:grid;grid-template-columns:240px 1fr;gap:20px;align-items:start">

      <!-- 左侧：源列表 -->
      <div style="background:white;border-radius:12px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,0.08)">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div style="font-size:14px;font-weight:600;color:#2d3748">我的源</div>
          <button onclick="loadMyFeeds()" style="background:none;border:none;color:#a0aec0;cursor:pointer;font-size:18px" title="刷新">⟳</button>
        </div>
        <div id="feedsList" style="display:grid;gap:6px">
          <div style="font-size:13px;color:#a0aec0;text-align:center;padding:16px">加载中...</div>
        </div>
      </div>

      <!-- 右侧：文章列表 -->
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">
          <div id="feedArticlesTitle" style="font-size:16px;font-weight:700;color:#2d3748">全部文章</div>
          <button onclick="refreshCurrentFeed()" id="refreshFeedBtn" style="display:none;background:#f7fafc;color:#4a5568;border:1.5px solid #e2e8f0;padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer">🔃 立即刷新</button>
        </div>
        <div id="feedArticlesList" style="display:grid;gap:10px">
          <div style="font-size:13px;color:#a0aec0;text-align:center;padding:32px">从左侧选择一个订阅源查看文章</div>
        </div>
      </div>
    </div>

  </div>
</div>

<div class="page" id="page-subscribe">
  <div class="main">

    <!-- 未登录提示 -->
    <div id="subLoginHint" style="display:none;text-align:center;padding:64px 0;color:#a0aec0">
      <div style="font-size:40px;margin-bottom:12px">📬</div>
      <div style="font-size:16px;font-weight:600;color:#4a5568;margin-bottom:8px">登录后设置关键词订阅</div>
      <div style="font-size:13px;margin-bottom:24px">每日从抓取的医疗AI文章中自动筛选，推送到你的邮箱</div>
      <button onclick="openAuthModal('login')"
        style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:10px 28px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
        登录 / 注册
      </button>
    </div>

    <!-- 已登录内容 -->
    <div id="subContent" style="display:none">

      <!-- 顶部状态卡片 -->
      <div id="subStatusCard" style="background:linear-gradient(135deg,#667eea,#764ba2);border-radius:14px;padding:24px 28px;color:white;margin-bottom:24px">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px">
          <div>
            <div style="font-size:13px;opacity:0.8;margin-bottom:4px">当前推送邮箱</div>
            <div style="font-size:16px;font-weight:700" id="subEmailDisplay">—</div>
          </div>
          <div id="subStatusBadge"
            style="background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);border-radius:20px;padding:5px 14px;font-size:13px;font-weight:600">
            未订阅
          </div>
        </div>
        <div style="margin-top:16px" id="subKeywordsDisplay" style="display:none">
          <div style="font-size:12px;opacity:0.75;margin-bottom:6px">当前关键词</div>
          <div id="subKwTags" style="display:flex;flex-wrap:wrap;gap:6px"></div>
        </div>
      </div>

      <!-- 领域标签选择区 -->
      <div style="background:white;border-radius:12px;padding:24px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:20px">
        <div style="font-size:15px;font-weight:600;color:#2d3748;margin-bottom:6px" id="subFormTitle">选择推送领域</div>
        <div style="font-size:13px;color:#718096;margin-bottom:16px">勾选你关注的方向，系统每日自动匹配相关文章推送到你的邮箱</div>
        <div style="display:grid;gap:16px">
          <!-- 标签选择 -->
          <div id="subTagArea">
            <div style="font-size:12px;font-weight:600;color:#a0aec0;letter-spacing:.06em;margin-bottom:10px">选择领域（可多选）</div>
            <div style="display:flex;flex-wrap:wrap;gap:8px" id="subTagList"></div>
          </div>
          <!-- 高级：自定义关键词 -->
          <details style="margin-top:4px">
            <summary style="font-size:13px;color:#667eea;cursor:pointer;font-weight:500">＋ 高级：自定义关键词</summary>
            <div style="margin-top:10px">
              <label style="font-size:13px;font-weight:500;color:#4a5568;display:block;margin-bottom:6px">
                额外关键词
                <span style="font-weight:400;color:#a0aec0">（逗号分隔，中英文均可）</span>
              </label>
              <input id="sub-keywords" type="text" placeholder="如：心电图,ECG,AI诊断"
                style="width:100%;box-sizing:border-box;padding:10px 12px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none"
                onfocus="this.style.borderColor='#667eea'" onblur="this.style.borderColor='#e2e8f0'">
            </div>
          </details>
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
            <button onclick="subSaveKeywords()"
              style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:9px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
              ✅ 保存并开启每日推送
            </button>
            <button onclick="subPreview()"
              style="background:#ebf4ff;color:#3182ce;border:none;padding:9px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
              🔍 预览匹配文章
            </button>
            <button onclick="subTestPush()" id="subTestBtn"
              style="background:#f7fafc;color:#4a5568;border:1.5px solid #e2e8f0;padding:9px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
              📤 立即推送一次
            </button>
            <button onclick="subCancel()" id="subCancelBtn" style="display:none;background:#fff5f5;color:#e53e3e;border:1.5px solid #fed7d7;padding:9px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
              🗑 取消订阅
            </button>
          </div>
          <div id="sub-msg" style="font-size:13px;display:none;padding:8px 12px;border-radius:6px"></div>
        </div>
      </div>

      <!-- 文章预览区 -->
      <div id="subPreviewArea" style="display:none">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
          <div style="font-size:15px;font-weight:600;color:#2d3748">
            匹配文章预览
            <span id="subPreviewCount" style="font-size:13px;font-weight:400;color:#718096;margin-left:8px"></span>
          </div>
          <div style="font-size:12px;color:#a0aec0">近7天数据</div>
        </div>
        <div id="subPreviewList" style="display:grid;gap:10px"></div>
      </div>

    </div>
  </div>
</div>

<!-- 生成文章弹窗 -->
<div class="modal-overlay" id="genOverlay" onclick="closeGenModal(event)">
  <div class="modal" id="genModal" style="max-width:860px">
    <button class="modal-close" onclick="closeGenModal()">×</button>
    <div class="modal-title" id="genModalTitle">✍️ 微信公众号文章生成</div>
    <div id="genModalBody"></div>
  </div>
</div>

<!-- 底部选择生成悬浮栏 -->
<div class="generate-bar" id="generateBar">
  <span class="sel-count" id="selCount">已选 0 篇</span>
  <span class="sel-hint">选择2-5篇相关文章，一键生成微信公众号文章（三轮自动审核）</span>
  <button class="btn-generate" id="genBtn" onclick="generateFromSelected()">✍️ 生成公众号文章</button>
  <button class="btn btn-secondary" onclick="clearSelection()">清除选择</button>
</div>

<!-- 登录/注册弹窗 -->
<div id="authModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9999;align-items:center;justify-content:center">
  <div style="background:white;border-radius:16px;width:90%;max-width:400px;padding:32px 28px;box-shadow:0 20px 60px rgba(0,0,0,.2);position:relative">
    <button onclick="closeAuthModal()" style="position:absolute;top:16px;right:20px;background:none;border:none;font-size:22px;color:#a0aec0;cursor:pointer;line-height:1">×</button>
    <div style="display:flex;gap:0;margin-bottom:24px;border-bottom:2px solid #e2e8f0">
      <button id="authTabLogin" onclick="switchAuthTab('login')"
        style="flex:1;padding:10px;background:none;border:none;font-size:15px;font-weight:700;color:#667eea;border-bottom:3px solid #667eea;margin-bottom:-2px;cursor:pointer">登录</button>
      <button id="authTabRegister" onclick="switchAuthTab('register')"
        style="flex:1;padding:10px;background:none;border:none;font-size:15px;font-weight:600;color:#a0aec0;border-bottom:3px solid transparent;margin-bottom:-2px;cursor:pointer">注册</button>
    </div>
    <div style="display:grid;gap:14px">
      <div>
        <label style="font-size:13px;font-weight:500;color:#4a5568;display:block;margin-bottom:6px">邮箱</label>
        <input id="authEmail" type="email" placeholder="your@email.com" autocomplete="email"
          style="width:100%;box-sizing:border-box;padding:10px 12px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none"
          onfocus="this.style.borderColor='#667eea'" onblur="this.style.borderColor='#e2e8f0'">
      </div>
      <div>
        <label style="font-size:13px;font-weight:500;color:#4a5568;display:block;margin-bottom:6px">密码</label>
        <input id="authPassword" type="password" placeholder="至少6位" autocomplete="current-password"
          style="width:100%;box-sizing:border-box;padding:10px 12px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none"
          onfocus="this.style.borderColor='#667eea'" onblur="this.style.borderColor='#e2e8f0'"
          onkeydown="if(event.key==='Enter')doAuth()">
      </div>
      <div id="authMsg" style="font-size:13px;display:none;padding:8px 12px;border-radius:6px"></div>
      <button onclick="doAuth()" id="authSubmitBtn"
        style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:11px;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer;width:100%">登录</button>
      <p id="authSwitchHint" style="text-align:center;font-size:13px;color:#718096;margin:0">
        没有账号？<a href="#" onclick="switchAuthTab('register');return false" style="color:#667eea;font-weight:600">立即注册</a>
      </p>
    </div>
  </div>
</div>

<script>
let polling = null;
let _currentUser = null;

async function loadData() {
  const res = await fetch('/api/digest');
  const data = await res.json();
  renderStats(data.stats);
  renderDigest(data.digest);
}

function renderStats(s) {
  document.getElementById('statsRow').innerHTML = `
    <div class="stat-card"><div class="num">${s.today || 0}</div><div class="label">今日新增</div></div>
    <div class="stat-card"><div class="num">${s.medical || s.articles}</div><div class="label">医疗相关文章</div></div>
    <div class="stat-card"><div class="num">${s.sources || '—'}</div><div class="label">覆盖来源</div></div>
  `;
}

function scoreColor(s) {
  if (s >= 8) return '#48bb78';
  if (s >= 6) return '#ed8936';
  return '#a0aec0';
}

function renderDigest(digest) {
  const sections = [
    { key: '顶刊论文', icon: '📄', cls: 'journal' },
    { key: '大组动态', icon: '🏛', cls: 'lab' },
    { key: '商业落地', icon: '🏢', cls: 'biz' },
    { key: '开源项目', icon: '💻', cls: 'github' },
  ];
  let html = '';
  let hasAny = false;
  for (const sec of sections) {
    const items = digest[sec.key] || [];
    if (!items.length) continue;
    hasAny = true;
    html += `<div class="section">
      <div class="section-title">
        ${sec.icon} ${sec.key}
        <span class="badge">${items.length} 篇</span>
      </div>
      <div class="articles">`;
    for (const a of items) {
      const link = a.url ? `<a href="${a.url}" target="_blank">${a.title}</a>` : a.title;
      const zhLine = a.title_zh ? `<div class="article-title-zh">🔤 ${a.title_zh}</div>` : '';
      const datePart = a.date ? ` · ${a.date}` : '';
      html += `<div class="article-card ${sec.cls}" id="acard-${a.id}">
        <div class="article-select-wrap">
          <input type="checkbox" id="chk-${a.id}" value="${a.id}" onchange="onArticleCheck(${a.id}, this.checked)">
          <div class="article-card-body">
            <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
              <div class="article-title" style="flex:1">${link}</div>
              <button class="star-btn" id="star-${a.id}" onclick="toggleStar(${a.id})" title="收藏" style="background:none;border:none;cursor:pointer;font-size:18px;line-height:1;flex-shrink:0;opacity:0.4" onmouseover="this.style.opacity=1" onmouseout="if(!this.dataset.starred)this.style.opacity=0.4">☆</button>
            </div>
            ${zhLine}
            <div class="article-meta">
              <span class="score-dot" style="background:${scoreColor(a.score)}"></span>
              ${a.score.toFixed(1)}分 · ${a.source}${datePart}
            </div>
            ${a.summary ? `<div class="article-summary">${a.summary}</div>` : ''}
          </div>
        </div>
      </div>`;
    }
    html += `</div></div>`;
  }
  if (!hasAny) {
    html = `<div class="empty">
      <div style="font-size:48px">📭</div>
      <p>暂无今日情报，点击「一键更新情报」开始抓取</p>
    </div>`;
  }
  document.getElementById('content').innerHTML = html;
}

async function runPipeline() {
  if (!requireLogin()) return;
  const topic = document.getElementById('topicInput').value.trim();
  const badge = document.getElementById('statusBadge');
  badge.textContent = '运行中...';
  badge.className = 'status-badge running';
  document.getElementById('logBox').innerHTML = '';

  await fetch('/api/run', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({topic})
  });

  polling = setInterval(async () => {
    const res = await fetch('/api/status');
    const s = await res.json();
    updateLog(s.log);
    badge.textContent = s.running ? `${s.step}...` : '完成';
    if (!s.running) {
      badge.className = 'status-badge';
      clearInterval(polling);
      await loadData();
    }
  }, 1500);
}

function updateLog(lines) {
  const box = document.getElementById('logBox');
  box.innerHTML = lines.map(l => {
    const cls = l.startsWith('✓') || l.startsWith('▶') ? 'ok' : l.startsWith('错误') ? 'err' : '';
    return `<div class="log-line ${cls}">${l}</div>`;
  }).join('');
  box.scrollTop = box.scrollHeight;
}

function toggleLog() {
  document.getElementById('logBox').classList.toggle('show');
}

async function loadRecommendations() {
  const sec = document.getElementById('recSection');
  const cards = document.getElementById('recCards');
  const note = document.getElementById('recEditorNote');
  cards.innerHTML = '<div style="color:#a0aec0;font-size:13px">正在生成写作推荐，约需15秒...</div>';
  sec.style.display = 'block';

  const topic = document.getElementById('topicInput').value.trim();
  const res = await fetch('/api/recommend' + (topic ? `?topic=${encodeURIComponent(topic)}` : ''));
  const data = await res.json();
  const recs = data.recommendations || [];

  if (!recs.length || recs[0]?.error) {
    cards.innerHTML = '<div style="color:#a0aec0;font-size:13px">暂无推荐</div>';
    return;
  }

  if (recs[0]?.editor_note) {
    note.textContent = '📝 编辑评语：' + recs[0].editor_note;
  }

  const laneColors = {'Lane A': '#ebf8ff', 'Lane B': '#f0fff4'};
  const laneText = {'Lane A': '大组研究解析', 'Lane B': '产品/应用落地'};

  storeRecData(recs);
  cards.innerHTML = recs.map((r, i) => `
    <div class="rec-card">
      <div class="rec-rank">推荐 #${r.rank || i+1}</div>
      <div class="rec-title">${r.working_title}</div>
      <span class="rec-lane" style="background:${laneColors[r.lane]||'#f7fafc'}">${r.lane} · ${laneText[r.lane]||''}</span>
      ${r.hook ? `<div style="background:#fffbeb;border-left:3px solid #f59e0b;border-radius:4px;padding:8px 12px;margin-top:10px;font-size:13px;color:#92400e"><b>⚡ 传播钩子：</b>${r.hook}</div>` : ''}
      <div class="rec-field">
        <div class="rec-label">核心观点</div>
        <div class="rec-value">${r.core_viewpoint}</div>
      </div>
      <div class="rec-field">
        <div class="rec-label">为什么今天写</div>
        <div class="rec-value">${r.why_write}</div>
      </div>
      <div class="rec-field">
        <div class="rec-label">标题候选</div>
        <div class="rec-titles">
          ${(r.titles||[]).map((t,ti)=>`<div class="rec-title-item" style="font-weight:${ti===0?'600':'400'}">${['判断型','数字型','场景型'][ti]||''}· ${t}</div>`).join('')}
        </div>
      </div>
      <button class="rec-gen-btn" id="recgenbtn-${i}" onclick="generateFromRec(${i})">✍️ 生成完整公众号文章（三轮审核）</button>
      <button class="rec-toggle" onclick="this.nextElementSibling.classList.toggle('open');this.textContent=this.nextElementSibling.classList.contains('open')?'▲ 收起详情':'▼ 展开大纲与详情'">▼ 展开大纲与详情</button>
      <div class="rec-detail">
        <div class="rec-field">
          <div class="rec-label">核心问题</div>
          <div class="rec-value">${r.core_question}</div>
        </div>
        <div class="rec-field">
          <div class="rec-label">切入点</div>
          <div class="rec-value">${r.writing_angle}</div>
        </div>
        <div class="rec-field">
          <div class="rec-label" style="color:#c05621">⚠ 要避免的坑</div>
          <div class="rec-value" style="color:#c05621">${r.risks}</div>
        </div>
        <div class="rec-field">
          <div class="rec-label">文章大纲</div>
          <ol class="rec-outline">
            ${(r.outline||[]).map(o=>`<li>${o}</li>`).join('')}
          </ol>
        </div>
      </div>
    </div>
  `).join('');
}

// ── 认证 ──────────────────────────────────────────────
let _authMode = 'login';
let _afterLoginCallback = null;

function openAuthModal(mode, callback) {
  _authMode = mode || 'login';
  _afterLoginCallback = callback || null;
  switchAuthTab(_authMode);
  document.getElementById('authEmail').value = '';
  document.getElementById('authPassword').value = '';
  document.getElementById('authMsg').style.display = 'none';
  document.getElementById('authModal').style.display = 'flex';
  setTimeout(() => document.getElementById('authEmail').focus(), 50);
}

function closeAuthModal() {
  document.getElementById('authModal').style.display = 'none';
}

function switchAuthTab(tab) {
  _authMode = tab;
  const isLogin = tab === 'login';
  document.getElementById('authTabLogin').style.cssText =
    isLogin ? 'flex:1;padding:10px;background:none;border:none;font-size:15px;font-weight:700;color:#667eea;border-bottom:3px solid #667eea;margin-bottom:-2px;cursor:pointer'
            : 'flex:1;padding:10px;background:none;border:none;font-size:15px;font-weight:600;color:#a0aec0;border-bottom:3px solid transparent;margin-bottom:-2px;cursor:pointer';
  document.getElementById('authTabRegister').style.cssText =
    !isLogin ? 'flex:1;padding:10px;background:none;border:none;font-size:15px;font-weight:700;color:#667eea;border-bottom:3px solid #667eea;margin-bottom:-2px;cursor:pointer'
             : 'flex:1;padding:10px;background:none;border:none;font-size:15px;font-weight:600;color:#a0aec0;border-bottom:3px solid transparent;margin-bottom:-2px;cursor:pointer';
  document.getElementById('authSubmitBtn').textContent = isLogin ? '登录' : '注册';
  const hint = document.getElementById('authSwitchHint');
  if (isLogin) {
    hint.innerHTML = '没有账号？';
    const a = document.createElement('a');
    a.href = '#'; a.style.cssText = 'color:#667eea;font-weight:600';
    a.textContent = '立即注册';
    a.onclick = function(){ switchAuthTab('register'); return false; };
    hint.appendChild(a);
  } else {
    hint.innerHTML = '已有账号？';
    const a = document.createElement('a');
    a.href = '#'; a.style.cssText = 'color:#667eea;font-weight:600';
    a.textContent = '去登录';
    a.onclick = function(){ switchAuthTab('login'); return false; };
    hint.appendChild(a);
  }
  document.getElementById('authMsg').style.display = 'none';
}

async function doAuth() {
  const email = document.getElementById('authEmail').value.trim();
  const password = document.getElementById('authPassword').value;
  const msgEl = document.getElementById('authMsg');
  const btn = document.getElementById('authSubmitBtn');
  if (!email || !password) {
    showAuthMsg('邮箱和密码不能为空', 'error'); return;
  }
  btn.disabled = true; btn.textContent = '处理中...';
  const url = _authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password})
    });
    const data = await res.json();
    if (data.ok) {
      _currentUser = {email: data.email};
      renderAuthArea();
      closeAuthModal();
      if (_afterLoginCallback) {
        const cb = _afterLoginCallback;
        _afterLoginCallback = null;
        await cb();
      } else {
        const activeTab = document.querySelector('.tab.active');
        if (activeTab) {
          const tab = activeTab.id.replace('tab-', '');
          if (tab === 'subscribe') initSubscribePage();
          if (tab === 'myfeeds') initMyFeeds();
        }
      }
    } else {
      showAuthMsg(data.msg || '操作失败', 'error');
    }
  } catch(e) {
    showAuthMsg('网络错误', 'error');
  }
  btn.disabled = false; btn.textContent = _authMode === 'login' ? '登录' : '注册';
}

function showAuthMsg(msg, type) {
  const el = document.getElementById('authMsg');
  el.textContent = msg;
  el.style.display = 'block';
  el.style.background = type === 'error' ? '#fff5f5' : '#f0fff4';
  el.style.color = type === 'error' ? '#c53030' : '#276749';
  el.style.border = type === 'error' ? '1px solid #fed7d7' : '1px solid #9ae6b4';
}

async function doLogout() {
  await fetch('/api/auth/logout', {method: 'POST'});
  _currentUser = null;
  renderAuthArea();
  const activeTab = document.querySelector('.tab.active');
  if (activeTab) {
    const tab = activeTab.id.replace('tab-', '');
    if (tab === 'subscribe') initSubscribePage();
    if (tab === 'myfeeds') initMyFeeds();
  }
}

function renderAuthArea() {
  const el = document.getElementById('authArea');
  if (_currentUser) {
    el.innerHTML = `
      <span style="font-size:13px;opacity:0.85">${_currentUser.email}</span>
      <button onclick="doLogout()" style="background:rgba(255,255,255,0.2);border:1px solid rgba(255,255,255,0.4);color:white;padding:6px 14px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">退出</button>
    `;
  } else {
    el.innerHTML = `
      <span style="font-size:12px;opacity:0.7;background:rgba(255,255,255,0.15);padding:3px 8px;border-radius:10px">游客模式</span>
      <button onclick="openAuthModal('login')" style="background:rgba(255,255,255,0.2);border:1px solid rgba(255,255,255,0.4);color:white;padding:6px 14px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">登录</button>
      <button onclick="openAuthModal('register')" style="background:white;border:none;color:#667eea;padding:6px 14px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer">注册</button>
    `;
  }
}

function requireLogin(callback) {
  if (_currentUser) return true;
  openAuthModal('login', callback || null);
  return false;
}

// 初始化：检查今日是否已拉取
async function initPage() {
  const [stateRes, meRes] = await Promise.all([
    fetch('/api/fetch-state'),
    fetch('/api/auth/me')
  ]);
  const data = await stateRes.json();
  const me = await meRes.json();
  if (me.logged_in) _currentUser = {email: me.email};
  renderAuthArea();

  const today = new Date().toISOString().slice(0, 10);
  const lastFetch = data.last_fetch_date || '';
  const badge = document.getElementById('statusBadge');
  const label = document.getElementById('lastUpdateLabel');
  if (lastFetch === today) {
    badge.textContent = '今日已更新';
    badge.className = 'status-badge';
    if (label) label.textContent = '更新于 ' + lastFetch;
  } else {
    badge.textContent = lastFetch ? '上次：' + lastFetch : '待更新';
    badge.className = 'status-badge running';
  }

  // 管理员工具只对指定邮箱显示
  const ADMIN_EMAILS = ['2471149840@qq.com', 'zhengwenxin79@gmail.com'];
  if (me.logged_in && ADMIN_EMAILS.includes(me.email)) {
    const adminTools = document.getElementById('adminTools');
    if (adminTools) adminTools.style.display = 'flex';
  }

  // 邀请链接处理：读取 ?invite=xxx，跳转订阅页并预选标签
  const inviteToken = new URLSearchParams(location.search).get('invite');
  if (inviteToken) {
    const res = await fetch('/api/invite/info?token=' + encodeURIComponent(inviteToken));
    const data = await res.json();
    if (data.ok) {
      // 清除 URL 参数，避免刷新重复触发
      history.replaceState({}, '', '/');
      switchTab('subscribe');
      // 等待订阅页初始化完成后预选标签
      await initSubscribePage();
      if (data.domain_tags) {
        renderSubTags(data.domain_tags);
        // 同步到关键词输入框
        const tagKws = DOMAIN_TAGS
          .filter(t => _selectedTags.has(t.label))
          .flatMap(t => t.keywords.split(','));
        document.getElementById('sub-keywords').value = [...new Set(tagKws)].join(',');
      }
      // 未登录时滚动到顶部，让用户看到登录提示
      if (!me.logged_in) window.scrollTo(0, 0);
    }
  }
}

initPage();
loadData();

// ── 邀请链接管理 ───────────────────────────────────────
let _inviteSelectedTags = new Set();

function showInviteModal() {
  const modal = document.getElementById('inviteModal');
  const tagList = document.getElementById('inviteTagList');
  _inviteSelectedTags.clear();
  document.getElementById('inviteResult').style.display = 'none';
  tagList.innerHTML = DOMAIN_TAGS.map(t =>
    `<button onclick="toggleInviteTag('${t.label}')" id="invitetag-${t.label.replace(/[^a-zA-Z0-9]/g,'_')}"
      style="padding:6px 13px;border-radius:18px;font-size:13px;font-weight:500;cursor:pointer;
             border:1.5px solid #e2e8f0;background:white;color:#4a5568">${t.label}</button>`
  ).join('');
  modal.style.display = 'flex';
}

function toggleInviteTag(label) {
  const id = 'invitetag-' + label.replace(/[^a-zA-Z0-9]/g,'_');
  const btn = document.getElementById(id);
  if (_inviteSelectedTags.has(label)) {
    _inviteSelectedTags.delete(label);
    btn.style.background = 'white'; btn.style.color = '#4a5568'; btn.style.borderColor = '#e2e8f0';
  } else {
    _inviteSelectedTags.add(label);
    btn.style.background = '#ebf4ff'; btn.style.color = '#667eea'; btn.style.borderColor = '#667eea';
  }
}

async function genInviteLink() {
  const tagKws = DOMAIN_TAGS
    .filter(t => _inviteSelectedTags.has(t.label))
    .flatMap(t => t.keywords.split(','));
  const domain_tags = [...new Set(tagKws)].join(',');
  const res = await fetch('/api/invite/create', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ domain_tags })
  });
  const data = await res.json();
  if (data.ok) {
    const link = location.origin + '/?invite=' + data.token;
    document.getElementById('inviteLinkInput').value = link;
    document.getElementById('inviteResult').style.display = 'block';
  }
}

function copyInviteLink() {
  const input = document.getElementById('inviteLinkInput');
  navigator.clipboard.writeText(input.value).then(() => {
    const btn = input.nextElementSibling;
    btn.textContent = '已复制 ✓'; btn.style.background = '#48bb78';
    setTimeout(() => { btn.textContent = '复制'; btn.style.background = '#667eea'; }, 2000);
  });
}

// ── Tab 切换 ──────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const tabEl = document.getElementById('tab-' + tab);
  const pageEl = document.getElementById('page-' + tab);
  if (tabEl) tabEl.classList.add('active');
  if (pageEl) pageEl.classList.add('active');
  document.getElementById('toolbar-digest').style.display = tab === 'digest' ? 'flex' : 'none';
  if (tab === 'starred') loadStarred();
  if (tab === 'subscribe') initSubscribePage();
  if (tab === 'myfeeds') initMyFeeds();
}

// ── 我的订阅 ──────────────────────────────────────────
let _currentFeedId = null;

function initMyFeeds() {
  const addBox = document.getElementById('myfeedsAddBox');
  const hint = document.getElementById('myfeedsLoginHint');
  const content = document.getElementById('myfeedsContent');
  if (_currentUser) {
    addBox.style.display = 'block';
    hint.style.display = 'none';
    content.style.display = 'grid';
    loadMyFeeds();
  } else {
    addBox.style.display = 'none';
    hint.style.display = 'block';
    content.style.display = 'none';
  }
}

async function addFeed() {
  if (!requireLogin()) return;
  const name = document.getElementById('feed-name').value.trim();
  const url = document.getElementById('feed-url').value.trim();
  const btn = document.getElementById('addFeedBtn');
  const msg = document.getElementById('feed-msg');
  btn.disabled = true; btn.textContent = '添加中...';
  try {
    const res = await fetch('/api/myfeeds/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, url})
    });
    const data = await res.json();
    msg.style.display = 'block';
    if (data.ok) {
      msg.textContent = '添加成功，正在后台抓取文章...';
      msg.style.background = '#f0fff4'; msg.style.color = '#276749';
      document.getElementById('feed-name').value = '';
      document.getElementById('feed-url').value = '';
      setTimeout(() => { loadMyFeeds(); loadFeedArticles(data.id); }, 3000);
    } else {
      msg.textContent = data.msg || '添加失败';
      msg.style.background = '#fff5f5'; msg.style.color = '#c53030';
    }
  } catch(e) {
    msg.style.display = 'block';
    msg.textContent = '网络错误';
    msg.style.background = '#fff5f5'; msg.style.color = '#c53030';
  }
  btn.disabled = false; btn.textContent = '+ 添加并立即抓取';
}

async function loadMyFeeds() {
  const list = document.getElementById('feedsList');
  list.innerHTML = '<div style="font-size:13px;color:#a0aec0;text-align:center;padding:16px">加载中...</div>';
  const res = await fetch('/api/myfeeds');
  if (res.status === 401) { initMyFeeds(); return; }
  const data = await res.json();
  const feeds = data.feeds || [];
  if (!feeds.length) {
    list.innerHTML = '<div style="font-size:13px;color:#a0aec0;text-align:center;padding:16px">还没有订阅源，在上方添加</div>';
    return;
  }
  list.innerHTML = feeds.map(f => {
    const active = _currentFeedId === f.id;
    return `<div class="feed-item${active ? ' active' : ''}" id="feeditem-${f.id}"
      onclick="loadFeedArticles(${f.id})"
      style="padding:10px 12px;border-radius:8px;cursor:pointer;border:1.5px solid ${active ? '#667eea' : '#e2e8f0'};background:${active ? '#ebf4ff' : 'white'};transition:all .15s">
      <div style="font-size:13px;font-weight:600;color:#2d3748;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escHtml(f.name)}</div>
      <div style="display:flex;align-items:center;justify-content:space-between;margin-top:4px">
        <div style="font-size:11px;color:#a0aec0">${f.last_fetched_at ? '已同步' : '待同步'}</div>
        <button onclick="event.stopPropagation();deleteFeed(${f.id})"
          style="background:none;border:none;color:#fc8181;cursor:pointer;font-size:12px;padding:0">删除</button>
      </div>
    </div>`;
  }).join('') + `<div onclick="loadFeedArticles(null)"
    style="padding:10px 12px;border-radius:8px;cursor:pointer;border:1.5px solid #e2e8f0;background:white;font-size:13px;font-weight:600;color:#667eea;text-align:center;margin-top:4px">
    全部文章
  </div>`;
}

async function loadFeedArticles(feedId) {
  _currentFeedId = feedId;
  // 更新选中样式
  document.querySelectorAll('[id^="feeditem-"]').forEach(el => {
    const id = parseInt(el.id.split('-')[1]);
    el.style.borderColor = id === feedId ? '#667eea' : '#e2e8f0';
    el.style.background = id === feedId ? '#ebf4ff' : 'white';
  });
  const title = document.getElementById('feedArticlesTitle');
  const refreshBtn = document.getElementById('refreshFeedBtn');
  const list = document.getElementById('feedArticlesList');
  title.textContent = feedId ? '文章列表' : '全部文章';
  refreshBtn.style.display = feedId ? 'block' : 'none';
  list.innerHTML = '<div style="font-size:13px;color:#a0aec0;text-align:center;padding:32px">加载中...</div>';
  const url = feedId ? '/api/myfeeds/articles?feed_id=' + feedId : '/api/myfeeds/articles';
  const res = await fetch(url);
  const data = await res.json();
  const articles = data.articles || [];
  if (!articles.length) {
    list.innerHTML = '<div style="font-size:13px;color:#a0aec0;text-align:center;padding:48px"><div style="font-size:32px;margin-bottom:8px">📭</div>暂无文章，稍后自动抓取或点击「立即刷新」</div>';
    return;
  }
  list.innerHTML = articles.map(a => `
    <div style="background:white;border-radius:12px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:4px solid #667eea">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
        <div>
          <div style="font-size:14px;font-weight:600;color:#2d3748;line-height:1.5">
            ${a.url ? `<a href="${escHtml(a.url)}" target="_blank" style="color:#2d3748;text-decoration:none" onmouseover="this.style.color='#667eea'" onmouseout="this.style.color='#2d3748'">${escHtml(a.title)}</a>` : escHtml(a.title)}
          </div>
          <div style="font-size:12px;color:#a0aec0;margin-top:5px">
            <span style="background:#ebf4ff;color:#3182ce;border-radius:10px;padding:1px 8px;font-size:11px;margin-right:6px">${escHtml(a.feed_name)}</span>
            ${a.published_at ? a.published_at.slice(0,10) : a.fetched_at.slice(0,10)}
          </div>
          ${a.content ? `<div style="font-size:13px;color:#4a5568;margin-top:8px;line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escHtml(a.content.replace(/<[^>]+>/g,''))}</div>` : ''}
        </div>
      </div>
    </div>
  `).join('');
}

async function deleteFeed(feedId) {
  if (!confirm('删除该订阅源及其所有文章？')) return;
  await fetch('/api/myfeeds/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: feedId})
  });
  if (_currentFeedId === feedId) {
    _currentFeedId = null;
    document.getElementById('feedArticlesList').innerHTML = '<div style="font-size:13px;color:#a0aec0;text-align:center;padding:32px">从左侧选择一个订阅源查看文章</div>';
    document.getElementById('refreshFeedBtn').style.display = 'none';
  }
  loadMyFeeds();
}

async function refreshCurrentFeed() {
  if (!_currentFeedId) return;
  const btn = document.getElementById('refreshFeedBtn');
  btn.textContent = '刷新中...'; btn.disabled = true;
  await fetch('/api/myfeeds/refresh', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: _currentFeedId})
  });
  btn.textContent = '🔃 立即刷新'; btn.disabled = false;
  setTimeout(() => loadFeedArticles(_currentFeedId), 15000);
}

// ── 收藏功能 ──────────────────────────────────────────
async function toggleStar(id) {
  if (!requireLogin()) return;
  const btn = document.getElementById('star-' + id);
  const isStarred = btn.dataset.starred === '1';
  const newVal = !isStarred;
  await fetch('/api/star', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id, starred: newVal})
  });
  btn.dataset.starred = newVal ? '1' : '';
  btn.textContent = newVal ? '★' : '☆';
  btn.style.opacity = newVal ? '1' : '0.4';
  btn.style.color = newVal ? '#f6ad55' : '';
}

// ── 收藏页 ────────────────────────────────────────────
let starSelected = new Set();

async function loadStarred() {
  const grid = document.getElementById('starredGrid');
  grid.innerHTML = '<div style="color:#a0aec0;font-size:13px;padding:20px">加载中...</div>';
  const res = await fetch('/api/starred');
  const data = await res.json();
  const articles = data.articles || [];
  if (!articles.length) {
    grid.innerHTML = '<div style="text-align:center;padding:48px;color:#a0aec0"><div style="font-size:36px">⭐</div><p style="margin-top:8px;font-size:14px">还没有收藏，在每日情报里点击 ☆ 收藏文章</p></div>';
    return;
  }
  const clsMap = {'顶刊论文':'journal','大组动态':'lab','商业落地':'biz','开源项目':'github'};
  grid.innerHTML = articles.map(a => {
    const cls = clsMap[a.category] || '';
    const link = a.url ? `<a href="${a.url}" target="_blank">${a.title}</a>` : a.title;
    const datePart = a.date ? ` · ${a.date}` : '';
    return `<div class="article-card ${cls}" id="sacard-${a.id}">
      <div class="article-select-wrap">
        <input type="checkbox" id="schk-${a.id}" value="${a.id}" onchange="onStarCheck(${a.id}, this.checked)">
        <div class="article-card-body">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
            <div class="article-title" style="flex:1">${link}</div>
            <button onclick="unstar(${a.id})" title="取消收藏" style="background:none;border:none;cursor:pointer;font-size:18px;color:#f6ad55;flex-shrink:0">★</button>
          </div>
          <div class="article-meta">
            <span class="score-dot" style="background:${scoreColor(a.score || 0)}"></span>
            ${(a.score||0).toFixed(1)}分 · ${a.source_name || a.source}${datePart}
            ${a.category ? ' · ' + a.category : ''}
          </div>
          ${a.summary ? `<div class="article-summary">${a.summary}</div>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
  window._starredData = {};
  articles.forEach(a => { window._starredData[a.id] = a; });
}

function onStarCheck(id, checked) {
  if (checked) starSelected.add(id); else starSelected.delete(id);
  const count = starSelected.size;
  const countEl = document.getElementById('starSelCount');
  const genBtn = document.getElementById('starGenBtn');
  countEl.textContent = '已选 ' + count + ' 篇';
  countEl.style.display = count > 0 ? 'inline' : 'none';
  genBtn.style.display = count > 0 ? 'inline-block' : 'none';
}

function clearStarSelection() {
  starSelected.forEach(id => {
    const chk = document.getElementById('schk-' + id);
    if (chk) chk.checked = false;
  });
  starSelected.clear();
  onStarCheck(0, false);
}

async function unstar(id) {
  await fetch('/api/star', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id, starred: false})
  });
  document.getElementById('sacard-' + id)?.remove();
  // 同步更新情报页的星号状态
  const btn = document.getElementById('star-' + id);
  if (btn) { btn.dataset.starred = ''; btn.textContent = '☆'; btn.style.color = ''; btn.style.opacity = '0.4'; }
}

async function generateFromStarred() {
  if (!requireLogin()) return;
  if (starSelected.size === 0) return;
  const btn = document.getElementById('starGenBtn');
  btn.disabled = true;
  btn.textContent = '生成中...';
  showGenModal('正在生成微信公众号文章（三轮自我审核）...', null);
  try {
    const res = await fetch('/api/generate/from-articles', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ article_ids: Array.from(starSelected) })
    });
    const data = await res.json();
    if (data.error) {
      showGenModal('生成失败：' + data.error, null);
    } else {
      pollGenTask(data.task_id);
    }
  } catch(e) {
    showGenModal('请求失败：' + e.message, null);
  } finally {
    btn.disabled = false;
    btn.textContent = '✍️ 生成公众号文章';
  }
}

// ── 公众号文章列表 ────────────────────────────────────
let wxLoaded = false;
async function loadWechatArticles() {
  const grid = document.getElementById('wxGrid');
  if (!wxLoaded) grid.innerHTML = '<div class="empty"><div style="font-size:36px">⏳</div><p>加载中...</p></div>';
  const res = await fetch('/api/wechat');
  const data = await res.json();
  const articles = data.articles || [];
  wxLoaded = true;
  if (!articles.length) {
    grid.innerHTML = '<div class="empty"><div style="font-size:36px">📭</div><p>暂无公众号文章，请先运行「一键更新情报」</p></div>';
    return;
  }
  grid.innerHTML = articles.map(a => `
    <div class="wx-card">
      <div class="wx-card-title">${a.title}</div>
      <div class="wx-card-meta">
        ${a.source_name || '公众号'}
        ${a.quality_score ? ' · 评分 ' + a.quality_score.toFixed(1) : ''}
        ${a.fetched_at ? ' · ' + a.fetched_at.slice(0,10) : ''}
      </div>
      ${a.content ? `<div class="article-summary" style="margin-top:8px">${a.content.slice(0,100)}</div>` : ''}
      <div style="display:flex;gap:8px;margin-top:12px;align-items:center">
        ${a.url ? `<a href="${a.url}" target="_blank" style="font-size:12px;color:#667eea">原文 ↗</a>` : ''}
        <button class="wx-card-btn" id="btn-${a.id}" onclick="showAnalysis(${a.id}, '${a.title.replace(/'/g,"\\'")}')">🔍 深度分析</button>
      </div>
    </div>
  `).join('');
}

// ── 文章深度分析弹窗 ──────────────────────────────────
async function showAnalysis(id, title) {
  const btn = document.getElementById('btn-' + id);
  if (btn) { btn.classList.add('loading'); btn.textContent = '分析中...'; }

  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalBody').innerHTML = '<div style="text-align:center;padding:40px;color:#a0aec0">⏳ DeepSeek 分析中，约需 10 秒...</div>';
  document.getElementById('modalOverlay').classList.add('open');

  const res = await fetch('/api/wechat/analyze/' + id);
  const d = await res.json();

  if (btn) { btn.classList.remove('loading'); btn.textContent = '🔍 深度分析'; }

  if (d.error) {
    document.getElementById('modalBody').innerHTML = `<div style="color:#e53e3e;padding:20px">分析失败：${d.error}</div>`;
    return;
  }

  const painPointsHtml = (d.pain_points || []).map(p => `
    <div class="pain-point">
      <div class="pain-point-name">📌 ${p.point}</div>
      <div class="pain-point-desc">${p.explanation}</div>
    </div>
  `).join('');

  const techniquesHtml = (d.writing_techniques || []).map(t =>
    `<span class="technique-tag">${t}</span>`
  ).join('');

  document.getElementById('modalBody').innerHTML = `
    <div class="analysis-block">
      <div class="analysis-label">🌟 整体亮点</div>
      <div class="analysis-text">${d.why_good || '—'}</div>
    </div>
    <div class="analysis-block">
      <div class="analysis-label">🎯 踩中的痛点</div>
      ${painPointsHtml || '<div class="analysis-text">—</div>'}
    </div>
    <div class="analysis-block">
      <div class="analysis-label">🏷 标题分析</div>
      <div class="analysis-text">${d.title_analysis || '—'}</div>
    </div>
    <div class="analysis-block">
      <div class="analysis-label">📐 结构亮点</div>
      <div class="analysis-text">${d.structure_highlights || '—'}</div>
    </div>
    <div class="analysis-block">
      <div class="analysis-label">✏️ 写作技巧</div>
      <div style="margin-top:4px">${techniquesHtml || '—'}</div>
    </div>
    <div class="learnable-box">
      <div class="analysis-label">💡 最值得学习的一点</div>
      <div class="analysis-text">${d.learnable || '—'}</div>
    </div>
  `;
}

function closeModal(event) {
  if (!event || event.target === document.getElementById('modalOverlay')) {
    document.getElementById('modalOverlay').classList.remove('open');
  }
}

// ── 文章选择与生成 ─────────────────────────────────────
let selectedArticles = new Set();

function onArticleCheck(id, checked) {
  if (checked) {
    selectedArticles.add(id);
  } else {
    selectedArticles.delete(id);
  }
  updateGenerateBar();
}

function updateGenerateBar() {
  const bar = document.getElementById('generateBar');
  const count = selectedArticles.size;
  document.getElementById('selCount').textContent = `已选 ${count} 篇`;
  if (count > 0) {
    bar.classList.add('show');
  } else {
    bar.classList.remove('show');
  }
}

function clearSelection() {
  selectedArticles.forEach(id => {
    const chk = document.getElementById('chk-' + id);
    if (chk) chk.checked = false;
    const card = document.getElementById('acard-' + id);
    if (card) card.style.background = '';
  });
  selectedArticles.clear();
  updateGenerateBar();
}

async function generateFromSelected() {
  if (!requireLogin()) return;
  if (selectedArticles.size === 0) return;
  const btn = document.getElementById('genBtn');
  btn.disabled = true;
  btn.textContent = '生成中...';
  showGenModal('正在生成微信公众号文章（三轮自我审核）...', null);

  try {
    const res = await fetch('/api/generate/from-articles', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ article_ids: Array.from(selectedArticles) })
    });
    const data = await res.json();
    if (data.error) {
      showGenModal('生成失败：' + data.error, null);
    } else {
      pollGenTask(data.task_id);
    }
  } catch(e) {
    showGenModal('请求失败：' + e.message, null);
  } finally {
    btn.disabled = false;
    btn.textContent = '✍️ 生成公众号文章';
  }
}

// ── 推荐生成全文 ──────────────────────────────────────
let recDataStore = [];

function storeRecData(recs) {
  recDataStore = recs;
}

async function generateFromRec(index) {
  if (!requireLogin()) return;
  const rec = recDataStore[index];
  if (!rec) return;
  const btn = document.getElementById('recgenbtn-' + index);
  if (btn) { btn.disabled = true; btn.textContent = '生成中，约需60秒...'; }
  showGenModal('正在按选题卡片生成完整文章（三轮自我审核）...', null);

  try {
    const res = await fetch('/api/generate/from-recommendation', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ rec })
    });
    const data = await res.json();
    if (data.error) {
      showGenModal('生成失败：' + data.error, null);
    } else {
      pollGenTask(data.task_id, () => {
        if (btn) { btn.disabled = false; btn.textContent = '✍️ 生成完整公众号文章（三轮审核）'; }
      });
    }
  } catch(e) {
    showGenModal('请求失败：' + e.message, null);
    if (btn) { btn.disabled = false; btn.textContent = '✍️ 生成完整公众号文章（三轮审核）'; }
  }
}

// ── 生成结果弹窗 ──────────────────────────────────────
function showGenModal(loadingMsg, data) {
  document.getElementById('genOverlay').classList.add('open');
  const body = document.getElementById('genModalBody');
  if (!data) {
    body.innerHTML = `<div style="text-align:center;padding:48px 20px">
      <div style="font-size:36px;margin-bottom:16px">⚙️</div>
      <div class="gen-progress-label" style="font-size:15px;color:#4a5568;font-weight:600">第1轮：DeepSeek 生成初稿...</div>
      <div class="gen-progress-sec" style="font-size:13px;color:#a0aec0;margin-top:8px">已等待 0 秒</div>
      <div style="margin-top:20px;display:flex;justify-content:center;gap:8px;flex-wrap:wrap">
        <span style="font-size:12px;color:#667eea;background:#ebf4ff;padding:4px 10px;border-radius:10px">第1轮：DeepSeek 初稿</span>
        <span style="font-size:12px;color:#c05621;background:#fef3c7;padding:4px 10px;border-radius:10px">第2轮：GPT-4.1 审核</span>
        <span style="font-size:12px;color:#276749;background:#c6f6d5;padding:4px 10px;border-radius:10px">第3轮：DeepSeek 润色</span>
      </div>
      <div style="font-size:12px;color:#a0aec0;margin-top:16px">可关闭弹窗，生成完成后重新打开草稿箱查看</div>
    </div>`;
  }
}

function escHtml(str) {
  return (str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function closeGenModal(event) {
  if (!event || event.target === document.getElementById('genOverlay')) {
    document.getElementById('genOverlay').classList.remove('open');
  }
}

// ── 生成任务轮询 ──────────────────────────────────────
function pollGenTask(taskId, onDone) {
  let elapsed = 0;
  const steps = [
    {at: 0,   label: '第1轮：DeepSeek 生成初稿...'},
    {at: 35,  label: '第2轮：GPT-4.1 独立审核中...'},
    {at: 90,  label: '第3轮：DeepSeek 润色终稿...'},
    {at: 140, label: '即将完成，最后润色中...'},
  ];
  function getStepLabel(sec) {
    let label = steps[0].label;
    for (const s of steps) { if (sec >= s.at) label = s.label; }
    return label;
  }
  const interval = setInterval(async () => {
    elapsed += 3;
    const labelEl = document.querySelector('.gen-progress-label');
    const secEl = document.querySelector('.gen-progress-sec');
    if (labelEl) labelEl.textContent = getStepLabel(elapsed);
    if (secEl) secEl.textContent = '已等待 ' + elapsed + ' 秒';
    try {
      const res = await fetch('/api/gen-status/' + taskId);
      const task = await res.json();
      if (task.status === 'done') {
        clearInterval(interval);
        if (onDone) onDone();
        renderGenResult(task.result);
      } else if (task.status === 'error') {
        clearInterval(interval);
        if (onDone) onDone();
        document.getElementById('genModalBody').innerHTML =
          `<div style="color:#e53e3e;padding:20px">生成失败：${task.result?.error || '未知错误'}</div>`;
      }
    } catch(e) {
      clearInterval(interval);
      document.getElementById('genModalBody').innerHTML =
        `<div style="color:#e53e3e;padding:20px">轮询失败：${e.message}</div>`;
    }
  }, 3000);
}

// ── 草稿保存 ──────────────────────────────────────────
let _lastGenData = null;

function renderGenResult(data) {
  _lastGenData = data;  // 记住最新生成结果，供保存用
  const body = document.getElementById('genModalBody');
  const title = data.title || '无标题';
  document.getElementById('genModalTitle').textContent = '✍️ ' + title;

  const review = data.review || {};
  const scores = review.scores || {};
  let reviewHtml = '';
  if (Object.keys(scores).length) {
    const scoreChips = Object.entries(scores).map(([k,v]) =>
      `<span class="gen-score-chip">${k}: ${v}分</span>`
    ).join('');
    const issues = (review.issues || review.red_flags || []).map(i => {
      if (typeof i === 'object' && i !== null) {
        const pri = i.priority ? `[${i.priority.toUpperCase()}] ` : '';
        const loc = i.location ? `「${i.location}」` : '';
        const prob = i.problem || '';
        const sug = i.suggestion ? ` → ${i.suggestion}` : '';
        return `• ${pri}${loc} ${prob}${sug}`;
      }
      return `• ${i}`;
    }).join('<br>');
    const strengths = (review.strengths || []).map(s => `• ${s}`).join('<br>');
    reviewHtml = `<div class="gen-review">
      <div class="gen-review-title">📊 第2轮 GPT-4.1 审核评分</div>
      <div class="gen-score-row">${scoreChips}</div>
      ${issues ? `<div class="gen-issues" style="color:#c05621"><b>待改进：</b><br>${issues}</div>` : ''}
      ${strengths ? `<div class="gen-issues" style="color:#276749;margin-top:6px"><b>亮点：</b><br>${strengths}</div>` : ''}
    </div>`;
  }

  const candidates = data.candidates_summary || [];
  const candidatesHtml = candidates.length ? `<div style="background:#ebf4ff;border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:12px;color:#2b4c8c">
    <b>🗳️ 3篇初稿竞选结果：</b>
    <div style="display:flex;gap:8px;margin-top:6px;flex-wrap:wrap">
      ${candidates.map(c => `<span style="background:${c.selected?'#667eea':'#c3dafe'};color:${c.selected?'#fff':'#2b4c8c'};border-radius:6px;padding:3px 10px;font-size:12px">${c.selected?'✅ ':''}初稿${c.index}：${c.overall}分</span>`).join('')}
    </div>
  </div>` : '';

  const finalReview = data.final_review || {};
  const finalScores = finalReview.scores || {};
  let finalReviewHtml = '';
  if (Object.keys(finalScores).length) {
    const overall = finalScores.overall ?? '?';
    const chips = Object.entries(finalScores).map(([k,v]) =>
      `<span class="gen-score-chip" style="background:${k==='overall'?'#667eea':'#e9d8fd'};color:${k==='overall'?'#fff':'#553c9a'}">${k}: ${v}分</span>`
    ).join('');
    finalReviewHtml = `<div class="gen-review" style="border-color:#9f7aea;background:#faf5ff">
      <div class="gen-review-title" style="color:#6b46c1">✅ 终稿 GPT-4.1 评分：<b style="font-size:16px">${overall}分</b></div>
      <div class="gen-score-row">${chips}</div>
    </div>`;
  }

  body.innerHTML = `
    ${candidatesHtml}
    ${finalReviewHtml}
    ${reviewHtml}
    <div class="gen-rounds">
      <button class="gen-round-tab" onclick="switchGenTab('v1', this)">第1轮初稿</button>
      <button class="gen-round-tab" onclick="switchGenTab('v2', this)">第2轮修改稿</button>
      <button class="gen-round-tab active" onclick="switchGenTab('final', this)">✅ 最终稿</button>
    </div>
    <div id="genContent-v1" class="gen-article" style="display:none">${escHtml(data.draft_v1 || '')}</div>
    <div id="genContent-v2" class="gen-article" style="display:none">${escHtml(data.draft_v2 || '')}</div>
    <div id="genContent-final" class="gen-article">${escHtml(data.content || '')}</div>
    <div style="display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap">
      <button class="gen-copy-btn" onclick="copyGenContent()">📋 复制最终稿</button>
      <button class="gen-copy-btn" style="background:#667eea" onclick="saveDraft()">💾 保存到草稿箱</button>
      <span id="copyTip" style="font-size:12px;color:#48bb78;display:none">已复制！</span>
      <span id="saveTip" style="font-size:12px;color:#667eea;display:none">已保存！</span>
    </div>
  `;
}

async function saveDraft() {
  if (!requireLogin()) return;
  if (!_lastGenData) return;
  const res = await fetch('/api/drafts/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      title: _lastGenData.title || '',
      content: _lastGenData.content || '',
      draft_v1: _lastGenData.draft_v1 || '',
      draft_v2: _lastGenData.draft_v2 || '',
      review: _lastGenData.review || {},
      source_article_ids: _lastGenData.source_article_ids || [],
      generate_type: _lastGenData.source_article_ids ? 'articles' : 'recommendation'
    })
  });
  const d = await res.json();
  if (d.ok) {
    const tip = document.getElementById('saveTip');
    tip.style.display = 'inline';
    setTimeout(() => { tip.style.display = 'none'; }, 2000);
    loadDraftBadge();
  }
}

// ── 草稿箱 ────────────────────────────────────────────
async function loadDraftBadge() {
  const res = await fetch('/api/drafts');
  const data = await res.json();
  const count = (data.drafts || []).length;
  const badge = document.getElementById('draftBadge');
  if (count > 0) {
    badge.textContent = count;
    badge.style.display = 'inline';
  } else {
    badge.style.display = 'none';
  }
}

async function loadDrafts() {
  const grid = document.getElementById('draftsGrid');
  grid.innerHTML = '<div style="color:#a0aec0;font-size:13px;padding:20px">加载中...</div>';
  const res = await fetch('/api/drafts');
  const data = await res.json();
  const drafts = data.drafts || [];
  loadDraftBadge();

  if (!drafts.length) {
    grid.innerHTML = '<div style="text-align:center;padding:48px;color:#a0aec0"><div style="font-size:36px">📭</div><p style="margin-top:8px;font-size:14px">草稿箱为空，生成文章后点「保存到草稿箱」</p></div>';
    return;
  }

  grid.innerHTML = drafts.map(d => {
    const typeLabel = d.generate_type === 'recommendation' ? '选题生成' : '文章生成';
    const preview = (d.content || '').replace(/标题：[^\\r\\n]*/g,'').trim().slice(0, 120);
    const scores = (d.review_json || {}).scores || {};
    const overall = scores.overall ? `综合 ${scores.overall} 分` : '';
    return `<div class="draft-card" id="draftcard-${d.id}">
      <div class="draft-card-header">
        <div>
          <div class="draft-title">${d.title || '（无标题）'}
            <span class="draft-type-tag">${typeLabel}</span>
          </div>
          <div class="draft-meta">${d.created_at ? (()=>{ const dt = new Date(d.created_at.replace(' ','T')+'Z'); return dt.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); })() : ''}${overall ? ' · ' + overall : ''}</div>
        </div>
      </div>
      <div class="draft-preview">${preview}</div>
      <div class="draft-actions">
        <button class="draft-btn draft-btn-view" onclick="viewDraft(${d.id})">👁 查看全文</button>
        <button class="draft-btn draft-btn-copy" onclick="copyDraft(${d.id})">📋 复制</button>
        <button class="draft-btn draft-btn-layout" onclick="openLayout(${d.id})">📐 微信排版</button>
        <button class="draft-btn draft-btn-del" onclick="deleteDraft(${d.id})">🗑 删除</button>
      </div>
    </div>`;
  }).join('');

  // 把草稿数据存到 window 供查看用
  window._draftsData = {};
  drafts.forEach(d => { window._draftsData[d.id] = d; });
}

function viewDraft(id) {
  const d = window._draftsData && window._draftsData[id];
  if (!d) return;
  // 复用生成结果弹窗
  const fakeData = {
    title: d.title,
    content: d.content || '',
    draft_v1: d.draft_v1 || '',
    draft_v2: d.draft_v2 || '',
    review: d.review_json || {},
    source_article_ids: d.source_article_ids || []
  };
  document.getElementById('genOverlay').classList.add('open');
  renderGenResult(fakeData);
  // 隐藏保存按钮（草稿箱里查看的不需要再保存）
  setTimeout(() => {
    const saveBtn = document.querySelector('#genModalBody button[onclick="saveDraft()"]');
    if (saveBtn) saveBtn.style.display = 'none';
  }, 50);
}

function copyDraft(id) {
  const d = window._draftsData && window._draftsData[id];
  if (!d) return;
  navigator.clipboard.writeText(d.content || '').then(() => {
    const btn = document.querySelector(`#draftcard-${id} .draft-btn-copy`);
    if (btn) { btn.textContent = '✓ 已复制'; setTimeout(() => { btn.textContent = '📋 复制'; }, 2000); }
  });
}

async function deleteDraft(id) {
  if (!confirm('确认删除这篇草稿？')) return;
  await fetch('/api/drafts/delete/' + id);
  document.getElementById('draftcard-' + id)?.remove();
  loadDraftBadge();
  // 如果删完了显示空状态
  const grid = document.getElementById('draftsGrid');
  if (!grid.querySelector('.draft-card')) {
    grid.innerHTML = '<div style="text-align:center;padding:48px;color:#a0aec0"><div style="font-size:36px">📭</div><p style="margin-top:8px;font-size:14px">草稿箱为空</p></div>';
  }
}

// ── 微信排版弹窗 ──────────────────────────────────────────
let _layoutMode = 'news';
let _layoutHTML = '', _layoutText = '';

function openLayout(draftId) {
  const d = window._draftsData && window._draftsData[draftId];
  if (!d) return;
  // 取最终稿内容，优先 final_content，其次 content
  const content = (d.final_content || d.content || '').trim();
  document.getElementById('layoutTA').value = content;
  document.getElementById('layoutErr').style.display = 'none';
  document.getElementById('layoutPreview').innerHTML = '<div style="text-align:center;padding:40px;color:#a0aec0;font-size:13px">📱 排版结果将在这里预览</div>';
  document.getElementById('layoutFoot').classList.remove('show');
  document.getElementById('layoutModal').classList.add('open');
}

function closeLayout() {
  document.getElementById('layoutModal').classList.remove('open');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeLayout();
});

document.querySelectorAll && document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.layout-chip').forEach(c => {
    c.onclick = () => {
      document.querySelectorAll('.layout-chip').forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      _layoutMode = c.dataset.v;
    };
  });
});

async function runLayout() {
  const article = document.getElementById('layoutTA').value.trim();
  const errEl = document.getElementById('layoutErr');
  errEl.style.display = 'none';
  if (!article) { errEl.textContent = '请填写文章内容'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('layoutGoBtn');
  btn.disabled = true; btn.textContent = '排版中...';
  document.getElementById('layoutPreview').innerHTML = '<div style="text-align:center;padding:40px"><div style="display:inline-block;width:28px;height:28px;border:3px solid #e0e0e0;border-top-color:#1677ff;border-radius:50%;animation:spin .7s linear infinite"></div><p style="margin-top:12px;font-size:13px;color:#999">AI 正在分析文章结构...</p></div>';
  document.getElementById('layoutFoot').classList.remove('show');

  try {
    const result = await callLayoutAI(article, _layoutMode);
    renderLayoutResult(result);
  } catch(e) {
    errEl.textContent = '排版失败：' + e.message; errEl.style.display = 'block';
    document.getElementById('layoutPreview').innerHTML = '<div style="text-align:center;padding:40px;color:#a0aec0;font-size:13px">排版失败，请重试</div>';
  }
  btn.disabled = false; btn.textContent = 'AI 智能排版';
}

async function callLayoutAI(article, style) {
  const styleDesc = {
    general: '通用公众号文章',
    knowledge: '知识干货型，读者希望获得实用知识',
    news: '行业资讯型，如动脉网、量子位，需要专业感',
    story: '情感故事型，叙事性强'
  }[style] || '通用公众号文章';

  const prompt = `你是专业的微信公众号编辑，擅长动脉网、量子位风格的排版。请对以下文章进行智能排版分析。

文章风格：${styleDesc}

原始文章：
${article}

请输出一个 JSON 对象，不要有任何说明文字或 markdown 代码块，直接输出纯 JSON：

{
  "title": "文章主标题（优化表达，吸引人）",
  "lead": "导读摘要，一两句话概括文章价值，40字以内",
  "tags": ["标签1", "标签2", "标签3"],
  "blocks": []
}

blocks 数组支持以下类型：
{"type":"h2","text":"小标题"}
{"type":"p","text":"正文，<strong>关键词</strong>用strong标签，只加粗专有名词/数据/核心概念，不滥用"}
{"type":"highlight","text":"重要结论或核心观点，每篇最多2-3个"}
{"type":"quote","text":"金句或引述"}
{"type":"img","desc":"插图描述15字内","reason":"插图原因10字内"}
{"type":"divider"}

排版规则：
1. 段落每段100-180字，超长拆分
2. 每隔2-3段插一个img，全文2-4处
3. 含数据、结论、重要观点的段落用highlight
4. 值得引用的金句用quote
5. 加粗只用于专有名词、关键数据、核心概念
6. 保留原文意思，可优化表达`;

  const resp = await fetch('https://api.deepseek.com/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${window._dsKey || ''}` },
    body: JSON.stringify({ model: 'deepseek-chat', max_tokens: 4000, temperature: 0.3,
      messages: [{ role: 'user', content: prompt }] })
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(`API 错误 ${resp.status}：${e?.error?.message || resp.statusText}`);
  }
  const data = await resp.json();
  const raw = data.choices?.[0]?.message?.content || '';
  const clean = raw.replace(/^```json\\s*/, '').replace(/^```\\s*/, '').replace(/\\s*```$/, '').trim();
  return JSON.parse(clean);
}

function renderLayoutResult(result) {
  let preHTML = '', wxHTML = '', plain = '';

  if (result.tags?.length) {
    preHTML += `<div class="layout-tags">${result.tags.map(t => `<span class="layout-tag">${t}</span>`).join('')}</div>`;
  }
  const title = result.title || '';
  preHTML += `<div class="wx-title">${title}</div>`;
  wxHTML += `<p style="font-size:22px;font-weight:bold;line-height:1.4;margin:0 0 6px;color:#111;text-indent:0;">${title}</p>`;
  plain += title + '\\n\\n';

  if (result.lead) {
    preHTML += `<div class="wx-lead">${result.lead}</div>`;
    wxHTML += `<p style="font-size:13px;color:#888;line-height:1.7;margin:6px 0 20px;padding-bottom:16px;border-bottom:1px solid #f0f0f0;text-indent:0;">${result.lead}</p>`;
    plain += result.lead + '\\n\\n';
  }

  for (const b of (result.blocks || [])) {
    if (b.type === 'h2') {
      preHTML += `<div class="wx-h2"><div class="wx-h2-bar"></div><div class="wx-h2-text">${b.text}</div></div>`;
      wxHTML += `<p style="font-size:16px;font-weight:bold;color:#111;line-height:1.5;margin:24px 0 10px;padding:9px 12px 9px 16px;background:#f0f7ff;border-left:4px solid #1677ff;text-indent:0;">${b.text}</p>`;
      plain += '\\n【' + b.text + '】\\n\\n';
    } else if (b.type === 'p') {
      preHTML += `<div class="wx-p">${b.text}</div>`;
      wxHTML += `<p style="font-size:15px;line-height:1.95;color:#333;margin:0 0 14px;text-indent:1em;">\u3000${b.text}</p>`;
      plain += '\u3000' + b.text.replace(/<[^>]+>/g,'') + '\\n\\n';
    } else if (b.type === 'highlight') {
      preHTML += `<div class="wx-highlight">${b.text}</div>`;
      wxHTML += `<p style="font-size:14px;line-height:1.8;color:#5c3d00;margin:16px 0;padding:11px 14px;background:#fffbe6;border-left:4px solid #faad14;text-indent:0;">${b.text}</p>`;
      plain += '\u3000' + b.text.replace(/<[^>]+>/g,'') + '\\n\\n';
    } else if (b.type === 'quote') {
      preHTML += `<div class="wx-quote">${b.text}</div>`;
      wxHTML += `<p style="font-size:14px;line-height:1.85;color:#555;margin:14px 0;padding:11px 14px;background:#f7f8fa;border-left:4px solid #1677ff;font-style:italic;text-indent:0;">${b.text}</p>`;
      plain += '"' + b.text.replace(/<[^>]+>/g,'') + '"\\n\\n';
    } else if (b.type === 'divider') {
      preHTML += `<div class="wx-divider"><div class="wx-divider-line"></div><div class="wx-divider-dot"></div><div class="wx-divider-line"></div></div>`;
      wxHTML += `<p style="text-align:center;margin:20px 0;font-size:14px;color:#ccc;letter-spacing:8px;text-indent:0;">···</p>`;
    } else if (b.type === 'img') {
      preHTML += `<div class="wx-img-ph">📷 建议插图：${b.desc}<br><span style="font-size:11px;color:#91caff">${b.reason || ''}</span></div>`;
    }
  }

  _layoutHTML = `<div style="font-family:'PingFang SC','Microsoft YaHei',sans-serif;max-width:677px;">${wxHTML}</div>`;
  _layoutText = plain;

  document.getElementById('layoutPreview').innerHTML = `
    <div class="layout-phone">
      <div class="layout-phone-bar">公众号文章</div>
      <div class="layout-phone-content">${preHTML}</div>
    </div>`;
  document.getElementById('layoutFoot').classList.add('show');
}

function copyLayout(type) {
  if (type === 'text') {
    navigator.clipboard.writeText(_layoutText).then(() => showToast('纯文字已复制')).catch(() => {
      const t = document.createElement('textarea');
      t.value = _layoutText; document.body.appendChild(t); t.select();
      document.execCommand('copy'); document.body.removeChild(t);
      showToast('纯文字已复制');
    });
    return;
  }
  const full = `<html><body>${_layoutHTML}</body></html>`;
  if (window.ClipboardItem) {
    navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([full], { type: 'text/html' }) })])
      .then(() => showToast('已复制！微信编辑器 Ctrl+V 粘贴即可'))
      .catch(() => fbLayoutRich(full));
  } else fbLayoutRich(full);
}
function fbLayoutRich(html) {
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
  d.innerHTML = html; document.body.appendChild(d);
  const sel = window.getSelection(), r = document.createRange();
  r.selectNodeContents(d); sel.removeAllRanges(); sel.addRange(r);
  try { document.execCommand('copy'); showToast('已复制！微信编辑器 Ctrl+V 粘贴即可'); }
  catch(e) { showToast('复制失败，请手动复制'); }
  sel.removeAllRanges(); document.body.removeChild(d);
}

// ── 关键词订阅 ────────────────────────────────────────────
function showSubMsg(msg, ok) {
  const el = document.getElementById('sub-msg');
  el.textContent = msg;
  el.style.display = 'block';
  el.style.background = ok ? '#f0fff4' : '#fff5f5';
  el.style.color = ok ? '#276749' : '#c53030';
  el.style.border = ok ? '1px solid #9ae6b4' : '1px solid #feb2b2';
}

function getSubForm() {
  return {
    email: document.getElementById('sub-email').value.trim(),
    keywords: document.getElementById('sub-keywords').value.trim(),
    api_key: document.getElementById('sub-apikey').value.trim()
  };
}

// ── 关键词订阅（登录用户专属）────────────────────────────────

// 领域标签配置：每个标签对应一组中英文关键词
const DOMAIN_TAGS = [
  { label: '医学影像 AI',  keywords: 'radiology,imaging,MRI,CT,X-ray,ultrasound,影像,放射' },
  { label: '病理 AI',      keywords: 'patholog,histolog,slide,WSI,病理' },
  { label: '临床 NLP / LLM', keywords: 'EHR,EMR,clinical NLP,large language model,LLM,临床文本,电子病历' },
  { label: '药物发现',     keywords: 'drug discovery,molecular,protein,drug design,药物,蛋白质' },
  { label: '手术机器人',   keywords: 'surgical robot,robotic surgery,laparoscopic,手术机器人' },
  { label: '基因组学',     keywords: 'genomic,genome,DNA,variant,基因,变异' },
  { label: '可穿戴 & CGM', keywords: 'wearable,CGM,glucose,ECG,EEG,biosensor,可穿戴,血糖' },
  { label: '政策 & 监管',  keywords: 'FDA,CE mark,regulation,approval,NMPA,监管,政策,审批' },
  { label: '融资 & 产业',  keywords: 'funding,investment,startup,acquisition,融资,投资,并购' },
  { label: '临床试验',     keywords: 'clinical trial,RCT,cohort,prospective,临床试验,队列' },
];

let _selectedTags = new Set();

function renderSubTags(currentKeywords) {
  const container = document.getElementById('subTagList');
  if (!container) return;
  // 从已有关键词里反推已选标签
  _selectedTags.clear();
  if (currentKeywords) {
    DOMAIN_TAGS.forEach(t => {
      const kwds = t.keywords.split(',');
      if (kwds.some(k => currentKeywords.toLowerCase().includes(k.toLowerCase()))) {
        _selectedTags.add(t.label);
      }
    });
  }
  container.innerHTML = DOMAIN_TAGS.map(t => {
    const on = _selectedTags.has(t.label);
    return `<button onclick="toggleSubTag('${t.label}')" id="subtag-${t.label.replace(/[^a-zA-Z0-9]/g,'_')}"
      style="padding:7px 14px;border-radius:20px;font-size:13px;font-weight:500;cursor:pointer;transition:all .15s;
             border:1.5px solid ${on ? '#667eea' : '#e2e8f0'};
             background:${on ? '#ebf4ff' : 'white'};
             color:${on ? '#667eea' : '#4a5568'}"
    >${t.label}</button>`;
  }).join('');
}

function toggleSubTag(label) {
  if (_selectedTags.has(label)) _selectedTags.delete(label);
  else _selectedTags.add(label);
  // 合并已选标签的关键词 + 用户自定义
  const tagKws = DOMAIN_TAGS
    .filter(t => _selectedTags.has(t.label))
    .flatMap(t => t.keywords.split(','));
  const customKws = (document.getElementById('sub-keywords').value || '')
    .split(',').map(s => s.trim()).filter(Boolean);
  const merged = [...new Set([...tagKws, ...customKws])].join(',');
  document.getElementById('sub-keywords').value = merged;
  renderSubTags(merged);
}

async function initSubscribePage() {
  const hint = document.getElementById('subLoginHint');
  const content = document.getElementById('subContent');
  if (!_currentUser) {
    hint.style.display = 'block';
    content.style.display = 'none';
    return;
  }
  hint.style.display = 'none';
  content.style.display = 'block';
  document.getElementById('subEmailDisplay').textContent = _currentUser.email;
  await loadSubscriptions();
}

async function loadSubscriptions() {
  const res = await fetch('/api/subscribe/me');
  const data = await res.json();
  const badge = document.getElementById('subStatusBadge');
  const kwDisplay = document.getElementById('subKeywordsDisplay');
  const kwTags = document.getElementById('subKwTags');
  const cancelBtn = document.getElementById('subCancelBtn');
  const testBtn = document.getElementById('subTestBtn');
  const formTitle = document.getElementById('subFormTitle');

  if (data.subscribed) {
    badge.textContent = '✅ 推送中';
    badge.style.background = 'rgba(72,187,120,0.25)';
    badge.style.borderColor = 'rgba(72,187,120,0.5)';
    kwDisplay.style.display = 'block';
    kwTags.innerHTML = data.keywords.split(',').filter(k=>k.trim()).slice(0,6).map(k =>
      `<span style="background:rgba(255,255,255,0.2);border:1px solid rgba(255,255,255,0.3);border-radius:14px;padding:3px 10px;font-size:12px">${k.trim()}</span>`
    ).join('') + (data.keywords.split(',').length > 6 ? `<span style="font-size:12px;opacity:.6"> +${data.keywords.split(',').length-6} 个</span>` : '');
    document.getElementById('sub-keywords').value = data.keywords;
    formTitle.textContent = '修改推送领域';
    cancelBtn.style.display = 'inline-block';
    testBtn.style.display = 'inline-block';
    if (data.last_sent_at) {
      document.getElementById('subEmailDisplay').textContent =
        _currentUser.email + '  ·  上次推送 ' + data.last_sent_at.slice(0,10);
    }
    renderSubTags(data.keywords);
  } else {
    badge.textContent = '未订阅';
    badge.style.background = 'rgba(255,255,255,0.15)';
    badge.style.borderColor = 'rgba(255,255,255,0.3)';
    kwDisplay.style.display = 'none';
    formTitle.textContent = '选择推送领域，开启每日推送';
    cancelBtn.style.display = 'none';
    testBtn.style.display = 'none';
    renderSubTags('');
  }
}

async function subSaveKeywords() {
  if (!_currentUser) { requireLogin(subSaveKeywords); return; }
  const keywords = document.getElementById('sub-keywords').value.trim();
  if (!keywords) { showSubMsg('请填写关键词', false); return; }
  const res = await fetch('/api/subscribe/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({keywords})
  });
  const data = await res.json();
  showSubMsg(data.msg, data.ok);
  if (data.ok) await loadSubscriptions();
}

function _renderPreviewCards(articles) {
  return articles.map((a, i) => `
    <div id="previewCard${i}" style="background:white;border-radius:10px;padding:14px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:3px solid #667eea">
      <div id="previewZh${i}" style="font-size:15px;font-weight:700;color:#2d3748;line-height:1.5;margin-bottom:3px;display:none"></div>
      <div id="previewEn${i}" style="font-size:14px;font-weight:600;color:#2d3748;line-height:1.4;margin-bottom:6px">
        ${a.url ? `<a href="${escHtml(a.url)}" target="_blank" style="color:inherit;text-decoration:none;border-bottom:1px dashed #cbd5e0" onmouseover="this.style.color='#667eea'" onmouseout="this.style.color=''">${escHtml(a.title)}</a>` : escHtml(a.title)}
      </div>
      ${a.content ? `<div style="font-size:12px;color:#a0aec0;line-height:1.6;margin-bottom:6px">${escHtml(a.content.replace(/<[^>]+>/g,''))}...</div>` : ''}
      <div style="font-size:11px;color:#b0bec5">
        ${escHtml(a.source_name)} · ${a.published_at || ''} · ⭐${(a.quality_score||0).toFixed(1)}
      </div>
    </div>
  `).join('');
}

async function subPreview() {
  const keywords = document.getElementById('sub-keywords').value.trim();
  if (!keywords && _selectedTags.size === 0) { showSubMsg('请先选择推送领域或填写关键词', false); return; }
  if (!keywords) { showSubMsg('请先点击领域标签后再预览', false); return; }
  const area = document.getElementById('subPreviewArea');
  const list = document.getElementById('subPreviewList');
  const count = document.getElementById('subPreviewCount');
  area.style.display = 'block';
  list.innerHTML = '<div style="text-align:center;padding:24px;color:#a0aec0;font-size:13px">正在匹配文章...</div>';

  const res = await fetch('/api/subscribe/preview?keywords=' + encodeURIComponent(keywords));
  const data = await res.json();
  const articles = data.articles || [];
  count.textContent = articles.length ? `找到 ${articles.length} 篇` : '近7天暂无匹配';
  if (!articles.length) {
    list.innerHTML = '<div style="background:white;border-radius:10px;padding:24px;text-align:center;color:#a0aec0;font-size:13px">近7天内没有匹配该关键词的文章<br>尝试换用英文关键词，或等待明日更新后再查看</div>';
    return;
  }

  // 立即渲染英文标题
  list.innerHTML = _renderPreviewCards(articles);

  // 异步拉取翻译，拿到后逐条更新
  const toTranslate = articles
    .map((a, i) => ({ i, title: a.title }))
    .filter(x => x.title && !/[一-鿿]/.test(x.title));
  if (!toTranslate.length) return;

  try {
    count.textContent += '　正在翻译标题...';
    const tr = await fetch('/api/subscribe/translate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({titles: toTranslate.map(x => x.title)})
    });
    const td = await tr.json();
    const translations = td.translations || [];
    toTranslate.forEach(({i}, rank) => {
      const zh = translations[rank];
      if (!zh) return;
      const zhEl = document.getElementById('previewZh' + i);
      const enEl = document.getElementById('previewEn' + i);
      if (zhEl && enEl) {
        zhEl.textContent = zh;
        zhEl.style.display = 'block';
        enEl.style.fontSize = '12px';
        enEl.style.color = '#718096';
        enEl.style.fontWeight = '400';
      }
    });
    count.textContent = `找到 ${articles.length} 篇`;
  } catch(e) {
    count.textContent = `找到 ${articles.length} 篇`;
  }
}

async function subTestPush() {
  if (!requireLogin()) return;
  const btn = document.getElementById('subTestBtn');
  btn.disabled = true; btn.textContent = '发送中...';
  try {
    const res = await fetch('/api/subscribe/push', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: _currentUser.email})
    });
    const data = await res.json();
    showSubMsg(data.msg, data.ok);
    btn.textContent = data.ok ? '✓ 已发送' : '发送失败';
    btn.style.background = data.ok ? '#48bb78' : '#fc8181';
    btn.style.color = 'white'; btn.style.border = 'none';
    setTimeout(() => {
      btn.textContent = '📤 立即推送一次';
      btn.style.background = ''; btn.style.color = ''; btn.style.border = '';
      btn.disabled = false;
    }, 3000);
  } catch(e) {
    showSubMsg('网络错误', false);
    btn.disabled = false; btn.textContent = '📤 立即推送一次';
  }
}

async function subCancel() {
  if (!confirm('确认取消订阅？将不再收到每日推送邮件。')) return;
  const res = await fetch('/api/subscribe/cancel/me', {method:'POST'});
  const data = await res.json();
  showSubMsg(data.msg, data.ok);
  if (data.ok) {
    document.getElementById('sub-keywords').value = '';
    document.getElementById('subPreviewArea').style.display = 'none';
    await loadSubscriptions();
  }
}

function showSubMsg(msg, ok) {
  const el = document.getElementById('sub-msg');
  el.textContent = msg;
  el.style.display = 'block';
  el.style.background = ok ? '#f0fff4' : '#fff5f5';
  el.style.color = ok ? '#276749' : '#c53030';
  el.style.border = ok ? '1px solid #9ae6b4' : '1px solid #fed7d7';
  setTimeout(() => { el.style.display = 'none'; }, 4000);
}
</script>

<!-- 微信排版弹窗 -->
<div id="layoutModal">
  <div class="layout-modal-box">
    <div class="layout-modal-header">
      <h3>📐 微信公众号排版</h3>
      <div style="display:flex;align-items:center;gap:12px">
        <div id="layoutKeyWrap" style="display:flex;align-items:center;gap:8px;background:#fffbe6;border:1px solid #ffe58f;border-radius:8px;padding:6px 12px">
          <span style="font-size:11px;color:#874d00;font-weight:500;white-space:nowrap">DeepSeek Key</span>
          <input type="password" id="layoutApiKey" placeholder="sk-xxxxxxxx"
            style="width:160px;border:none;background:transparent;font-size:12px;font-family:monospace;color:#333;outline:none"
            oninput="window._dsKey=this.value;localStorage.setItem('ds_layout_key',this.value)">
        </div>
        <button onclick="closeLayout()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#999;padding:4px">×</button>
      </div>
    </div>
    <div class="layout-modal-body">
      <!-- 左：输入 -->
      <div class="layout-panel">
        <div class="layout-panel-head">输入文章</div>
        <div class="layout-panel-body">
          <div>
            <div style="font-size:11px;color:#999;font-weight:500;margin-bottom:6px">文章风格</div>
            <div class="layout-chips">
              <div class="layout-chip on" data-v="general" onclick="setLayoutMode(this,'general')">通用</div>
              <div class="layout-chip" data-v="knowledge" onclick="setLayoutMode(this,'knowledge')">知识干货</div>
              <div class="layout-chip on" data-v="news" onclick="setLayoutMode(this,'news')">行业资讯</div>
              <div class="layout-chip" data-v="story" onclick="setLayoutMode(this,'story')">情感故事</div>
            </div>
          </div>
          <textarea id="layoutTA" class="layout-textarea" placeholder="文章内容..."></textarea>
          <div class="layout-err" id="layoutErr"></div>
          <button class="layout-go-btn" id="layoutGoBtn" onclick="runLayout()">AI 智能排版</button>
        </div>
      </div>
      <!-- 右：预览 -->
      <div class="layout-panel">
        <div class="layout-panel-head">
          <span>预览</span>
        </div>
        <div class="layout-preview-scroll" id="layoutPreview">
          <div style="text-align:center;padding:40px;color:#a0aec0;font-size:13px">📱 排版结果将在这里预览</div>
        </div>
        <div class="layout-foot" id="layoutFoot">
          <button class="layout-copy-main" onclick="copyLayout('rich')">复制到微信编辑器</button>
          <button class="layout-copy-sec" onclick="copyLayout('text')">复制纯文字</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
function setLayoutMode(el, v) {
  document.querySelectorAll('.layout-chip').forEach(x => x.classList.remove('on'));
  el.classList.add('on'); _layoutMode = v;
}
// 自动从服务器获取 DeepSeek Key
(async function() {
  try {
    const res = await fetch('/api/config');
    const data = await res.json();
    if (data.deepseek_key) {
      window._dsKey = data.deepseek_key;
      const wrap = document.getElementById('layoutKeyWrap');
      if (wrap) wrap.style.display = 'none';
    }
  } catch(e) {
    const saved = localStorage.getItem('ds_layout_key');
    if (saved) { document.getElementById('layoutApiKey').value = saved; window._dsKey = saved; }
  }
})();
</script>
</body>
</html>"""

STUDIO_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>创作工具 · 医疗AI</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
         background: #f0f4f8; color: #1a202c; min-height: 100vh; }
  .header { background: linear-gradient(135deg, #764ba2 0%, #667eea 100%);
            color: white; padding: 20px 32px;
            display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 20px; font-weight: 700; }
  .header p { font-size: 13px; opacity: 0.8; margin-top: 4px; }
  .back-link { font-size: 13px; color: rgba(255,255,255,0.8); text-decoration: none;
               border: 1px solid rgba(255,255,255,0.3); padding: 5px 14px;
               border-radius: 6px; }
  .back-link:hover { background: rgba(255,255,255,0.15); }
  .main { max-width: 900px; margin: 32px auto; padding: 0 24px; }
  .notice { background: #fffbeb; border: 1px solid #f6e05e; border-radius: 10px;
            padding: 14px 18px; font-size: 13px; color: #744210; margin-bottom: 24px; }
  .card { background: white; border-radius: 12px; padding: 24px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 20px; }
  .card-title { font-size: 16px; font-weight: 700; color: #2d3748; margin-bottom: 8px; }
  .card-desc { font-size: 13px; color: #718096; margin-bottom: 16px; line-height: 1.6; }
  .btn { display: inline-block; padding: 9px 20px; border-radius: 8px; border: none;
         cursor: pointer; font-size: 14px; font-weight: 600; text-decoration: none;
         background: linear-gradient(135deg,#667eea,#764ba2); color: white; }
  .btn:hover { opacity: .88; }
  .login-hint { text-align: center; padding: 48px 0; color: #a0aec0; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>✍️ 内容创作工具</h1>
    <p>公众号文章生成 · 写作推荐 · 草稿管理</p>
  </div>
  <a href="/" class="back-link">← 返回情报站</a>
</div>
<div class="main">
  <div class="notice">
    💡 这里是内容创作工具区，供公众号作者使用。普通用户请前往
    <a href="/" style="color:#b7791f;font-weight:600">情报站首页</a> 查看每日情报。
  </div>
  <div id="studioLoginHint" class="login-hint" style="display:none">
    <div style="font-size:36px;margin-bottom:12px">🔒</div>
    <p style="font-size:15px;margin-bottom:16px;color:#4a5568">登录后使用创作工具</p>
    <button onclick="location.href='/'" class="btn">返回首页登录</button>
  </div>
  <div id="studioContent">
    <div class="card">
      <div class="card-title">📄 公众号文章生成</div>
      <div class="card-desc">从情报站选取 2-5 篇相关文章，经三轮 AI 自动审核生成完整文章。请先在情报站首页选文章，再回到这里生成。</div>
      <a href="/" class="btn">→ 去情报站选文章</a>
    </div>
    <div class="card">
      <div class="card-title">💬 公众号文章分析</div>
      <div class="card-desc">上传或粘贴高阅读量公众号文章，AI 深度分析痛点踩中逻辑、标题技巧和结构亮点。</div>
      <button class="btn" onclick="location.href='/?tab=wechat'">→ 进入分析</button>
    </div>
    <div class="card">
      <div class="card-title">📝 草稿箱</div>
      <div class="card-desc">查看和管理所有已生成的文章草稿，支持对比、复制和排版。</div>
      <button class="btn" onclick="location.href='/?tab=drafts'">→ 打开草稿箱</button>
    </div>
  </div>
</div>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 关闭默认日志

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/studio":
            body = STUDIO_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/auth/me":
            user = _get_session(self)
            if user:
                self.send_json({"logged_in": True, "email": user["email"]})
            else:
                self.send_json({"logged_in": False})

        elif path == "/api/digest":
            digest = get_digest_data(days=3)
            db_stats = stats(DB_PATH)
            self.send_json({"digest": digest, "stats": db_stats})

        elif path == "/api/status":
            self.send_json(task_status)

        elif path == "/api/recommend":
            qs = parse_qs(urlparse(self.path).query)
            topic = qs.get("topic", [""])[0]
            recs = get_writing_recommendations(days=3, topic=topic)
            self.send_json({"recommendations": recs})

        elif path == "/api/wechat":
            articles = get_wechat_articles()
            self.send_json({"articles": articles})

        elif path.startswith("/api/wechat/analyze/"):
            try:
                art_id = int(path.split("/")[-1])
                result = analyze_wechat_article(art_id)
                self.send_json(result)
            except ValueError:
                self.send_json({"error": "invalid id"}, 400)

        elif path.startswith("/api/gen-status/"):
            task_id = path.split("/")[-1]
            task = _gen_tasks.get(task_id, {"status": "not_found", "result": None})
            self.send_json(task)

        elif path == "/api/config":
            self.send_json({"deepseek_key": DEEPSEEK_API_KEY})

        elif path == "/api/fetch-state":
            last = get_app_state("last_fetch_date") or ""
            self.send_json({"last_fetch_date": last})

        elif path == "/api/starred":
            self.send_json({"articles": get_starred_articles()})

        elif path == "/api/drafts":
            self.send_json({"drafts": get_drafts()})

        elif path.startswith("/api/drafts/delete/"):
            try:
                draft_id = int(path.split("/")[-1])
                delete_draft(draft_id)
                self.send_json({"ok": True})
            except ValueError:
                self.send_json({"error": "invalid id"}, 400)

        elif path == "/api/myfeeds":
            user = _get_session(self)
            if not user:
                self.send_json({"error": "请先登录"}, 401); return
            from db import get_user_feeds
            self.send_json({"feeds": get_user_feeds(user["id"], DB_PATH)})

        elif path == "/api/myfeeds/articles":
            user = _get_session(self)
            if not user:
                self.send_json({"error": "请先登录"}, 401); return
            qs = parse_qs(urlparse(self.path).query)
            feed_id = int(qs["feed_id"][0]) if "feed_id" in qs else None
            from db import get_user_articles
            self.send_json({"articles": get_user_articles(user["id"], feed_id, db_path=DB_PATH)})

        elif path == "/api/subscribe/me":
            user = _get_session(self)
            if not user:
                self.send_json({"subscribed": False}); return
            from db import get_active_subscriptions
            subs = get_active_subscriptions(DB_PATH)
            sub = next((s for s in subs if s["email"] == user["email"]), None)
            if sub:
                self.send_json({"subscribed": True, "keywords": sub["keywords"],
                                "last_sent_at": sub.get("last_sent_at") or ""})
            else:
                self.send_json({"subscribed": False})

        elif path == "/api/subscribe/preview":
            qs = parse_qs(urlparse(self.path).query)
            keywords = qs.get("keywords", [""])[0].strip()
            if not keywords:
                self.send_json({"articles": []}); return
            from mailer import match_articles
            articles = match_articles(keywords, days=7, db_path=DB_PATH)
            safe = [{"id": a["id"], "title": a["title"], "title_zh": "",
                     "source_name": a["source_name"],
                     "url": a.get("url",""), "quality_score": a.get("quality_score",0),
                     "content": (a.get("content") or "")[:200],
                     "published_at": (a.get("published_at") or "")[:10]} for a in articles]
            self.send_json({"articles": safe})

        elif path == "/api/subscribe/list":
            from db import get_active_subscriptions
            subs = get_active_subscriptions()
            safe = [{"id": s["id"], "email": s["email"], "keywords": s["keywords"],
                     "active": s["active"], "created_at": s["created_at"],
                     "last_sent_at": s["last_sent_at"]} for s in subs]
            self.send_json({"subscriptions": safe})

        else:
            self.send_response(404)
            self.end_headers()

    def _require_login(self):
        """返回当前用户 dict，未登录则发 401 并返回 None"""
        user = _get_session(self)
        if not user:
            self.send_json({"error": "请先登录", "need_login": True}, 401)
        return user

    def do_POST(self):
        if self.path == "/api/auth/register":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")
            if not email or not password:
                self.send_json({"ok": False, "msg": "邮箱和密码不能为空"}, 400)
                return
            if len(password) < 6:
                self.send_json({"ok": False, "msg": "密码至少6位"}, 400)
                return
            from db import register_user
            result = register_user(email, password, DB_PATH)
            if result["ok"]:
                token = _make_session(result["user"])
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie",
                    f"session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
                body_bytes = json.dumps({"ok": True, "email": result["user"]["email"]},
                                        ensure_ascii=False).encode()
                self.send_header("Content-Length", len(body_bytes))
                self.end_headers()
                self.wfile.write(body_bytes)
            else:
                self.send_json(result, 400)
            return

        elif self.path == "/api/auth/login":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")
            from db import login_user
            result = login_user(email, password, DB_PATH)
            if result["ok"]:
                token = _make_session(result["user"])
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie",
                    f"session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
                body_bytes = json.dumps({"ok": True, "email": result["user"]["email"]},
                                        ensure_ascii=False).encode()
                self.send_header("Content-Length", len(body_bytes))
                self.end_headers()
                self.wfile.write(body_bytes)
            else:
                self.send_json(result, 401)
            return

        elif self.path == "/api/auth/logout":
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("session="):
                    delete_session(part[len("session="):], db_path=DB_PATH)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie",
                "session=; Path=/; HttpOnly; Max-Age=0")
            body_bytes = b'{"ok":true}'
            self.send_header("Content-Length", len(body_bytes))
            self.end_headers()
            self.wfile.write(body_bytes)
            return

        elif self.path == "/api/run":
            if not self._require_login():
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            topic = body.get("topic", "")
            if not task_status["running"]:
                t = threading.Thread(target=run_pipeline, args=(topic,), daemon=True)
                t.start()
            self.send_json({"ok": True})

        elif self.path == "/api/generate/from-articles":
            if not self._require_login():
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            article_ids = body.get("article_ids", [])
            style_hint = body.get("style_hint", "")
            if not article_ids:
                self.send_json({"error": "请至少选择一篇文章"}, 400)
                return
            task_id = str(uuid.uuid4())
            _gen_tasks[task_id] = {"status": "running", "result": None}
            def _run(tid, ids, hint):
                try:
                    result = generate_wechat_article(ids, hint)
                    _gen_tasks[tid]["result"] = result
                    save_draft(
                        title=result.get("title", ""),
                        content=result.get("content", ""),
                        draft_v1=result.get("draft_v1", ""),
                        draft_v2=result.get("draft_v2", ""),
                        review_json=result.get("review", {}),
                        source_article_ids=result.get("source_article_ids", []),
                        generate_type="articles"
                    )
                    _gen_tasks[tid]["status"] = "done"
                except Exception as e:
                    _gen_tasks[tid] = {"status": "error", "result": {"error": str(e)}}
            threading.Thread(target=_run, args=(task_id, article_ids, style_hint), daemon=True).start()
            self.send_json({"task_id": task_id})

        elif self.path == "/api/star":
            if not self._require_login():
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            art_id = body.get("id")
            starred = body.get("starred", True)
            if art_id:
                set_starred(int(art_id), starred)
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "missing id"}, 400)

        elif self.path == "/api/drafts/save":
            if not self._require_login():
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            try:
                draft_id = save_draft(
                    title=body.get("title", ""),
                    content=body.get("content", ""),
                    draft_v1=body.get("draft_v1", ""),
                    draft_v2=body.get("draft_v2", ""),
                    review_json=body.get("review", {}),
                    source_article_ids=body.get("source_article_ids", []),
                    generate_type=body.get("generate_type", "articles")
                )
                self.send_json({"ok": True, "draft_id": draft_id})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif self.path == "/api/generate/from-recommendation":
            if not self._require_login():
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            rec_data = body.get("rec", {})
            if not rec_data:
                self.send_json({"error": "缺少选题数据"}, 400)
                return
            task_id = str(uuid.uuid4())
            _gen_tasks[task_id] = {"status": "running", "result": None}
            def _run_rec(tid, rec):
                try:
                    result = generate_article_from_recommendation(rec)
                    _gen_tasks[tid]["result"] = result
                    save_draft(
                        title=result.get("title", ""),
                        content=result.get("content", ""),
                        draft_v1=result.get("draft_v1", ""),
                        draft_v2=result.get("draft_v2", ""),
                        review_json=result.get("review", {}),
                        source_article_ids=[],
                        generate_type="recommendation"
                    )
                    _gen_tasks[tid]["status"] = "done"
                except Exception as e:
                    _gen_tasks[tid] = {"status": "error", "result": {"error": str(e)}}
            threading.Thread(target=_run_rec, args=(task_id, rec_data), daemon=True).start()
            self.send_json({"task_id": task_id})

        elif self.path == "/api/myfeeds/add":
            user = self._require_login()
            if not user: return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            name = body.get("name", "").strip()
            url = body.get("url", "").strip()
            if not name or not url:
                self.send_json({"ok": False, "msg": "名称和 URL 不能为空"}, 400); return
            if not url.startswith("http"):
                self.send_json({"ok": False, "msg": "请输入完整的 URL（以 http 开头）"}, 400); return
            from db import add_user_feed
            result = add_user_feed(user["id"], name, url, DB_PATH)
            if result["ok"]:
                # 立即抓取这条新源
                def _fetch_new(uid, fid, furl):
                    try:
                        import feedparser
                        from db import save_user_article, update_feed_fetched
                        parsed = feedparser.parse(furl)
                        for entry in parsed.entries[:30]:
                            t = entry.get("title", "").strip()
                            u = entry.get("link", "").strip()
                            c = entry.get("summary", "") or ""
                            p = entry.get("published", "") or entry.get("updated", "")
                            if t and u:
                                save_user_article(uid, fid, t, u, c[:2000] or None, None, p, DB_PATH)
                        update_feed_fetched(fid, DB_PATH)
                    except Exception:
                        pass
                threading.Thread(target=_fetch_new,
                    args=(user["id"], result["id"], url), daemon=True).start()
            self.send_json(result)

        elif self.path == "/api/myfeeds/delete":
            user = self._require_login()
            if not user: return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            feed_id = body.get("id")
            if not feed_id:
                self.send_json({"ok": False, "msg": "缺少 id"}, 400); return
            from db import delete_user_feed
            self.send_json(delete_user_feed(int(feed_id), user["id"], DB_PATH))

        elif self.path == "/api/myfeeds/refresh":
            user = self._require_login()
            if not user: return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            feed_id = int(body.get("id", 0))
            from db import get_user_feeds
            feeds = get_user_feeds(user["id"], DB_PATH)
            target = next((f for f in feeds if f["id"] == feed_id), None)
            if not target:
                self.send_json({"ok": False, "msg": "源不存在"}, 404); return
            def _refresh(uid, fid, furl):
                try:
                    import feedparser
                    from db import save_user_article, update_feed_fetched
                    parsed = feedparser.parse(furl)
                    for entry in parsed.entries[:30]:
                        t = entry.get("title", "").strip()
                        u = entry.get("link", "").strip()
                        c = entry.get("summary", "") or ""
                        p = entry.get("published", "") or entry.get("updated", "")
                        if t and u:
                            save_user_article(uid, fid, t, u, c[:2000] or None, None, p, DB_PATH)
                    update_feed_fetched(fid, DB_PATH)
                except Exception:
                    pass
            threading.Thread(target=_refresh,
                args=(user["id"], feed_id, target["url"]), daemon=True).start()
            self.send_json({"ok": True, "msg": "正在后台刷新，约15秒后可查看新文章"})

        elif self.path == "/api/subscribe/translate":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            titles = body.get("titles", [])
            translations = [""] * len(titles)
            if titles:
                try:
                    from analyze import get_client, chat
                    client = get_client()
                    titles_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
                    prompt = (f"将以下英文标题逐条翻译成简洁的中文（保留专业术语缩写），"
                              f"只输出JSON数组，格式 [\"译文1\",\"译文2\",...]，不要多余文字：\n{titles_text}")
                    resp = chat(client, prompt, max_tokens=800)
                    resp = resp.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                    translations = json.loads(resp)
                except Exception:
                    pass
            self.send_json({"translations": translations})

        elif self.path == "/api/subscribe/save":
            user = self._require_login()
            if not user: return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            keywords = body.get("keywords", "").strip()
            if not keywords:
                self.send_json({"ok": False, "msg": "关键词不能为空"}, 400); return
            from db import add_subscription, update_subscription, get_active_subscriptions
            subs = get_active_subscriptions(DB_PATH)
            existing = next((s for s in subs if s["email"] == user["email"]), None)
            if existing:
                result = update_subscription(user["email"], keywords, db_path=DB_PATH)
            else:
                result = add_subscription(user["email"], keywords, db_path=DB_PATH)
            self.send_json(result)

        elif self.path == "/api/subscribe/cancel/me":
            user = self._require_login()
            if not user: return
            from db import cancel_subscription
            self.send_json(cancel_subscription(user["email"], DB_PATH))

        elif self.path == "/api/subscribe":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            email = body.get("email", "").strip()
            keywords = body.get("keywords", "").strip()
            api_key = body.get("api_key", "").strip()
            if not email or not keywords:
                self.send_json({"ok": False, "msg": "邮箱和关键词不能为空"}, 400)
                return
            from db import add_subscription
            result = add_subscription(email, keywords, api_key or None)
            self.send_json(result)

        elif self.path == "/api/subscribe/update":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            email = body.get("email", "").strip()
            keywords = body.get("keywords", "").strip()
            api_key = body.get("api_key", "").strip()
            if not email or not keywords:
                self.send_json({"ok": False, "msg": "邮箱和关键词不能为空"}, 400)
                return
            from db import update_subscription
            result = update_subscription(email, keywords, api_key or None)
            self.send_json(result)

        elif self.path == "/api/subscribe/cancel":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            email = body.get("email", "").strip()
            if not email:
                self.send_json({"ok": False, "msg": "邮箱不能为空"}, 400)
                return
            from db import cancel_subscription
            result = cancel_subscription(email)
            self.send_json(result)

        elif self.path == "/api/subscribe/list":
            from db import get_active_subscriptions
            subs = get_active_subscriptions()
            # 不返回api_key明文
            safe = [{"id": s["id"], "email": s["email"], "keywords": s["keywords"],
                     "active": s["active"], "created_at": s["created_at"],
                     "last_sent_at": s["last_sent_at"]} for s in subs]
            self.send_json({"subscriptions": safe})

        elif self.path == "/api/subscribe/push":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            email = body.get("email", "").strip()
            if not email:
                self.send_json({"ok": False, "msg": "邮箱不能为空"}, 400)
                return
            try:
                from mailer import push_single
                result = push_single(email)
                self.send_json(result)
            except Exception as e:
                self.send_json({"ok": False, "msg": str(e)})

        elif self.path == "/api/invite/create":
            user = self._require_login()
            if not user:
                return
            ADMIN_EMAILS = ['2471149840@qq.com', 'zhengwenxin79@gmail.com']
            if user["email"] not in ADMIN_EMAILS:
                self.send_json({"ok": False, "msg": "无权限"}, 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            domain_tags = body.get("domain_tags", "")
            from db import create_invite_token
            token = create_invite_token(domain_tags, user["email"], DB_PATH)
            self.send_json({"ok": True, "token": token})

        elif path == "/api/invite/info":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            token = qs.get("token", [""])[0]
            if not token:
                self.send_json({"ok": False}, 400)
                return
            from db import get_invite_token, use_invite_token
            info = get_invite_token(token, DB_PATH)
            if not info:
                self.send_json({"ok": False, "msg": "邀请链接无效"}, 404)
                return
            use_invite_token(token, DB_PATH)
            self.send_json({"ok": True, "domain_tags": info["domain_tags"]})

        elif self.path == "/api/invite/list":
            user = self._require_login()
            if not user:
                return
            ADMIN_EMAILS = ['2471149840@qq.com', 'zhengwenxin79@gmail.com']
            if user["email"] not in ADMIN_EMAILS:
                self.send_json({"ok": False, "msg": "无权限"}, 403)
                return
            from db import list_invite_tokens
            tokens = list_invite_tokens(user["email"], DB_PATH)
            self.send_json({"ok": True, "tokens": tokens})

        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    # 启动时自动初始化数据库（云端首次部署无数据库时必须）
    from db import init_db
    init_db(db_path=DB_PATH)

    # 启动用户 RSS 定时抓取（首次延迟60秒，之后每6小时）
    t = threading.Timer(60, fetch_user_feeds_once)
    t.daemon = True
    t.start()

    port = int(os.environ.get("PORT", 8888))
    print(f"✓ 服务启动：http://localhost:{port}")
    print("  按 Ctrl+C 停止")
    HTTPServer(("", port), Handler).serve_forever()
