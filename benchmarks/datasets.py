"""Dataset adapters for offline recommendation benchmarks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .metrics import VALID_LABELS


@dataclass(frozen=True)
class EvalQuery:
    id: str
    title: str
    direction: str
    keywords: list[str] = field(default_factory=list)
    expected_domains: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    content: str = ""
    source: str = ""
    source_name: str = ""
    url: str = ""
    published_at: str = ""
    fetched_at: str = ""
    quality_score: float = 0.0
    tags: str = ""
    domain_tags: str = ""
    category: str = ""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [str(v).strip() for v in loaded if str(v).strip()]
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in text.split(",") if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def load_queries_jsonl(path: str | Path) -> list[EvalQuery]:
    queries: list[EvalQuery] = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        data = json.loads(raw)
        missing = [key for key in ("id", "title", "direction") if not data.get(key)]
        if missing:
            raise ValueError(f"{path}:{line_no} missing required field(s): {', '.join(missing)}")
        known = {"id", "title", "direction", "keywords", "expected_domains", "negative_keywords"}
        queries.append(EvalQuery(
            id=str(data["id"]),
            title=str(data["title"]),
            direction=str(data["direction"]),
            keywords=_as_list(data.get("keywords")),
            expected_domains=_as_list(data.get("expected_domains")),
            negative_keywords=_as_list(data.get("negative_keywords")),
            metadata={k: v for k, v in data.items() if k not in known},
        ))
    return queries


def load_queries_from_db(db_path: str | Path) -> list[EvalQuery]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(eval_queries)").fetchall()}
        if not columns:
            return []
        wanted = ["id", "title", "direction", "keywords"]
        for optional in ("expected_domains", "negative_keywords"):
            if optional in columns:
                wanted.append(optional)
        rows = conn.execute(f"SELECT {', '.join(wanted)} FROM eval_queries ORDER BY created_at").fetchall()
    finally:
        conn.close()
    queries = []
    for row in rows:
        queries.append(EvalQuery(
            id=row["id"],
            title=row["title"],
            direction=row["direction"],
            keywords=_as_list(row["keywords"]),
            expected_domains=_as_list(row["expected_domains"]) if "expected_domains" in row.keys() else [],
            negative_keywords=_as_list(row["negative_keywords"]) if "negative_keywords" in row.keys() else [],
        ))
    return queries


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def load_articles_from_sqlite(db_path: str | Path, days: int = 14, limit: int = 500) -> list[Article]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = _table_columns(conn, "articles")
        if not columns:
            return []
        wanted = [
            "id", "title", "content", "source", "source_name", "url", "published_at",
            "fetched_at", "quality_score", "tags", "domain_tags", "category",
        ]
        selected = [c for c in wanted if c in columns]
        rows = conn.execute(
            f"""
            SELECT {', '.join(selected)}
            FROM articles
            WHERE fetched_at >= datetime('now', ?)
              AND COALESCE(quality_score, 0) >= 0
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (f"-{days} days", limit),
        ).fetchall()
    finally:
        conn.close()
    articles = []
    for row in rows:
        data = {key: row[key] for key in row.keys()}
        articles.append(Article(
            id=int(data.get("id")),
            title=data.get("title") or "",
            content=data.get("content") or "",
            source=data.get("source") or "",
            source_name=data.get("source_name") or "",
            url=data.get("url") or "",
            published_at=data.get("published_at") or "",
            fetched_at=data.get("fetched_at") or "",
            quality_score=float(data.get("quality_score") or 0.0),
            tags=data.get("tags") or "",
            domain_tags=data.get("domain_tags") or "",
            category=data.get("category") or "",
        ))
    return articles


def load_judgments_from_sqlite(db_path: str | Path) -> dict[str, dict[int, str]]:
    if not Path(db_path).exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_columns(conn, "eval_judgments"):
            return {}
        rows = conn.execute("SELECT query_id, article_id, label FROM eval_judgments").fetchall()
    finally:
        conn.close()
    return _judgment_rows_to_map(rows)


def load_judgments_jsonl(path: str | Path) -> dict[str, dict[int, str]]:
    rows = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        data = json.loads(raw)
        try:
            rows.append({
                "query_id": data["query_id"],
                "article_id": data["article_id"],
                "label": data["label"],
            })
        except KeyError as exc:
            raise ValueError(f"{path}:{line_no} missing required field: {exc.args[0]}") from exc
    return _judgment_rows_to_map(rows)


def _judgment_rows_to_map(rows: list[Any]) -> dict[str, dict[int, str]]:
    judgments: dict[str, dict[int, str]] = {}
    for row in rows:
        qid = str(row["query_id"])
        aid = int(row["article_id"])
        label = str(row["label"]).strip().lower()
        if label not in VALID_LABELS:
            raise ValueError(
                f"Invalid label for query={qid}, article={aid}: {label}. "
                f"Expected one of {sorted(VALID_LABELS)}"
            )
        judgments.setdefault(qid, {})[aid] = label
    return judgments


class LitSearchAdapter:
    """Interface placeholder for future LitSearch integration.

    Expected responsibilities:
    - convert LitSearch topics into EvalQuery objects;
    - convert corpus records into Article objects;
    - convert graded/qrel labels into core/inspiring/skim/irrelevant.

    This adapter intentionally avoids downloads so the sample benchmark remains
    runnable offline. A concrete implementation can subclass this and implement
    the three methods below using local LitSearch files.
    """

    def load_queries(self, path: str | Path) -> list[EvalQuery]:
        raise NotImplementedError("LitSearch query loading is reserved for a local adapter.")

    def load_articles(self, path: str | Path) -> list[Article]:
        raise NotImplementedError("LitSearch corpus loading is reserved for a local adapter.")

    def load_judgments(self, path: str | Path) -> dict[str, dict[int, str]]:
        raise NotImplementedError("LitSearch qrel loading is reserved for a local adapter.")
