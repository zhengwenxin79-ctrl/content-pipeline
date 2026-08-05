from __future__ import annotations

import json
from datetime import datetime

from .evidence_pack import build_evidence_pack
from .llm_client import LLMNotConfigured, complete_json
from .schema import BenchProfile, LLMAnalysis, LLMAnswer, RawDocument


FIELDS = [
    "core_question",
    "motivation",
    "evaluated_capability",
    "benchmark_design",
    "scoring",
    "model_results",
    "failure_modes",
    "reliability",
]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _prompt(profile: BenchProfile, evidence_pack: str) -> str:
    return f"""
你是一个 Benchmark 论文分析助手。请只基于下面的 evidence pack 做分析，不要编造未被证据支持的事实。

任务：为 Bench「{profile.name}」生成中文、论文级、可用于组会讨论的深度分析。

输出必须是严格 JSON，字段如下：
{{
  "one_sentence": "一句话概括这个 Bench 是什么、测什么、为什么重要",
  "core_question": {{"answer": "...", "evidence_refs": ["S1","D1","E2"], "confidence": 0.0}},
  "motivation": {{"answer": "...", "evidence_refs": [], "confidence": 0.0}},
  "evaluated_capability": {{"answer": "...", "evidence_refs": [], "confidence": 0.0}},
  "benchmark_design": {{"answer": "...", "evidence_refs": [], "confidence": 0.0}},
  "scoring": {{"answer": "...", "evidence_refs": [], "confidence": 0.0}},
  "model_results": {{"answer": "...", "evidence_refs": [], "confidence": 0.0}},
  "failure_modes": {{"answer": "...", "evidence_refs": [], "confidence": 0.0}},
  "reliability": {{"answer": "...", "evidence_refs": [], "confidence": 0.0}},
  "unsupported_claims": ["证据不足但值得人工复核的问题"]
}}

要求：
- 用中文回答。
- 每个 answer 控制在 2-5 句话。
- 如果证据不足，明确写“证据不足，需人工复核”。
- evidence_refs 只能引用 evidence pack 中出现的 S/E/R/D 编号。
- 不要输出 Markdown，不要输出 JSON 外的解释。

Evidence pack:
{evidence_pack}
""".strip()


def _answer(value: dict | None) -> LLMAnswer:
    if not isinstance(value, dict):
        return LLMAnswer(answer="证据不足，需人工复核。", confidence=0.0)
    refs = value.get("evidence_refs", [])
    if not isinstance(refs, list):
        refs = []
    return LLMAnswer(
        answer=str(value.get("answer", "")).strip(),
        evidence_refs=[str(ref) for ref in refs[:8]],
        confidence=float(value.get("confidence") or 0.0),
    )


def _fallback_analysis(profile: BenchProfile, error: str) -> LLMAnalysis:
    analysis = profile.paper_analysis
    design = analysis.benchmark_design
    scoring = analysis.rubric_and_scoring
    fallback = LLMAnalysis(
        status="fallback",
        provider="local",
        model="extractive-fallback",
        generated_at=_utc_now(),
        one_sentence=profile.localized_brief.one_liner or f"{profile.name} 主要关注：{profile.evaluates or '待复核'}",
        error=error,
    )
    fallback.core_question = LLMAnswer(answer=analysis.core_question or "证据不足，需人工复核。", confidence=0.45)
    fallback.motivation = LLMAnswer(answer=analysis.motivation or "证据不足，需人工复核。", confidence=0.45)
    capability = "、".join(analysis.evaluated_capabilities or profile.capability_tags)
    fallback.evaluated_capability = LLMAnswer(answer=capability or profile.evaluates or "证据不足，需人工复核。", confidence=0.45)
    fallback.benchmark_design = LLMAnswer(
        answer=design.task_construction or design.data_source or profile.task_format or "证据不足，需人工复核。",
        confidence=0.45,
    )
    fallback.scoring = LLMAnswer(
        answer=scoring.scoring_protocol or scoring.gold_definition or profile.evaluation_method or "证据不足，需人工复核。",
        confidence=0.45,
    )
    fallback.model_results = LLMAnswer(answer=analysis.model_results_summary or "模型结果解释需要进一步读取论文表格。", confidence=0.35)
    fallback.failure_modes = LLMAnswer(answer="；".join(analysis.failure_modes) or "证据不足，需人工复核。", confidence=0.4)
    fallback.reliability = LLMAnswer(
        answer="；".join(analysis.reliability_notes or profile.reliability_notes) or "证据不足，需人工复核。",
        confidence=0.4,
    )
    fallback.unsupported_claims = ["真实 LLM 调用失败，当前内容为规则抽取 fallback；请在 API 额度恢复后复跑。"]
    return fallback


def generate_llm_analysis(profile: BenchProfile, documents: list[RawDocument]) -> LLMAnalysis:
    evidence_pack = build_evidence_pack(profile, documents)
    if len(evidence_pack.strip()) < 500:
        return LLMAnalysis(
            status="skipped",
            generated_at=_utc_now(),
            error="Not enough evidence text for LLM analysis.",
        )
    try:
        response = complete_json(_prompt(profile, evidence_pack))
        payload = json.loads(response.text)
        analysis = LLMAnalysis(
            status="completed",
            provider=response.provider,
            model=response.model,
            generated_at=_utc_now(),
            one_sentence=str(payload.get("one_sentence", "")).strip(),
            unsupported_claims=[str(item) for item in payload.get("unsupported_claims", []) if item],
        )
        for field in FIELDS:
            setattr(analysis, field, _answer(payload.get(field)))
        return analysis
    except LLMNotConfigured as exc:
        return _fallback_analysis(profile, str(exc))
    except Exception as exc:
        return _fallback_analysis(profile, str(exc))
