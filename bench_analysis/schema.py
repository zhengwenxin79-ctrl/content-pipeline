from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceRecord:
    title: str
    url: str
    type: str
    note: str = ""
    relevance_score: float = 0.0
    discovered_by: str = ""
    retrieved_date: str = ""


@dataclass
class RawDocument:
    url: str
    type: str
    path: str
    source_url: str = ""
    title: str = ""
    status_code: int = 0
    content_type: str = ""
    text_path: str = ""
    text_preview: str = ""
    error: str = ""
    cache_status: str = ""
    fetched_at: str = ""


@dataclass
class EvidenceRecord:
    field: str
    value: str
    source_url: str
    confidence: float = 0.0
    snippet: str = ""


@dataclass
class ModelResult:
    model: str
    metric: str
    score: str
    source_url: str
    context: str = ""
    confidence: float = 0.0
    date: str = ""
    source_type: str = ""
    extraction_method: str = "regex_line"
    verification_status: str = "candidate"


@dataclass
class ConflictRecord:
    field: str
    existing_value: str
    candidate_value: str
    source_url: str
    note: str = ""


@dataclass
class BenchmarkDesign:
    data_source: str = ""
    task_construction: str = ""
    task_types: list[str] = field(default_factory=list)
    gold_generation: str = ""
    world_or_case_design: str = ""
    tools_or_environment: list[str] = field(default_factory=list)
    expert_involvement: str = ""


@dataclass
class RubricScoring:
    gold_definition: str = ""
    rubric_dimensions: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    scoring_protocol: str = ""
    judge_type: str = ""
    judge_model: str = ""
    human_review: str = ""


@dataclass
class PaperAnalysis:
    core_question: str = ""
    motivation: str = ""
    gap_claimed: str = ""
    evaluated_capabilities: list[str] = field(default_factory=list)
    benchmark_design: BenchmarkDesign = field(default_factory=BenchmarkDesign)
    rubric_and_scoring: RubricScoring = field(default_factory=RubricScoring)
    model_results_summary: str = ""
    main_findings: list[str] = field(default_factory=list)
    conclusions: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    reliability_notes: list[str] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)


@dataclass
class LocalizedBrief:
    language: str = "zh-CN"
    status: str = "prototype"
    one_liner: str = ""
    core_question: str = ""
    motivation: str = ""
    gap_claimed: str = ""
    capability_summary: str = ""
    benchmark_design_summary: str = ""
    scoring_summary: str = ""
    model_results_summary: str = ""
    main_findings: list[str] = field(default_factory=list)
    conclusions: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    reproducibility_notes: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class LLMAnswer:
    answer: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    note: str = ""


@dataclass
class LLMAnalysis:
    status: str = "not_run"
    provider: str = ""
    model: str = ""
    generated_at: str = ""
    mode: str = "evidence_grounded"
    one_sentence: str = ""
    core_question: LLMAnswer = field(default_factory=LLMAnswer)
    motivation: LLMAnswer = field(default_factory=LLMAnswer)
    evaluated_capability: LLMAnswer = field(default_factory=LLMAnswer)
    benchmark_design: LLMAnswer = field(default_factory=LLMAnswer)
    scoring: LLMAnswer = field(default_factory=LLMAnswer)
    model_results: LLMAnswer = field(default_factory=LLMAnswer)
    failure_modes: LLMAnswer = field(default_factory=LLMAnswer)
    reliability: LLMAnswer = field(default_factory=LLMAnswer)
    unsupported_claims: list[str] = field(default_factory=list)
    error: str = ""


def paper_analysis_from_dict(value: PaperAnalysis | dict | None) -> PaperAnalysis:
    if isinstance(value, PaperAnalysis):
        return value
    if not isinstance(value, dict):
        return PaperAnalysis()
    data = dict(value)
    design = data.get("benchmark_design")
    if isinstance(design, dict):
        data["benchmark_design"] = BenchmarkDesign(**design)
    scoring = data.get("rubric_and_scoring")
    if isinstance(scoring, dict):
        data["rubric_and_scoring"] = RubricScoring(**scoring)
    evidence = data.get("evidence")
    if isinstance(evidence, list):
        data["evidence"] = [item if isinstance(item, EvidenceRecord) else EvidenceRecord(**item) for item in evidence]
    return PaperAnalysis(**data)


def localized_brief_from_dict(value: LocalizedBrief | dict | None) -> LocalizedBrief:
    if isinstance(value, LocalizedBrief):
        return value
    if not isinstance(value, dict):
        return LocalizedBrief()
    return LocalizedBrief(**value)


def llm_answer_from_dict(value: LLMAnswer | dict | None) -> LLMAnswer:
    if isinstance(value, LLMAnswer):
        return value
    if not isinstance(value, dict):
        return LLMAnswer()
    return LLMAnswer(**value)


def llm_analysis_from_dict(value: LLMAnalysis | dict | None) -> LLMAnalysis:
    if isinstance(value, LLMAnalysis):
        return value
    if not isinstance(value, dict):
        return LLMAnalysis()
    data = dict(value)
    for key in [
        "core_question",
        "motivation",
        "evaluated_capability",
        "benchmark_design",
        "scoring",
        "model_results",
        "failure_modes",
        "reliability",
    ]:
        data[key] = llm_answer_from_dict(data.get(key))
    return LLMAnalysis(**data)


@dataclass
class BenchProfile:
    name: str
    slug: str
    phase: str
    status: str
    aliases: list[str] = field(default_factory=list)
    organization: str = ""
    year: str = ""
    domain: list[str] = field(default_factory=list)
    evaluates: str = ""
    task_format: str = ""
    task_categories: list[str] = field(default_factory=list)
    capability_tags: list[str] = field(default_factory=list)
    evaluation_method: str = ""
    dataset_size: str = ""
    data_access: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    sources: list[SourceRecord] = field(default_factory=list)
    raw_documents: list[RawDocument] = field(default_factory=list)
    extracted_facts: list[EvidenceRecord] = field(default_factory=list)
    model_results: list[ModelResult] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    paper_analysis: PaperAnalysis = field(default_factory=PaperAnalysis)
    localized_brief: LocalizedBrief = field(default_factory=LocalizedBrief)
    llm_analysis: LLMAnalysis = field(default_factory=LLMAnalysis)
    reliability_notes: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def __post_init__(self) -> None:
        self.paper_analysis = paper_analysis_from_dict(self.paper_analysis)
        self.localized_brief = localized_brief_from_dict(self.localized_brief)
        self.llm_analysis = llm_analysis_from_dict(self.llm_analysis)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
