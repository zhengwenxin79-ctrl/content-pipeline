from __future__ import annotations

import argparse
import json
from pathlib import Path

from .brief_render import write_brief, write_brief_index
from .job_runner import JobOptions, default_db_path, run_batch_job
from .job_store import JobStore
from .pipeline import analyze_all, analyze_bench, list_benches, write_profile
from .render import write_index, write_report
from .source_discovery import discover_sources
from .web_app import run_server


DEFAULT_OUTPUT_DIR = Path("bench_analysis_outputs")
DEFAULT_BRIEF_BENCHES = ["APEX", "OneMillion-Bench", "SpreadsheetBench v2"]


def analyze_one(
    name: str,
    output_dir: Path,
    with_web: bool = False,
    discovery_limit: int = 10,
    fetch_limit: int = 6,
    include_general_search: bool = False,
) -> None:
    profile = analyze_bench(
        name,
        with_web=with_web,
        output_dir=output_dir,
        discovery_limit=discovery_limit,
        fetch_limit=fetch_limit,
        include_general_search=include_general_search,
    )
    json_path = write_profile(profile, output_dir)
    html_path = write_report(profile, output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {html_path}")


def analyze_batch(
    output_dir: Path,
    with_web: bool = False,
    discovery_limit: int = 10,
    fetch_limit: int = 6,
    include_general_search: bool = False,
) -> None:
    profiles = analyze_all(
        with_web=with_web,
        output_dir=output_dir,
        discovery_limit=discovery_limit,
        fetch_limit=fetch_limit,
        include_general_search=include_general_search,
    )
    for profile in profiles:
        write_profile(profile, output_dir)
        write_report(profile, output_dir)
    index_path = write_index(profiles, output_dir)
    print(f"Wrote {index_path}")
    print(f"Generated {len(profiles)} bench reports.")


def discover_only(name: str, limit: int, include_general_search: bool = False) -> None:
    profile = analyze_bench(name)
    sources = discover_sources(
        name,
        aliases=profile.aliases,
        seed_sources=profile.sources,
        limit=limit,
        include_general_search=include_general_search,
    )
    for source in sources:
        print(f"{source.relevance_score:.2f}\t{source.type}\t{source.title}\t{source.url}")


def _read_bench_names(values: list[str], bench_file: Path | None) -> list[str]:
    names = [value.strip() for value in values if value.strip()]
    if bench_file:
        file_names = [
            line.strip()
            for line in bench_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        names.extend(file_names)
    deduped = []
    seen = set()
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(name)
    return deduped


def run_job(args: argparse.Namespace) -> None:
    bench_names = _read_bench_names(args.bench_names, args.bench_file)
    options = JobOptions(
        with_web=not args.no_web,
        discovery_limit=args.discovery_limit,
        fetch_limit=args.fetch_limit,
        include_general_search=args.include_general_search,
    )
    job_id = run_batch_job(
        bench_names=bench_names,
        output_root=args.output_dir,
        options=options,
        db_path=args.db_path,
    )
    store = JobStore(args.db_path or default_db_path(args.output_dir))
    job = store.get_job(job_id)
    print(f"Created job: {job_id}")
    print(f"Status: {job['status']}")
    print(f"Output: {job['output_dir']}")
    print(f"Index: {Path(job['output_dir']) / 'index.html'}")
    print(f"Manifest: {Path(job['output_dir']) / 'job.json'}")


def list_jobs(args: argparse.Namespace) -> None:
    store = JobStore(args.db_path or default_db_path(args.output_dir))
    jobs = store.list_jobs(limit=args.limit)
    for job in jobs:
        print(
            f"{job['job_id']}\t{job['status']}\t{job['bench_count']} benches\t"
            f"{job['created_at']}\t{job['output_dir']}"
        )


def show_job(args: argparse.Namespace) -> None:
    store = JobStore(args.db_path or default_db_path(args.output_dir))
    job = store.get_job(args.job_id)
    if job is None:
        raise SystemExit(f"Job not found: {args.job_id}")
    if args.json:
        print(json.dumps(job, ensure_ascii=False, indent=2))
        return
    print(f"Job: {job['job_id']}")
    print(f"Status: {job['status']}")
    print(f"Output: {job['output_dir']}")
    print(f"Manifest: {Path(job['output_dir']) / 'job.json'}")
    print(f"Options: {json.dumps(job['options'], ensure_ascii=False)}")
    print("Bench runs:")
    for run in job["bench_runs"]:
        print(f"- {run['bench_name']} [{run['status']}]")
        for step in run["steps"]:
            error = f" - {step['error'].splitlines()[0]}" if step["error"] else ""
            print(f"  {step['step_name']}: {step['status']}{error}")
        if run["report_html"]:
            print(f"  report: {run['report_html']}")


def brief_prototype(args: argparse.Namespace) -> None:
    bench_names = args.bench_names or DEFAULT_BRIEF_BENCHES
    profiles = [analyze_bench(name, with_web=False) for name in bench_names]
    output_dir = args.output_dir
    for profile in profiles:
        path = write_brief(profile, output_dir, language=args.lang)
        print(f"Wrote {path}")
    index_path = write_brief_index(profiles, output_dir, language=args.lang)
    print(f"Wrote {index_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal Bench analysis pipeline")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List seeded benches")
    list_parser.set_defaults(func=lambda args: print("\n".join(list_benches())))

    one_parser = subparsers.add_parser("analyze", help="Analyze one bench")
    one_parser.add_argument("name")
    one_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    one_parser.add_argument("--with-web", action="store_true", help="Discover, fetch, extract, and reconcile web sources")
    one_parser.add_argument("--discovery-limit", type=int, default=10)
    one_parser.add_argument("--fetch-limit", type=int, default=6)
    one_parser.add_argument("--include-general-search", action="store_true", help="Also try generic search-engine HTML results")
    one_parser.set_defaults(
        func=lambda args: analyze_one(
            args.name,
            args.output_dir,
            with_web=args.with_web,
            discovery_limit=args.discovery_limit,
            fetch_limit=args.fetch_limit,
            include_general_search=args.include_general_search,
        )
    )

    batch_parser = subparsers.add_parser("batch", help="Analyze all seeded benches")
    batch_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    batch_parser.add_argument("--with-web", action="store_true", help="Discover, fetch, extract, and reconcile web sources")
    batch_parser.add_argument("--discovery-limit", type=int, default=10)
    batch_parser.add_argument("--fetch-limit", type=int, default=6)
    batch_parser.add_argument("--include-general-search", action="store_true", help="Also try generic search-engine HTML results")
    batch_parser.set_defaults(
        func=lambda args: analyze_batch(
            args.output_dir,
            with_web=args.with_web,
            discovery_limit=args.discovery_limit,
            fetch_limit=args.fetch_limit,
            include_general_search=args.include_general_search,
        )
    )

    discover_parser = subparsers.add_parser("discover", help="Search candidate sources for one bench")
    discover_parser.add_argument("name")
    discover_parser.add_argument("--limit", type=int, default=10)
    discover_parser.add_argument("--include-general-search", action="store_true", help="Also try generic search-engine HTML results")
    discover_parser.set_defaults(func=lambda args: discover_only(args.name, args.limit, args.include_general_search))

    job_run_parser = subparsers.add_parser("job-run", help="Create and run a batch analysis job")
    job_run_parser.add_argument("bench_names", nargs="*", help="Bench names. Quote names with spaces.")
    job_run_parser.add_argument("--bench-file", type=Path, help="Optional newline-separated bench list")
    job_run_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    job_run_parser.add_argument("--db-path", type=Path)
    job_run_parser.add_argument("--no-web", action="store_true", help="Disable web discovery/fetch/extraction for this job")
    job_run_parser.add_argument("--discovery-limit", type=int, default=10)
    job_run_parser.add_argument("--fetch-limit", type=int, default=6)
    job_run_parser.add_argument("--include-general-search", action="store_true", help="Also try generic search-engine HTML results")
    job_run_parser.set_defaults(func=run_job)

    job_list_parser = subparsers.add_parser("job-list", help="List recent batch jobs")
    job_list_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    job_list_parser.add_argument("--db-path", type=Path)
    job_list_parser.add_argument("--limit", type=int, default=20)
    job_list_parser.set_defaults(func=list_jobs)

    job_show_parser = subparsers.add_parser("job-show", help="Show one batch job")
    job_show_parser.add_argument("job_id")
    job_show_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    job_show_parser.add_argument("--db-path", type=Path)
    job_show_parser.add_argument("--json", action="store_true")
    job_show_parser.set_defaults(func=show_job)

    web_parser = subparsers.add_parser("web", help="Start the local Bench Analysis web UI")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    web_parser.set_defaults(func=lambda args: run_server(args.host, args.port, args.output_dir))

    brief_parser = subparsers.add_parser("brief-prototype", help="Generate sky-blue research brief HTML prototypes")
    brief_parser.add_argument("bench_names", nargs="*", help="Bench names. Defaults to APEX, OneMillion-Bench, SpreadsheetBench v2.")
    brief_parser.add_argument("--lang", default="zh-CN", choices=["zh-CN"], help="Brief display language")
    brief_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "research_briefs")
    brief_parser.set_defaults(func=brief_prototype)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        args = parser.parse_args(["batch"])
    args.func(args)


if __name__ == "__main__":
    main()
