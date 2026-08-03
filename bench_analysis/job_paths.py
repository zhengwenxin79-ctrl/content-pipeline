from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobPaths:
    output_root: Path
    job_id: str

    @property
    def db_path(self) -> Path:
        return self.output_root / "bench_jobs.sqlite"

    @property
    def job_dir(self) -> Path:
        return self.output_root / "jobs" / self.job_id

    @property
    def manifest_path(self) -> Path:
        return self.job_dir / "job.json"

    @property
    def index_path(self) -> Path:
        return self.job_dir / "index.html"

    def bench_dir(self, slug: str) -> Path:
        return self.job_dir / slug

    def raw_dir(self, slug: str) -> Path:
        return self.bench_dir(slug) / "raw"

    def profile_path(self, slug: str) -> Path:
        return self.bench_dir(slug) / "profile.json"

    def report_path(self, slug: str) -> Path:
        return self.bench_dir(slug) / "report.html"


def default_db_path(output_root: Path) -> Path:
    return output_root / "bench_jobs.sqlite"
