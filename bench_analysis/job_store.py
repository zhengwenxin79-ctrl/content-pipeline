from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import SourceRecord


TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed", "skipped"}


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.init_db()

    def init_db(self) -> None:
        with connect(self.db_path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    bench_names_json TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS bench_runs (
                    run_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    bench_name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    status TEXT NOT NULL,
                    profile_json TEXT DEFAULT '',
                    report_html TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS job_steps (
                    run_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT DEFAULT '',
                    PRIMARY KEY(run_id, step_name),
                    FOREIGN KEY(run_id) REFERENCES bench_runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS bench_seeds (
                    slug TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    aliases_json TEXT DEFAULT '[]',
                    sources_json TEXT DEFAULT '[]',
                    manual_sources_json TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    latest_job_id TEXT DEFAULT '',
                    latest_run_id TEXT DEFAULT '',
                    latest_profile_json TEXT DEFAULT '',
                    latest_report_html TEXT DEFAULT '',
                    latest_brief_html TEXT DEFAULT '',
                    latest_status TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_searched_at TEXT DEFAULT ''
                );
                """
            )

    def create_job(
        self,
        job_id: str,
        bench_names: list[str],
        options: dict[str, Any],
        output_dir: Path,
    ) -> None:
        now = utc_now()
        with connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO jobs (
                    job_id, status, bench_names_json, options_json, output_dir,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    "pending",
                    json.dumps(bench_names, ensure_ascii=False),
                    json.dumps(options, ensure_ascii=False),
                    str(output_dir),
                    now,
                ),
            )

    def update_job(self, job_id: str, status: str, error: str = "") -> None:
        now = utc_now()
        fields = ["status = ?"]
        values: list[Any] = [status]
        if status == "running":
            fields.append("started_at = COALESCE(started_at, ?)")
            values.append(now)
        if status in TERMINAL_STATUSES:
            fields.append("finished_at = ?")
            values.append(now)
        if error:
            fields.append("error = ?")
            values.append(error)
        values.append(job_id)
        with connect(self.db_path) as db:
            db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", values)

    def create_bench_run(self, run_id: str, job_id: str, bench_name: str, slug: str) -> None:
        now = utc_now()
        with connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO bench_runs (
                    run_id, job_id, bench_name, slug, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, job_id, bench_name, slug, "pending", now),
            )

    def update_bench_run(
        self,
        run_id: str,
        status: str,
        profile_json: str = "",
        report_html: str = "",
        error: str = "",
    ) -> None:
        now = utc_now()
        fields = ["status = ?"]
        values: list[Any] = [status]
        if status == "running":
            fields.append("started_at = COALESCE(started_at, ?)")
            values.append(now)
        if status in TERMINAL_STATUSES:
            fields.append("finished_at = ?")
            values.append(now)
        if profile_json:
            fields.append("profile_json = ?")
            values.append(profile_json)
        if report_html:
            fields.append("report_html = ?")
            values.append(report_html)
        if error:
            fields.append("error = ?")
            values.append(error)
        values.append(run_id)
        with connect(self.db_path) as db:
            db.execute(f"UPDATE bench_runs SET {', '.join(fields)} WHERE run_id = ?", values)

    def set_step(self, run_id: str, step_name: str, status: str, error: str = "") -> None:
        now = utc_now()
        started_at = now if status == "running" else None
        finished_at = now if status in TERMINAL_STATUSES else None
        with connect(self.db_path) as db:
            existing = db.execute(
                "SELECT started_at FROM job_steps WHERE run_id = ? AND step_name = ?",
                (run_id, step_name),
            ).fetchone()
            if existing:
                next_started_at = existing["started_at"] or started_at
                db.execute(
                    """
                    UPDATE job_steps
                    SET status = ?, started_at = ?, finished_at = COALESCE(?, finished_at), error = ?
                    WHERE run_id = ? AND step_name = ?
                    """,
                    (status, next_started_at, finished_at, error, run_id, step_name),
                )
            else:
                db.execute(
                    """
                    INSERT INTO job_steps (
                        run_id, step_name, status, started_at, finished_at, error
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, step_name, status, started_at, finished_at, error),
                )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as db:
            job = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job is None:
                return None
            runs = db.execute(
                "SELECT * FROM bench_runs WHERE job_id = ? ORDER BY created_at, bench_name",
                (job_id,),
            ).fetchall()
            run_items = []
            for run in runs:
                steps = db.execute(
                    "SELECT * FROM job_steps WHERE run_id = ? ORDER BY rowid",
                    (run["run_id"],),
                ).fetchall()
                run_items.append({**dict(run), "steps": [dict(step) for step in steps]})
            return {
                **dict(job),
                "bench_names": json.loads(job["bench_names_json"]),
                "options": json.loads(job["options_json"]),
                "bench_runs": run_items,
            }

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with connect(self.db_path) as db:
            rows = db.execute(
                """
                SELECT
                    jobs.*,
                    COUNT(bench_runs.run_id) AS bench_count
                FROM jobs
                LEFT JOIN bench_runs ON jobs.job_id = bench_runs.job_id
                GROUP BY jobs.job_id
                ORDER BY jobs.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                {
                    **dict(row),
                    "bench_names": json.loads(row["bench_names_json"]),
                    "options": json.loads(row["options_json"]),
                }
                for row in rows
            ]

    def upsert_bench_seed(
        self,
        slug: str,
        name: str,
        aliases: list[str] | None = None,
        sources: list[SourceRecord] | None = None,
        manual_sources: list[SourceRecord] | None = None,
        notes: str = "",
    ) -> None:
        now = utc_now()
        aliases_json = json.dumps(aliases or [], ensure_ascii=False)
        sources_json = json.dumps([asdict(source) for source in sources or []], ensure_ascii=False)
        manual_sources_json = json.dumps([asdict(source) for source in manual_sources or []], ensure_ascii=False)
        with connect(self.db_path) as db:
            existing = db.execute("SELECT * FROM bench_seeds WHERE slug = ?", (slug,)).fetchone()
            if existing:
                fields = ["name = ?", "aliases_json = ?", "updated_at = ?"]
                values: list[Any] = [name, aliases_json, now]
                if sources is not None:
                    fields.append("sources_json = ?")
                    values.append(sources_json)
                    fields.append("last_searched_at = ?")
                    values.append(now)
                if manual_sources is not None:
                    fields.append("manual_sources_json = ?")
                    values.append(manual_sources_json)
                if notes:
                    fields.append("notes = ?")
                    values.append(notes)
                values.append(slug)
                db.execute(f"UPDATE bench_seeds SET {', '.join(fields)} WHERE slug = ?", values)
                return
            db.execute(
                """
                INSERT INTO bench_seeds (
                    slug, name, aliases_json, sources_json, manual_sources_json,
                    notes, created_at, updated_at, last_searched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    name,
                    aliases_json,
                    sources_json,
                    manual_sources_json,
                    notes,
                    now,
                    now,
                    now if sources is not None else "",
                ),
            )

    def update_seed_artifacts(
        self,
        slug: str,
        job_id: str,
        run_id: str,
        status: str,
        profile_json: str = "",
        report_html: str = "",
        brief_html: str = "",
    ) -> None:
        now = utc_now()
        with connect(self.db_path) as db:
            db.execute(
                """
                UPDATE bench_seeds
                SET latest_job_id = ?, latest_run_id = ?, latest_status = ?,
                    latest_profile_json = COALESCE(NULLIF(?, ''), latest_profile_json),
                    latest_report_html = COALESCE(NULLIF(?, ''), latest_report_html),
                    latest_brief_html = COALESCE(NULLIF(?, ''), latest_brief_html),
                    updated_at = ?
                WHERE slug = ?
                """,
                (job_id, run_id, status, profile_json, report_html, brief_html, now, slug),
            )

    def get_bench_seed(self, slug: str) -> dict[str, Any] | None:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM bench_seeds WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                return None
            return self._seed_row_to_dict(row)

    def find_bench_seed(self, name_or_slug: str) -> dict[str, Any] | None:
        key = name_or_slug.strip().lower()
        with connect(self.db_path) as db:
            row = db.execute(
                "SELECT * FROM bench_seeds WHERE lower(slug) = ? OR lower(name) = ?",
                (key, key),
            ).fetchone()
            if row is not None:
                return self._seed_row_to_dict(row)
            rows = db.execute("SELECT * FROM bench_seeds ORDER BY updated_at DESC").fetchall()
        for row in rows:
            seed = self._seed_row_to_dict(row)
            aliases = [alias.lower() for alias in seed.get("aliases", [])]
            if key in aliases:
                return seed
        return None

    def list_bench_seeds(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        normalized = query.strip().lower()
        with connect(self.db_path) as db:
            rows = db.execute(
                "SELECT * FROM bench_seeds ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        seeds = [self._seed_row_to_dict(row) for row in rows]
        if not normalized:
            return seeds
        return [
            seed
            for seed in seeds
            if normalized in seed["name"].lower()
            or normalized in seed["slug"].lower()
            or normalized in " ".join(seed.get("aliases", [])).lower()
            or normalized in " ".join(source.get("url", "") for source in seed.get("sources", [])).lower()
            or normalized in " ".join(source.get("url", "") for source in seed.get("manual_sources", [])).lower()
        ]

    def _seed_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["aliases"] = json.loads(item.pop("aliases_json") or "[]")
        item["sources"] = json.loads(item.pop("sources_json") or "[]")
        item["manual_sources"] = json.loads(item.pop("manual_sources_json") or "[]")
        return item
