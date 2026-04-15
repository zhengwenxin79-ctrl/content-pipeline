"""
内容Pipeline主入口
用法示例：
  python main.py init          # 初始化数据库
  python main.py fetch         # 抓取RSS热点
  python main.py score         # 对文章打分
  python main.py titles        # 生成标题建议
  python main.py import-post   # 录入自己的历史文章
  python main.py stats         # 查看统计
  python main.py daily         # 一键执行每日pipeline（fetch+score+titles）
"""

import argparse
import sys
from db import init_db, stats


def cmd_init(args):
    init_db(db_path=args.db)
    print("✓ 初始化完成，可以开始导入数据了")
    print("\n建议第一步：录入你的历史高赞文章")
    print("  python main.py import-post")


def cmd_fetch(args):
    from scrapers.rss import fetch_all
    fetch_all(db_path=args.db)


def cmd_score(args):
    from analyze import score_articles
    score_articles(limit=args.limit, db_path=args.db)


def cmd_titles(args):
    from analyze import recommend_titles
    recommend_titles(topic=args.topic, db_path=args.db)


def cmd_import_post(args):
    if args.file:
        from scrapers.manual import import_from_file
        import_from_file(args.file, db_path=args.db)
    else:
        from scrapers.manual import interactive_import_my_post
        interactive_import_my_post(db_path=args.db)


def cmd_import_wechat(args):
    if args.url:
        from scrapers.wechat import fetch_wechat_url
        fetch_wechat_url(args.url, source_name=args.source or "微信公众号", db_path=args.db)
    elif args.url_file:
        from scrapers.wechat import batch_from_url_list
        batch_from_url_list(args.url_file, source_name=args.source or "微信公众号", db_path=args.db)
    elif args.md_dir:
        from scrapers.wechat import import_markdown_dir
        import_markdown_dir(args.md_dir, source_name=args.source or "微信公众号", db_path=args.db)
    else:
        print("请指定 --url / --url-file / --md-dir")


def cmd_stats(args):
    s = stats(db_path=args.db)
    print("=== 语料库统计 ===")
    labels = {
        "articles": "外部文章（热点/竞品）",
        "my_posts": "自己的历史文章",
        "title_suggestions": "标题推荐记录",
        "drafts": "文章草稿"
    }
    for k, v in s.items():
        print(f"  {labels.get(k, k)}: {v} 条")


def cmd_enrich(args):
    from scrapers.enrich import enrich_articles
    enrich_articles(
        db_path=args.db,
        min_score=args.min_score,
        max_content_len=args.max_len,
        limit=args.limit,
        s2_key=args.s2_key,
    )


def cmd_digest(args):
    from analyze import classify_and_digest
    classify_and_digest(topic=args.topic, days=args.days)


def cmd_daily(args):
    """每日一键pipeline"""
    print("=" * 50)
    print("每日内容Pipeline 开始")
    print("=" * 50)

    print("\n[1/3] 抓取RSS热点...")
    from scrapers.rss import fetch_all
    fetch_all(db_path=args.db)

    print("\n[2/3] AI评分筛选...")
    from analyze import score_articles
    score_articles(limit=30, db_path=args.db)

    print("\n[3/3] 生成标题建议...")
    from analyze import recommend_titles
    recommend_titles(topic=args.topic, db_path=args.db)

    print("\n" + "=" * 50)
    print("✓ 每日Pipeline完成！")
    print("下一步：选择一个标题，运行 python generate.py 生成初稿")


def main():
    parser = argparse.ArgumentParser(
        description="小红书内容Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--db", default="corpus/corpus.db", help="数据库路径")

    subparsers = parser.add_subparsers(dest="command")

    # init
    subparsers.add_parser("init", help="初始化数据库")

    # fetch
    subparsers.add_parser("fetch", help="抓取RSS热点文章")

    # score
    p_score = subparsers.add_parser("score", help="AI评分筛选文章")
    p_score.add_argument("--limit", type=int, default=20)

    # titles
    p_titles = subparsers.add_parser("titles", help="AI推荐标题候选")
    p_titles.add_argument("--topic", type=str, help="今日话题方向")

    # import-post
    p_post = subparsers.add_parser("import-post", help="导入自己的历史文章")
    p_post.add_argument("--file", type=str, help="从文件批量导入")

    # import-wechat
    p_wx = subparsers.add_parser("import-wechat", help="导入微信公众号文章")
    p_wx.add_argument("--url", type=str)
    p_wx.add_argument("--url-file", type=str)
    p_wx.add_argument("--md-dir", type=str)
    p_wx.add_argument("--source", type=str)

    # stats
    subparsers.add_parser("stats", help="查看语料库统计")

    # digest
    p_digest = subparsers.add_parser("digest", help="今日情报摘要（顶刊/大组/商业落地）")
    p_digest.add_argument("--topic", type=str, help="今日关注方向，如'AI辅助诊断'")
    p_digest.add_argument("--days", type=int, default=2, help="看最近几天的文章（默认2天）")

    # enrich
    p_enrich = subparsers.add_parser("enrich", help="为短内容文章补充完整摘要（CrossRef/arXiv/S2）")
    p_enrich.add_argument("--limit", type=int, default=50, help="本次处理篇数上限")
    p_enrich.add_argument("--min-score", type=float, default=6.0, dest="min_score")
    p_enrich.add_argument("--max-len", type=int, default=400, dest="max_len")
    p_enrich.add_argument("--s2-key", type=str, default="", dest="s2_key", help="Semantic Scholar API key（可选）")

    # daily
    p_daily = subparsers.add_parser("daily", help="每日一键pipeline")
    p_daily.add_argument("--topic", type=str, help="今日话题方向")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "fetch": cmd_fetch,
        "enrich": cmd_enrich,
        "score": cmd_score,
        "titles": cmd_titles,
        "digest": cmd_digest,
        "import-post": cmd_import_post,
        "import-wechat": cmd_import_wechat,
        "stats": cmd_stats,
        "daily": cmd_daily,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
