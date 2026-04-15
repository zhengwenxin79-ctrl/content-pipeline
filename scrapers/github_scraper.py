"""
GitHub医疗AI项目抓取模块
策略：搜索高star项目 + 近期新建项目，筛出有价值的入库
不需要GitHub Token（每小时60次免费额度，够用）
"""

import requests
import json
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from db import get_conn, add_article

# 搜索关键词组合
SEARCH_QUERIES = [
    # 高star经典项目（按star排序）
    {"q": "medical AI deep learning",        "sort": "stars",   "min_stars": 500},
    {"q": "healthcare large language model",  "sort": "stars",   "min_stars": 200},
    {"q": "medical image segmentation",       "sort": "stars",   "min_stars": 300},
    {"q": "clinical NLP transformer",         "sort": "stars",   "min_stars": 200},
    # 近期新项目（按更新时间）
    {"q": "medical AI",                       "sort": "updated", "min_stars": 50,  "days": 7},
    {"q": "biomedical LLM",                   "sort": "updated", "min_stars": 20,  "days": 7},
    {"q": "radiology AI diagnosis",           "sort": "updated", "min_stars": 30,  "days": 14},
]

HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "content-pipeline-bot",
}
# 如果有GitHub Token可以填这里提升限额（可选）
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"


def fetch_repos(query_config: dict, per_page: int = 10) -> list:
    """按条件搜索GitHub仓库"""
    q = query_config["q"]
    sort = query_config.get("sort", "stars")
    min_stars = query_config.get("min_stars", 0)
    days = query_config.get("days", None)

    # 加上时间过滤
    if days and sort == "updated":
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        q += f" pushed:>{since}"

    if min_stars:
        q += f" stars:>={min_stars}"

    try:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": q, "sort": sort, "order": "desc", "per_page": per_page},
            headers=HEADERS,
            timeout=10
        )
        if resp.status_code == 403:
            print("  ⚠ GitHub API限额已用完，请1小时后再试（或配置GITHUB_TOKEN）")
            return []
        data = resp.json()
        return data.get("items", [])
    except Exception as e:
        print(f"  ✗ 请求失败: {e}")
        return []


def repo_to_content(repo: dict) -> str:
    """把仓库信息拼成可读的内容摘要"""
    lines = []
    if repo.get("description"):
        lines.append(repo["description"])
    lines.append(f"⭐ {repo['stargazers_count']} stars")
    if repo.get("language"):
        lines.append(f"语言: {repo['language']}")
    if repo.get("topics"):
        lines.append(f"标签: {', '.join(repo['topics'][:6])}")
    lines.append(f"最近更新: {repo['updated_at'][:10]}")
    return "\n".join(lines)


def fetch_github_projects(db_path: str = "corpus/corpus.db") -> int:
    """抓取GitHub医疗AI项目，去重后入库"""
    print("  抓取GitHub医疗AI项目...")
    seen_ids = set()
    count = 0

    for qconfig in SEARCH_QUERIES:
        repos = fetch_repos(qconfig, per_page=8)
        for repo in repos:
            if repo["id"] in seen_ids:
                continue
            seen_ids.add(repo["id"])

            title = f"[GitHub] {repo['full_name']} ⭐{repo['stargazers_count']}"
            content = repo_to_content(repo)
            url = repo["html_url"]
            published = repo["updated_at"][:10]
            topics = repo.get("topics", [])

            art_id = add_article(
                source="github",
                source_name="GitHub",
                title=title,
                content=content,
                url=url,
                published_at=published,
                tags=["GitHub", "开源项目"] + topics[:3],
                db_path=db_path
            )
            if art_id:
                count += 1

    print(f"  ✓ GitHub: 新增 {count} 个项目")
    return count


if __name__ == "__main__":
    fetch_github_projects()
