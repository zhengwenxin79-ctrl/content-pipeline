from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from .schema import BenchmarkDesign, EvidenceRecord, PaperAnalysis, RawDocument, RubricScoring


SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")
METRIC_PATTERN = re.compile(
    r"\b(pass@\d+|accuracy|f1|success rate|win rate|mean score|expert score|task accuracy|"
    r"modification accuracy|precision|recall|auc)\b",
    flags=re.IGNORECASE,
)

FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "core_question": (
        "benchmark",
        "evaluate",
        "assess",
        "measure",
        "can llm",
        "can agent",
        "ability",
        "capability",
    ),
    "motivation": (
        "motivation",
        "existing benchmark",
        "existing benchmarks",
        "current benchmark",
        "lack",
        "limited",
        "do not capture",
        "fail to capture",
        "real-world",
    ),
    "gap_claimed": (
        "gap",
        "missing",
        "lack",
        "not reflect",
        "do not reflect",
        "fails to",
        "sim-to-real",
        "real-world",
    ),
    "data_source": (
        "data source",
        "collected from",
        "curated from",
        "derived from",
        "dataset consists",
        "we collect",
        "we curated",
    ),
    "task_construction": (
        "task construction",
        "we construct",
        "we created",
        "tasks are",
        "benchmark consists",
        "contains",
        "comprises",
    ),
    "gold_generation": (
        "gold",
        "ground truth",
        "reference answer",
        "answer key",
        "expert annotation",
        "human annotation",
    ),
    "scoring_protocol": (
        "scoring",
        "rubric",
        "judge",
        "graded",
        "evaluate the answer",
        "metric",
        "metrics",
    ),
    "model_results_summary": (
        "results show",
        "we find",
        "outperform",
        "performance",
        "best model",
        "state-of-the-art",
    ),
    "main_findings": (
        "we find",
        "our findings",
        "results show",
        "indicate that",
        "suggest that",
        "demonstrate",
    ),
    "conclusions": (
        "conclusion",
        "we conclude",
        "in summary",
        "overall",
        "future",
        "remain",
    ),
    "failure_modes": (
        "failure",
        "failures",
        "error",
        "errors",
        "struggle",
        "struggles",
        "incorrect",
        "timeout",
        "hallucination",
    ),
}

CAPABILITY_HINTS: tuple[tuple[str, str], ...] = (
    ("agent", "Agent 长流程执行"),
    ("tool", "工具使用"),
    ("search", "搜索与检索"),
    ("retrieval", "检索与证据整合"),
    ("spreadsheet", "电子表格工作流"),
    ("excel", "电子表格工作流"),
    ("financial", "金融专业任务"),
    ("finance", "金融专业任务"),
    ("expert", "专家级任务"),
    ("real-world", "真实工作场景"),
    ("long-horizon", "长流程规划"),
    ("planning", "规划与执行"),
    ("multi-step", "多步推理"),
    ("web", "网页与多源信息"),
    ("pdf", "文件理解"),
)

TASK_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("question answering", "问答"),
    ("search", "搜索"),
    ("spreadsheet", "表格操作"),
    ("workflow", "工作流"),
    ("tool", "工具使用"),
    ("generation", "生成"),
    ("debug", "调试"),
    ("visualization", "可视化"),
    ("multi-step", "多步任务"),
)

TOOL_HINTS: tuple[tuple[str, str], ...] = (
    ("browser", "browser"),
    ("web", "web"),
    ("search", "search"),
    ("spreadsheet", "spreadsheet"),
    ("excel", "excel"),
    ("python", "python"),
    ("pdf", "pdf"),
    ("file", "files"),
    ("email", "email"),
    ("calendar", "calendar"),
)


def _read_text(document: RawDocument, max_chars: int = 160_000) -> str:
    if not document.text_path:
        return ""
    path = Path(document.text_path)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[:max_chars]


def _normalize_snippet(value: str, max_chars: int = 520) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def _sentences_for(documents: list[RawDocument]) -> list[tuple[RawDocument, str]]:
    rows: list[tuple[RawDocument, str]] = []
    for document in documents:
        text = _read_text(document)
        if not text:
            continue
        for sentence in SENTENCE_SPLIT.split(text):
            sentence = _normalize_snippet(sentence)
            if 80 <= len(sentence) <= 620:
                rows.append((document, sentence))
    return rows


def _score_sentence(sentence: str, keywords: tuple[str, ...], document: RawDocument) -> float:
    lowered = sentence.lower()
    score = 0.0
    for keyword in keywords:
        if keyword in lowered:
            score += 1.0
    if document.type == "paper":
        score += 0.8
    elif document.type in {"official", "project"}:
        score += 0.45
    if "benchmark" in lowered:
        score += 0.35
    return score


def _best_sentence(
    rows: list[tuple[RawDocument, str]],
    field: str,
    min_score: float = 1.35,
) -> EvidenceRecord | None:
    keywords = FIELD_KEYWORDS[field]
    scored = [
        (_score_sentence(sentence, keywords, document), document, sentence)
        for document, sentence in rows
    ]
    scored = [item for item in scored if item[0] >= min_score]
    if not scored:
        return None
    score, document, sentence = sorted(scored, key=lambda item: item[0], reverse=True)[0]
    return EvidenceRecord(
        field=f"paper_analysis.{field}",
        value=sentence,
        source_url=document.url,
        confidence=min(0.82, 0.36 + score * 0.08),
        snippet=sentence,
    )


def _top_sentences(
    rows: list[tuple[RawDocument, str]],
    field: str,
    limit: int = 3,
    min_score: float = 1.35,
) -> list[EvidenceRecord]:
    keywords = FIELD_KEYWORDS[field]
    scored = [
        (_score_sentence(sentence, keywords, document), document, sentence)
        for document, sentence in rows
    ]
    records: list[EvidenceRecord] = []
    seen = set()
    for score, document, sentence in sorted(scored, key=lambda item: item[0], reverse=True):
        if score < min_score:
            break
        key = sentence.lower()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            EvidenceRecord(
                field=f"paper_analysis.{field}",
                value=sentence,
                source_url=document.url,
                confidence=min(0.8, 0.34 + score * 0.08),
                snippet=sentence,
            )
        )
        if len(records) >= limit:
            break
    return records


def _tags_from_text(rows: list[tuple[RawDocument, str]], hints: tuple[tuple[str, str], ...], limit: int = 8) -> list[str]:
    text = " ".join(sentence for _, sentence in rows[:1200]).lower()
    tags = []
    for keyword, label in hints:
        if keyword in text and label not in tags:
            tags.append(label)
        if len(tags) >= limit:
            break
    return tags


def _metrics_from_text(rows: list[tuple[RawDocument, str]], limit: int = 8) -> list[str]:
    values: list[str] = []
    for _, sentence in rows:
        for match in METRIC_PATTERN.finditer(sentence):
            value = match.group(1)
            canonical = value.lower()
            if canonical not in [item.lower() for item in values]:
                values.append(value)
            if len(values) >= limit:
                return values
    return values


def _apply(record: EvidenceRecord | None, target: PaperAnalysis, attr: str) -> None:
    if record is None:
        return
    setattr(target, attr, record.value)
    target.evidence.append(record)


def extract_paper_analysis(documents: list[RawDocument]) -> PaperAnalysis:
    rows = _sentences_for(documents)
    if not rows:
        return PaperAnalysis()

    analysis = PaperAnalysis()
    _apply(_best_sentence(rows, "core_question"), analysis, "core_question")
    _apply(_best_sentence(rows, "motivation"), analysis, "motivation")
    _apply(_best_sentence(rows, "gap_claimed"), analysis, "gap_claimed")
    _apply(_best_sentence(rows, "model_results_summary"), analysis, "model_results_summary")

    design = BenchmarkDesign()
    data_source = _best_sentence(rows, "data_source")
    task_construction = _best_sentence(rows, "task_construction")
    gold_generation = _best_sentence(rows, "gold_generation")
    if data_source:
        design.data_source = data_source.value
        analysis.evidence.append(data_source)
    if task_construction:
        design.task_construction = task_construction.value
        analysis.evidence.append(task_construction)
    if gold_generation:
        design.gold_generation = gold_generation.value
        analysis.evidence.append(gold_generation)
    design.task_types = _tags_from_text(rows, TASK_TYPE_HINTS, limit=6)
    design.tools_or_environment = _tags_from_text(rows, TOOL_HINTS, limit=6)
    analysis.benchmark_design = design

    scoring = RubricScoring()
    scoring_protocol = _best_sentence(rows, "scoring_protocol")
    if scoring_protocol:
        scoring.scoring_protocol = scoring_protocol.value
        analysis.evidence.append(scoring_protocol)
    scoring.metrics = _metrics_from_text(rows)
    if "rubric" in " ".join(sentence for _, sentence in rows[:1200]).lower():
        scoring.rubric_dimensions = ["rubric-based evaluation"]
    if "human" in " ".join(sentence for _, sentence in rows[:1200]).lower() or "expert" in " ".join(sentence for _, sentence in rows[:1200]).lower():
        scoring.human_review = "Mentions human or expert involvement in the paper/source text; verify exact role."
    if "judge" in " ".join(sentence for _, sentence in rows[:1200]).lower():
        scoring.judge_type = "LLM/human judge mentioned; verify exact protocol."
    analysis.rubric_and_scoring = scoring

    analysis.evaluated_capabilities = _tags_from_text(rows, CAPABILITY_HINTS)
    findings = _top_sentences(rows, "main_findings", limit=3)
    conclusions = _top_sentences(rows, "conclusions", limit=3)
    failures = _top_sentences(rows, "failure_modes", limit=4)
    analysis.main_findings = [record.value for record in findings]
    analysis.conclusions = [record.value for record in conclusions]
    analysis.failure_modes = [record.value for record in failures]
    analysis.evidence.extend(findings)
    analysis.evidence.extend(conclusions)
    analysis.evidence.extend(failures)
    if analysis.evidence:
        analysis.reliability_notes.append(
            "PaperAnalysis v1 was populated by heuristic sentence extraction; source snippets require human review."
        )
    return replace(analysis)
