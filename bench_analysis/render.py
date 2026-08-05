from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .classify import CAPABILITY_RULES, display_tag
from .schema import BenchProfile

STATUS_LABELS = {
    "pending": "等待中",
    "running": "运行中",
    "completed": "已完成",
    "completed_with_warnings": "完成，有警告",
    "failed": "失败",
    "skipped": "已跳过",
    "resolved": "已确认",
    "ambiguous": "有歧义",
    "unresolved": "未解析",
}

REVIEW_LABELS = {
    "ready": "可直接查看",
    "review_recommended": "建议复核",
    "needs_human_review": "需要人工复核",
    "failed_review": "运行失败",
}

WARNING_LABELS = {
    "ambiguous_identity": "身份有歧义",
    "unresolved_identity": "身份未解析",
    "missing_core_field": "核心字段缺失",
    "missing_paper_analysis_field": "论文分析字段缺失",
    "raw_fetch_failure": "原始资料抓取失败",
    "field_conflict": "字段冲突",
}

ERROR_LABELS = {
    "failed_step": "步骤失败",
    "failed_run": "Bench 运行失败",
}


STYLE = """
:root {
  color-scheme: light;
  --ink: #1c2430;
  --muted: #5e6875;
  --line: #d9dee6;
  --paper: #ffffff;
  --wash: #f5f7fa;
  --blue: #255f9f;
  --green: #287a4e;
  --amber: #9a6400;
  --red: #9e3434;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  color: var(--ink);
  background: var(--wash);
  line-height: 1.55;
}
main { max-width: 1120px; margin: 0 auto; padding: 32px 20px 56px; }
h1 { margin: 0 0 8px; font-size: 32px; letter-spacing: 0; }
h2 { margin: 32px 0 12px; font-size: 20px; }
h3 { margin: 18px 0 8px; font-size: 16px; }
a { color: var(--blue); text-decoration: none; }
a:hover { text-decoration: underline; }
.lede { color: var(--muted); margin: 0 0 24px; max-width: 840px; }
.hero {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 24px;
  margin-bottom: 16px;
}
.hero h1 { font-size: 36px; }
.eyebrow {
  color: var(--blue);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: .04em;
  margin-bottom: 8px;
}
.panel {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 18px;
  margin: 16px 0;
}
.section-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: end;
  margin: 30px 0 12px;
}
.section-head h2 { margin: 0; }
.section-head p { margin: 4px 0 0; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.summary-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.two-col { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 16px; align-items: start; }
.kv { border-top: 1px solid var(--line); padding-top: 10px; }
.kv b { display: block; font-size: 13px; color: var(--muted); margin-bottom: 4px; }
.pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 4px 9px;
  margin: 3px 4px 3px 0;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: #fbfcfd;
  font-size: 13px;
  white-space: nowrap;
}
.phase { font-weight: 600; color: var(--green); }
.test { color: var(--amber); }
.ambiguous, .unresolved { color: var(--red); }
table { width: 100%; border-collapse: collapse; background: var(--paper); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }
th { background: #eef2f6; font-size: 13px; color: #334155; }
tr:last-child td { border-bottom: 0; }
.yes { color: var(--green); font-weight: 700; }
.no { color: #a1a8b3; }
.small { color: var(--muted); font-size: 13px; }
.status-completed { color: var(--green); font-weight: 700; }
.status-completed_with_warnings { color: var(--amber); font-weight: 700; }
.status-failed { color: var(--red); font-weight: 700; }
.toc {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 14px 0 0;
}
.toc a {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: #fbfcfd;
  padding: 5px 10px;
  font-size: 13px;
}
.callout {
  border-left: 4px solid var(--blue);
  padding: 12px 14px;
  background: #eef5fb;
  margin: 14px 0;
}
.appendix {
  margin-top: 36px;
  border-top: 2px solid var(--line);
  padding-top: 10px;
}
@media (max-width: 760px) {
  main { padding: 24px 14px 40px; }
  .grid, .summary-grid, .two-col { grid-template-columns: 1fr; }
  table { display: block; overflow-x: auto; }
}
"""


def _tags(values: list[str]) -> str:
    return "".join(f'<span class="pill">{escape(value)}</span>' for value in values)


def _capability_tags(values: list[str]) -> str:
    return "".join(f'<span class="pill">{escape(display_tag(value))}</span>' for value in values)


def _link(url: str, label: str | None = None) -> str:
    label = label or url
    return f'<a href="{escape(url)}">{escape(label)}</a>'


def _counts(value: dict[str, int]) -> str:
    if not value:
        return '<span class="small">无</span>'
    return "".join(
        f'<span class="pill">{escape(WARNING_LABELS.get(key) or ERROR_LABELS.get(key) or key)}: {count}</span>'
        for key, count in sorted(value.items())
    )


def _list(values: list[str]) -> str:
    if not values:
        return '<span class="small">待复核</span>'
    return "<ul>" + "".join(f"<li>{escape(value)}</li>" for value in values) + "</ul>"


def _text(value: str) -> str:
    return escape(value) if value else '<span class="small">待复核</span>'


def _status(value: str) -> str:
    return STATUS_LABELS.get(value, value)


def _review(value: str) -> str:
    return REVIEW_LABELS.get(value, value)


def _llm_block(title: str, answer) -> str:
    refs = ", ".join(answer.evidence_refs) if answer.evidence_refs else "未标注"
    confidence = f"{answer.confidence:.2f}"
    return (
        f"<h3>{escape(title)}</h3>"
        f"<p>{_text(answer.answer)}</p>"
        f"<p class=\"small\">证据：{escape(refs)} · 置信度：{confidence}</p>"
    )


def render_profile(profile: BenchProfile) -> str:
    analysis = profile.paper_analysis
    llm_analysis = profile.llm_analysis
    design = analysis.benchmark_design
    scoring = analysis.rubric_and_scoring
    source_items = "".join(
        f"<li>{_link(source.url, source.title)} <span class=\"small\">[{escape(source.type)}] {escape(source.note)}</span></li>"
        for source in profile.sources
    ) or '<li><span class="small">No source attached yet.</span></li>'
    artifact_rows = "".join(
        f"<tr><td>{escape(kind)}</td><td>{_link(url)}</td></tr>"
        for kind, url in profile.artifacts.items()
        if url
    ) or '<tr><td colspan="2" class="small">No artifact link yet.</td></tr>'
    notes = "".join(f"<li>{escape(note)}</li>" for note in profile.reliability_notes)
    raw_rows = "".join(
        f"<tr><td>{escape(document.type)}</td><td>{_link(document.url, document.title or document.url)}</td>"
        f"<td>{escape(document.content_type or 'unknown')}</td><td>{escape(document.path or document.error)}</td></tr>"
        for document in profile.raw_documents
    ) or '<tr><td colspan="4" class="small">No raw document fetched yet.</td></tr>'
    fact_rows = "".join(
        f"<tr><td>{escape(fact.field)}</td><td>{escape(fact.value)}</td><td>{fact.confidence:.2f}</td><td>{_link(fact.source_url, 'source')}</td></tr>"
        for fact in profile.extracted_facts[:40]
    ) or '<tr><td colspan="4" class="small">No extracted fact yet.</td></tr>'
    result_rows = "".join(
        f"<tr><td>{escape(result.model)}</td><td>{escape(result.metric)}</td><td>{escape(result.score)}</td>"
        f"<td>{escape(result.verification_status)}</td><td>{escape(result.extraction_method)}</td>"
        f"<td>{result.confidence:.2f}</td><td>{escape(result.context)}</td><td>{_link(result.source_url, 'source')}</td></tr>"
        for result in profile.model_results[:40]
    ) or '<tr><td colspan="8" class="small">本次没有抽到结构化模型分数行；这不代表论文没有模型结果，可能需要 PDF 表格、官方页面或人工来源补充。</td></tr>'
    evidence_rows = "".join(
        f"<tr><td>{escape(record.field)}</td><td>{escape(record.snippet or record.value)}</td>"
        f"<td>{record.confidence:.2f}</td><td>{_link(record.source_url, 'source')}</td></tr>"
        for record in analysis.evidence[:60]
    ) or '<tr><td colspan="4" class="small">No PaperAnalysis evidence snippet yet.</td></tr>'
    conflict_rows = "".join(
        f"<tr><td>{escape(conflict.field)}</td><td>{escape(conflict.existing_value)}</td>"
        f"<td>{escape(conflict.candidate_value)}</td><td>{_link(conflict.source_url, 'source')}</td></tr>"
        for conflict in profile.conflicts
    ) or '<tr><td colspan="4" class="small">No conflict detected.</td></tr>'
    llm_rows = ""
    if llm_analysis.status in {"completed", "fallback"}:
        fallback_note = ""
        if llm_analysis.status == "fallback":
            fallback_note = (
                "<div class=\"callout\"><b>注意：</b>真实 LLM 调用未完成，"
                "本区当前使用规则抽取结果生成 fallback，适合占位预览，不应当作为最终 LLM 分析。</div>"
            )
        llm_rows = (
            f"<p><b>模型：</b>{escape(llm_analysis.provider)} / {escape(llm_analysis.model)} "
            f"<span class=\"small\">{escape(llm_analysis.generated_at)}</span></p>"
            f"{fallback_note}"
            f"<div class=\"callout\"><b>一句话判断：</b>{escape(llm_analysis.one_sentence or '待复核')}</div>"
            f"{_llm_block('核心问题', llm_analysis.core_question)}"
            f"{_llm_block('提出动机', llm_analysis.motivation)}"
            f"{_llm_block('能力定位', llm_analysis.evaluated_capability)}"
            f"{_llm_block('设计逻辑', llm_analysis.benchmark_design)}"
            f"{_llm_block('评分解读', llm_analysis.scoring)}"
            f"{_llm_block('模型结果解读', llm_analysis.model_results)}"
            f"{_llm_block('失败模式诊断', llm_analysis.failure_modes)}"
            f"{_llm_block('可靠性判断', llm_analysis.reliability)}"
            f"<h3>需要人工复核</h3>{_list(llm_analysis.unsupported_claims)}"
        )
    elif llm_analysis.status != "not_run":
        llm_rows = (
            f"<p><b>状态：</b>{escape(llm_analysis.status)}</p>"
            f"<p class=\"small\">{escape(llm_analysis.error or 'LLM analysis did not complete.')}</p>"
        )
    else:
        llm_rows = '<span class="small">本报告生成时尚未运行 LLM 深度分析。</span>'

    key_takeaway = profile.localized_brief.one_liner or profile.evaluates
    result_count = len(profile.model_results)
    verified_count = len([result for result in profile.model_results if result.verification_status == "verified"])

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(profile.name)} | Bench 论文分析报告</title>
  <style>{STYLE}</style>
</head>
<body>
<main>
  <p><a href="../index.html">返回批量对比报告</a></p>

  <section class="hero">
    <div class="eyebrow">单 Bench 论文分析报告</div>
    <h1>{escape(profile.name)}</h1>
    <p class="lede">{escape(key_takeaway)}</p>
    <div class="toc">
      <a href="#question">核心问题</a>
      <a href="#capability">能力定位</a>
      <a href="#design">Benchmark 设计</a>
      <a href="#scoring">评分协议</a>
      <a href="#results">模型结果</a>
      <a href="#llm">LLM 深度分析</a>
      <a href="#findings">发现与失败</a>
      <a href="#appendix">证据与调试</a>
    </div>
  </section>

  <section class="summary-grid">
    <div class="panel kv"><b>状态</b><span class="{escape(profile.status)}">{escape(_status(profile.status))}</span></div>
    <div class="panel kv"><b>置信度</b>{profile.confidence:.2f}</div>
    <div class="panel kv"><b>年份</b>{escape(profile.year or "待复核")}</div>
    <div class="panel kv"><b>数据规模</b>{escape(profile.dataset_size or "待复核")}</div>
    <div class="panel kv"><b>模型结果</b>{result_count}<span class="small"> · verified {verified_count}</span></div>
  </section>

  <section id="question" class="two-col">
    <div>
      <div class="section-head">
        <div>
          <h2>1. 核心问题与动机</h2>
          <p class="muted">先回答这个 Bench 为什么被提出，以及它认为旧评测缺什么。</p>
        </div>
      </div>
      <div class="panel">
        <p><b>核心问题：</b>{_text(analysis.core_question)}</p>
        <p><b>提出动机：</b>{_text(analysis.motivation)}</p>
        <p><b>评测缺口：</b>{_text(analysis.gap_claimed)}</p>
      </div>
    </div>
    <aside class="panel">
      <h3>基础画像</h3>
      <p><b>机构/作者：</b>{escape(profile.organization or "待复核")}</p>
      <p><b>领域：</b>{_tags(profile.domain)}</p>
      <p><b>任务形态：</b>{escape(profile.task_format)}</p>
      <p><b>数据开放：</b>{escape(profile.data_access)}</p>
    </aside>
  </section>

  <section id="capability">
    <div class="section-head">
      <div>
        <h2>2. 能力评估定位</h2>
        <p class="muted">这一部分回答：它到底适合评估模型或 Agent 的哪类能力。</p>
      </div>
    </div>
    <div class="panel">
      <p>{escape(profile.evaluates)}</p>
      <h3>能力标签</h3>
      {_capability_tags(profile.capability_tags)}
      <h3>论文中的能力描述</h3>
      {_tags(analysis.evaluated_capabilities) or '<span class="small">待复核</span>'}
    </div>
  </section>

  <section id="design">
    <div class="section-head">
      <div>
        <h2>3. Benchmark 怎么设计</h2>
        <p class="muted">看任务、数据、case/world、工具环境和专家参与方式。</p>
      </div>
    </div>
    <div class="panel">
      <p><b>数据来源：</b>{_text(design.data_source)}</p>
      <p><b>任务构造：</b>{_text(design.task_construction)}</p>
      <p><b>任务类型：</b>{_tags(design.task_types or profile.task_categories) or '<span class="small">待复核</span>'}</p>
      <p><b>Gold / 标准答案生成：</b>{_text(design.gold_generation)}</p>
      <p><b>World / Case 设计：</b>{_text(design.world_or_case_design)}</p>
      <p><b>工具 / 环境：</b>{_tags(design.tools_or_environment) or '<span class="small">待复核</span>'}</p>
      <p><b>专家参与：</b>{_text(design.expert_involvement)}</p>
    </div>
  </section>

  <section id="scoring">
    <div class="section-head">
      <div>
        <h2>4. Gold、Rubric 与评分协议</h2>
        <p class="muted">看它如何判断模型输出是否真正达标。</p>
      </div>
    </div>
    <div class="panel">
      <p><b>Gold 定义：</b>{_text(scoring.gold_definition)}</p>
      <p><b>Rubric 维度：</b>{_tags(scoring.rubric_dimensions) or '<span class="small">待复核</span>'}</p>
      <p><b>评价指标：</b>{_tags(scoring.metrics) or '<span class="small">待复核</span>'}</p>
      <p><b>评分协议：</b>{_text(scoring.scoring_protocol)}</p>
      <p><b>Judge 类型：</b>{_text(scoring.judge_type)}</p>
      <p><b>Judge 模型：</b>{_text(scoring.judge_model)}</p>
      <p><b>人工复核：</b>{_text(scoring.human_review)}</p>
    </div>
  </section>

  <section id="results">
    <div class="section-head">
      <div>
        <h2>5. 模型结果与分数表</h2>
        <p class="muted">先看论文结论，再看 pipeline 从表格或文本中抽到的候选分数。</p>
      </div>
    </div>
    <div class="panel">
      <p>{_text(analysis.model_results_summary)}</p>
    </div>
    <table>
      <thead><tr><th>模型</th><th>指标</th><th>分数</th><th>状态</th><th>方法</th><th>置信度</th><th>上下文</th><th>来源</th></tr></thead>
      <tbody>{result_rows}</tbody>
    </table>
  </section>

  <section id="llm">
    <div class="section-head">
      <div>
        <h2>6. LLM 深度分析</h2>
        <p class="muted">基于抓取材料和 evidence pack 生成的中文解释，用于补强规则抽取无法覆盖的理解、归纳和诊断。</p>
      </div>
    </div>
    <div class="panel">
      {llm_rows}
    </div>
  </section>

  <section id="findings" class="two-col">
    <div>
      <div class="section-head">
        <div>
          <h2>7. 主要发现与论文结论</h2>
          <p class="muted">把论文想证明的趋势和结论抽出来。</p>
        </div>
      </div>
      <div class="panel">
        <h3>主要发现</h3>
        {_list(analysis.main_findings)}
        <h3>论文结论</h3>
        {_list(analysis.conclusions)}
      </div>
    </div>
    <aside>
      <div class="section-head">
        <div>
          <h2>8. 失败模式</h2>
          <p class="muted">模型为什么会在这个 Bench 上失分。</p>
        </div>
      </div>
      <div class="panel">
        {_list(analysis.failure_modes)}
      </div>
    </aside>
  </section>

  <section class="callout">
    <b>我们的使用判断：</b>
    这个 Bench 适合用于观察 {_tags(profile.capability_tags[:4]) or "模型能力"}。当前报告中的自动抽取字段仍需要结合 evidence 复核，尤其是模型分数和失败模式。
  </section>

  <section id="appendix" class="appendix">
    <div class="section-head">
      <div>
        <h2>证据与调试附录</h2>
        <p class="muted">以下内容用于追溯来源、排查 pipeline 和人工复核，普通阅读可以跳过。</p>
      </div>
    </div>

    <h3>可靠性 / 复现性备注</h3>
    <div class="panel">{_list(analysis.reliability_notes or profile.reliability_notes)}</div>

    <h3>PaperAnalysis Evidence</h3>
    <table>
      <thead><tr><th>字段</th><th>Source snippet</th><th>置信度</th><th>来源</th></tr></thead>
      <tbody>{evidence_rows}</tbody>
    </table>

    <h3>资源链接</h3>
    <table>
      <thead><tr><th>类型</th><th>URL</th></tr></thead>
      <tbody>{artifact_rows}</tbody>
    </table>

    <h3>来源列表</h3>
    <div class="panel"><ul>{source_items}</ul></div>

    <h3>Raw Cache</h3>
    <table>
      <thead><tr><th>类型</th><th>来源</th><th>Content type</th><th>本地路径 / 错误</th></tr></thead>
      <tbody>{raw_rows}</tbody>
    </table>

    <h3>自动抽取字段</h3>
    <table>
      <thead><tr><th>字段</th><th>候选值</th><th>置信度</th><th>来源</th></tr></thead>
      <tbody>{fact_rows}</tbody>
    </table>

    <h3>字段冲突</h3>
    <table>
      <thead><tr><th>字段</th><th>Seed 值</th><th>抽取值</th><th>来源</th></tr></thead>
      <tbody>{conflict_rows}</tbody>
    </table>

    <h3>Legacy Reliability Notes</h3>
    <div class="panel"><ul>{notes}</ul></div>
  </section>
</main>
</body>
</html>"""


def render_index(profiles: list[BenchProfile]) -> str:
    capability_keys = list(CAPABILITY_RULES.keys())
    rows = []
    for profile in profiles:
        detail = f"{profile.slug}/report.html"
        brief = f"briefs/{profile.slug}.html"
        cells = "".join(
            f'<td class="{"yes" if key in profile.capability_tags else "no"}">{"✓" if key in profile.capability_tags else "·"}</td>'
            for key in capability_keys
        )
        rows.append(
            "<tr>"
            f'<td>{_link(detail, profile.name)}<div class="small">{_link(brief, "中文 Brief")} · {escape(", ".join(profile.aliases[:3]))}</div></td>'
            f'<td><span class="phase {escape(profile.phase)}">{escape(profile.phase)}</span></td>'
            f'<td><span class="{escape(profile.status)}">{escape(profile.status)}</span></td>'
            f"<td>{escape(profile.paper_analysis.gap_claimed or '待复核')}</td>"
            f"<td>{escape(profile.paper_analysis.core_question or '待复核')}</td>"
            f"<td>{escape(', '.join(profile.domain))}</td>"
            f"<td>{escape(profile.task_format)}</td>"
            f"<td>{profile.confidence:.2f}</td>"
            f"{cells}"
            "</tr>"
        )
    capability_heads = "".join(f"<th>{escape(display_tag(key))}</th>" for key in capability_keys)
    body_rows = "".join(rows)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bench 分析总览</title>
  <style>{STYLE}</style>
</head>
<body>
<main>
  <h1>Bench 分析管道 MVP</h1>
  <p class="lede">这个最小版本先实现“能力标签化”：把训练 Bench 和测试 Bench 映射到统一 schema，并生成可追溯的 JSON + HTML。</p>
  <div class="panel">
    <b>训练 Bench：</b>GDPval, SpreadsheetBench v2, FAB<br>
    <b>测试 Bench：</b>APEX, FinSearchComp, OneMillion-Bench, IBFE
  </div>
  <h2>能力矩阵</h2>
  <table>
    <thead>
      <tr>
        <th>Bench</th><th>阶段</th><th>状态</th><th>核心缺口</th><th>核心问题</th><th>领域</th><th>任务形态</th><th>置信度</th>{capability_heads}
      </tr>
    </thead>
    <tbody>{body_rows}</tbody>
  </table>
</main>
</body>
</html>"""


def render_job_index(profiles: list[BenchProfile], job_manifest: dict[str, Any]) -> str:
    capability_keys = list(CAPABILITY_RULES.keys())
    run_by_slug = {run["slug"]: run for run in job_manifest.get("bench_runs", [])}
    rows = []
    for profile in profiles:
        run = run_by_slug.get(profile.slug, {})
        summary = run.get("summary", {})
        detail = f"{profile.slug}/report.html"
        brief = f"briefs/{profile.slug}.html"
        cells = "".join(
            f'<td class="{"yes" if key in profile.capability_tags else "no"}">{"✓" if key in profile.capability_tags else "·"}</td>'
            for key in capability_keys
        )
        rows.append(
            "<tr>"
            f'<td>{_link(detail, profile.name)}<div class="small">{_link(brief, "中文简报")} · {escape(", ".join(profile.aliases[:3]))}</div></td>'
            f'<td><span class="status-{escape(run.get("status", profile.status))}">{escape(_status(run.get("status", profile.status)))}</span></td>'
            f"<td>{escape(_review(summary.get('review_status', '')))}</td>"
            f"<td>{escape(profile.paper_analysis.gap_claimed or '待复核')}</td>"
            f"<td>{escape('; '.join(profile.paper_analysis.failure_modes[:2]) or '待复核')}</td>"
            f"<td>{profile.confidence:.2f}</td>"
            f"<td>{summary.get('sources_count', len(profile.sources))}</td>"
            f"<td>{summary.get('raw_documents_count', len(profile.raw_documents))}</td>"
            f"<td>{summary.get('raw_failures_count', 0)}</td>"
            f"<td>{summary.get('raw_cache_hits_count', 0)}</td>"
            f"<td>{summary.get('extracted_facts_count', len(profile.extracted_facts))}</td>"
            f"<td>{summary.get('model_results_count', len(profile.model_results))}"
            f"<div class=\"small\">已验证表格行 {summary.get('verified_model_results_count', 0)}</div></td>"
            f"<td>{summary.get('conflicts_count', len(profile.conflicts))}</td>"
            f"<td>{summary.get('warning_count', 0)}</td>"
            f"{cells}"
            "</tr>"
        )
    failed_runs = [
        run for run in job_manifest.get("bench_runs", []) if run.get("status") == "failed" or run.get("error")
    ]
    failure_rows = "".join(
        f"<tr><td>{escape(run.get('bench_name', ''))}</td><td>{escape(_status(run.get('status', '')))}</td>"
        f"<td>{escape((run.get('error') or '').splitlines()[0])}</td></tr>"
        for run in failed_runs
    ) or '<tr><td colspan="3" class="small">没有失败的 Bench 运行。</td></tr>'
    summary = job_manifest.get("summary", {})
    options = job_manifest.get("options", {})
    capability_heads = "".join(f"<th>{escape(display_tag(key))}</th>" for key in capability_keys)
    capability_counts: dict[str, int] = {}
    for profile in profiles:
        for tag in profile.capability_tags:
            capability_counts[tag] = capability_counts.get(tag, 0) + 1
    capability_summary = "".join(
        f'<span class="pill">{escape(display_tag(tag))}: {count}</span>'
        for tag, count in sorted(capability_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    ) or '<span class="small">待复核</span>'
    review_rows = "".join(
        f"<tr><td>{escape(run.get('bench_name', ''))}</td>"
        f"<td>{escape(_review(run.get('summary', {}).get('review_status', '')))}</td>"
        f"<td>{escape(', '.join(run.get('summary', {}).get('missing_core_fields', [])) or '无')}</td>"
        f"<td>{escape(', '.join(run.get('summary', {}).get('missing_paper_analysis_fields', [])) or '无')}</td>"
        f"<td>{run.get('summary', {}).get('warning_count', 0)}</td></tr>"
        for run in job_manifest.get("bench_runs", [])
        if run.get("summary", {}).get("review_status") != "ready" or run.get("summary", {}).get("warning_count", 0)
    ) or '<tr><td colspan="5" class="small">暂无需要优先复核的 Bench。</td></tr>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bench 批量分析报告 | {escape(job_manifest.get("job_id", ""))}</title>
  <style>{STYLE}</style>
</head>
<body>
<main>
  <section class="hero">
    <div class="eyebrow">Batch Overview</div>
    <h1>Bench 批量对比报告</h1>
    <p class="lede">先横向看这一批 Bench 分别测什么、结果是否可信、哪些值得深入，再进入单个 Bench 的论文分析报告。</p>
    <div class="toc">
      <a href="#compare">横向对比</a>
      <a href="#review">复核队列</a>
      <a href="#artifacts">任务产物</a>
      <a href="#failures">失败记录</a>
    </div>
  </section>

  <div class="panel summary-grid">
    <div class="kv"><b>任务 ID</b>{escape(job_manifest.get("job_id", ""))}</div>
    <div class="kv"><b>状态</b><span class="status-{escape(job_manifest.get("status", ""))}">{escape(_status(job_manifest.get("status", "")))}</span></div>
    <div class="kv"><b>Bench 数</b>{summary.get("bench_count", 0)}</div>
    <div class="kv"><b>警告</b>{summary.get("warning_count", 0)}</div>
    <div class="kv"><b>错误</b>{summary.get("error_count", 0)}</div>
  </div>

  <div class="two-col">
    <div class="panel">
      <h2>本批次覆盖能力</h2>
      <p class="muted">这些标签来自每个 Bench 的 profile 和论文分析字段，用于快速判断这一批评测覆盖了哪些模型能力。</p>
      {capability_summary}
    </div>
    <aside class="panel">
      <h2>运行选项</h2>
      <p><b>联网发现资料：</b>{escape("是" if options.get("with_web") else "否")}</p>
      <p><b>来源发现上限：</b>{escape(str(options.get("discovery_limit", "")))}</p>
      <p><b>抓取资料上限：</b>{escape(str(options.get("fetch_limit", "")))}</p>
      <p><b>创建 / 完成：</b>{escape(job_manifest.get("created_at", ""))} / {escape(job_manifest.get("finished_at", ""))}</p>
    </aside>
  </div>

  <section id="compare">
  <div class="section-head">
    <div>
      <h2>Bench 横向对比</h2>
      <p class="muted">点击 Bench 名进入单个 Bench 的详细论文分析；点击中文简报进入轻量展示页。</p>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Bench</th><th>状态</th><th>复核建议</th><th>核心缺口</th><th>失败模式</th><th>置信度</th><th>来源</th><th>原始资料</th><th>抓取失败</th><th>缓存命中</th><th>字段候选</th><th>模型结果</th><th>冲突</th><th>警告</th>{capability_heads}
      </tr>
    </thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  </section>

  <section id="review">
  <div class="section-head">
    <div>
      <h2>复核队列</h2>
      <p class="muted">优先处理字段缺失、来源抓取失败或存在冲突的 Bench。</p>
    </div>
  </div>
  <table>
    <thead><tr><th>Bench</th><th>复核建议</th><th>缺失核心字段</th><th>缺失论文分析字段</th><th>警告数</th></tr></thead>
    <tbody>{review_rows}</tbody>
  </table>
  <div class="panel">
    <p><b>警告类型：</b>{_counts(summary.get("warnings_by_type", {}))}</p>
    <p><b>错误类型：</b>{_counts(summary.get("errors_by_type", {}))}</p>
  </div>
  </section>

  <section id="artifacts">
  <div class="section-head">
    <div>
      <h2>任务产物</h2>
      <p class="muted">这里是工程产物入口，普通阅读优先看中文简报和单 Bench 报告。</p>
    </div>
  </div>
  <table>
    <thead><tr><th>产物</th><th>路径</th></tr></thead>
    <tbody>
      <tr><td>中文简报首页</td><td>{_link("briefs/index.html", "briefs/index.html")}</td></tr>
      <tr><td>任务清单 JSON</td><td>{_link("job.json", "job.json")}</td></tr>
      <tr><td>批量 HTML 报告</td><td>{_link("index.html", "index.html")}</td></tr>
    </tbody>
  </table>
  </section>

  <section id="failures">
  <div class="section-head">
    <div>
      <h2>失败记录</h2>
      <p class="muted">只有运行失败或启动异常才会出现在这里。</p>
    </div>
  </div>
  <table>
    <thead><tr><th>Bench</th><th>状态</th><th>错误</th></tr></thead>
    <tbody>{failure_rows}</tbody>
  </table>
  </section>
</main>
</body>
</html>"""


def write_report(profile: BenchProfile, output_dir: Path) -> Path:
    target_dir = output_dir / profile.slug
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "report.html"
    path.write_text(render_profile(profile), encoding="utf-8")
    return path


def write_index(profiles: list[BenchProfile], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "index.html"
    path.write_text(render_index(profiles), encoding="utf-8")
    return path


def write_job_index(profiles: list[BenchProfile], job_manifest: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "index.html"
    path.write_text(render_job_index(profiles, job_manifest), encoding="utf-8")
    return path
