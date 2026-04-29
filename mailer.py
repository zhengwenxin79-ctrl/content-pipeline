"""
邮件推送模块
- 每日从数据库中按订阅关键词匹配文章
- 调用DeepSeek生成中文摘要
- 通过QQ邮箱SMTP发送HTML格式邮件
"""

import os
import smtplib
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 加载项目 .env 文件（cron/非交互式环境下 bashrc 不会自动加载）
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from db import get_conn, get_active_subscriptions, update_last_sent

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
SENDER_EMAIL = os.environ.get("MAIL_SENDER", "2471149840@qq.com")
SENDER_PASSWD = os.environ.get("MAIL_PASSWD", "rwezxcacyrepebjh")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
XHS_COOKIE = os.environ.get("XHS_COOKIE", "")
DB_PATH = os.environ.get("DB_PATH", "corpus/corpus.db")


# ── 小红书抓取 ────────────────────────────────────────────────

def _build_xhs_queries(keywords: str) -> list:
    """把用户关键词转成医疗AI语境的小红书搜索词"""
    raw = [k.strip() for k in keywords.split(",") if k.strip()]
    queries = []
    for kw in raw[:3]:  # 最多取前3个关键词，避免请求过多
        kw_lower = kw.lower()
        if "ai" in kw_lower or "医疗" in kw_lower or "临床" in kw_lower:
            queries.append(kw)
        else:
            queries.append(f"AI{kw}")
    queries.append("医疗AI")  # 兜底：始终搜一条医疗AI综合词
    return list(dict.fromkeys(queries))  # 去重保序


def rerank_articles(articles: list, research_direction: str, api_key: str = "") -> list:
    """
    用 LLM 按用户研究方向对候选文章重排序，返回 Top 5，每篇附推荐理由。
    如果重排失败，原样返回前5篇（保证推送不中断）。
    """
    if not research_direction or not articles:
        return articles[:5]

    key = api_key or DEEPSEEK_API_KEY
    if not key:
        return articles[:5]

    candidates = articles[:30]  # 最多取30篇喂给LLM，控制token消耗
    lines = []
    for a in candidates:
        summary = (a.get("content") or a.get("summary") or "")[:200]
        lines.append(f'{a["id"]}|{a["title"]}|{summary}')
    candidates_text = "\n".join(lines)

    prompt = f"""你是一位科研助手，请根据用户的研究方向，从以下候选论文中挑出最相关的5篇。

用户研究方向：{research_direction}

候选论文（格式：ID|标题|摘要片段）：
{candidates_text}

要求：
1. 只返回最相关的5篇，按相关性从高到低排序
2. 每篇写一句话推荐理由（15字以内，说明为什么和用户研究方向相关）
3. 严格按以下格式输出，每行一篇，不要输出其他内容：
ID|推荐理由"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            timeout=30,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        id_map = {a["id"]: a for a in candidates}
        result = []
        for line in resp.choices[0].message.content.strip().split("\n"):
            if "|" not in line:
                continue
            parts = line.split("|", 1)
            try:
                aid = int(parts[0].strip())
            except ValueError:
                continue
            if aid in id_map:
                article = dict(id_map[aid])
                article["recommend_reason"] = parts[1].strip() if len(parts) > 1 else ""
                result.append(article)
        # 如果LLM返回不足5篇，用原始顺序补齐
        returned_ids = {a["id"] for a in result}
        for a in candidates:
            if len(result) >= 5:
                break
            if a["id"] not in returned_ids:
                result.append(a)
        print(f"  → LLM重排序完成，返回 {len(result)} 篇")
        return result
    except Exception as e:
        print(f"  ⚠ LLM重排序失败，使用原始排序: {e}")
        return articles[:5]


def fetch_xhs_for_keywords(keywords: str, cookie: str = "", candidate_pool: int = 5) -> list:
    """按关键词抓取小红书热门笔记（自动加医疗AI语境前缀）"""
    if not cookie:
        return []
    try:
        from scrapers.xhs_fetcher import fetch_xhs_notes
        queries = _build_xhs_queries(keywords)
        results = fetch_xhs_notes(queries, candidate_pool=candidate_pool, cookies_str=cookie)
        return results
    except Exception as e:
        print(f"⚠ 小红书抓取失败: {e}")
        return []


# ── 关键词匹配 ────────────────────────────────────────────────

_ZH_EN_MAP = {
    "诊断": ["diagnos", "detection"],
    "影像": ["imaging", "radiology", "MRI", "CT", "X-ray"],
    "病理": ["patholog"],
    "心电图": ["ECG", "EKG", "electrocardiog"],
    "超声": ["ultrasound", "echocardiog"],
    "眼科": ["ophthalmol", "retinal", "fundus"],
    "皮肤": ["dermatol", "skin"],
    "肿瘤": ["tumor", "cancer", "oncol"],
    "药物": ["drug", "pharmacol", "therapeut"],
    "基因": ["gene", "genom", "DNA"],
    "蛋白质": ["protein", "proteom"],
    "手术": ["surgery", "surgical", "operat"],
    "预测": ["predict", "prognos", "forecast"],
    "分类": ["classif", "categor"],
    "分割": ["segment"],
    "检测": ["detect", "screen"],
    "电子病历": ["EHR", "EMR", "clinical record"],
    "大模型": ["LLM", "large language model", "GPT", "foundation model"],
    "多模态": ["multimodal", "multi-modal"],
    "临床": ["clinical", "clinic"],
    "医院": ["hospital", "medical center"],
    "患者": ["patient"],
    "治疗": ["treatment", "therapy", "therapeut"],
}


def _expand_keywords(kws: list) -> list:
    """将中文关键词扩展为英文同义词，同时保留原词"""
    expanded = list(kws)
    for kw in kws:
        for zh, en_list in _ZH_EN_MAP.items():
            if zh in kw:
                expanded.extend(en_list)
    return list(dict.fromkeys(expanded))  # 去重保序


def match_articles(keywords: str, days: int = 1, db_path: str = DB_PATH) -> list:
    """从今日新抓取的文章中匹配关键词，返回匹配列表（自动扩展中文词为英文）"""
    kws = [k.strip() for k in keywords.split(",") if k.strip()]
    if not kws:
        return []

    kws = _expand_keywords(kws)

    conn = get_conn(db_path)
    conditions = " OR ".join(
        ["(title LIKE ? OR content LIKE ?)"] * len(kws)
    )
    params = []
    for kw in kws:
        params += [f"%{kw}%", f"%{kw}%"]
    params.append(f"-{days} days")

    rows = conn.execute(f"""
        SELECT id, title, content, source_name, url, published_at, quality_score
        FROM articles
        WHERE ({conditions})
          AND fetched_at >= datetime('now', ?)
        ORDER BY quality_score DESC
        LIMIT 10
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 摘要生成 ──────────────────────────────────────────────────

def generate_summaries(articles: list, api_key: str) -> list:
    """用DeepSeek为匹配文章生成一句话中文摘要"""
    if not api_key or not articles:
        return articles

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=60)

        articles_text = "\n\n".join([
            f"ID:{a['id']} 标题:{a['title']}\n内容:{(a['content'] or '')[:500]}"
            for a in articles
        ])

        prompt = f"""你是医疗AI领域的科研助手。请为以下论文/文章各写一句话核心总结，要求：
1. 40字以内
2. 格式：[做了什么] + [关键结论或数字]
3. 直接说结论，不要用"本文"、"研究者"等开头
4. 举例：「提出基于Transformer的ECG分类模型，在MIT-BIH数据集上F1达97.3%，超越现有方法4%」

只输出JSON，格式：{{"summaries": [{{"id": 1, "summary": "一句话总结"}}]}}

文章列表：
{articles_text}"""

        resp = client.chat.completions.create(
            model="deepseek-chat", max_tokens=800, temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = resp.choices[0].message.content.strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        summary_map = {s["id"]: s["summary"] for s in result["summaries"]}
        for a in articles:
            a["summary"] = summary_map.get(a["id"], "")
    except Exception as e:
        print(f"⚠ 摘要生成失败: {e}，将只推送标题")
        for a in articles:
            a["summary"] = ""

    return articles


# ── HTML邮件模板 ───────────────────────────────────────────────

def build_html(keywords: str, articles: list, date_str: str, xhs_notes: list = None) -> str:
    kw_tags = "".join([
        f'<span style="background:#ebf4ff;color:#3182ce;padding:2px 8px;border-radius:12px;font-size:12px;margin-right:6px">{k.strip()}</span>'
        for k in keywords.split(",") if k.strip()
    ])

    articles_html = ""
    for i, a in enumerate(articles, 1):
        summary = a.get("summary", "")
        url = a.get("url") or "#"
        source = a.get("source_name") or "未知来源"
        pub = (a.get("published_at") or "")[:10] or "未知日期"
        score = a.get("quality_score") or 0

        reason = a.get("recommend_reason", "")
        reason_html = (
            f'<div style="font-size:12px;color:#667eea;margin-bottom:6px;font-weight:500">'
            f'🎯 为什么推给你：{reason}</div>'
        ) if reason else ""
        summary_html = (
            f'<div style="font-size:13px;font-weight:500;color:#2d3748;margin-bottom:8px;'
            f'line-height:1.6;background:#fffbeb;padding:8px 12px;border-radius:6px;'
            f'border-left:3px solid #f6ad55">💡 {summary}</div>'
        ) if summary else ""
        articles_html += f"""
        <div style="padding:16px 0;border-bottom:1px solid #f0f0f0">
          <div style="font-size:15px;font-weight:600;color:#2d3748;margin-bottom:6px;line-height:1.5">
            <a href="{url}" style="color:#2d3748;text-decoration:none">{i}. {a['title']}</a>
          </div>
          {summary_html}
          {reason_html}
          <div style="font-size:12px;color:#a0aec0">
            📰 {source} &nbsp;·&nbsp; 📅 {pub} &nbsp;·&nbsp; ⭐ {score:.1f}分
            &nbsp;·&nbsp; <a href="{url}" style="color:#667eea">查看原文 →</a>
          </div>
        </div>"""

    if not articles_html:
        articles_html = '<div style="text-align:center;padding:32px;color:#a0aec0">今日暂无匹配文章</div>'

    # 小红书板块
    xhs_section = ""
    if xhs_notes:
        xhs_items = ""
        for i, n in enumerate(xhs_notes, 1):
            url = n.get("url") or "#"
            title = n.get("title") or "无标题"
            liked = n.get("liked_count") or 0
            content = (n.get("content") or "")[:80]
            if content:
                content += "..."
            xhs_items += f"""
            <div style="padding:12px 0;border-bottom:1px solid #fff0f0">
              <div style="font-size:14px;font-weight:600;color:#2d3748;margin-bottom:4px">
                <a href="{url}" style="color:#2d3748;text-decoration:none">{i}. {title}</a>
              </div>
              {"" if not content else f'<div style="font-size:12px;color:#718096;margin-bottom:6px;line-height:1.5">{content}</div>'}
              <div style="font-size:12px;color:#a0aec0">
                ❤️ {liked} 点赞 &nbsp;·&nbsp; <a href="{url}" style="color:#fe2c55">查看笔记 →</a>
              </div>
            </div>"""
        xhs_section = f"""
    <div style="margin:0 32px 24px;border-radius:8px;overflow:hidden;border:1px solid #ffe4e8">
      <div style="background:linear-gradient(135deg,#fe2c55,#ff6b81);padding:12px 16px">
        <span style="font-size:14px;font-weight:700;color:white">📕 小红书热门笔记</span>
        <span style="font-size:12px;color:rgba(255,255,255,0.8);margin-left:8px">按关键词筛选</span>
      </div>
      <div style="padding:0 16px;background:#fffafa">
        {xhs_items}
      </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:620px;margin:32px auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">

    <!-- 头部 -->
    <div style="background:linear-gradient(135deg,#667eea,#764ba2);padding:28px 32px">
      <div style="font-size:20px;font-weight:700;color:white">📊 医疗AI每日情报</div>
      <div style="font-size:13px;color:rgba(255,255,255,0.85);margin-top:6px">{date_str} · 共 {len(articles)} 篇匹配文章</div>
    </div>

    <!-- 关键词 -->
    <div style="padding:16px 32px;background:#fafbff;border-bottom:1px solid #edf2f7">
      <span style="font-size:12px;color:#718096;margin-right:8px">您的关键词：</span>
      {kw_tags}
    </div>

    <!-- 文章列表 -->
    <div style="padding:8px 32px 24px">
      {articles_html}
    </div>

    {xhs_section}

    <!-- 尾部 -->
    <div style="background:#f7fafc;padding:20px 32px;border-top:1px solid #edf2f7;text-align:center">
      <div style="font-size:12px;color:#a0aec0">
        由「医疗AI每日情报」自动推送
        &nbsp;·&nbsp;
        如需退订，回复此邮件或联系管理员
      </div>
    </div>
  </div>
</body>
</html>"""


# ── 发送单封邮件 ───────────────────────────────────────────────

def send_email(to_email: str, subject: str, html: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = to_email
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as s:
            s.login(SENDER_EMAIL, SENDER_PASSWD)
            s.sendmail(SENDER_EMAIL, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"  ✗ 发送失败 {to_email}: {e}")
        return False


# ── 每日推送主函数 ─────────────────────────────────────────────

def run_daily_push(db_path: str = DB_PATH):
    """遍历所有启用的订阅，匹配文章并发送邮件"""
    cst = datetime.now(timezone(timedelta(hours=8)))
    date_str = cst.strftime("%Y年%m月%d日")

    subs = get_active_subscriptions(db_path)
    if not subs:
        print("⚠ 没有启用的订阅，跳过推送")
        return

    print(f"\n📬 开始每日推送，共 {len(subs)} 个订阅...")

    for sub in subs:
        email = sub["email"]
        keywords = sub["keywords"]
        encoded_key = sub.get("api_key") or ""

        # 解码 API Key
        api_key = ""
        if encoded_key:
            try:
                api_key = base64.b64decode(encoded_key.encode()).decode()
            except Exception:
                api_key = DEEPSEEK_API_KEY  # 回退到系统key

        research_direction = sub.get("research_direction") or ""
        print(f"  处理: {email} | 关键词: {keywords}")
        if research_direction:
            print(f"  研究方向: {research_direction[:40]}...")

        # 匹配文章（初筛）
        articles = match_articles(keywords, days=1, db_path=db_path)
        if not articles:
            print(f"  → 今日无匹配文章，跳过")
            continue

        # LLM 重排序（有研究方向时启用）
        if research_direction:
            articles = rerank_articles(articles, research_direction, api_key or DEEPSEEK_API_KEY)
        else:
            articles = articles[:10]

        print(f"  → 匹配到 {len(articles)} 篇，生成摘要...")

        # 生成摘要
        articles = generate_summaries(articles, api_key or DEEPSEEK_API_KEY)

        # 抓取小红书热门笔记
        print(f"  → 抓取小红书热门笔记...")
        xhs_notes = fetch_xhs_for_keywords(keywords, cookie=XHS_COOKIE, candidate_pool=5)
        print(f"  → 小红书抓到 {len(xhs_notes)} 篇")

        # 构建并发送邮件
        subject = f"【医疗AI日报】{keywords.split(',')[0]} | 今日{len(articles)}篇 · {date_str}"
        html = build_html(keywords, articles, date_str, xhs_notes=xhs_notes)

        ok = send_email(email, subject, html)
        if ok:
            update_last_sent(email, db_path)
            print(f"  ✓ 已发送至 {email}")

    print(f"\n✓ 每日推送完成")


def push_single(email: str, db_path: str = DB_PATH) -> dict:
    """立即为指定邮箱推送一次，不受定时限制"""
    cst = datetime.now(timezone(timedelta(hours=8)))
    date_str = cst.strftime("%Y年%m月%d日")

    subs = get_active_subscriptions(db_path)
    sub = next((s for s in subs if s["email"] == email), None)
    if not sub:
        return {"ok": False, "msg": "未找到该邮箱的有效订阅"}

    keywords = sub["keywords"]
    encoded_key = sub.get("api_key") or ""
    api_key = ""
    if encoded_key:
        try:
            api_key = base64.b64decode(encoded_key.encode()).decode()
        except Exception:
            api_key = DEEPSEEK_API_KEY

    # 扩大到3天，避免今日文章太少
    articles = match_articles(keywords, days=3, db_path=db_path)
    if not articles:
        return {"ok": False, "msg": "近3天内没有匹配文章，无法推送"}

    articles = generate_summaries(articles, api_key or DEEPSEEK_API_KEY)

    # 抓取小红书热门笔记
    xhs_notes = fetch_xhs_for_keywords(keywords, cookie=XHS_COOKIE, candidate_pool=5)

    subject = f"【医疗AI情报】{keywords.split(',')[0]} | {len(articles)}篇精选 · {date_str}"
    html = build_html(keywords, articles, date_str, xhs_notes=xhs_notes)

    ok = send_email(email, subject, html)
    if ok:
        update_last_sent(email, db_path)
        return {"ok": True, "msg": f"已成功推送 {len(articles)} 篇文章至 {email}"}
    else:
        return {"ok": False, "msg": "邮件发送失败，请检查SMTP配置"}


if __name__ == "__main__":
    run_daily_push()
