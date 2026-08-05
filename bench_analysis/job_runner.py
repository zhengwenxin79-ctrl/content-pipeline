from __future__ import annotations

import random
import re
import string
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .extract import extract_facts
from .fetch import fetch_sources
from .brief_render import write_brief, write_brief_index
from .job_manifest import build_job_manifest, write_job_manifest
from .job_paths import JobPaths, default_db_path
from .job_store import JobStore
from .llm_analysis import generate_llm_analysis
from .paper_analysis_extract import extract_paper_analysis
from .pipeline import analyze_bench, normalize_name, write_profile
from .reconcile import reconcile_profile
from .render import write_index, write_job_index, write_report
from .results import extract_model_results
from .schema import BenchProfile, RawDocument, SourceRecord
from .source_discovery import discover_sources


JOB_STEPS = [
    "resolve_identity",
    "discover_sources",
    "fetch_raw",
    "extract_fields",
    "extract_paper_analysis",
    "extract_results",
    "reconcile",
    "llm_analysis",
    "render_report",
]


@dataclass
class JobOptions:
    with_web: bool = True
    discovery_limit: int = 10
    fetch_limit: int = 6
    include_general_search: bool = False
    manual_sources: dict[str, list[dict]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "with_web": self.with_web,
            "discovery_limit": self.discovery_limit,
            "fetch_limit": self.fetch_limit,
            "include_general_search": self.include_general_search,
            "manual_sources": self.manual_sources,
        }


def new_job_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(5))
    return f"{stamp}-{suffix}"


def slug_for_input(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or normalize_name(value) or "unknown-bench"


def _run_step(store: JobStore, run_id: str, step_name: str, fn: Callable):
    store.set_step(run_id, step_name, "running")
    try:
        result = fn()
    except Exception as exc:
        store.set_step(run_id, step_name, "failed", error=str(exc))
        raise
    store.set_step(run_id, step_name, "completed")
    return result


def _skip_step(store: JobStore, run_id: str, step_name: str, reason: str) -> None:
    store.set_step(run_id, step_name, "skipped", error=reason)


def _has_missing_core_fields(profile: BenchProfile) -> bool:
    values = {
        "evaluates": profile.evaluates,
        "task_format": profile.task_format,
        "evaluation_method": profile.evaluation_method,
        "dataset_size": profile.dataset_size,
        "data_access": profile.data_access,
    }
    return any(not value or value.strip().lower().startswith("unknown") for value in values.values())


def _source_from_dict(value: SourceRecord | dict) -> SourceRecord | None:
    if isinstance(value, SourceRecord):
        return value
    if not isinstance(value, dict):
        return None
    allowed = {field.name for field in SourceRecord.__dataclass_fields__.values()}
    payload = {key: item_value for key, item_value in value.items() if key in allowed}
    if not payload.get("url"):
        return None
    return SourceRecord(**payload)


def _dedupe_sources(sources: list[SourceRecord]) -> list[SourceRecord]:
    by_url: dict[str, SourceRecord] = {}
    for source in sources:
        existing = by_url.get(source.url)
        if existing is None or source.relevance_score > existing.relevance_score:
            by_url[source.url] = source
    return list(by_url.values())


def _sources_from_seed(seed: dict | None) -> tuple[list[SourceRecord], list[SourceRecord]]:
    if not seed:
        return [], []
    cached = [_source_from_dict(source) for source in seed.get("sources", [])]
    manual = [_source_from_dict(source) for source in seed.get("manual_sources", [])]
    return [source for source in cached if source], [source for source in manual if source]


def _manual_sources_from_options(options: JobOptions, bench_name: str, profile: BenchProfile) -> list[SourceRecord]:
    lookup_keys = {
        bench_name.strip().lower(),
        profile.name.strip().lower(),
        profile.slug.strip().lower(),
    }
    sources: list[SourceRecord] = []
    for key, values in options.manual_sources.items():
        if key.strip().lower() not in lookup_keys:
            continue
        for value in values:
            source = _source_from_dict(value)
            if source:
                sources.append(source)
    return _dedupe_sources(sources)


def run_one_bench(
    bench_name: str,
    job_id: str,
    run_id: str,
    output_dir: Path,
    options: JobOptions,
    store: JobStore,
) -> BenchProfile | None:
    store.update_bench_run(run_id, "running")
    try:
        profile = _run_step(
            store,
            run_id,
            "resolve_identity",
            lambda: analyze_bench(bench_name, with_web=False),
        )
        seed = store.find_bench_seed(bench_name) or store.find_bench_seed(profile.slug) or store.find_bench_seed(profile.name)
        cached_seed_sources, manual_seed_sources = _sources_from_seed(seed)
        manual_option_sources = _manual_sources_from_options(options, bench_name, profile)
        manual_sources = _dedupe_sources([*manual_seed_sources, *manual_option_sources])
        seed_sources = _dedupe_sources([*profile.sources, *cached_seed_sources, *manual_sources])
        store.upsert_bench_seed(
            slug=profile.slug,
            name=profile.name,
            aliases=profile.aliases,
            manual_sources=manual_sources if manual_sources else None,
        )

        if options.with_web:
            sources = _run_step(
                store,
                run_id,
                "discover_sources",
                lambda: discover_sources(
                    profile.name,
                    aliases=profile.aliases,
                    seed_sources=seed_sources,
                    limit=options.discovery_limit,
                    include_general_search=options.include_general_search,
                ),
            )
            documents = _run_step(
                store,
                run_id,
                "fetch_raw",
                lambda: fetch_sources(
                    sources,
                    raw_dir=output_dir / profile.slug / "raw",
                    limit=options.fetch_limit,
                ),
            )
            facts = _run_step(store, run_id, "extract_fields", lambda: extract_facts(documents))
            paper_analysis = _run_step(
                store,
                run_id,
                "extract_paper_analysis",
                lambda: extract_paper_analysis(documents),
            )
            model_results = _run_step(
                store,
                run_id,
                "extract_results",
                lambda: extract_model_results(documents, bench_name=profile.name),
            )
        else:
            sources = seed_sources
            documents: list[RawDocument] = []
            facts = []
            paper_analysis = None
            model_results = []
            _skip_step(store, run_id, "discover_sources", "with_web disabled")
            _skip_step(store, run_id, "fetch_raw", "with_web disabled")
            _skip_step(store, run_id, "extract_fields", "with_web disabled")
            _skip_step(store, run_id, "extract_paper_analysis", "with_web disabled")
            _skip_step(store, run_id, "extract_results", "with_web disabled")

        profile = _run_step(
            store,
            run_id,
            "reconcile",
            lambda: reconcile_profile(
                profile,
                discovered_sources=sources,
                raw_documents=documents,
                extracted_facts=facts,
                model_results=model_results,
                paper_analysis=paper_analysis,
            ),
        )
        llm_analysis = _run_step(
            store,
            run_id,
            "llm_analysis",
            lambda: generate_llm_analysis(profile, documents),
        )
        profile.llm_analysis = llm_analysis
        if llm_analysis.one_sentence:
            profile.localized_brief.one_liner = llm_analysis.one_sentence
        store.upsert_bench_seed(
            slug=profile.slug,
            name=profile.name,
            aliases=profile.aliases,
            sources=[source for source in profile.sources if source.url not in {manual.url for manual in manual_sources}],
            manual_sources=manual_sources if manual_sources else None,
        )
        def render_outputs() -> tuple[Path, Path]:
            profile_json_path = write_profile(profile, output_dir)
            report_html_path = write_report(profile, output_dir)
            write_brief(profile, output_dir / "briefs", language="zh-CN")
            return profile_json_path, report_html_path

        profile_json, report_html = _run_step(store, run_id, "render_report", render_outputs)
        has_warnings = bool(
            profile.conflicts
            or [doc for doc in profile.raw_documents if doc.error]
            or profile.status in {"ambiguous", "unresolved"}
            or _has_missing_core_fields(profile)
        )
        final_status = "completed_with_warnings" if has_warnings else "completed"
        store.update_bench_run(
            run_id,
            final_status,
            profile_json=str(profile_json),
            report_html=str(report_html),
        )
        store.update_seed_artifacts(
            slug=profile.slug,
            job_id=job_id,
            run_id=run_id,
            status=final_status,
            profile_json=str(profile_json),
            report_html=str(report_html),
            brief_html=str(output_dir / "briefs" / f"{profile.slug}.html"),
        )
        return profile
    except Exception:
        store.update_bench_run(run_id, "failed", error=traceback.format_exc(limit=6))
        return None


def run_batch_job(
    bench_names: list[str],
    output_root: Path,
    options: JobOptions,
    db_path: Path | None = None,
    job_id: str | None = None,
) -> str:
    if not bench_names:
        raise ValueError("At least one bench name is required.")

    output_root.mkdir(parents=True, exist_ok=True)
    job_id = job_id or new_job_id()
    paths = JobPaths(output_root=output_root, job_id=job_id)
    job_output_dir = paths.job_dir
    job_output_dir.mkdir(parents=True, exist_ok=True)

    store = JobStore(db_path or paths.db_path)
    store.create_job(job_id, bench_names, options.to_dict(), job_output_dir)
    store.update_job(job_id, "running")

    profiles: list[BenchProfile] = []
    for index, bench_name in enumerate(bench_names, start=1):
        slug = slug_for_input(bench_name)
        run_id = f"{job_id}-{index:02d}-{slug}"
        store.create_bench_run(run_id, job_id, bench_name, slug)
        profile = run_one_bench(
            bench_name=bench_name,
            job_id=job_id,
            run_id=run_id,
            output_dir=job_output_dir,
            options=options,
            store=store,
        )
        if profile is not None:
            profiles.append(profile)

    job = store.get_job(job_id)
    failed_runs = [run for run in job["bench_runs"] if run["status"] == "failed"] if job else []
    warning_runs = [run for run in job["bench_runs"] if run["status"] == "completed_with_warnings"] if job else []
    if failed_runs and len(failed_runs) == len(bench_names):
        store.update_job(job_id, "failed", error="All bench runs failed.")
    elif failed_runs or warning_runs:
        store.update_job(job_id, "completed_with_warnings")
    else:
        store.update_job(job_id, "completed")

    job = store.get_job(job_id)
    if job:
        write_job_manifest(job, job_output_dir)
        if profiles:
            write_job_index(profiles, build_job_manifest(job), job_output_dir)
            write_brief_index(profiles, job_output_dir / "briefs", language="zh-CN")
        else:
            write_index(profiles, job_output_dir)
    return job_id
