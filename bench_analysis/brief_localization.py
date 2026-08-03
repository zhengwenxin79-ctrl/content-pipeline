from __future__ import annotations

from typing import Any

from .schema import BenchProfile, LocalizedBrief


ZH_BRIEF_OVERRIDES: dict[str, dict[str, Any]] = {
    "apex": {
        "language": "zh-CN",
        "status": "prototype",
        "one_liner": "APEX-Agents 关注真实专业服务场景中的长流程 Agent 能力，强调模型从模拟任务走向真实工作交付时仍存在明显落差。",
        "core_question": "前沿 AI Agent 是否能够完成具有经济价值、跨应用、长流程的专业服务工作？",
        "motivation": "传统 Bench 往往更像受控环境里的任务集合，难以反映投行、咨询、公司法等真实专业服务工作中的文件处理、工具使用、上下文管理和交付质量要求。",
        "gap_claimed": "APEX 要解决的是 sim-to-real gap：模型在简化评测中表现不错，并不等于能在真实工作环境中稳定完成专业交付。",
        "capability_summary": "主要评估长流程规划、跨工具操作、专业服务交付、文件理解、任务执行稳定性和真实工作产出质量。",
        "benchmark_design_summary": "任务被设计成跨应用专业工作环境，涉及文档、表格、PDF、邮件、聊天、日历等真实工作材料和工具。",
        "scoring_summary": "以任务成功和 rubrics 为核心，关注最终交付物是否满足专业工作要求，而不是只看短答案是否正确。",
        "model_results_summary": "现有模型在这类长流程专业服务任务上仍有明显失败空间，尤其是在工具链、上下文保持和细节检查方面。",
        "main_findings": [
            "真实专业服务任务比短问答或单步工具调用更能暴露 Agent 的能力短板。",
            "长流程任务中，模型不仅要懂专业知识，还要稳定管理文件、工具和中间状态。",
            "APEX 更适合作为评估真实工作可用性的 Bench，而不是单纯知识能力测试。",
        ],
        "conclusions": [
            "Agent 评估需要走向真实工作流和真实交付物。",
            "模型在专业服务场景中的可靠性仍需通过长流程任务检验。",
        ],
        "failure_modes": [
            "长流程执行中遗漏关键步骤或中间状态。",
            "工具使用不稳定，跨应用操作失败。",
            "专业交付物看似完整但不满足 rubric 细节。",
            "文件、表格、PDF 等多材料之间的信息整合不足。",
        ],
        "reproducibility_notes": [
            "APEX 是一个 benchmark family，使用时需要明确具体版本，例如 APEX-Agents。",
            "任务环境复杂，复现实验需要关注工具、文件和运行环境配置。",
        ],
    },
    "onemillion-bench": {
        "language": "zh-CN",
        "status": "prototype",
        "one_liner": "OneMillion-Bench 关注高经济价值专家任务，强调考试能力不能代表专业工作能力。",
        "core_question": "语言 Agent 是否能够完成法律、金融、工业、医疗、自然科学等领域中具有高经济价值的专家级复杂任务？",
        "motivation": "许多模型在考试类 Bench 上表现很强，但真实专家工作需要权威资料检索、证据冲突处理、专业规范判断和可执行方案。",
        "gap_claimed": "它要填补的是从考试题能力到专家级真实任务能力之间的差距。",
        "capability_summary": "主要评估专家级推理、权威资料检索、多源证据整合、专业合规、长任务规划和复杂决策。",
        "benchmark_design_summary": "任务覆盖多个高门槛专业领域，强调真实约束、权威来源、领域规则和专家判断。",
        "scoring_summary": "采用 rubric 维度评价事实准确性、逻辑一致性、实际可行性和专业合规性。",
        "model_results_summary": "该 Bench 用于观察语言 Agent 在高价值专家任务中的能力边界，而不是只比较通用问答分数。",
        "main_findings": [
            "专家任务需要的不只是知识回忆，还包括证据选择、规则理解和方案约束。",
            "模型在跨来源冲突处理和专业合规判断上仍可能不稳定。",
            "高价值任务 Bench 对模型能力差异更敏感。",
        ],
        "conclusions": [
            "未来 Agent 评估应更多关注经济价值和专家工作质量。",
            "单纯考试型高分不足以证明模型能够承担专业责任。",
        ],
        "failure_modes": [
            "引用不权威或证据链不完整。",
            "忽略领域约束、合规要求或专业边界。",
            "面对冲突资料时无法做可靠判断。",
            "生成的方案理论上通顺但实际不可执行。",
        ],
        "reproducibility_notes": [
            "需要确认代码和数据开放状态。",
            "专家级任务评分依赖 rubric 质量和评审一致性。",
        ],
    },
    "spreadsheetbench-v2": {
        "language": "zh-CN",
        "status": "prototype",
        "one_liner": "SpreadsheetBench v2 从单步表格操作转向端到端业务表格工作流，测试 Agent 是否真的能完成 spreadsheet 交付。",
        "core_question": "Agent 是否能够完成真实业务场景中的端到端电子表格工作流，而不只是写一个公式或修改一个单元格？",
        "motivation": "旧表格 Bench 常聚焦单步公式、局部单元格或静态问答，无法覆盖真实业务工作中跨 sheet、长流程、debug 和可视化的复杂性。",
        "gap_claimed": "它要解决的是单步 spreadsheet 操作与完整业务 spreadsheet workflow 之间的评测缺口。",
        "capability_summary": "主要评估多 sheet 推理、表格生成、公式修改、错误调试、图表生成、长流程工具使用和 workbook 状态跟踪。",
        "benchmark_design_summary": "任务以多工作表 workbook 为核心，要求 Agent 在业务场景下完成生成、debug、visualization 等完整流程。",
        "scoring_summary": "通过任务完成情况、单元格修改正确性和输出 workbook 状态来评分，强调端到端结果而非单点答案。",
        "model_results_summary": "现有 Agent 在真实 spreadsheet workflow 上仍有明显困难，特别是长流程、多 sheet 状态保持和细粒度修改。",
        "main_findings": [
            "端到端表格工作流比单步表格题更能暴露 Agent 的实际能力。",
            "debug、visualization 和 generation 对模型提出了不同类型的要求。",
            "长 workbook 状态跟踪和细粒度单元格修改是主要难点。",
        ],
        "conclusions": [
            "Spreadsheet Agent 的评估应从单步操作升级到完整业务工作流。",
            "未来模型需要更好的状态管理、工具调用和输出校验能力。",
        ],
        "failure_modes": [
            "跨 sheet 推理时丢失上下文。",
            "单元格修改位置或公式逻辑错误。",
            "debug 过程无法定位根因。",
            "图表或可视化不符合任务要求。",
        ],
        "reproducibility_notes": [
            "公开项目、GitHub 和 Hugging Face dataset 有助于复现。",
            "仍需进一步核对论文表格中的模型结果和评分细节。",
        ],
    },
}


def get_localized_brief(slug: str, language: str = "zh-CN") -> dict[str, Any]:
    if language != "zh-CN":
        return {}
    return ZH_BRIEF_OVERRIDES.get(slug, {})


def _clean(value: str) -> str:
    return " ".join(value.split()) if value else ""


def _fallback(value: str, fallback: str = "待复核") -> str:
    value = _clean(value)
    return value or fallback


def _zh_statement(prefix: str, value: str, fallback: str = "待复核") -> str:
    value = _clean(value)
    if not value:
        return fallback
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        return value
    return f"{prefix}：{value}"


def _first_sentences(values: list[str], limit: int = 3) -> list[str]:
    return [_zh_statement("原文要点", value) for value in values if _clean(value)][:limit]


def generate_localized_brief(
    profile: BenchProfile,
    *,
    language: str = "zh-CN",
    status: str = "formal",
) -> LocalizedBrief:
    if language != "zh-CN":
        return LocalizedBrief(language=language, status=status)

    override = get_localized_brief(profile.slug, language=language)
    analysis = profile.paper_analysis
    design = analysis.benchmark_design
    scoring = analysis.rubric_and_scoring
    if override:
        data = dict(override)
        data["status"] = status if status == "formal" else data.get("status", status)
        evidence_refs = [record.source_url for record in analysis.evidence[:12] if record.source_url]
        data.setdefault("evidence_refs", evidence_refs)
        return LocalizedBrief(**data)

    capability_text = "、".join(analysis.evaluated_capabilities or profile.capability_tags)
    task_text = design.task_construction or profile.task_format
    scoring_text = scoring.scoring_protocol or profile.evaluation_method
    evidence_refs = []
    for record in analysis.evidence[:12]:
        if record.source_url and record.source_url not in evidence_refs:
            evidence_refs.append(record.source_url)

    return LocalizedBrief(
        language=language,
        status=status,
        one_liner=f"{profile.name} 主要关注：{_fallback(profile.evaluates)}",
        core_question=_zh_statement("该 Bench 关注的核心问题", analysis.core_question),
        motivation=_zh_statement("论文提出该 Bench 的动机", analysis.motivation),
        gap_claimed=_zh_statement("论文声称要补足的评测缺口", analysis.gap_claimed),
        capability_summary=f"该 Bench 主要评估{capability_text}。" if capability_text else _fallback(profile.evaluates),
        benchmark_design_summary=_zh_statement("Benchmark 设计方式", task_text),
        scoring_summary=_zh_statement("评分协议", scoring_text),
        model_results_summary=_zh_statement("模型结果概览", analysis.model_results_summary),
        main_findings=_first_sentences(analysis.main_findings),
        conclusions=_first_sentences(analysis.conclusions),
        failure_modes=_first_sentences(analysis.failure_modes, limit=4),
        reproducibility_notes=_first_sentences(analysis.reliability_notes or profile.reliability_notes, limit=4),
        evidence_refs=evidence_refs,
    )
