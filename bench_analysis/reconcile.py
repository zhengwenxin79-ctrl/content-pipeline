from __future__ import annotations

from dataclasses import replace

from .brief_localization import generate_localized_brief
from .classify import infer_capability_tags
from .schema import BenchProfile, ConflictRecord, EvidenceRecord, ModelResult, PaperAnalysis, RawDocument, SourceRecord


SOURCE_PRIORITY = {
    "official": 1.0,
    "project": 0.9,
    "paper": 0.85,
    "leaderboard": 0.8,
    "github": 0.75,
    "dataset": 0.7,
}


def _source_priority(source_type: str) -> float:
    return SOURCE_PRIORITY.get(source_type, 0.5)


def _dedupe_sources(sources: list[SourceRecord]) -> list[SourceRecord]:
    by_url: dict[str, SourceRecord] = {}
    for source in sources:
        existing = by_url.get(source.url)
        current_score = source.relevance_score + _source_priority(source.type)
        existing_score = -1 if existing is None else existing.relevance_score + _source_priority(existing.type)
        if existing is None or current_score > existing_score:
            by_url[source.url] = source
    return sorted(
        by_url.values(),
        key=lambda item: (_source_priority(item.type), item.relevance_score),
        reverse=True,
    )


def _dedupe_results(results: list[ModelResult]) -> list[ModelResult]:
    by_key: dict[tuple[str, str, str, str], ModelResult] = {}
    for result in results:
        key = (result.model.lower(), result.metric.lower(), result.score, result.source_url)
        existing = by_key.get(key)
        if existing is None or (result.verification_status == "verified", result.confidence) > (
            existing.verification_status == "verified",
            existing.confidence,
        ):
            by_key[key] = result
    return sorted(by_key.values(), key=lambda item: item.confidence, reverse=True)


def _is_unknown(value: str) -> bool:
    return not value or value.strip().lower() in {"unknown", "unknown.", "n/a", "unresolved"}


def _best_fact(facts: list[EvidenceRecord], field: str) -> EvidenceRecord | None:
    candidates = [fact for fact in facts if fact.field == field and fact.value]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.confidence, reverse=True)[0]


def _apply_fact(profile: BenchProfile, field: str, fact: EvidenceRecord | None) -> None:
    if fact is None or not hasattr(profile, field):
        return
    existing = getattr(profile, field)
    if not isinstance(existing, str):
        return
    if _is_unknown(existing):
        setattr(profile, field, fact.value)
        return
    if fact.value.lower() not in existing.lower() and existing.lower() not in fact.value.lower():
        profile.conflicts.append(
            ConflictRecord(
                field=field,
                existing_value=existing,
                candidate_value=fact.value,
                source_url=fact.source_url,
                note="Candidate extracted from fetched source differs from seeded profile.",
            )
        )


def _artifact_key_for_source(source: SourceRecord) -> str:
    if source.type in {"paper", "github", "dataset", "leaderboard"}:
        return source.type
    if source.type in {"official", "project"}:
        return "project"
    return source.type


def _merge_unique(existing: list[str], candidates: list[str], limit: int = 12) -> list[str]:
    values = []
    seen = set()
    for value in [*existing, *candidates]:
        value = value.strip() if value else ""
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def _fill_text(existing: str, candidate: str) -> str:
    return candidate if _is_unknown(existing) and candidate else existing


def _merge_paper_analysis(existing: PaperAnalysis, candidate: PaperAnalysis | None) -> PaperAnalysis:
    if candidate is None:
        return existing
    merged = replace(existing)
    merged.core_question = _fill_text(merged.core_question, candidate.core_question)
    merged.motivation = _fill_text(merged.motivation, candidate.motivation)
    merged.gap_claimed = _fill_text(merged.gap_claimed, candidate.gap_claimed)
    merged.model_results_summary = _fill_text(merged.model_results_summary, candidate.model_results_summary)
    merged.evaluated_capabilities = _merge_unique(
        merged.evaluated_capabilities,
        candidate.evaluated_capabilities,
    )
    merged.main_findings = _merge_unique(merged.main_findings, candidate.main_findings)
    merged.conclusions = _merge_unique(merged.conclusions, candidate.conclusions)
    merged.failure_modes = _merge_unique(merged.failure_modes, candidate.failure_modes)
    merged.reliability_notes = _merge_unique(merged.reliability_notes, candidate.reliability_notes)
    merged.evidence = [*merged.evidence, *candidate.evidence]

    design = replace(merged.benchmark_design)
    candidate_design = candidate.benchmark_design
    design.data_source = _fill_text(design.data_source, candidate_design.data_source)
    design.task_construction = _fill_text(design.task_construction, candidate_design.task_construction)
    design.gold_generation = _fill_text(design.gold_generation, candidate_design.gold_generation)
    design.world_or_case_design = _fill_text(design.world_or_case_design, candidate_design.world_or_case_design)
    design.expert_involvement = _fill_text(design.expert_involvement, candidate_design.expert_involvement)
    design.task_types = _merge_unique(design.task_types, candidate_design.task_types)
    design.tools_or_environment = _merge_unique(design.tools_or_environment, candidate_design.tools_or_environment)
    merged.benchmark_design = design

    scoring = replace(merged.rubric_and_scoring)
    candidate_scoring = candidate.rubric_and_scoring
    scoring.gold_definition = _fill_text(scoring.gold_definition, candidate_scoring.gold_definition)
    scoring.scoring_protocol = _fill_text(scoring.scoring_protocol, candidate_scoring.scoring_protocol)
    scoring.judge_type = _fill_text(scoring.judge_type, candidate_scoring.judge_type)
    scoring.judge_model = _fill_text(scoring.judge_model, candidate_scoring.judge_model)
    scoring.human_review = _fill_text(scoring.human_review, candidate_scoring.human_review)
    scoring.rubric_dimensions = _merge_unique(scoring.rubric_dimensions, candidate_scoring.rubric_dimensions)
    scoring.metrics = _merge_unique(scoring.metrics, candidate_scoring.metrics)
    merged.rubric_and_scoring = scoring
    return merged


def reconcile_profile(
    profile: BenchProfile,
    discovered_sources: list[SourceRecord] | None = None,
    raw_documents: list[RawDocument] | None = None,
    extracted_facts: list[EvidenceRecord] | None = None,
    model_results: list[ModelResult] | None = None,
    paper_analysis: PaperAnalysis | None = None,
) -> BenchProfile:
    profile = replace(profile)
    discovered_sources = discovered_sources or []
    raw_documents = raw_documents or []
    extracted_facts = extracted_facts or []
    model_results = model_results or []

    profile.sources = _dedupe_sources([*profile.sources, *discovered_sources])
    profile.raw_documents = raw_documents
    profile.extracted_facts = extracted_facts
    profile.model_results = _dedupe_results(model_results)
    profile.paper_analysis = _merge_paper_analysis(profile.paper_analysis, paper_analysis)

    for source in profile.sources:
        key = _artifact_key_for_source(source)
        profile.artifacts.setdefault(key, source.url)

    for field in ["dataset_size", "evaluation_method", "task_format", "data_access", "year"]:
        _apply_fact(profile, field, _best_fact(extracted_facts, field))

    existing_notes = set(profile.reliability_notes)
    if discovered_sources:
        existing_notes.add(f"Web discovery attached {len(discovered_sources)} candidate sources.")
    successful_fetches = len([doc for doc in raw_documents if not doc.error])
    if raw_documents:
        existing_notes.add(f"Fetched {successful_fetches}/{len(raw_documents)} sources into raw cache.")
    if model_results:
        verified_count = len([result for result in profile.model_results if result.verification_status == "verified"])
        existing_notes.add(
            f"Extracted {len(profile.model_results)} model score rows ({verified_count} verified table rows, "
            f"{len(profile.model_results) - verified_count} candidates)."
        )
    if paper_analysis and paper_analysis.evidence:
        existing_notes.add(f"Extracted PaperAnalysis v1 evidence snippets for {len(paper_analysis.evidence)} fields.")
    if profile.conflicts:
        existing_notes.add(f"Found {len(profile.conflicts)} candidate field conflicts during reconciliation.")
    profile.reliability_notes = sorted(existing_notes)

    tag_seed = profile.to_dict()
    profile.capability_tags = infer_capability_tags(tag_seed)
    if discovered_sources and successful_fetches:
        profile.confidence = min(0.99, profile.confidence + 0.03)
    elif discovered_sources:
        profile.confidence = min(0.98, profile.confidence + 0.01)
    profile.localized_brief = generate_localized_brief(profile, status="formal")
    return profile
