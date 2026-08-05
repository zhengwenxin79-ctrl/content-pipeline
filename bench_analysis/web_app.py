from __future__ import annotations

import json
import mimetypes
import re
import threading
import traceback
import zipfile
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .job_manifest import build_job_manifest
from .job_paths import JobPaths, default_db_path
from .job_runner import JobOptions, new_job_id, run_batch_job, slug_for_input
from .job_store import JobStore
from .schema import SourceRecord
from .source_discovery import source_records_from_urls


STYLE = """
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  color: #1c2430;
  background: #f5f7fa;
  line-height: 1.5;
}
main { max-width: 1180px; margin: 0 auto; padding: 28px 18px 52px; }
header { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 18px; }
h1 { font-size: 30px; margin: 0 0 6px; }
h2 { font-size: 20px; margin: 28px 0 12px; }
h3 { font-size: 16px; margin: 18px 0 10px; }
a { color: #255f9f; text-decoration: none; }
a:hover { text-decoration: underline; }
.muted { color: #647184; }
.hero {
  background: #ffffff;
  border: 1px solid #d9dee6;
  border-radius: 8px;
  padding: 22px;
  margin-bottom: 16px;
}
.hero h1 { font-size: 34px; }
.eyebrow {
  color: #255f9f;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: .04em;
  margin-bottom: 8px;
}
.panel {
  background: white;
  border: 1px solid #d9dee6;
  border-radius: 8px;
  padding: 18px;
  margin: 14px 0;
}
.section-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: end;
  margin-top: 28px;
}
.section-head h2 { margin: 0; }
.section-head p { margin: 4px 0 0; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.two-col { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 14px; align-items: start; }
.kv { border-top: 1px solid #d9dee6; padding-top: 10px; min-width: 0; }
.kv b { display: block; font-size: 13px; color: #647184; margin-bottom: 4px; }
.next-actions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.action-card {
  display: block;
  border: 1px solid #d9dee6;
  border-radius: 8px;
  padding: 14px;
  background: #fbfcfd;
}
.action-card b { display: block; color: #1c2430; margin-bottom: 4px; }
.action-card span { display: block; color: #647184; font-size: 13px; }
.flow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.flow span {
  border: 1px solid #d9dee6;
  border-radius: 999px;
  background: #fbfcfd;
  padding: 5px 10px;
  font-size: 13px;
}
textarea {
  width: 100%;
  min-height: 160px;
  border: 1px solid #cfd6df;
  border-radius: 8px;
  padding: 12px;
  font: 14px ui-monospace, SFMono-Regular, Menlo, monospace;
  resize: vertical;
}
input[type="number"] {
  width: 96px;
  border: 1px solid #cfd6df;
  border-radius: 6px;
  padding: 8px;
}
label { display: inline-flex; gap: 8px; align-items: center; margin-right: 18px; }
button {
  appearance: none;
  border: 1px solid #255f9f;
  background: #255f9f;
  color: white;
  border-radius: 7px;
  padding: 9px 14px;
  font-weight: 700;
  cursor: pointer;
}
button.secondary {
  border-color: #cfd6df;
  background: white;
  color: #255f9f;
}
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.inline-form { display: inline; margin: 0; }
input[type="search"] {
  width: min(460px, 100%);
  border: 1px solid #cfd6df;
  border-radius: 7px;
  padding: 9px 11px;
}
pre {
  margin: 0;
  white-space: pre-wrap;
  font: 13px ui-monospace, SFMono-Regular, Menlo, monospace;
}
table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #d9dee6; }
th, td { padding: 10px 12px; border-bottom: 1px solid #d9dee6; text-align: left; vertical-align: top; font-size: 14px; }
th { background: #eef2f6; color: #334155; font-size: 13px; }
.status-completed, .ready { color: #287a4e; font-weight: 700; }
.status-running { color: #255f9f; font-weight: 700; }
.status-completed_with_warnings, .review_recommended, .needs_human_review { color: #9a6400; font-weight: 700; }
.status-failed, .failed_review { color: #9e3434; font-weight: 700; }
.pill {
  display: inline-block;
  border: 1px solid #d9dee6;
  border-radius: 999px;
  padding: 3px 8px;
  margin: 2px 4px 2px 0;
  font-size: 13px;
  background: #fbfcfd;
}
.one-liner {
  max-width: 760px;
  color: #334155;
}
@media (max-width: 780px) {
  header { display: block; }
  .grid, .two-col, .next-actions { grid-template-columns: 1fr; }
  table { display: block; overflow-x: auto; }
}
"""


DEFAULT_BENCHES = "GDPval\nFAB\nSpreadsheetBench v2\nFinSearchComp"

STATUS_LABELS = {
    "pending": "等待中",
    "running": "运行中",
    "completed": "已完成",
    "completed_with_warnings": "完成，有警告",
    "failed": "失败",
    "skipped": "已跳过",
}

REVIEW_LABELS = {
    "ready": "可直接查看",
    "review_recommended": "建议复核",
    "needs_human_review": "需要人工复核",
    "failed_review": "运行失败",
}

STEP_LABELS = {
    "resolve_identity": "解析 Bench 身份",
    "discover_sources": "发现来源",
    "fetch_raw": "抓取原始资料",
    "extract_fields": "抽取元信息字段",
    "extract_paper_analysis": "抽取论文分析",
    "extract_results": "抽取模型结果",
    "reconcile": "合并与冲突处理",
    "llm_analysis": "LLM 深度分析",
    "render_report": "生成报告",
}

SOURCE_STATUS_LABELS = {
    "failed": "失败",
    "fetched": "已抓取",
}


class BenchWebApp:
    def __init__(self, output_root: Path):
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(default_db_path(self.output_root))

    def start_job(self, bench_names: list[str], options: JobOptions) -> str:
        job_id = new_job_id()

        def target() -> None:
            try:
                run_batch_job(
                    bench_names=bench_names,
                    output_root=self.output_root,
                    options=options,
                    db_path=default_db_path(self.output_root),
                    job_id=job_id,
                )
            except Exception:
                # If failure occurs before the store is fully initialized, keep a visible trace.
                error_dir = JobPaths(self.output_root, job_id).job_dir
                error_dir.mkdir(parents=True, exist_ok=True)
                (error_dir / "startup_error.txt").write_text(traceback.format_exc(), encoding="utf-8")

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return job_id

    def rerun_job(self, job_id: str, bench_names: list[str] | None = None) -> str | None:
        job = self.store.get_job(job_id)
        if job is None:
            return None
        options = JobOptions(**job.get("options", {}))
        return self.start_job(bench_names or job.get("bench_names", []), options)


def page(title: str, body: str, refresh: bool = False) -> bytes:
    refresh_tag = '<meta http-equiv="refresh" content="2">' if refresh else ""
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_tag}
  <title>{escape(title)}</title>
  <style>{STYLE}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>"""
    return html.encode("utf-8")


def status_class(status: str) -> str:
    return f"status-{escape(status)}"


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def review_label(status: str) -> str:
    return REVIEW_LABELS.get(status, status)


def step_label(step_name: str) -> str:
    return STEP_LABELS.get(step_name, step_name)


def source_status_label(status: str) -> str:
    return SOURCE_STATUS_LABELS.get(status, status)


def parse_bench_names(raw: str) -> list[str]:
    names = []
    seen = set()
    for line in raw.replace(",", "\n").splitlines():
        name = line.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def render_home(app: BenchWebApp) -> bytes:
    jobs = app.store.list_jobs(limit=12)
    rows = "".join(
        f"<tr><td><a href=\"/jobs/{escape(job['job_id'])}\">{escape(job['job_id'])}</a></td>"
        f"<td><span class=\"{status_class(job['status'])}\">{escape(status_label(job['status']))}</span></td>"
        f"<td>{job['bench_count']}</td><td>{escape(job['created_at'])}</td>"
        f"<td>{escape(job['output_dir'])}</td></tr>"
        for job in jobs
    ) or '<tr><td colspan="5" class="muted">还没有运行记录。</td></tr>'
    body = f"""
<header>
  <div>
    <h1>Bench 分析工作台</h1>
    <div class="muted">先批量分析，再横向对比，最后进入单个 Bench 深读和证据复核。</div>
  </div>
  <nav class="actions">
    <a href="/seeds">种子库</a>
    <a href="/jobs">历史任务</a>
  </nav>
</header>

<section class="hero">
  <div class="eyebrow">内部 Demo · Batch-first</div>
  <h1>从 Bench 名称生成研究报告</h1>
  <div class="muted">输入一组 Bench，系统会发现资料、抓取论文/PDF、抽取论文分析字段、模型结果和证据，最后生成中文简报与 HTML 报告。</div>
  <div class="flow">
    <span>1. 输入 Bench</span><span>2. 运行 batch</span><span>3. 看批量对比</span><span>4. 深读单个 Bench</span><span>5. 复核证据</span>
  </div>
</section>

<div class="two-col">
  <form class="panel" method="post" action="/jobs">
    <h2>新建批量分析</h2>
    <p class="muted">每行一个 Bench 名称。建议先用 3-5 个 Bench 跑通，再扩大到更多测试集。</p>
    <textarea name="bench_names">{escape(DEFAULT_BENCHES)}</textarea>
    <p>
      <label><input type="checkbox" name="with_web" checked> 联网发现资料</label>
      <label><input type="checkbox" name="include_general_search"> 包含通用网页搜索</label>
    </p>
    <p>
      <label>来源发现上限 <input type="number" name="discovery_limit" value="6" min="1" max="20"></label>
      <label>抓取资料上限 <input type="number" name="fetch_limit" value="3" min="0" max="12"></label>
    </p>
    <button type="submit">开始批量分析</button>
  </form>

  <aside class="panel">
    <h2>阅读顺序</h2>
    <p><b>任务控制台</b><br><span class="muted">看运行是否完成、哪些 Bench 需要复核。</span></p>
    <p><b>批量对比报告</b><br><span class="muted">横向比较多个 Bench 的核心问题、能力定位和失败模式。</span></p>
    <p><b>单 Bench 论文报告</b><br><span class="muted">深入看设计、评分、模型结果、证据和调试附录。</span></p>
  </aside>
</div>

<div class="two-col">
  <form class="panel" method="post" action="/seeds/manual">
    <h2>手动补充来源</h2>
    <p class="muted">当网络搜索不到 Bench，或结果不准时，在这里贴 GitHub、arXiv、官网、PDF 或 leaderboard 链接。</p>
    <p><input type="search" name="bench_name" placeholder="Bench 名称，例如 IBFE" required></p>
    <textarea name="source_urls" placeholder="每行一个链接，例如&#10;https://arxiv.org/abs/xxxx.xxxxx&#10;https://github.com/org/repo"></textarea>
    <p><input type="search" name="notes" placeholder="备注，例如 师兄手动确认的论文和代码链接"></p>
    <p>
      <label><input type="checkbox" name="with_web" checked> 基于这些来源立即分析</label>
    </p>
    <button type="submit">保存并分析</button>
  </form>

  <aside class="panel">
    <h2>种子库机制</h2>
    <p><b>自动沉淀</b><br><span class="muted">每次 batch 完成后，来源、最新报告和状态都会写入种子库。</span></p>
    <p><b>手动兜底</b><br><span class="muted">搜索不到时，手动链接会作为高优先级来源进入抓取和抽取。</span></p>
    <p><b>复用来源</b><br><span class="muted">下次分析同一个 Bench，会优先复用种子库，不再从零开始搜索。</span></p>
    <p><a href="/seeds">打开种子库列表</a></p>
  </aside>
</div>

<div class="section-head">
  <div>
    <h2>最近任务</h2>
    <p class="muted">进入任务控制台后，优先看“推荐下一步”。</p>
  </div>
</div>
<table>
  <thead><tr><th>任务</th><th>状态</th><th>Bench 数</th><th>创建时间</th><th>输出目录</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
"""
    return page("Bench 分析工作台", body)


def render_jobs(app: BenchWebApp, query: str = "") -> bytes:
    jobs = app.store.list_jobs(limit=50)
    normalized_query = query.strip().lower()
    if normalized_query:
        jobs = [
            job
            for job in jobs
            if normalized_query in job["job_id"].lower()
            or normalized_query in job["status"].lower()
            or normalized_query in " ".join(job.get("bench_names", [])).lower()
        ]
    rows = "".join(
        f"<tr><td><a href=\"/jobs/{escape(job['job_id'])}\">{escape(job['job_id'])}</a></td>"
        f"<td><span class=\"{status_class(job['status'])}\">{escape(status_label(job['status']))}</span></td>"
        f"<td>{job['bench_count']}</td><td>{escape(job['created_at'])}</td><td>{escape(job['finished_at'] or '')}</td></tr>"
        for job in jobs
    ) or '<tr><td colspan="5" class="muted">没有匹配的任务。</td></tr>'
    body = f"""
<header><div><h1>历史任务</h1><div class="muted">查看、搜索和复盘已经运行过的 batch。</div></div><a href="/">新建批量分析</a></header>
<form class="panel actions" method="get" action="/jobs">
  <input type="search" name="query" value="{escape(query)}" placeholder="按任务 ID、状态或 Bench 名搜索">
  <button class="secondary" type="submit">搜索</button>
</form>
<table>
  <thead><tr><th>任务</th><th>状态</th><th>Bench 数</th><th>创建时间</th><th>完成时间</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
"""
    return page("历史任务", body)


def _artifact_relative_for_job(job_id: str, path_value: str) -> str:
    normalized = str(path_value).replace("\\", "/")
    for marker in (f"/jobs/{job_id}/", f"jobs/{job_id}/"):
        if marker in normalized:
            return normalized.split(marker, 1)[1].lstrip("/")
    for prefix in (f"{job_id}/", "/"):
        if normalized.startswith(prefix):
            return normalized[len(prefix):].lstrip("/")
    return normalized.lstrip("/")


def _seed_artifact_path(app: BenchWebApp, seed: dict, path_key: str) -> Path | None:
    job_id = seed.get("latest_job_id", "")
    path_value = seed.get(path_key, "")
    if not job_id or not path_value:
        return None
    base = JobPaths(app.output_root, job_id).job_dir.resolve()
    target = Path(path_value)
    if target.exists():
        return target
    relative = _artifact_relative_for_job(job_id, path_value)
    candidate = base / relative
    if candidate.exists():
        return candidate
    return None


def _seed_artifact_link(app: BenchWebApp, seed: dict, path_key: str, label: str) -> str:
    job_id = seed.get("latest_job_id", "")
    path_value = seed.get(path_key, "")
    if not job_id or not path_value:
        return ""
    base = JobPaths(app.output_root, job_id).job_dir.resolve()
    target = _seed_artifact_path(app, seed, path_key) or Path(path_value)
    try:
        relative = str(target.resolve().relative_to(base))
    except (ValueError, OSError):
        relative = _artifact_relative_for_job(job_id, path_value)
    return f'<a href="/artifact/{escape(job_id)}/{escape(relative)}">{escape(label)}</a>'


def _source_rows(sources: list[dict]) -> str:
    return "".join(
        f"<tr><td>{escape(source.get('type', ''))}</td>"
        f"<td><a href=\"{escape(source.get('url', ''))}\">{escape(source.get('title') or source.get('url', ''))}</a></td>"
        f"<td>{escape(source.get('discovered_by', ''))}</td><td>{escape(source.get('note', ''))}</td></tr>"
        for source in sources
    )


def _load_seed_profile(app: BenchWebApp, seed: dict) -> dict:
    path = _seed_artifact_path(app, seed, "latest_profile_json")
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _seed_one_liner(app: BenchWebApp, seed: dict) -> str:
    profile = _load_seed_profile(app, seed)
    localized_brief = profile.get("localized_brief", {})
    paper_analysis = profile.get("paper_analysis", {})
    llm_analysis = profile.get("llm_analysis", {})
    candidates = [
        llm_analysis.get("one_sentence", ""),
        localized_brief.get("one_liner", ""),
        profile.get("evaluates", ""),
        paper_analysis.get("core_question", ""),
        paper_analysis.get("gap_claimed", ""),
    ]
    for value in candidates:
        if value and not str(value).strip().lower().startswith("unknown"):
            return str(value).strip()
    if seed.get("manual_sources"):
        return "已保存手动来源，建议基于这些链接运行一次分析生成简介。"
    return "待分析：运行一次 Bench 分析后会生成一句话简介。"


def render_seeds(app: BenchWebApp, query: str = "") -> bytes:
    seeds = app.store.list_bench_seeds(query=query, limit=200)
    rows = ""
    for seed in seeds:
        report_link = _seed_artifact_link(app, seed, "latest_report_html", "报告")
        brief_link = _seed_artifact_link(app, seed, "latest_brief_html", "简报")
        one_liner = _seed_one_liner(app, seed)
        rows += (
            f"<tr><td><a href=\"/seeds/{escape(seed['slug'])}\">{escape(seed['name'])}</a></td>"
            f"<td class=\"one-liner\">{escape(one_liner)}</td>"
            f"<td>{report_link} {brief_link}</td></tr>"
        )
    rows = rows or '<tr><td colspan="3" class="muted">还没有种子记录。</td></tr>'
    body = f"""
<header><div><h1>Bench 种子库</h1><div class="muted">保存每次搜索和手动补充的来源，支持回看与复用。</div></div><a href="/">返回工作台</a></header>
<form class="panel actions" method="get" action="/seeds">
  <input type="search" name="query" value="{escape(query)}" placeholder="按 Bench 名称、别名或来源链接搜索">
  <button class="secondary" type="submit">查询</button>
</form>
<table>
  <thead><tr><th>Bench</th><th>一句话简介</th><th>最新报告</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
"""
    return page("Bench 种子库", body)


def render_seed_detail(app: BenchWebApp, slug: str) -> bytes:
    seed = app.store.get_bench_seed(slug)
    if not seed:
        return page("种子不存在", f"<h1>种子不存在</h1><p>{escape(slug)}</p>")
    automatic_rows = _source_rows(seed.get("sources", [])) or '<tr><td colspan="4" class="muted">暂无自动来源。</td></tr>'
    manual_rows = _source_rows(seed.get("manual_sources", [])) or '<tr><td colspan="4" class="muted">暂无手动来源。</td></tr>'
    report_link = _seed_artifact_link(app, seed, "latest_report_html", "打开最新报告")
    brief_link = _seed_artifact_link(app, seed, "latest_brief_html", "打开最新简报")
    one_liner = _seed_one_liner(app, seed)
    body = f"""
<header><div><h1>{escape(seed['name'])}</h1><div class="muted">种子记录 · {escape(seed['slug'])}</div></div><a href="/seeds">返回种子库</a></header>
<section class="hero">
  <div class="eyebrow">Seed Record</div>
  <h1>{escape(seed['name'])}</h1>
  <p class="one-liner">{escape(one_liner)}</p>
</section>
<div class="panel actions">
  <form class="inline-form" method="post" action="/seeds/manual">
    <input type="hidden" name="bench_name" value="{escape(seed['name'])}">
    <input type="hidden" name="source_urls" value="">
    <input type="hidden" name="notes" value="从种子库复跑">
    <input type="hidden" name="with_web" value="on">
    <button type="submit">用种子来源复跑</button>
  </form>
  {report_link} {brief_link}
</div>
<div class="panel">
  <h2>继续补充手动来源</h2>
  <form method="post" action="/seeds/manual">
    <input type="hidden" name="bench_name" value="{escape(seed['name'])}">
    <textarea name="source_urls" placeholder="每行一个新增链接"></textarea>
    <p><input type="search" name="notes" value="{escape(seed.get('notes', ''))}" placeholder="备注"></p>
    <p><label><input type="checkbox" name="with_web" checked> 保存后立即分析</label></p>
    <button type="submit">保存新增来源</button>
  </form>
</div>
<h2>手动来源</h2>
<table><thead><tr><th>类型</th><th>来源</th><th>方式</th><th>备注</th></tr></thead><tbody>{manual_rows}</tbody></table>
<h2>自动来源</h2>
<table><thead><tr><th>类型</th><th>来源</th><th>方式</th><th>备注</th></tr></thead><tbody>{automatic_rows}</tbody></table>
"""
    return page(seed["name"], body)


def _urls_from_text(value: str) -> list[str]:
    return re.findall(r"https?://[^\s,，]+|arxiv:[A-Za-z0-9._/-]+", value)


def _source_records_from_seed_items(items: list[dict]) -> list[SourceRecord]:
    records = []
    for item in items:
        if not item.get("url"):
            continue
        allowed = {field.name for field in SourceRecord.__dataclass_fields__.values()}
        records.append(SourceRecord(**{key: value for key, value in item.items() if key in allowed}))
    return records


def _merge_source_records(*groups: list[SourceRecord]) -> list[SourceRecord]:
    by_url: dict[str, SourceRecord] = {}
    for group in groups:
        for source in group:
            existing = by_url.get(source.url)
            if existing is None or source.relevance_score > existing.relevance_score:
                by_url[source.url] = source
    return list(by_url.values())


def load_manifest_for_job(app: BenchWebApp, job_id: str) -> tuple[dict | None, dict | None]:
    job = app.store.get_job(job_id)
    if job is None:
        return None, None
    manifest_path = Path(job["output_dir"]) / "job.json"
    manifest = build_job_manifest(job)
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return job, manifest


def _load_profile(job: dict, profile_link: str) -> dict:
    if not profile_link:
        return {}
    profile_path = Path(job["output_dir"]) / profile_link
    if not profile_path.exists():
        return {}
    try:
        return json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def render_job_detail(app: BenchWebApp, job_id: str) -> bytes:
    job, manifest = load_manifest_for_job(app, job_id)
    if job is None or manifest is None:
        paths = JobPaths(app.output_root, job_id)
        startup_error = paths.job_dir / "startup_error.txt"
        message = "任务正在启动，页面会自动刷新。" if not startup_error.exists() else startup_error.read_text(encoding="utf-8")
        return page("任务启动中", f"<h1>{escape(job_id)}</h1><div class=\"panel\"><pre>{escape(message)}</pre></div>", refresh=True)

    summary = manifest.get("summary", {})
    refresh = job["status"] == "running"
    output_links = f"""
<div class="next-actions">
  <a class="action-card" href="/artifact/{escape(job_id)}/briefs/index.html"><b>打开中文简报</b><span>适合快速阅读和组会展示。</span></a>
  <a class="action-card" href="/artifact/{escape(job_id)}/index.html"><b>打开批量对比报告</b><span>横向比较本批次所有 Bench。</span></a>
  <a class="action-card" href="/jobs/{escape(job_id)}/sources"><b>来源与证据复核</b><span>检查来源、raw cache 和 evidence snippets。</span></a>
  <a class="action-card" href="/jobs/{escape(job_id)}/compare"><b>报告横向对比</b><span>用表格快速对齐核心问题、指标和失败模式。</span></a>
  <a class="action-card" href="/artifact/{escape(job_id)}/export.zip"><b>导出 zip</b><span>下载本任务的全部报告和 JSON 产物。</span></a>
  <a class="action-card" href="/artifact/{escape(job_id)}/job.json"><b>打开任务清单 JSON</b><span>开发调试和自动复盘使用。</span></a>
</div>
"""
    run_rows = ""
    for run in manifest.get("bench_runs", []):
        run_summary = run.get("summary", {})
        artifacts = run.get("artifacts", {})
        report_link = artifacts.get("report_html", "")
        profile_link = artifacts.get("profile_json", "")
        brief_link = artifacts.get("brief_html", "")
        profile = _load_profile(job, profile_link)
        paper_analysis = profile.get("paper_analysis", {})
        artifact_links = []
        if report_link:
            artifact_links.append(f'<a href="/artifact/{escape(job_id)}/{escape(report_link)}">HTML</a>')
        if brief_link:
            artifact_links.append(f'<a href="/artifact/{escape(job_id)}/{escape(brief_link)}">简报</a>')
        if profile_link:
            artifact_links.append(f'<a href="/artifact/{escape(job_id)}/{escape(profile_link)}">JSON</a>')
        run_rows += (
            f"<tr><td>{escape(run.get('bench_name', ''))}</td>"
            f"<td><span class=\"{status_class(run.get('status', ''))}\">{escape(status_label(run.get('status', '')))}</span></td>"
            f"<td><span class=\"{escape(run_summary.get('review_status', ''))}\">{escape(review_label(run_summary.get('review_status', '')))}</span></td>"
            f"<td>{escape(paper_analysis.get('gap_claimed') or '待复核')}</td>"
            f"<td>{run_summary.get('sources_count', 0)}</td>"
            f"<td>{run_summary.get('raw_documents_count', 0)}</td>"
            f"<td>{run_summary.get('raw_failures_count', 0)}</td>"
            f"<td>{run_summary.get('warning_count', 0)}</td>"
            f"<td>{run_summary.get('error_count', 0)}</td>"
            f"<td>{' '.join(artifact_links)}</td>"
            f"<td><form class=\"inline-form\" method=\"post\" action=\"/jobs/{escape(job_id)}/rerun-bench\">"
            f"<input type=\"hidden\" name=\"bench_name\" value=\"{escape(run.get('bench_name', ''))}\">"
            f"<button class=\"secondary\" type=\"submit\">复跑</button></form></td></tr>"
        )
    step_rows = ""
    for run in job["bench_runs"]:
        for step in run["steps"]:
            step_error_lines = (step["error"] or "").splitlines()
            step_error = step_error_lines[0] if step_error_lines else ""
            step_rows += (
                f"<tr><td>{escape(run['bench_name'])}</td><td>{escape(step_label(step['step_name']))}</td>"
                f"<td><span class=\"{status_class(step['status'])}\">{escape(status_label(step['status']))}</span></td>"
                f"<td>{escape(step['started_at'] or '')}</td><td>{escape(step['finished_at'] or '')}</td>"
                f"<td>{escape(step_error)}</td></tr>"
            )
    body = f"""
<header>
  <div>
    <h1>任务控制台</h1>
    <div class="muted">任务 {escape(job_id)} · 输出目录：{escape(job['output_dir'])}</div>
  </div>
  <a href="/">新建批量分析</a>
</header>

<section class="hero">
  <div class="eyebrow">运行状态</div>
  <h1><span class="{status_class(job['status'])}">{escape(status_label(job['status']))}</span></h1>
  <div class="grid">
    <div class="kv"><b>Bench 数</b>{summary.get('bench_count', len(job['bench_runs']))}</div>
    <div class="kv"><b>完成</b>{summary.get('completed_count', 0)}</div>
    <div class="kv"><b>警告</b>{summary.get('warning_count', 0)}</div>
    <div class="kv"><b>错误</b>{summary.get('error_count', 0)}</div>
  </div>
</section>

<div class="section-head">
  <div>
    <h2>推荐下一步</h2>
    <p class="muted">先读中文简报或批量对比报告；只有需要追溯来源时再看证据页和 JSON。</p>
  </div>
</div>
<div class="panel">{output_links}</div>

<div class="panel actions">
  <form class="inline-form" method="post" action="/jobs/{escape(job_id)}/rerun">
    <button type="submit">复跑整个批次</button>
  </form>
  <span class="muted">复跑会创建一个新的任务，并沿用本次联网与抓取上限设置。</span>
</div>

<div class="section-head">
  <div>
    <h2>Bench 运行结果</h2>
    <p class="muted">这里用于判断哪些 Bench 已经可读、哪些还需要复核。</p>
  </div>
</div>
<table>
  <thead><tr><th>Bench</th><th>状态</th><th>复核建议</th><th>核心缺口</th><th>来源</th><th>原始资料</th><th>抓取失败</th><th>警告</th><th>错误</th><th>产物</th><th>操作</th></tr></thead>
  <tbody>{run_rows or '<tr><td colspan="11" class="muted">还没有 Bench 运行结果。</td></tr>'}</tbody>
</table>

<div class="section-head">
  <div>
    <h2>执行日志</h2>
    <p class="muted">这部分主要用于排查问题，正常阅读报告时可以跳过。</p>
  </div>
</div>
<table>
  <thead><tr><th>Bench</th><th>步骤</th><th>状态</th><th>开始时间</th><th>结束时间</th><th>错误</th></tr></thead>
  <tbody>{step_rows or '<tr><td colspan="6" class="muted">还没有步骤记录。</td></tr>'}</tbody>
</table>
"""
    return page(f"任务 {job_id}", body, refresh=refresh)


def render_source_review(app: BenchWebApp, job_id: str) -> bytes:
    job, manifest = load_manifest_for_job(app, job_id)
    if job is None or manifest is None:
        return page("任务不存在", f"<h1>{escape(job_id)}</h1><p>没有找到这个任务。</p>")
    blocks = []
    for run in manifest.get("bench_runs", []):
        profile = _load_profile(job, run.get("artifacts", {}).get("profile_json", ""))
        sources = profile.get("sources", [])
        raw_documents = profile.get("raw_documents", [])
        evidence = profile.get("paper_analysis", {}).get("evidence", [])
        source_rows = "".join(
            f"<tr><td>{escape(source.get('type', ''))}</td><td><a href=\"{escape(source.get('url', ''))}\">{escape(source.get('title', '来源'))}</a></td><td>{escape(source.get('note', ''))}</td></tr>"
            for source in sources
        ) or '<tr><td colspan="3" class="muted">暂无来源。</td></tr>'
        raw_rows = "".join(
            f"<tr><td>{escape(document.get('type', ''))}</td><td>{escape(source_status_label('failed' if document.get('error') else 'fetched'))}</td>"
            f"<td>{escape(document.get('cache_status', ''))}</td><td>{escape(document.get('error', '') or document.get('text_preview', ''))}</td></tr>"
            for document in raw_documents
        ) or '<tr><td colspan="4" class="muted">暂无原始资料。</td></tr>'
        evidence_rows = "".join(
            f"<tr><td>{escape(record.get('field', ''))}</td><td><pre>{escape(record.get('snippet') or record.get('value', ''))}</pre></td>"
            f"<td>{escape(str(record.get('confidence', '')))}</td><td><a href=\"{escape(record.get('source_url', ''))}\">来源</a></td></tr>"
            for record in evidence
        ) or '<tr><td colspan="4" class="muted">暂无 PaperAnalysis evidence。</td></tr>'
        blocks.append(
            f"""
<h2>{escape(run.get('bench_name', ''))}</h2>
<div class="panel">
  <h3>来源列表</h3>
  <table><thead><tr><th>类型</th><th>来源</th><th>备注</th></tr></thead><tbody>{source_rows}</tbody></table>
  <h3>原始资料抓取</h3>
  <table><thead><tr><th>类型</th><th>状态</th><th>缓存</th><th>预览 / 错误</th></tr></thead><tbody>{raw_rows}</tbody></table>
  <h3>论文分析证据</h3>
  <table><thead><tr><th>字段</th><th>Snippet</th><th>置信度</th><th>来源</th></tr></thead><tbody>{evidence_rows}</tbody></table>
</div>
"""
        )
    return page(
        "来源与证据复核",
        f"<header><div><h1>来源与证据复核</h1><div class=\"muted\">任务 {escape(job_id)}</div></div><a href=\"/jobs/{escape(job_id)}\">返回任务</a></header>{''.join(blocks)}",
    )


def render_compare(app: BenchWebApp, job_id: str) -> bytes:
    job, manifest = load_manifest_for_job(app, job_id)
    if job is None or manifest is None:
        return page("任务不存在", f"<h1>{escape(job_id)}</h1><p>没有找到这个任务。</p>")
    rows = []
    for run in manifest.get("bench_runs", []):
        profile = _load_profile(job, run.get("artifacts", {}).get("profile_json", ""))
        paper = profile.get("paper_analysis", {})
        brief = profile.get("localized_brief", {})
        scoring = paper.get("rubric_and_scoring", {})
        rows.append(
            f"<tr><td>{escape(run.get('bench_name', ''))}</td>"
            f"<td>{escape(paper.get('core_question') or '待复核')}</td>"
            f"<td>{escape(paper.get('gap_claimed') or '待复核')}</td>"
            f"<td>{escape(', '.join(paper.get('evaluated_capabilities') or profile.get('capability_tags', [])))}</td>"
            f"<td>{escape(', '.join(scoring.get('metrics', [])))}</td>"
            f"<td>{escape('; '.join(paper.get('failure_modes', [])[:3]) or '待复核')}</td>"
            f"<td>{escape(brief.get('status', ''))}</td></tr>"
        )
    body = f"""
<header><div><h1>报告横向对比</h1><div class="muted">任务 {escape(job_id)}</div></div><a href="/jobs/{escape(job_id)}">返回任务</a></header>
<table>
  <thead><tr><th>Bench</th><th>核心问题</th><th>评测缺口</th><th>能力定位</th><th>指标</th><th>失败模式</th><th>简报状态</th></tr></thead>
  <tbody>{''.join(rows) or '<tr><td colspan="7" class="muted">暂无可对比的 profile。</td></tr>'}</tbody>
</table>
"""
    return page("报告横向对比", body)


class RequestHandler(BaseHTTPRequestHandler):
    app: BenchWebApp

    def log_message(self, format: str, *args) -> None:
        return

    def send_html(self, content: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, path: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_html(render_home(self.app))
            return
        if path == "/seeds":
            query = parse_qs(parsed.query).get("query", [""])[0]
            self.send_html(render_seeds(self.app, query=query))
            return
        if path.startswith("/seeds/"):
            slug = unquote(path.strip("/").split("/", 1)[1])
            self.send_html(render_seed_detail(self.app, slug))
            return
        if path == "/jobs":
            query = parse_qs(parsed.query).get("query", [""])[0]
            self.send_html(render_jobs(self.app, query=query))
            return
        if path.startswith("/jobs/"):
            parts = path.strip("/").split("/")
            job_id = unquote(parts[1]) if len(parts) >= 2 else ""
            if len(parts) == 3 and parts[2] == "sources":
                self.send_html(render_source_review(self.app, job_id))
                return
            if len(parts) == 3 and parts[2] == "compare":
                self.send_html(render_compare(self.app, job_id))
                return
            self.send_html(render_job_detail(self.app, job_id))
            return
        if path.startswith("/artifact/"):
            self.serve_artifact(path)
            return
        self.send_html(page("页面不存在", "<h1>页面不存在</h1>"), status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/seeds/manual":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="ignore")
            data = parse_qs(body)
            bench_name = data.get("bench_name", [""])[0].strip()
            if not bench_name:
                self.send_html(page("缺少 Bench", "<h1>缺少 Bench</h1><p>请填写 Bench 名称。</p>"), status=HTTPStatus.BAD_REQUEST)
                return
            notes = data.get("notes", [""])[0].strip()
            urls = _urls_from_text(data.get("source_urls", [""])[0])
            new_manual_sources = source_records_from_urls(
                urls,
                bench_name=bench_name,
                note=notes or "用户手动补充来源。",
                discovered_by="manual-ui",
            )
            slug = slug_for_input(bench_name)
            existing = self.app.store.find_bench_seed(bench_name) or self.app.store.get_bench_seed(slug)
            existing_manual_sources = _source_records_from_seed_items(existing.get("manual_sources", [])) if existing else []
            manual_sources = _merge_source_records(existing_manual_sources, new_manual_sources)
            self.app.store.upsert_bench_seed(
                slug=existing.get("slug", slug) if existing else slug,
                name=existing.get("name", bench_name) if existing else bench_name,
                aliases=existing.get("aliases", []) if existing else [],
                manual_sources=manual_sources,
                notes=notes,
            )
            if "with_web" not in data:
                self.redirect(f"/seeds/{existing.get('slug', slug) if existing else slug}")
                return
            options = JobOptions(
                with_web=True,
                include_general_search=False,
                discovery_limit=8,
                fetch_limit=5,
                manual_sources={bench_name.lower(): [source.__dict__ for source in manual_sources]},
            )
            job_id = self.app.start_job([bench_name], options)
            self.redirect(f"/jobs/{job_id}")
            return
        if parsed.path.startswith("/jobs/"):
            parts = parsed.path.strip("/").split("/")
            job_id = unquote(parts[1]) if len(parts) >= 2 else ""
            if len(parts) == 3 and parts[2] == "rerun":
                new_job_id = self.app.rerun_job(job_id)
                if not new_job_id:
                    self.send_html(page("任务不存在", "<h1>任务不存在</h1>"), status=HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f"/jobs/{new_job_id}")
                return
            if len(parts) == 3 and parts[2] == "rerun-bench":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8", errors="ignore")
                data = parse_qs(body)
                bench_name = data.get("bench_name", [""])[0].strip()
                if not bench_name:
                    self.send_html(page("缺少 Bench", "<h1>缺少 Bench</h1>"), status=HTTPStatus.BAD_REQUEST)
                    return
                new_job_id = self.app.rerun_job(job_id, bench_names=[bench_name])
                if not new_job_id:
                    self.send_html(page("任务不存在", "<h1>任务不存在</h1>"), status=HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f"/jobs/{new_job_id}")
                return
        if parsed.path != "/jobs":
            self.send_html(page("页面不存在", "<h1>页面不存在</h1>"), status=HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        data = parse_qs(body)
        bench_names = parse_bench_names(data.get("bench_names", [""])[0])
        if not bench_names:
            self.send_html(page("缺少 Bench", "<h1>缺少 Bench</h1><p>请至少输入一个 Bench 名称。</p>"), status=HTTPStatus.BAD_REQUEST)
            return
        options = JobOptions(
            with_web="with_web" in data,
            include_general_search="include_general_search" in data,
            discovery_limit=int(data.get("discovery_limit", ["6"])[0] or 6),
            fetch_limit=int(data.get("fetch_limit", ["3"])[0] or 3),
        )
        job_id = self.app.start_job(bench_names, options)
        self.redirect(f"/jobs/{job_id}")

    def serve_export_zip(self, base: Path, job_id: str) -> None:
        target = base / "export.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in base.rglob("*"):
                if not path.is_file() or path == target:
                    continue
                archive.write(path, path.relative_to(base))
        payload = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{job_id}.zip"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def serve_artifact(self, path: str) -> None:
        parts = path.split("/", 3)
        if len(parts) < 4:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        job_id = unquote(parts[2])
        relative = unquote(parts[3])
        base = JobPaths(self.app.output_root, job_id).job_dir.resolve()
        if relative == "export.zip" and base.exists():
            self.serve_export_zip(base, job_id)
            return
        target = (base / relative).resolve()
        if not str(target).startswith(str(base)) or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        payload = target.read_bytes()
        if target.suffix in {".html", ".json", ".txt", ".md"}:
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".txt": "text/plain; charset=utf-8",
                ".md": "text/markdown; charset=utf-8",
            }[target.suffix]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_server(host: str, port: int, output_root: Path) -> None:
    app = BenchWebApp(output_root)
    handler = type("BenchRequestHandler", (RequestHandler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Bench 分析工作台：http://{host}:{port}")
    print(f"输出目录：{output_root.resolve()}")
    server.serve_forever()
