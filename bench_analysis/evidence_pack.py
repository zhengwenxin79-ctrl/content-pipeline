from __future__ import annotations

from pathlib import Path

from .schema import BenchProfile, RawDocument


MAX_DOCUMENT_CHARS = 9000
MAX_PACK_CHARS = 32000


def _read_document_text(document: RawDocument, limit: int = MAX_DOCUMENT_CHARS) -> str:
    for path_value in [document.text_path, document.path]:
        if not path_value:
            continue
        path = Path(path_value)
        if not path.exists() or not path.is_file():
            continue
        try:
            return path.read_text(encoding="utf-8", errors="ignore")[:limit]
        except UnicodeDecodeError:
            continue
    return document.text_preview[:limit]


def build_evidence_pack(profile: BenchProfile, documents: list[RawDocument]) -> str:
    lines = [
        f"# Bench: {profile.name}",
        "",
        "## Seeded Profile",
        f"- evaluates: {profile.evaluates}",
        f"- task_format: {profile.task_format}",
        f"- evaluation_method: {profile.evaluation_method}",
        f"- dataset_size: {profile.dataset_size}",
        f"- data_access: {profile.data_access}",
        "",
        "## Sources",
    ]
    for index, source in enumerate(profile.sources, start=1):
        lines.append(f"[S{index}] type={source.type} title={source.title} url={source.url} note={source.note}")

    if profile.paper_analysis.evidence:
        lines.extend(["", "## Extracted Evidence Snippets"])
        for index, record in enumerate(profile.paper_analysis.evidence[:30], start=1):
            snippet = record.snippet or record.value
            lines.append(f"[E{index}] field={record.field} source={record.source_url} snippet={snippet}")

    if profile.model_results:
        lines.extend(["", "## Extracted Model Result Candidates"])
        for index, result in enumerate(profile.model_results[:40], start=1):
            lines.append(
                f"[R{index}] model={result.model} metric={result.metric} score={result.score} "
                f"status={result.verification_status} source={result.source_url} context={result.context}"
            )

    lines.extend(["", "## Raw Document Text"])
    for index, document in enumerate(documents[:6], start=1):
        if document.error:
            lines.append(f"[D{index}] type={document.type} url={document.url} fetch_error={document.error}")
            continue
        text = _read_document_text(document)
        lines.append(f"[D{index}] type={document.type} url={document.url} title={document.title}\n{text}")

    pack = "\n".join(lines)
    return pack[:MAX_PACK_CHARS]

