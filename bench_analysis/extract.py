from __future__ import annotations

import re
from pathlib import Path

from .schema import EvidenceRecord, RawDocument


FIELD_PATTERNS = {
    "dataset_size": [
        r"(?P<value>\b\d[\d,]*(?:\.\d+)?\s+(?:tasks|questions|examples|instances|samples|problems|workflows|cases)\b)",
        r"(?P<value>\b\d[\d,]*(?:\.\d+)?\s+(?:expert-curated|full-set|open)\s+(?:tasks|questions|examples)\b)",
    ],
    "evaluation_method": [
        r"(?P<value>[^.\n]{0,120}(?:rubric|grader|grading|accuracy|pass@1|judge|blind comparison|evaluation)[^.\n]{0,160})",
    ],
    "task_format": [
        r"(?P<value>[^.\n]{0,120}(?:tasks?|workflow|spreadsheet|question answering|deliverables?|agents?)[^.\n]{0,180})",
    ],
    "data_access": [
        r"(?P<value>[^.\n]{0,100}(?:public|open-source|open source|GitHub|Hugging Face|dataset|not publicly|private)[^.\n]{0,120})",
    ],
    "year": [
        r"(?P<value>\b20[2-3]\d\b)",
    ],
}


def read_document_text(document: RawDocument) -> str:
    if not document.text_path:
        return ""
    path = Path(document.text_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _sentencify(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" -:;|")
    return value[:320]


def extract_facts_from_document(document: RawDocument, max_per_field: int = 4) -> list[EvidenceRecord]:
    text = read_document_text(document)
    if not text:
        return []
    facts: list[EvidenceRecord] = []
    for field, patterns in FIELD_PATTERNS.items():
        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = _sentencify(match.group("value"))
                compact = value.lower()
                if not value or compact in seen:
                    continue
                seen.add(compact)
                confidence = 0.55
                if document.type in {"official", "project", "paper"}:
                    confidence += 0.15
                if field == "dataset_size" and re.search(r"\d", value):
                    confidence += 0.1
                facts.append(
                    EvidenceRecord(
                        field=field,
                        value=value,
                        source_url=document.url,
                        confidence=min(confidence, 0.9),
                        snippet=value,
                    )
                )
                if len([fact for fact in facts if fact.field == field]) >= max_per_field:
                    break
            if len([fact for fact in facts if fact.field == field]) >= max_per_field:
                break
    return facts


def extract_facts(documents: list[RawDocument]) -> list[EvidenceRecord]:
    facts: list[EvidenceRecord] = []
    for document in documents:
        facts.extend(extract_facts_from_document(document))
    return facts
