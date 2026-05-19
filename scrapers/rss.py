"""
RSS抓取模块
- 外部热点源（直接配置URL）
- 微信公众号（通过 wewe-rss 转成RSS后抓取）
依赖: feedparser
"""

import feedparser
import yaml
import json
import re
from datetime import datetime
from pathlib import Path
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from db import add_article

# 提取 arXiv 标题关键词时过滤掉的停用词
_STOPWORDS = {
    'a', 'an', 'the', 'for', 'with', 'using', 'via', 'based', 'on', 'in',
    'of', 'by', 'from', 'to', 'and', 'or', 'is', 'are', 'we', 'our',
    'this', 'that', 'its', 'as', 'be', 'at', 'it', 'into', 'over',
    'under', 'new', 'towards', 'toward', 'through', 'between', 'among',
    'across', 'large', 'deep', 'learning', 'model', 'models', 'method',
    'approach', 'framework', 'network', 'networks', 'system', 'systems',
}

# arXiv 分类 → 可读领域名
_ARXIV_CATEGORY_MAP = {
    'cs.AI':  'AI',
    'cs.CV':  'Computer Vision',
    'cs.LG':  'Machine Learning',
    'cs.CL':  'NLP',
    'cs.RO':  'Robotics',
    'cs.NE':  'Neural Networks',
    'cs.IR':  'Information Retrieval',
    'cs.HC':  'Human-Computer Interaction',
    'eess.IV': 'Image & Video',
    'eess.SP': 'Signal Processing',
    'q-bio.QM': 'Quantitative Biology',
    'q-bio.GN': 'Genomics',
    'q-bio.NC': 'Neuroscience',
    'stat.ML': 'Statistics & ML',
    'physics.med-ph': 'Medical Physics',
}


def _build_arxiv_tags(source_name: str, title: str) -> str:
    """从 source_name 提取 arXiv 分类，从标题提取关键词，返回 JSON 字符串。"""
    # 从 source_name 中提取形如 "cs.AI" 的分类码
    m = re.search(r'arXiv\s+([a-z\-]+\.[A-Z]+)', source_name)
    category_code = m.group(1) if m else ''
    category_label = _ARXIV_CATEGORY_MAP.get(category_code, category_code)

    # 从标题提取有意义的词（字母开头，长度>=4，不在停用词表）
    words = re.findall(r'[A-Za-z][A-Za-z0-9\-]*', title)
    keywords = [w for w in words
                if len(w) >= 4 and w.lower() not in _STOPWORDS][:6]

    tags = ([category_label] if category_label else []) + keywords
    return json.dumps(tags, ensure_ascii=False)


DEFAULT_SOURCES = [
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/feed/",
        "tags": ["AI", "科技"]
    },
    {
        "name": "STAT News",
        "url": "https://www.statnews.com/feed/",
        "tags": ["医疗AI", "新闻"]
    },
    {
        "name": "Healthcare IT News",
        "url": "https://www.healthcareitnews.com/rss.xml",
        "tags": ["医疗IT"]
    },
    {
        "name": "The Lancet Digital Health",
        "url": "https://www.thelancet.com/rssfeed/landig_current.xml",
        "tags": ["医学AI", "研究"]
    },
    {
        "name": "MedCity News",
        "url": "https://medcitynews.com/feed/",
        "tags": ["医疗", "创业"]
    },
]


def load_config(config_path: str = "config.yaml") -> dict:
    p = Path(config_path)
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    return {}


def fetch_rss(source: dict, db_path: str = "corpus/corpus.db") -> int:
    """抓取单个RSS源，返回新增文章数"""
    print(f"  抓取: {source['name']} ...")
    try:
        feed = feedparser.parse(source["url"])
    except Exception as e:
        print(f"  ✗ 解析失败: {e}")
        return 0

    if feed.bozo and not feed.entries:
        print(f"  ✗ RSS解析异常，可能URL失效")
        return 0

    count = 0
    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", None)
        content = (getattr(entry, "summary", None) or
                   (getattr(entry, "content", [{}])[0].get("value", "") if entry.get("content") else ""))
        published = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            published = datetime(*entry.published_parsed[:6]).isoformat()

        if not title:
            continue

        is_arxiv = source["name"].startswith("arXiv")
        article_id = add_article(
            source=source.get("source_type", "rss"),
            title=title,
            content=content[:5000] if content else None,
            url=url,
            source_name=source["name"],
            published_at=published,
            tags=source.get("tags", []),
            domain_tags=_build_arxiv_tags(source["name"], title) if is_arxiv else '',
            quality_score=7.0 if is_arxiv else 0.0,
            is_processed=1 if is_arxiv else 0,
            db_path=db_path
        )
        if article_id:
            count += 1

    print(f"  ✓ {source['name']}: 新增 {count} 篇")
    return count


def build_wewe_sources(config: dict) -> list:
    """
    从config.yaml读取wewe-rss配置，构建成source列表
    wewe-rss把每个公众号暴露为一个Atom feed，格式：
      <base_url>/feeds/<fakeid>.atom
    """
    wewe = config.get("wewe_rss", {})
    if not wewe.get("enabled", False):
        return []

    base_url = wewe.get("base_url", "http://localhost:4000").rstrip("/")
    accounts = wewe.get("accounts", [])
    sources = []
    for acc in accounts:
        name = acc.get("name", "未知公众号")
        feed_path = acc.get("feed_path", "")
        if not feed_path:
            continue
        sources.append({
            "name": name,
            "url": f"{base_url}{feed_path}",
            "tags": acc.get("tags", ["微信公众号"]),
            "source_type": "wechat_wewe",
        })
    return sources


def fetch_github(db_path: str = "corpus/corpus.db"):
    """抓取GitHub医疗AI项目"""
    try:
        from scrapers.github_scraper import fetch_github_projects
    except ImportError:
        from github_scraper import fetch_github_projects
    fetch_github_projects(db_path=db_path)


def fetch_all(sources: list = None, db_path: str = "corpus/corpus.db",
              config_path: str = "config.yaml") -> int:
    """
    抓取所有RSS源 + wewe-rss公众号源
    sources参数若传入则覆盖默认列表
    """
    config = load_config(config_path)

    # 外部RSS源：优先用config.yaml，否则用DEFAULT_SOURCES
    rss_sources = sources
    if rss_sources is None:
        cfg_sources = config.get("rss_sources", [])
        rss_sources = cfg_sources if cfg_sources else DEFAULT_SOURCES

    # wewe-rss公众号源
    wewe_sources = build_wewe_sources(config)
    if wewe_sources:
        print(f"\n--- 微信公众号（wewe-rss，共{len(wewe_sources)}个）---")
    else:
        print("\n（wewe-rss未启用，仅抓取外部RSS源）")
        print("如需接入微信公众号，参考README配置 wewe_rss 部分\n")

    all_sources = rss_sources + wewe_sources
    total = 0
    for source in all_sources:
        total += fetch_rss(source, db_path=db_path)

    # GitHub项目
    print("\n--- GitHub医疗AI项目 ---")
    fetch_github(db_path=db_path)

    print(f"\n✓ 抓取完成，共新增 {total} 篇文章（GitHub项目另计）")
    return total


if __name__ == "__main__":
    fetch_all()
