from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "1.2"
CORE_FIELDS = ["evaluates", "task_format", "evaluation_method", "dataset_size", "data_access"]
PAPER_ANALYSIS_FIELDS = [
    "core_question",
    "motivation",
    "gap_claimed",
    "model_results_summary",
]


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _load_json(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_path(path_value: str, base_dir: Path) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(path)


def _add_warning(warnings: list[dict[str, str]], warning_type: str, message: str, source: str = "") -> None:
    warnings.append({"type": warning_type, "severity": "warning", "message": message, "source": source})


def _add_error(errors: list[dict[str, str]], error_type: str, message: str, source: str = "") -> None:
    errors.append({"type": error_type, "severity": "error", "message": message, "source": source})


def _count_by_type(items: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        item_type = item.get("type", "unknown")
        counts[item_type] = counts.get(item_type, 0) + 1
    return counts


def review_status_for(error_count: int, warning_count: int, missing_core_fields: list[str]) -> str:
    if error_count:
        return "failed_review"
    if missing_core_fields:
        return "needs_human_review"
    if warning_count:
        return "review_recommended"
    return "ready"


def summarize_profile(profile: dict[str, Any], run: dict[str, Any] | None = None) -> dict[str, Any]:
    run = run or {}
    raw_documents = profile.get("raw_documents", [])
    model_results = profile.get("model_results", [])
    failed_raw = [document for document in raw_documents if document.get("error")]
    cached_raw = [document for document in raw_documents if document.get("cache_status") == "hit"]
    successful_raw = [document for document in raw_documents if not document.get("error")]
    missing_core_fields = [field for field in CORE_FIELDS if not profile.get(field) or str(profile.get(field)).strip().lower().startswith("unknown")]
    paper_analysis = profile.get("paper_analysis", {})
    missing_paper_analysis_fields = [
        field
        for field in PAPER_ANALYSIS_FIELDS
        if not paper_analysis.get(field) or str(paper_analysis.get(field)).strip().lower().startswith("needs review")
    ]
    if not paper_analysis.get("failure_modes"):
        missing_paper_analysis_fields.append("failure_modes")
    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    if profile.get("status") == "ambiguous":
        _add_warning(warnings, "ambiguous_identity", "Bench identity is ambiguous and needs human confirmation.")
    if profile.get("status") == "unresolved":
        _add_warning(warnings, "unresolved_identity", "Bench name was not resolved against the seed catalog.")
    for field in missing_core_fields:
        _add_warning(warnings, "missing_core_field", f"Missing or unknown core field: {field}.")
    for field in missing_paper_analysis_fields:
        _add_warning(warnings, "missing_paper_analysis_field", f"Missing paper-analysis field: {field}.")
    for document in failed_raw:
        _add_warning(
            warnings,
            "raw_fetch_failure",
            document.get("error", "Raw fetch failed."),
            source=document.get("url", ""),
        )
    for conflict in profile.get("conflicts", []):
        _add_warning(
            warnings,
            "field_conflict",
            f"Conflict in field: {conflict.get('field', 'unknown')}.",
            source=conflict.get("source_url", ""),
        )
    for step in run.get("steps", []):
        if step.get("status") == "failed":
            _add_error(errors, "failed_step", step.get("error") or f"Step failed: {step.get('step_name', '')}.")
    if run.get("status") == "failed":
        _add_error(errors, "failed_run", run.get("error") or "Bench run failed.")

    warning_count = len(warnings)
    error_count = len(errors)
    return {
        "sources_count": len(profile.get("sources", [])),
        "raw_documents_count": len(raw_documents),
        "raw_success_count": len(successful_raw),
        "raw_failures_count": len(failed_raw),
        "raw_cache_hits_count": len(cached_raw),
        "extracted_facts_count": len(profile.get("extracted_facts", [])),
        "model_results_count": len(model_results),
        "verified_model_results_count": len(
            [result for result in model_results if result.get("verification_status") == "verified"]
        ),
        "candidate_model_results_count": len(
            [result for result in model_results if result.get("verification_status") != "verified"]
        ),
        "conflicts_count": len(profile.get("conflicts", [])),
        "missing_core_fields": missing_core_fields,
        "missing_paper_analysis_fields": missing_paper_analysis_fields,
        "warnings": warnings,
        "errors": errors,
        "warnings_by_type": _count_by_type(warnings),
        "errors_by_type": _count_by_type(errors),
        "warning_count": warning_count,
        "error_count": error_count,
        "review_status": review_status_for(error_count, warning_count, missing_core_fields),
        "source_statuses": [
            {
                "source_url": document.get("source_url") or document.get("url", ""),
                "fetched_url": document.get("url", ""),
                "type": document.get("type", ""),
                "status": "failed" if document.get("error") else "fetched",
                "cache_status": document.get("cache_status", ""),
                "error": document.get("error", ""),
                "path": document.get("path", ""),
                "text_path": document.get("text_path", ""),
            }
            for document in raw_documents
        ],
    }


def build_job_manifest(job: dict[str, Any]) -> dict[str, Any]:
    output_dir = Path(job.get("output_dir", ""))
    runs = []
    total_warning_count = 0
    total_error_count = 0
    warnings_by_type: dict[str, int] = {}
    errors_by_type: dict[str, int] = {}
    for run in job.get("bench_runs", []):
        profile = _load_json(run.get("profile_json", ""))
        profile_summary = summarize_profile(profile, run=run)
        total_warning_count += profile_summary["warning_count"]
        total_error_count += profile_summary["error_count"]
        for warning_type, count in profile_summary["warnings_by_type"].items():
            warnings_by_type[warning_type] = warnings_by_type.get(warning_type, 0) + count
        for error_type, count in profile_summary["errors_by_type"].items():
            errors_by_type[error_type] = errors_by_type.get(error_type, 0) + count
        runs.append(
            {
                "run_id": run.get("run_id", ""),
                "bench_name": run.get("bench_name", ""),
                "slug": run.get("slug", ""),
                "status": run.get("status", ""),
                "created_at": run.get("created_at", ""),
                "started_at": run.get("started_at", ""),
                "finished_at": run.get("finished_at", ""),
                "profile_json": run.get("profile_json", ""),
                "report_html": run.get("report_html", ""),
                "artifacts": {
                    "profile_json": _relative_path(run.get("profile_json", ""), output_dir),
                    "report_html": _relative_path(run.get("report_html", ""), output_dir),
                    "brief_html": f"briefs/{run.get('slug', '')}.html",
                    "raw_dir": f"{run.get('slug', '')}/raw",
                },
                "error": run.get("error", ""),
                "steps": run.get("steps", []),
                "summary": profile_summary,
            }
        )
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": utc_now(),
        "job_id": job.get("job_id", ""),
        "status": job.get("status", ""),
        "bench_names": job.get("bench_names", []),
        "options": job.get("options", {}),
        "output_dir": job.get("output_dir", ""),
        "artifacts": {
            "job_json": "job.json",
            "index_html": "index.html",
            "sqlite_db": "../../bench_jobs.sqlite",
        },
        "output_layout": {
            "job_dir": "jobs/{job_id}/",
            "manifest": "job.json",
            "batch_report": "index.html",
            "brief_index": "briefs/index.html",
            "bench_profile": "{bench_slug}/profile.json",
            "bench_report": "{bench_slug}/report.html",
            "bench_brief": "briefs/{bench_slug}.html",
            "raw_cache": "{bench_slug}/raw/",
        },
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
        "error": job.get("error", ""),
        "summary": {
            "bench_count": len(runs),
            "completed_count": len([run for run in runs if run["status"] == "completed"]),
            "warning_count": total_warning_count,
            "error_count": total_error_count,
            "warnings_by_type": warnings_by_type,
            "errors_by_type": errors_by_type,
            "failed_count": len([run for run in runs if run["status"] == "failed"]),
            "completed_with_warnings_count": len(
                [run for run in runs if run["status"] == "completed_with_warnings"]
            ),
        },
        "bench_runs": runs,
    }


def write_job_manifest(job: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_job_manifest(job)
    path = output_dir / "job.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
