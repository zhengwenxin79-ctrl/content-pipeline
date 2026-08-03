from __future__ import annotations

import re
from pathlib import Path

from .schema import ModelResult, RawDocument


MODEL_HINT = re.compile(
    r"\b("
    r"GPT[-\s]?\d(?:\.\d)?|GPT[-\s]?4(?:o)?|GPT[-\s]?5|"
    r"o[134](?:[-\s]?(?:mini|pro))?|"
    r"Claude\s+(?:Sonnet|Opus|Haiku)\s+\d(?:\.\d)?|"
    r"Claude(?:[-\s]?\d(?:\.\d)?|[-\s]?(?:Sonnet|Opus|Haiku))?|"
    r"Gemini(?:[-\s]?\d(?:\.\d)?|[-\s]?(?:Pro|Flash|Ultra))?|"
    r"Grok[-\s]?\d|DeepSeek[-\s]?[A-Za-z0-9.]+|Qwen[-\s]?[A-Za-z0-9.]+|"
    r"Llama[-\s]?\d(?:\.\d)?|Mistral[-\s]?[A-Za-z0-9.]+|"
    r"Nex[-\s]?[A-Za-z0-9.]+|NexForge"
    r")\b",
    flags=re.IGNORECASE,
)
SCORE_HINT = re.compile(r"(?P<score>\b\d{1,3}(?:\.\d+)?\s?%|\b0?\.\d{2,4}\b|\b\d{1,3}\.\d{1,3}\b)")
METRIC_HINT = re.compile(
    r"\b(accuracy|score|pass@1|f1|success rate|win rate|avg|average|elo|expert preference|"
    r"human preference|preference rate|expert score|cost|time)\b",
    flags=re.IGNORECASE,
)
ELO_ON_GDPVAL = re.compile(
    r"(?:from\s+\d{3,4}\s+to\s+)?(?P<score>\d{3,4})\s+Elo\s+on\s+GDPval",
    flags=re.IGNORECASE,
)
PREFERENCE_SCORE = re.compile(
    r"(?P<score>\d{1,3}(?:\.\d+)?\s?%)\s+(?:expert|human|reviewer|preference|win-rate|win rate)",
    flags=re.IGNORECASE,
)
TABLE_SEPARATOR = re.compile(r"\s{2,}|\t+")


def _read_text(document: RawDocument) -> str:
    if not document.text_path:
        return ""
    path = Path(document.text_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _document_matches_bench(document: RawDocument, bench_name: str) -> bool:
    if not bench_name:
        return True
    bench_key = _compact(bench_name)
    aliases = {bench_key}
    if bench_key.endswith("bench"):
        aliases.add(bench_key.removesuffix("bench"))
    haystack = _compact(f"{document.title} {document.source_url} {document.url}")
    return any(alias and alias in haystack for alias in aliases)


def _verification_status(document: RawDocument, method: str) -> str:
    if method in {"markdown_table", "text_table"} and document.type in {"leaderboard", "paper"}:
        return "verified"
    return "candidate"


def _base_confidence(document: RawDocument, method: str) -> float:
    confidence = 0.42
    if document.type in {"leaderboard", "paper"}:
        confidence += 0.22
    if method in {"markdown_table", "text_table"}:
        confidence += 0.14
    return min(confidence, 0.88)


def _parse_markdown_cells(line: str) -> list[str]:
    if "|" not in line:
        return []
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 2:
        return []
    if all(set(cell.replace(" ", "")) <= {"-", ":"} for cell in cells):
        return []
    return cells


def _parse_spaced_cells(line: str) -> list[str]:
    cells = [cell.strip() for cell in TABLE_SEPARATOR.split(line.strip()) if cell.strip()]
    return cells if len(cells) >= 3 else []


def _score_outside_model_cell(cells: list[str], model_index: int) -> str:
    for index, cell in enumerate(cells):
        if index == model_index:
            continue
        match = SCORE_HINT.search(cell)
        if match:
            return match.group("score")
    for cell in cells:
        match = SCORE_HINT.search(cell)
        if match:
            return match.group("score")
    return ""


def _first_score_after_model(line: str, model_span: tuple[int, int]) -> re.Match[str] | None:
    model_spans = [match.span() for match in MODEL_HINT.finditer(line)]
    for match in SCORE_HINT.finditer(line):
        if match.start() <= model_span[1]:
            continue
        prefix = line[max(0, match.start() - 8) : match.start()].lower()
        if re.search(r"(fig|figure)\.?\s*$", prefix):
            continue
        overlaps_model = any(match.start() < span[1] and match.end() > span[0] for span in model_spans)
        if not overlaps_model:
            return match
    return None


def _nearest_model_before(line: str, position: int) -> re.Match[str] | None:
    candidates = [match for match in MODEL_HINT.finditer(line) if match.start() <= position]
    if candidates:
        return candidates[-1]
    candidates = list(MODEL_HINT.finditer(line))
    return candidates[0] if candidates else None


def _result_from_special_match(
    document: RawDocument,
    line: str,
    match: re.Match[str],
    metric: str,
    method: str,
) -> ModelResult | None:
    model_match = _nearest_model_before(line, match.start())
    if not model_match:
        return None
    model = model_match.group(1)
    score = match.group("score")
    confidence = 0.72
    if document.type in {"leaderboard", "paper"}:
        confidence += 0.12
    return ModelResult(
        model=model,
        metric=metric,
        score=score,
        source_url=document.url,
        context=line[:360],
        confidence=min(confidence, 0.9),
        date=document.fetched_at,
        source_type=document.type,
        extraction_method=method,
        verification_status="candidate",
    )


def _result_from_cells(
    cells: list[str],
    document: RawDocument,
    line: str,
    method: str,
) -> ModelResult | None:
    model_cell = ""
    score_cell = ""
    metric_cell = ""
    model_index = -1
    for index, cell in enumerate(cells):
        if not model_cell and MODEL_HINT.search(cell):
            model_cell = MODEL_HINT.search(cell).group(1)
            model_index = index
        if not metric_cell and METRIC_HINT.search(cell):
            metric_cell = METRIC_HINT.search(cell).group(1)
    if model_index >= 0:
        score_cell = _score_outside_model_cell(cells, model_index)
    if not model_cell or not score_cell:
        return None
    metric = metric_cell or "score"
    confidence = _base_confidence(document, method)
    if "%" in score_cell or metric.lower() in {"accuracy", "pass@1", "success rate", "win rate"}:
        confidence += 0.06
    return ModelResult(
        model=model_cell,
        metric=metric,
        score=score_cell,
        source_url=document.url,
        context=line[:240],
        confidence=min(confidence, 0.92),
        date=document.fetched_at,
        source_type=document.type,
        extraction_method=method,
        verification_status=_verification_status(document, method),
    )


def extract_table_results_from_document(document: RawDocument, max_results: int = 40) -> list[ModelResult]:
    text = _read_text(document)
    if not text:
        return []
    results: list[ModelResult] = []
    seen = set()
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        if len(line) > 360:
            continue
        cells = _parse_markdown_cells(line)
        method = "markdown_table"
        if not cells:
            cells = _parse_spaced_cells(line)
            method = "text_table"
        if not cells:
            continue
        result = _result_from_cells(cells, document, line, method)
        if result is None:
            continue
        key = (result.model.lower(), result.metric.lower(), result.score, result.source_url)
        if key in seen:
            continue
        seen.add(key)
        results.append(result)
        if len(results) >= max_results:
            break
    return results


def extract_specialized_results_from_document(
    document: RawDocument,
    bench_name: str = "",
    max_results: int = 30,
) -> list[ModelResult]:
    text = _read_text(document)
    if not text:
        return []
    results: list[ModelResult] = []
    seen = set()
    chunks = []
    for paragraph in re.split(r"\n{2,}|(?<=[.!?])\s+", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if 30 <= len(paragraph) <= 900:
            chunks.append(paragraph)
    for line in chunks:
        if bench_name and _compact(bench_name) not in _compact(line):
            continue
        for metric, pattern in [("Elo on GDPval", ELO_ON_GDPVAL), ("expert/human preference", PREFERENCE_SCORE)]:
            for match in pattern.finditer(line):
                result = _result_from_special_match(
                    document=document,
                    line=line,
                    match=match,
                    metric=metric,
                    method="metric_pattern",
                )
                if result is None:
                    continue
                key = (result.model.lower(), result.metric.lower(), result.score, result.source_url)
                if key in seen:
                    continue
                seen.add(key)
                results.append(result)
                if len(results) >= max_results:
                    return results
    return results


def extract_results_from_document(document: RawDocument, max_results: int = 20) -> list[ModelResult]:
    text = _read_text(document)
    if not text:
        return []
    results: list[ModelResult] = []
    seen = set()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if len(line) > 260:
            continue
        if "gdpval" in line.lower() and "elo" in line.lower():
            continue
        model_match = MODEL_HINT.search(line)
        score_match = _first_score_after_model(line, model_match.span()) if model_match else None
        if not model_match or not score_match:
            continue
        metric_match = METRIC_HINT.search(line)
        model = model_match.group(1)
        score = score_match.group("score")
        metric = metric_match.group(1) if metric_match else "score"
        key = (model.lower(), metric.lower(), score, document.url)
        if key in seen:
            continue
        seen.add(key)
        confidence = 0.45
        if document.type in {"leaderboard", "paper"}:
            confidence += 0.25
        if "%" in score or metric.lower() in {"accuracy", "pass@1", "success rate", "win rate"}:
            confidence += 0.1
        results.append(
            ModelResult(
                model=model,
                metric=metric,
                score=score,
                source_url=document.url,
                context=line[:240],
                confidence=min(confidence, 0.85),
                date=document.fetched_at,
                source_type=document.type,
                extraction_method="regex_line",
                verification_status="candidate",
            )
        )
        if len(results) >= max_results:
            break
    return results


def extract_model_results(documents: list[RawDocument], bench_name: str = "") -> list[ModelResult]:
    results: list[ModelResult] = []
    for document in documents:
        matches_bench = _document_matches_bench(document, bench_name)
        if matches_bench:
            results.extend(extract_table_results_from_document(document))
        results.extend(extract_specialized_results_from_document(document, bench_name=bench_name if not matches_bench else ""))
        if matches_bench:
            results.extend(extract_results_from_document(document))
    by_key: dict[tuple[str, str, str, str], ModelResult] = {}
    for result in results:
        key = (result.model.lower(), result.metric.lower(), result.score, result.source_url)
        existing = by_key.get(key)
        if existing is None or (result.verification_status == "verified", result.confidence) > (
            existing.verification_status == "verified",
            existing.confidence,
        ):
            by_key[key] = result
    return list(by_key.values())
