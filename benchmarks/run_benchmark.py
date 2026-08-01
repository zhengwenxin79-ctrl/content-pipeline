#!/usr/bin/env python3
"""Run offline recommendation benchmarks and write JSON/Markdown reports."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.datasets import (  # noqa: E402
    Article,
    load_articles_from_sqlite,
    load_judgments_from_sqlite,
    load_judgments_jsonl,
    load_queries_from_db,
    load_queries_jsonl,
)
from benchmarks.metrics import MetricConfig, evaluate_ranking, macro_average  # noqa: E402
from benchmarks.rankers import BaseRanker, RankedArticle, get_rankers  # noqa: E402


def _fmt(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _merge_judgments(*maps: dict[str, dict[int, str]]) -> dict[str, dict[int, str]]:
    merged: dict[str, dict[int, str]] = {}
    for mapping in maps:
        for qid, labels in mapping.items():
            merged.setdefault(qid, {}).update(labels)
    return merged


def _ranked_preview(rows: list[RankedArticle], judgments: dict[int, str], top_k: int) -> list[dict[str, Any]]:
    preview = []
    for idx, row in enumerate(rows[:top_k], start=1):
        article = row.article
        preview.append({
            "rank": idx,
            "article_id": article.id,
            "title": article.title,
            "url": article.url,
            "source_name": article.source_name,
            "quality_score": article.quality_score,
            "score": round(row.score, 6),
            "label": judgments.get(article.id),
            "features": row.features,
        })
    return preview


def evaluate_ranker(
    ranker: BaseRanker,
    queries,
    articles: list[Article],
    judgments_by_query: dict[str, dict[int, str]],
    metric_config: MetricConfig,
    preview_k: int,
) -> dict[str, Any]:
    per_query = []
    for query in queries:
        ranked = ranker.rank(query, articles)
        ranked_ids = [row.article.id for row in ranked]
        judgments = judgments_by_query.get(query.id, {})
        metrics = evaluate_ranking(ranked_ids, judgments, metric_config)
        per_query.append({
            "query_id": query.id,
            "title": query.title,
            "judgments": len(judgments),
            "relevant_judgments": sum(1 for label in judgments.values() if label in {"core", "inspiring"}),
            "metrics": metrics,
            "top_results": _ranked_preview(ranked, judgments, preview_k),
        })
    return {
        "ranker": ranker.name,
        "metrics": macro_average(row["metrics"] for row in per_query),
        "per_query": per_query,
    }


def print_console_table(results: list[dict[str, Any]]) -> None:
    if not results:
        print("No results.")
        return
    metric_names = list(results[0]["metrics"].keys())
    headers = ["ranker", *metric_names]
    widths = {h: len(h) for h in headers}
    rows = []
    for result in results:
        row = {"ranker": result["ranker"]}
        row.update({name: _fmt(result["metrics"].get(name)) for name in metric_names})
        rows.append(row)
        for key, value in row.items():
            widths[key] = max(widths[key], len(value))
    header = " | ".join(h.ljust(widths[h]) for h in headers)
    sep = "-+-".join("-" * widths[h] for h in headers)
    print(header)
    print(sep)
    for row in rows:
        print(" | ".join(row[h].ljust(widths[h]) for h in headers))


def _markdown_table(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No results.\n"
    metric_names = list(results[0]["metrics"].keys())
    lines = [
        "| Ranker | " + " | ".join(metric_names) + " |",
        "|---|" + "|".join("---:" for _ in metric_names) + "|",
    ]
    for result in results:
        vals = [_fmt(result["metrics"].get(name)) for name in metric_names]
        lines.append(f"| {result['ranker']} | " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def _recommendations(results: list[dict[str, Any]]) -> list[str]:
    if not results:
        return ["先接入候选文章和人工标注，否则无法判断推荐质量。"]
    metric = "nDCG@10"
    scored = [r for r in results if r["metrics"].get(metric) is not None]
    if not scored:
        return [
            "优先积累 30-50 个真实 query，每个 query 标注 20-50 篇候选文章。",
            "把当前 benchmark 作为标注验收工具，先看 JudgedRate@10，再看排序指标。",
            "接入 embedding recall 后，用同一 query set 对比 keyword / hybrid / embedding 的 Recall@20。",
        ]
    best = max(scored, key=lambda r: r["metrics"][metric])
    recs = [
        f"当前按 {metric} 看，最佳 baseline 是 `{best['ranker']}`，后续改造应以它作为离线对照组。",
        "增加 embedding recall：用研究方向召回语义相关但关键词不重合的论文，重点观察 Recall@20。",
        "增加 LLM rerank：只重排 Top 50-100 候选，重点观察 nDCG@10 与 IrrelevantRate@10。",
        "积累用户反馈训练集：把 useful/star/read/irrelevant 映射到偏好标签，为 learning-to-rank 准备特征。",
        "上线前保留 quality_score 与 freshness 作为 guardrail，避免纯语义召回带来旧文或低质量噪音。",
    ]
    return recs


def write_reports(report: dict[str, Any], output_dir: str | Path, prefix: str) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{prefix}.json"
    md_path = out / f"{prefix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# AI+X Recommendation Benchmark Report",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- queries: `{report['dataset']['queries']}`",
        f"- articles: `{report['dataset']['articles']}`",
        f"- judgments: `{report['dataset']['judgments']}`",
        "",
        "## Aggregate Metrics",
        "",
        _markdown_table(report["results"]),
        "## Next Recommendations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["next_recommendations"])
    lines.extend([
        "",
        "## Notes",
        "",
        "- Labels: `core=3`, `inspiring=2`, `skim=1`, `irrelevant=0`.",
        "- Precision/Recall/MRR treat `core` and `inspiring` as relevant.",
        "- Unjudged Top-K items count as non-relevant for Precision/nDCG, while IrrelevantRate only counts explicit `irrelevant` labels.",
        "- The LitSearch adapter is intentionally a local-file interface; the sample benchmark does not download external data.",
        "",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="corpus/corpus.db", help="SQLite corpus path.")
    parser.add_argument("--queries", default="data/eval_queries.sample.jsonl", help="JSONL query set path.")
    parser.add_argument("--queries-from-db", action="store_true", help="Load eval_queries from SQLite instead of JSONL.")
    parser.add_argument("--judgments-jsonl", help="Optional JSONL judgments; overrides/adds to DB judgments.")
    parser.add_argument("--days", type=int, default=14, help="Candidate article window.")
    parser.add_argument("--limit", type=int, default=500, help="Max candidate articles.")
    parser.add_argument("--rankers", default="all", help="Comma-separated rankers: keyword,quality,hybrid,all.")
    parser.add_argument("--preview-k", type=int, default=10, help="Top results stored per query.")
    parser.add_argument("--top-k", type=int, dest="preview_k", help="Backward-compatible alias for --preview-k.")
    parser.add_argument("--precision-k", type=int, default=5)
    parser.add_argument("--recall-k", type=int, default=20)
    parser.add_argument("--ndcg-k", type=int, default=10)
    parser.add_argument("--irrelevant-k", type=int, default=10)
    parser.add_argument("--output-dir", default="benchmarks/reports")
    parser.add_argument("--report-prefix", default="")
    parser.add_argument("--no-report", action="store_true", help="Only print console table.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    queries = load_queries_from_db(args.db) if args.queries_from_db else load_queries_jsonl(args.queries)
    articles = load_articles_from_sqlite(args.db, args.days, args.limit)
    db_judgments = load_judgments_from_sqlite(args.db)
    file_judgments = load_judgments_jsonl(args.judgments_jsonl) if args.judgments_jsonl else {}
    judgments = _merge_judgments(db_judgments, file_judgments)
    ranker_names = [name.strip() for name in args.rankers.split(",") if name.strip()]
    rankers = get_rankers(ranker_names)
    metric_config = MetricConfig(
        precision_k=args.precision_k,
        recall_k=args.recall_k,
        ndcg_k=args.ndcg_k,
        irrelevant_k=args.irrelevant_k,
        judged_k=args.ndcg_k,
    )

    print(f"queries={len(queries)} articles={len(articles)} judgments={sum(len(v) for v in judgments.values())}")
    results = [
        evaluate_ranker(ranker, queries, articles, judgments, metric_config, args.preview_k)
        for ranker in rankers
    ]
    print_console_table(results)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": vars(args),
        "dataset": {
            "queries": len(queries),
            "articles": len(articles),
            "judgments": sum(len(v) for v in judgments.values()),
            "query_ids": [q.id for q in queries],
        },
        "results": results,
        "next_recommendations": _recommendations(results),
    }
    if not args.no_report:
        prefix = args.report_prefix or f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        json_path, md_path = write_reports(report, args.output_dir, prefix)
        print(f"JSON report: {json_path}")
        print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
