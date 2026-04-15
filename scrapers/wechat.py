"""
微信公众号文章导入模块

由于微信限制，支持两种方式：
1. 粘贴链接 + requests抓取（需要手动复制链接）
2. 通过 wechat-article-for-ai 工具导出的 Markdown 文件批量导入

wechat-article-for-ai: https://github.com/wechat-article-for-ai/wechat-article-for-ai
"""

import sys
import os
import requests
from pathlib import Path
from bs4 import BeautifulSoup
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from db import add_article


def fetch_wechat_url(url: str, source_name: str = "微信公众号",
                     tags: list = None,
                     db_path: str = "corpus/corpus.db") -> bool:
    """
    抓取单篇微信公众号文章（需要文章是公开可访问的）
    注意：微信会限制爬虫，建议用于手动复制的链接
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://mp.weixin.qq.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 提取标题
        title_tag = soup.find("h1", {"id": "activity-name"}) or \
                    soup.find("h2", {"class": "rich_media_title"})
        title = title_tag.get_text(strip=True) if title_tag else ""

        # 提取正文
        content_div = soup.find("div", {"id": "js_content"}) or \
                      soup.find("div", {"class": "rich_media_content"})
        content = content_div.get_text(separator="\n", strip=True) if content_div else ""

        if not title:
            print(f"  ✗ 未能提取标题，可能被限制访问: {url}")
            return False

        art_id = add_article(
            source="wechat", title=title, content=content[:8000],
            url=url, source_name=source_name,
            tags=tags or ["微信公众号"],
            db_path=db_path
        )
        if art_id:
            print(f"  ✓ 已抓取: {title[:40]}...")
            return True
        else:
            print(f"  ⚠ 已存在: {title[:40]}...")
            return False

    except Exception as e:
        print(f"  ✗ 抓取失败: {e}")
        return False


def import_markdown_dir(dir_path: str, source_name: str = "微信公众号",
                        tags: list = None,
                        db_path: str = "corpus/corpus.db") -> int:
    """
    批量导入wechat-article-for-ai导出的Markdown目录
    每个.md文件视为一篇文章，文件名作为标题
    """
    path = Path(dir_path)
    if not path.exists() or not path.is_dir():
        print(f"目录不存在: {dir_path}")
        return 0

    md_files = list(path.glob("**/*.md"))
    print(f"发现 {len(md_files)} 个Markdown文件")

    count = 0
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        lines = text.strip().split("\n")

        # 尝试从第一行提取标题
        title = lines[0].lstrip("#").strip() if lines else md_file.stem
        content = "\n".join(lines[1:]).strip()

        art_id = add_article(
            source="wechat",
            title=title,
            content=content[:8000],
            url=None,
            source_name=source_name,
            tags=tags or ["微信公众号"],
            db_path=db_path
        )
        if art_id:
            print(f"  ✓ {title[:50]}")
            count += 1

    print(f"\n✓ 共导入 {count} 篇微信文章")
    return count


def batch_from_url_list(url_file: str, source_name: str = "微信公众号",
                        db_path: str = "corpus/corpus.db") -> int:
    """
    从文本文件批量抓取，每行一个URL
    用法: 把要抓的公众号文章链接贴进 data/wechat_urls.txt，一行一个
    """
    path = Path(url_file)
    if not path.exists():
        print(f"URL文件不存在: {url_file}")
        return 0

    urls = [u.strip() for u in path.read_text().splitlines() if u.strip()]
    print(f"共 {len(urls)} 个URL待抓取")

    count = 0
    for url in urls:
        if fetch_wechat_url(url, source_name=source_name, db_path=db_path):
            count += 1

    print(f"\n✓ 成功抓取 {count}/{len(urls)} 篇")
    return count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="微信公众号文章导入")
    parser.add_argument("--url", type=str, help="抓取单篇文章URL")
    parser.add_argument("--url-file", type=str, help="从文件批量抓取URL列表")
    parser.add_argument("--md-dir", type=str, help="从Markdown目录批量导入")
    parser.add_argument("--source", type=str, default="微信公众号", help="来源名称")
    args = parser.parse_args()

    if args.url:
        fetch_wechat_url(args.url, source_name=args.source)
    elif args.url_file:
        batch_from_url_list(args.url_file, source_name=args.source)
    elif args.md_dir:
        import_markdown_dir(args.md_dir, source_name=args.source)
    else:
        parser.print_help()
