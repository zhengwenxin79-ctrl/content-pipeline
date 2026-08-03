from __future__ import annotations

from dataclasses import asdict
from html import escape
from pathlib import Path

from .classify import display_tag
from .brief_localization import get_localized_brief
from .schema import BenchProfile


BRIEF_STYLE = """
:root {
  color-scheme: light;
  --ink: #102033;
  --muted: #607084;
  --soft: #eef8ff;
  --soft-2: #f7fbff;
  --paper: #ffffff;
  --line: #cfe7f8;
  --line-2: #e2eef7;
  --sky: #1e9be8;
  --sky-2: #62c6ff;
  --navy: #12415f;
  --green: #0f8a6b;
  --amber: #a76405;
  --red: #b42f45;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    linear-gradient(180deg, rgba(199, 235, 255, .72), rgba(247, 251, 255, 0) 320px),
    var(--soft-2);
  color: var(--ink);
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  line-height: 1.62;
}
main { max-width: 1180px; margin: 0 auto; padding: 28px 22px 60px; }
a { color: #0876bf; text-decoration: none; }
a:hover { text-decoration: underline; }
.hero {
  border: 1px solid rgba(30, 155, 232, .24);
  border-radius: 18px;
  background:
    radial-gradient(circle at 86% 0%, rgba(98, 198, 255, .32), transparent 36%),
    linear-gradient(135deg, #ffffff 0%, #ecf8ff 54%, #d9f1ff 100%);
  padding: 34px;
  box-shadow: 0 20px 60px rgba(18, 65, 95, .10);
}
.statusbar {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  border: 1px solid rgba(30, 155, 232, .26);
  border-radius: 14px;
  background: rgba(255, 255, 255, .74);
  color: #31526b;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.statusbar b {
  color: var(--sky);
  white-space: nowrap;
}
.eyebrow {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  color: var(--navy);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
}
h1 {
  margin: 12px 0 10px;
  font-size: clamp(34px, 5vw, 58px);
  line-height: 1.03;
  letter-spacing: 0;
}
.lede {
  max-width: 820px;
  margin: 0;
  color: #365066;
  font-size: 18px;
}
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
.chip {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 5px 11px;
  border: 1px solid rgba(30, 155, 232, .22);
  border-radius: 999px;
  background: rgba(255, 255, 255, .72);
  color: #16415e;
  font-size: 13px;
  white-space: nowrap;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0 26px;
}
.metric {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 8px 24px rgba(18, 65, 95, .06);
}
.metric b { display: block; color: var(--sky); font-size: 26px; line-height: 1.15; }
.metric span { display: block; color: var(--muted); font-size: 13px; margin-top: 5px; }
.section-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, .48fr);
  gap: 18px;
  align-items: start;
}
.card {
  background: rgba(255, 255, 255, .92);
  border: 1px solid var(--line-2);
  border-radius: 14px;
  padding: 20px;
  margin: 0 0 18px;
  box-shadow: 0 8px 28px rgba(18, 65, 95, .05);
}
.card h2 {
  margin: 0 0 12px;
  color: var(--navy);
  font-size: 20px;
  letter-spacing: 0;
}
.question {
  color: #15354e;
  font-size: 19px;
  font-weight: 750;
  margin: 0 0 10px;
}
.label {
  display: block;
  margin: 14px 0 5px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .05em;
  text-transform: uppercase;
}
.verdicts {
  display: grid;
  gap: 10px;
}
.verdict {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-2);
}
.verdict:last-child { border-bottom: 0; }
.score { font-weight: 800; color: var(--sky); }
.score.medium { color: var(--amber); }
.score.high { color: var(--green); }
.score.low { color: var(--red); }
ul { margin: 8px 0 0; padding-left: 20px; }
li { margin: 5px 0; }
.tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.tag {
  display: inline-flex;
  padding: 5px 10px;
  border-radius: 999px;
  background: #e7f6ff;
  border: 1px solid #bee6ff;
  color: #14567d;
  font-size: 13px;
  font-weight: 650;
}
table {
  width: 100%;
  border-collapse: collapse;
  overflow: hidden;
  border-radius: 12px;
  border: 1px solid var(--line-2);
}
th, td {
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid var(--line-2);
  font-size: 14px;
}
th { background: #eaf7ff; color: #19445f; font-size: 13px; }
tr:last-child td { border-bottom: 0; }
.appendix { margin-top: 18px; }
.small { color: var(--muted); font-size: 13px; }
.topnav { margin: 0 0 14px; }
@media (max-width: 860px) {
  main { padding: 18px 14px 44px; }
  .hero { padding: 24px; border-radius: 14px; }
  .metrics, .section-grid { grid-template-columns: 1fr; }
}
"""


STATUS_LABELS = {
    "resolved": "已确认",
    "ambiguous": "有歧义",
    "unresolved": "未解析",
    "train": "训练样例",
    "test": "测试样例",
}


def _status_label(value: str) -> str:
    return STATUS_LABELS.get(value, value)


def _fallback(value: str, fallback: str = "待复核") -> str:
    value = value.strip() if value else ""
    return escape(value or fallback)


def _items(values: list[str], fallback: str = "待复核") -> str:
    values = [value for value in values if value]
    if not values:
        return f'<p class="small">{escape(fallback)}</p>'
    return "<ul>" + "".join(f"<li>{escape(value)}</li>" for value in values[:6]) + "</ul>"


def _tags(values: list[str]) -> str:
    values = [value for value in values if value]
    if not values:
        return '<span class="small">待复核</span>'
    return '<div class="tags">' + "".join(f'<span class="tag">{escape(value)}</span>' for value in values[:12]) + "</div>"


def _artifact_chips(profile: BenchProfile) -> str:
    chips = []
    for kind, url in profile.artifacts.items():
        if url:
            chips.append(f'<a class="chip" href="{escape(url)}">{escape(kind)}</a>')
    if not chips:
        chips.append('<span class="chip">来源待复核</span>')
    return "".join(chips)


def _verdict(label: str, value: str, level: str = "") -> str:
    return f'<div class="verdict"><span>{escape(label)}</span><span class="score {escape(level)}">{escape(value)}</span></div>'


def _infer_verdicts(profile: BenchProfile) -> list[tuple[str, str, str]]:
    has_data = bool(profile.artifacts.get("dataset") or "public" in profile.data_access.lower())
    has_leaderboard = bool(profile.artifacts.get("leaderboard"))
    has_rubric = bool(profile.paper_analysis.rubric_and_scoring.metrics or profile.evaluation_method)
    realism = "高" if any(tag in profile.capability_tags for tag in ["real_world_work", "long_horizon_agent"]) else "中"
    return [
        ("使用价值", "高" if profile.status == "resolved" else "待复核", "high" if profile.status == "resolved" else "medium"),
        ("任务真实度", realism, "high" if realism == "高" else "medium"),
        ("可复现性", "中" if has_data else "待复核", "medium"),
        ("评分清晰度", "中" if has_rubric else "待复核", "medium"),
        ("榜单状态", "有榜单" if has_leaderboard else "待复核", "high" if has_leaderboard else "medium"),
    ]


def _brief_data(profile: BenchProfile, language: str) -> dict:
    localized = profile.localized_brief
    if localized.language == language and any(
        [
            localized.core_question,
            localized.motivation,
            localized.capability_summary,
            localized.model_results_summary,
        ]
    ):
        return asdict(localized)
    return get_localized_brief(profile.slug, language=language)


def _status_copy(status: str) -> tuple[str, str]:
    if status == "formal":
        return (
            "正式分析报告",
            "本页由 pipeline 生成；中文正文来自 localized_brief，底部保留英文 source、artifact 和 evidence，方便追溯与复核。",
        )
    return (
        "原型样例",
        "本页用于验证 Research Brief 的视觉风格与信息结构；主体中文内容来自 seed profile / 人工整理，尚未代表完整自动抽取结果。底部保留英文 source / artifact / evidence，方便追溯。",
    )


def render_brief(profile: BenchProfile, language: str = "zh-CN") -> str:
    analysis = profile.paper_analysis
    design = analysis.benchmark_design
    scoring = analysis.rubric_and_scoring
    brief = _brief_data(profile, language=language)
    status_label, status_text = _status_copy(brief.get("status", "prototype"))
    one_liner = brief.get("one_liner") or profile.evaluates
    core_question = brief.get("core_question") or analysis.core_question
    motivation = brief.get("motivation") or analysis.motivation
    gap_claimed = brief.get("gap_claimed") or analysis.gap_claimed
    capability_summary = brief.get("capability_summary") or profile.evaluates
    benchmark_design_summary = brief.get("benchmark_design_summary") or design.task_construction or profile.task_format
    scoring_summary = brief.get("scoring_summary") or scoring.scoring_protocol or profile.evaluation_method
    model_results_summary = brief.get("model_results_summary") or analysis.model_results_summary
    main_findings = brief.get("main_findings") or analysis.main_findings
    conclusions = brief.get("conclusions") or analysis.conclusions
    failure_modes = brief.get("failure_modes") or analysis.failure_modes
    reproducibility_notes = brief.get("reproducibility_notes") or analysis.reliability_notes or profile.reliability_notes
    capability_tags = [display_tag(tag) for tag in profile.capability_tags]
    capability_tags.extend(brief.get("evaluated_capabilities") or analysis.evaluated_capabilities)
    source_rows = "".join(
        f"<tr><td>{escape(source.type)}</td><td><a href=\"{escape(source.url)}\">{escape(source.title)}</a></td><td>{escape(source.note)}</td></tr>"
        for source in profile.sources
    ) or '<tr><td colspan="3" class="small">暂无来源，待复核</td></tr>'
    artifact_rows = "".join(
        f"<tr><td>{escape(kind)}</td><td><a href=\"{escape(url)}\">{escape(url)}</a></td></tr>"
        for kind, url in profile.artifacts.items()
        if url
    ) or '<tr><td colspan="2" class="small">暂无资源链接，待复核</td></tr>'
    evidence_rows = "".join(
        f"<tr><td>{escape(record.field)}</td><td>{escape(record.snippet or record.value)}</td><td><a href=\"{escape(record.source_url)}\">source</a></td></tr>"
        for record in analysis.evidence[:30]
    ) or '<tr><td colspan="3" class="small">暂无 evidence snippet，待复核</td></tr>'
    verdicts = "".join(_verdict(label, value, level) for label, value, level in _infer_verdicts(profile))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(profile.name)} | 研究简报</title>
  <style>{BRIEF_STYLE}</style>
</head>
<body>
<main>
  <p class="topnav"><a href="index.html">返回原型首页</a></p>
  <div class="statusbar">
    <b>{escape(status_label)}</b>
    <span>{escape(status_text)}</span>
  </div>
  <section class="hero">
    <div class="eyebrow">Bench 研究简报 · {escape(_status_label(profile.phase))} · {escape(_status_label(profile.status))}</div>
    <h1>{escape(profile.name)}</h1>
    <p class="lede">{_fallback(one_liner)}</p>
    <div class="chips">{_artifact_chips(profile)}</div>
  </section>

  <section class="metrics">
    <div class="metric"><b>{escape(profile.dataset_size or "待复核")}</b><span>数据规模</span></div>
    <div class="metric"><b>{escape(profile.year or "待复核")}</b><span>发布时间</span></div>
    <div class="metric"><b>{profile.confidence:.2f}</b><span>画像置信度</span></div>
    <div class="metric"><b>{len(profile.sources)}</b><span>已记录来源</span></div>
  </section>

  <section class="section-grid">
    <div>
      <article class="card">
        <h2>执行摘要</h2>
        <p class="question">{_fallback(core_question, profile.evaluates)}</p>
        <span class="label">核心动机</span>
        <p>{_fallback(motivation, profile.evaluates)}</p>
        <span class="label">要解决的缺口</span>
        <p>{_fallback(gap_claimed, profile.task_format)}</p>
      </article>

      <article class="card">
        <h2>能力评估定位</h2>
        <p>{_fallback(capability_summary)}</p>
        {_tags(capability_tags)}
      </article>

      <article class="card">
        <h2>Benchmark 设计</h2>
        <span class="label">数据来源</span>
        <p>{_fallback(design.data_source, profile.data_access)}</p>
        <span class="label">任务构造</span>
        <p>{_fallback(benchmark_design_summary, profile.task_format)}</p>
        <span class="label">任务类型</span>
        {_tags(design.task_types or profile.task_categories)}
        <span class="label">工具 / 环境</span>
        {_tags(design.tools_or_environment)}
      </article>

      <article class="card">
        <h2>评分与 Rubric</h2>
        <span class="label">Gold 定义</span>
        <p>{_fallback(scoring.gold_definition)}</p>
        <span class="label">评分协议</span>
        <p>{_fallback(scoring_summary, profile.evaluation_method)}</p>
        <span class="label">指标</span>
        {_tags(scoring.metrics)}
        <span class="label">Rubric 维度</span>
        {_tags(scoring.rubric_dimensions)}
      </article>

      <article class="card">
        <h2>结果与结论</h2>
        <span class="label">模型结果概览</span>
        <p>{_fallback(model_results_summary)}</p>
        <span class="label">主要发现</span>
        {_items(main_findings)}
        <span class="label">论文结论</span>
        {_items(conclusions)}
      </article>
    </div>

    <aside>
      <article class="card">
        <h2>快速判断</h2>
        <div class="verdicts">{verdicts}</div>
      </article>

      <article class="card">
        <h2>失败模式</h2>
        {_items(failure_modes)}
      </article>

      <article class="card">
        <h2>复现性备注</h2>
        {_items(reproducibility_notes)}
      </article>

      <article class="card">
        <h2>身份信息</h2>
        <span class="label">发布机构</span>
        <p>{_fallback(profile.organization)}</p>
        <span class="label">别名</span>
        {_tags(profile.aliases)}
        <span class="label">领域</span>
        {_tags(profile.domain)}
      </article>
    </aside>
  </section>

  <section class="appendix card">
    <h2>证据附录</h2>
    <span class="label">资源链接</span>
    <table><thead><tr><th>类型</th><th>URL</th></tr></thead><tbody>{artifact_rows}</tbody></table>
    <span class="label">来源</span>
    <table><thead><tr><th>类型</th><th>来源</th><th>备注</th></tr></thead><tbody>{source_rows}</tbody></table>
    <span class="label">Evidence snippets</span>
    <table><thead><tr><th>字段</th><th>Snippet</th><th>来源</th></tr></thead><tbody>{evidence_rows}</tbody></table>
  </section>
</main>
</body>
</html>"""


def render_brief_index(profiles: list[BenchProfile], language: str = "zh-CN") -> str:
    cards = []
    is_formal = any(profile.localized_brief.status == "formal" for profile in profiles)
    for profile in profiles:
        brief = _brief_data(profile, language=language)
        summary = brief.get("one_liner") or profile.evaluates
        cards.append(
            f"""<article class="card">
  <h2><a href="{escape(profile.slug)}.html">{escape(profile.name)}</a></h2>
  <p>{escape(summary)}</p>
  <div class="tags"><span class="tag">{escape(profile.status)}</span><span class="tag">{profile.confidence:.2f}</span></div>
</article>"""
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>研究简报原型</title>
  <style>{BRIEF_STYLE}</style>
</head>
<body>
<main>
  <section class="hero">
    <div class="eyebrow">{'正式输出' if is_formal else '原型'} · 天蓝色研究简报</div>
    <h1>Bench 研究简报</h1>
    <p class="lede">{'由 pipeline 生成的中文 Bench 论文精读简报索引，重点是先结论、后证据，适合内部展示、复盘和导出。' if is_formal else '低密度、浅色、天蓝色方向的 Bench 论文精读页原型。重点是先结论、后证据，适合内部展示和未来外部用户阅读。'}</p>
  </section>
  <div class="statusbar" style="margin-top:14px;">
    <b>{'正式分析报告索引' if is_formal else '视觉与结构原型'}</b>
    <span>{'当前页面由 batch pipeline 生成；中文展示层写入 localized_brief，英文证据和原始链接保留在各单页附录中。' if is_formal else '当前页面用于验证中文 Research Brief 的信息架构与视觉风格；内容来自 seed profile / 人工整理，不代表完整自动抽取结果。'}</span>
  </div>
  <section class="section-grid" style="margin-top:18px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
    {"".join(cards)}
  </section>
</main>
</body>
</html>"""


def write_brief(profile: BenchProfile, output_dir: Path, language: str = "zh-CN") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{profile.slug}.html"
    path.write_text(render_brief(profile, language=language), encoding="utf-8")
    return path


def write_brief_index(profiles: list[BenchProfile], output_dir: Path, language: str = "zh-CN") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "index.html"
    path.write_text(render_brief_index(profiles, language=language), encoding="utf-8")
    return path
