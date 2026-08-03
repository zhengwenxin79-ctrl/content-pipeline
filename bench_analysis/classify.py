from __future__ import annotations

from collections import OrderedDict


CAPABILITY_RULES = OrderedDict(
    [
        ("real_world_work", ["real-world", "professional", "economically", "occupation", "deliverable"]),
        ("long_horizon_agent", ["long-horizon", "multi-turn", "agent", "cross-application", "workflow"]),
        ("finance_research", ["finance", "financial", "sec", "filing", "market", "company"]),
        ("spreadsheet_workflow", ["spreadsheet", "workbook", "worksheet", "cell", "excel"]),
        ("search_and_retrieval", ["search", "retrieval", "source", "fetching", "lookup"]),
        ("tool_use", ["tool", "edgar", "files", "email", "calendar", "application"]),
        ("expert_rubric_eval", ["expert", "rubric", "grader", "blind", "compliance"]),
        ("multi_source_reasoning", ["multi-source", "conflicting", "evidence", "reconciliation"]),
    ]
)

DISPLAY_NAMES = {
    "real_world_work": "真实工作产出",
    "long_horizon_agent": "长流程 Agent",
    "finance_research": "金融研究/分析",
    "spreadsheet_workflow": "表格工作流",
    "search_and_retrieval": "搜索与检索",
    "tool_use": "工具使用",
    "expert_rubric_eval": "专家/ Rubric 评估",
    "multi_source_reasoning": "多源证据推理",
}


def infer_capability_tags(seed: dict) -> list[str]:
    text_parts = [
        seed.get("name", ""),
        seed.get("evaluates", ""),
        seed.get("task_format", ""),
        seed.get("evaluation_method", ""),
        " ".join(seed.get("domain", [])),
        " ".join(seed.get("task_categories", [])),
    ]
    haystack = " ".join(text_parts).lower()
    tags = []
    for key, keywords in CAPABILITY_RULES.items():
        if any(keyword in haystack for keyword in keywords):
            tags.append(key)
    return tags


def display_tag(tag: str) -> str:
    return DISPLAY_NAMES.get(tag, tag.replace("_", " "))
