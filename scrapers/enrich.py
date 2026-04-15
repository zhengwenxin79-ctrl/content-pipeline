"""
摘要补充模块 - 为内容过短的文章从免费 API 补充完整摘要

数据来源优先级：
1. CrossRef API  — 适合 Nature/Lancet/JAMA/ScienceDirect 等 DOI 文章
2. arXiv API    — 适合 arXiv 预印本（备用，RSS 抓取时已有摘要）
3. Semantic Scholar — 限频备选（需 API key 才能高频使用）

完全免费，无需 API key（S2 无 key 时限 100 req/5min，谨慎使用）
"""

import re
import time
import urllib.request
import urllib.parse
import urllib.error
import json
import xml.etree.ElementTree as ET
from db import get_conn


# ── DOI 提取 ────────────────────────────────────────────
def extract_doi(url: str) -> str:
    """从 URL 提取 DOI"""
    if not url:
        return ""

    # 通用 doi.org 格式（最优先）
    m = re.search(r"doi\.org/([^?#\s]+)", url)
    if m:
        return m.group(1)

    # nature.com/articles/s41746-xxx → 10.1038/s41746-xxx
    m = re.search(r"nature\.com/articles/([^?#\s]+)", url)
    if m:
        slug = m.group(1).split("?")[0]
        # 判断出版商前缀
        prefix_map = {
            "s41746": "10.1038", "s41591": "10.1038", "s41586": "10.1038",
            "s41562": "10.1038", "s43856": "10.1038", "s41597": "10.1038",
        }
        for key, prefix in prefix_map.items():
            if slug.startswith(key):
                return f"{prefix}/{slug}"
        return f"10.1038/{slug}"

    # thelancet.com
    m = re.search(r"thelancet\.com/journals/\w+/article/(PIIS[^/\s]+)/", url)
    if m:
        pii = m.group(1)
        return f"10.1016/{pii}"

    # ScienceDirect — PII 直接构造 Elsevier DOI（格式：10.1016/j.xxx.yyyy.mm.nnn）
    # PII 本身不是 DOI，但 CrossRef 支持 alternative-id 过滤
    m = re.search(r"sciencedirect\.com/science/article/pii/([A-Z0-9]+)", url, re.IGNORECASE)
    if m:
        pii = m.group(1)
        try:
            q = urllib.parse.quote(pii)
            search_url = f"https://api.crossref.org/works?filter=alternative-id:{q}&rows=1"
            req = urllib.request.Request(search_url, headers={"User-Agent": "content-pipeline/1.0 (mailto:research@example.com)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            items = data.get("message", {}).get("items", [])
            if items:
                return items[0].get("DOI", "")
        except Exception:
            pass
        # 备选：返回 PII 让 fetch_crossref_abstract 直接用 PII 查
        return f"pii:{pii}"

    # NEJM
    m = re.search(r"nejm\.org/doi/([^?#\s]+)", url)
    if m:
        return m.group(1)

    # JAMA
    m = re.search(r"jamanetwork\.com/journals/\w+/fullarticle/(\d+)", url)
    if m:
        jama_id = m.group(1)
        return f"10.1001/jama.{jama_id}"

    # IEEE
    m = re.search(r"ieeexplore\.ieee\.org/document/(\d+)", url)
    if m:
        return f"10.1109/TPAMI.{m.group(1)}"  # 近似，S2 可以补全

    return ""


def extract_arxiv_id(url: str) -> str:
    """从 URL 提取 arXiv ID"""
    if not url:
        return ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", url)
    return m.group(1) if m else ""


# ── CrossRef API ─────────────────────────────────────────
def fetch_crossref_abstract(doi: str) -> str:
    """通过 CrossRef API 获取 DOI 对应文章摘要（免费无 key）"""
    if not doi:
        return ""
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "content-pipeline/1.0 (mailto:research@example.com)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        abstract_raw = data.get("message", {}).get("abstract", "")
        if not abstract_raw:
            return ""
        # 去掉 JATS XML 标签
        abstract = re.sub(r"<[^>]+>", " ", abstract_raw)
        abstract = re.sub(r"\s+", " ", abstract).strip()
        return abstract
    except Exception:
        return ""


# ── arXiv API ────────────────────────────────────────────
def fetch_arxiv_abstract(arxiv_id: str) -> str:
    """通过 arXiv 官方 API 获取摘要（免费）"""
    if not arxiv_id:
        return ""
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "content-pipeline/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read().decode("utf-8")
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_data)
        entry = root.find("atom:entry", ns)
        if entry is None:
            return ""
        summary = entry.find("atom:summary", ns)
        return summary.text.strip() if summary is not None and summary.text else ""
    except Exception:
        return ""


# ── CrossRef 标题搜索 ────────────────────────────────────
def fetch_crossref_by_title(title: str) -> str:
    """通过标题在 CrossRef 搜索并获取摘要（适合 ScienceDirect/IEEE 等）"""
    if not title or len(title) < 10:
        return ""
    q = urllib.parse.quote(title[:120])
    url = f"https://api.crossref.org/works?query.title={q}&rows=1&select=DOI,abstract,title"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "content-pipeline/1.0 (mailto:research@example.com)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        items = data.get("message", {}).get("items", [])
        if not items:
            return ""
        item = items[0]
        # 验证标题匹配度（避免返回不相关的文章）
        found_title = " ".join(item.get("title", [""])).lower()
        if not any(w in found_title for w in title.lower().split()[:4] if len(w) > 4):
            return ""
        abstract_raw = item.get("abstract", "")
        if not abstract_raw:
            return ""
        abstract = re.sub(r"<[^>]+>", " ", abstract_raw)
        return re.sub(r"\s+", " ", abstract).strip()
    except Exception:
        return ""


# ── Semantic Scholar API（限频备选）─────────────────────
def fetch_s2_abstract(doi: str = "", title: str = "", s2_key: str = "") -> str:
    """通过 Semantic Scholar 获取摘要（有 key 时高频，无 key 慎用）"""
    headers = {"User-Agent": "content-pipeline/1.0"}
    if s2_key:
        headers["x-api-key"] = s2_key

    # 优先用 DOI 精确查询
    if doi:
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi, safe='/')}?fields=abstract,tldr"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            abstract = data.get("abstract") or ""
            if not abstract:
                tldr = data.get("tldr") or {}
                abstract = tldr.get("text", "")
            return abstract
        except Exception:
            pass

    # 用标题搜索
    if title:
        q = urllib.parse.quote(title[:100])
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&fields=abstract,tldr&limit=1"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            papers = data.get("data", [])
            if papers:
                abstract = papers[0].get("abstract") or ""
                if not abstract:
                    tldr = papers[0].get("tldr") or {}
                    abstract = tldr.get("text", "")
                return abstract
        except Exception:
            pass

    return ""


# ── DeepSeek 兜底推断 ────────────────────────────────────
def fetch_deepseek_abstract(title: str, url: str, deepseek_key: str) -> str:
    """当所有 API 都拿不到摘要时，用 DeepSeek 根据标题推断研究内容（标注为推断）"""
    from openai import OpenAI
    client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
    prompt = f"""你是一个医疗AI领域的专家。以下是一篇学术论文的标题，请根据标题推断这篇论文的研究内容，写一段简洁的摘要（150-250字，中文）。

论文标题：{title}
来源链接：{url}

要求：
- 根据标题中的关键词（方法名、疾病、技术）推断研究目的和可能的贡献
- 语气客观，用"该研究""研究者"等表述
- 末尾加一句：「注：本摘要为基于标题的推断，非原文摘要。」
- 只输出摘要文字，不要其他说明"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat", timeout=30, max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return ""


# ── 主入口 ───────────────────────────────────────────────
def enrich_articles(db_path: str = "corpus/corpus.db",
                    min_score: float = 6.5,
                    max_content_len: int = 400,
                    limit: int = 50,
                    s2_key: str = "",
                    deepseek_key: str = "",
                    delay: float = 1.0):
    """
    批量为内容不足的高分文章补充摘要

    参数：
        min_score       只处理质量分 >= 此值的文章
        max_content_len 只处理内容长度 <= 此值的文章（短内容才需要补充）
        limit           本次最多处理多少篇
        s2_key          Semantic Scholar API key（可选）
        delay           请求间隔秒数（避免触发限频）
    """
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT id, title, url, source, content
        FROM articles
        WHERE quality_score >= ?
          AND url IS NOT NULL AND url != ''
          AND source != 'github'
          AND (
            length(coalesce(content, '')) <= ?
            OR content LIKE '<%'
            OR content LIKE '%Published online%'
            OR content LIKE '%doi.org%'
          )
        ORDER BY quality_score DESC
        LIMIT ?
    """, (min_score, max_content_len, limit)).fetchall()
    conn.close()

    if not rows:
        print("没有需要补充摘要的文章")
        return 0

    print(f"找到 {len(rows)} 篇需要补充摘要的文章，开始处理...")
    enriched = 0

    for row in rows:
        article_id = row["id"]
        title = row["title"] or ""
        url = row["url"] or ""
        source = row["source"] or ""

        abstract = ""
        method = ""

        # 1. arXiv 官方 API（最稳定，有完整 abstract）
        arxiv_id = extract_arxiv_id(url)
        if arxiv_id:
            abstract = fetch_arxiv_abstract(arxiv_id)
            if abstract:
                method = f"arXiv:{arxiv_id}"

        # 2. CrossRef DOI 精确查询
        if not abstract:
            doi = extract_doi(url)
            if doi and not doi.startswith("pii:"):
                abstract = fetch_crossref_abstract(doi)
                if abstract:
                    method = f"CrossRef:{doi[:40]}"

        # 3. CrossRef 标题搜索（适合 ScienceDirect / IEEE 等 DOI 不在 URL 里的）
        if not abstract and title and not arxiv_id:
            abstract = fetch_crossref_by_title(title)
            if abstract:
                method = "CrossRef(title)"

        # 4. Semantic Scholar（有 key 时全用，无 key 时限量）
        if not abstract and (s2_key or enriched < 8):
            doi = extract_doi(url)
            if doi.startswith("pii:"):
                doi = ""
            abstract = fetch_s2_abstract(doi=doi, title=title, s2_key=s2_key)
            if abstract:
                method = "S2"
            if not s2_key:
                time.sleep(1.5)

        # 5. DeepSeek 兜底：对未出版/无法获取摘要的文章，根据标题推断
        if not abstract and deepseek_key and title and not url.startswith("https://mp.weixin.qq.com"):
            abstract = fetch_deepseek_abstract(title, url, deepseek_key)
            if abstract:
                method = "DeepSeek(inferred)"

        if abstract and len(abstract) > 100:
            conn = get_conn(db_path)
            conn.execute("UPDATE articles SET content = ? WHERE id = ?", (abstract, article_id))
            conn.commit()
            conn.close()
            enriched += 1
            print(f"  ✓ [{method}] {title[:60]} ({len(abstract)}字符)")
        else:
            print(f"  ✗ 未找到摘要: {title[:60]}")

        time.sleep(delay)

    print(f"\n完成：共补充 {enriched}/{len(rows)} 篇文章摘要")
    return enriched


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="为短内容文章补充摘要")
    parser.add_argument("--limit", type=int, default=50, help="本次处理篇数上限")
    parser.add_argument("--min-score", type=float, default=6.5, help="最低质量分")
    parser.add_argument("--max-len", type=int, default=400, help="内容长度上限（超过此长度不处理）")
    parser.add_argument("--s2-key", type=str, default="", help="Semantic Scholar API key（可选）")
    parser.add_argument("--db", type=str, default="corpus/corpus.db")
    args = parser.parse_args()

    enrich_articles(
        db_path=args.db,
        min_score=args.min_score,
        max_content_len=args.max_len,
        limit=args.limit,
        s2_key=args.s2_key,
    )
