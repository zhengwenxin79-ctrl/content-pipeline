from __future__ import annotations

import json
import re
from pathlib import Path

from .catalog import SEED_BENCHES
from .classify import infer_capability_tags
from .extract import extract_facts
from .fetch import fetch_sources
from .paper_analysis_extract import extract_paper_analysis
from .reconcile import reconcile_profile
from .results import extract_model_results
from .schema import BenchProfile
from .source_discovery import discover_sources


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def build_index() -> dict[str, dict]:
    index = {}
    for seed in SEED_BENCHES:
        names = [seed["name"], seed["slug"], *seed.get("aliases", [])]
        for name in names:
            index[normalize_name(name)] = seed
    return index


def list_benches() -> list[str]:
    return [seed["name"] for seed in SEED_BENCHES]


def analyze_bench(
    name: str,
    *,
    with_web: bool = False,
    output_dir: Path | None = None,
    discovery_limit: int = 10,
    fetch_limit: int = 6,
    include_general_search: bool = False,
) -> BenchProfile:
    seed = build_index().get(normalize_name(name))
    if seed is None:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown-bench"
        seed = {
            "name": name,
            "slug": slug,
            "phase": "adhoc",
            "status": "unresolved",
            "evaluates": "Unknown. Add a source link or seed profile to resolve this benchmark.",
            "task_format": "Unknown.",
            "evaluation_method": "Unknown.",
            "dataset_size": "Unknown.",
            "data_access": "Unknown.",
            "reliability_notes": ["No seed profile matched this name."],
            "confidence": 0.1,
        }

    data = dict(seed)
    data["capability_tags"] = infer_capability_tags(data)
    profile = BenchProfile(**data)
    if not with_web:
        return profile
    return enrich_profile(
        profile,
        output_dir=output_dir or Path("bench_analysis_outputs"),
        discovery_limit=discovery_limit,
        fetch_limit=fetch_limit,
        include_general_search=include_general_search,
    )


def enrich_profile(
    profile: BenchProfile,
    output_dir: Path,
    discovery_limit: int = 10,
    fetch_limit: int = 6,
    include_general_search: bool = False,
) -> BenchProfile:
    discovered = discover_sources(
        profile.name,
        aliases=profile.aliases,
        seed_sources=profile.sources,
        limit=discovery_limit,
        include_general_search=include_general_search,
    )
    raw_dir = output_dir / profile.slug / "raw"
    documents = fetch_sources(discovered, raw_dir=raw_dir, limit=fetch_limit)
    facts = extract_facts(documents)
    paper_analysis = extract_paper_analysis(documents)
    model_results = extract_model_results(documents, bench_name=profile.name)
    return reconcile_profile(
        profile,
        discovered_sources=discovered,
        raw_documents=documents,
        extracted_facts=facts,
        model_results=model_results,
        paper_analysis=paper_analysis,
    )


def analyze_all(
    *,
    with_web: bool = False,
    output_dir: Path | None = None,
    discovery_limit: int = 10,
    fetch_limit: int = 6,
    include_general_search: bool = False,
) -> list[BenchProfile]:
    return [
        analyze_bench(
            seed["name"],
            with_web=with_web,
            output_dir=output_dir,
            discovery_limit=discovery_limit,
            fetch_limit=fetch_limit,
            include_general_search=include_general_search,
        )
        for seed in SEED_BENCHES
    ]


def write_profile(profile: BenchProfile, output_dir: Path) -> Path:
    target_dir = output_dir / profile.slug
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "profile.json"
    path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
