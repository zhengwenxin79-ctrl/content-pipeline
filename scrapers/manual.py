"""
手动导入模块 - 两种方式：
1. 逐篇录入自己的历史文章（带互动数据）
2. 批量从TXT/Markdown文件导入
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from db import add_my_post, add_article


def interactive_import_my_post(db_path: str = "corpus/corpus.db"):
    """交互式录入一篇自己的历史文章"""
    print("\n=== 导入自己的历史文章 ===")
    print("（这是语料库最重要的种子，建议把小红书高赞文章都录进来）\n")

    title = input("标题: ").strip()
    if not title:
        print("标题不能为空")
        return

    print("内容（多行，输入 END 结束）:")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    content = "\n".join(lines)

    published_at = input("发布时间 (如 2024-03-15，留空跳过): ").strip() or None
    read_count = int(input("阅读量 (留空填0): ").strip() or "0")
    like_count = int(input("点赞数 (留空填0): ").strip() or "0")
    collect_count = int(input("收藏数 (留空填0): ").strip() or "0")
    comment_count = int(input("评论数 (留空填0): ").strip() or "0")
    tags_str = input("标签 (逗号分隔，如 医疗AI,转行,产品经理): ").strip()
    tags = [t.strip() for t in tags_str.split(",")] if tags_str else []
    notes = input("备注 (文章角度/类型，可留空): ").strip() or None

    post_id = add_my_post(
        title=title, content=content, published_at=published_at,
        read_count=read_count, like_count=like_count,
        collect_count=collect_count, comment_count=comment_count,
        tags=tags, notes=notes, db_path=db_path
    )
    print(f"\n✓ 已导入，ID={post_id}")


def import_from_file(file_path: str, db_path: str = "corpus/corpus.db"):
    """
    从文件批量导入自己的文章
    文件格式（每篇用 --- 分隔）：

    ---
    title: 标题
    published: 2024-03-15
    likes: 120
    collects: 45
    reads: 3000
    tags: 医疗AI, 转行
    ---
    正文内容...
    """
    path = Path(file_path)
    if not path.exists():
        print(f"文件不存在: {file_path}")
        return

    text = path.read_text(encoding="utf-8")
    # 按 --- 分割
    blocks = [b.strip() for b in text.split("---\n") if b.strip()]

    count = 0
    i = 0
    while i < len(blocks):
        meta_block = blocks[i]
        content_block = blocks[i + 1] if i + 1 < len(blocks) else ""

        # 解析meta
        meta = {}
        for line in meta_block.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip()] = val.strip()

        title = meta.get("title", "").strip()
        if not title:
            i += 2
            continue

        tags_str = meta.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",")] if tags_str else []

        post_id = add_my_post(
            title=title,
            content=content_block,
            published_at=meta.get("published"),
            read_count=int(meta.get("reads", 0)),
            like_count=int(meta.get("likes", 0)),
            collect_count=int(meta.get("collects", 0)),
            comment_count=int(meta.get("comments", 0)),
            tags=tags,
            db_path=db_path
        )
        print(f"  ✓ 导入: {title} (ID={post_id})")
        count += 1
        i += 2

    print(f"\n✓ 共导入 {count} 篇文章")


def import_external_article(db_path: str = "corpus/corpus.db"):
    """手动录入一篇外部文章（竞品/热点）"""
    print("\n=== 手动录入外部文章 ===")
    title = input("标题: ").strip()
    url = input("链接 (可留空): ").strip() or None
    source_name = input("来源名称 (如 健康界): ").strip()
    print("内容摘要（输入 END 结束）:")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    content = "\n".join(lines)
    tags_str = input("标签 (逗号分隔): ").strip()
    tags = [t.strip() for t in tags_str.split(",")] if tags_str else []

    art_id = add_article(
        source="manual", title=title, content=content,
        url=url, source_name=source_name, tags=tags, db_path=db_path
    )
    if art_id:
        print(f"✓ 已录入，ID={art_id}")
    else:
        print("⚠ 该URL已存在，跳过")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="手动导入文章")
    parser.add_argument("--my-post", action="store_true", help="录入自己的历史文章")
    parser.add_argument("--from-file", type=str, help="从文件批量导入自己的文章")
    parser.add_argument("--external", action="store_true", help="手动录入外部文章")
    args = parser.parse_args()

    if args.my_post:
        interactive_import_my_post()
    elif args.from_file:
        import_from_file(args.from_file)
    elif args.external:
        import_external_article()
    else:
        parser.print_help()
