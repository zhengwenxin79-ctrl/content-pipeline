"""Ranking metrics for recommendation benchmarks.

The metric functions treat unjudged documents as non-relevant for ranking
quality. That is conservative and avoids the optimistic bias that appears when
Precision@K only divides by judged recommendations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


LABEL_GAINS = {
    "core": 3,
    "inspiring": 2,
    "skim": 1,
    "irrelevant": 0,
}

RELEVANT_LABELS = {"core", "inspiring"}
VALID_LABELS = set(LABEL_GAINS)


def relevance_gain(label: str | None) -> int:
    return LABEL_GAINS.get((label or "").strip().lower(), 0)


def is_relevant(label: str | None) -> bool:
    return (label or "").strip().lower() in RELEVANT_LABELS


def _top_ids(ranked_ids: Iterable[int], k: int) -> list[int]:
    return list(ranked_ids)[: max(k, 0)]


def precision_at(ranked_ids: Iterable[int], judgments: dict[int, str], k: int) -> float | None:
    top = _top_ids(ranked_ids, k)
    if not top:
        return None
    hits = sum(1 for aid in top if is_relevant(judgments.get(aid)))
    return hits / len(top)


def recall_at(ranked_ids: Iterable[int], judgments: dict[int, str], k: int) -> float | None:
    relevant_total = sum(1 for label in judgments.values() if is_relevant(label))
    if relevant_total == 0:
        return None
    top = _top_ids(ranked_ids, k)
    hits = sum(1 for aid in top if is_relevant(judgments.get(aid)))
    return hits / relevant_total


def ndcg_at(ranked_ids: Iterable[int], judgments: dict[int, str], k: int) -> float | None:
    if not judgments:
        return None
    top = _top_ids(ranked_ids, k)
    gains = [relevance_gain(judgments.get(aid)) for aid in top]
    judged_gains = [relevance_gain(label) for label in judgments.values()]
    ideal = sorted(judged_gains, reverse=True)[:k]
    if not ideal or sum(ideal) == 0:
        return None
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else None


def mrr(ranked_ids: Iterable[int], judgments: dict[int, str]) -> float | None:
    if not any(is_relevant(label) for label in judgments.values()):
        return None
    for idx, aid in enumerate(ranked_ids, start=1):
        if is_relevant(judgments.get(aid)):
            return 1 / idx
    return 0.0


def irrelevant_rate_at(ranked_ids: Iterable[int], judgments: dict[int, str], k: int) -> float | None:
    top = _top_ids(ranked_ids, k)
    if not top:
        return None
    explicit_irrelevant = sum(
        1 for aid in top if (judgments.get(aid) or "").strip().lower() == "irrelevant"
    )
    return explicit_irrelevant / len(top)


def judged_rate_at(ranked_ids: Iterable[int], judgments: dict[int, str], k: int) -> float | None:
    top = _top_ids(ranked_ids, k)
    if not top:
        return None
    judged = sum(1 for aid in top if aid in judgments)
    return judged / len(top)


@dataclass(frozen=True)
class MetricConfig:
    precision_k: int = 5
    recall_k: int = 20
    ndcg_k: int = 10
    irrelevant_k: int = 10
    judged_k: int = 10


def evaluate_ranking(
    ranked_ids: Iterable[int],
    judgments: dict[int, str],
    config: MetricConfig | None = None,
) -> dict[str, float | None]:
    cfg = config or MetricConfig()
    ids = list(ranked_ids)
    return {
        f"Precision@{cfg.precision_k}": precision_at(ids, judgments, cfg.precision_k),
        f"Recall@{cfg.recall_k}": recall_at(ids, judgments, cfg.recall_k),
        f"nDCG@{cfg.ndcg_k}": ndcg_at(ids, judgments, cfg.ndcg_k),
        "MRR": mrr(ids, judgments),
        f"IrrelevantRate@{cfg.irrelevant_k}": irrelevant_rate_at(ids, judgments, cfg.irrelevant_k),
        f"JudgedRate@{cfg.judged_k}": judged_rate_at(ids, judgments, cfg.judged_k),
    }


def macro_average(metric_rows: Iterable[dict[str, float | None]]) -> dict[str, float | None]:
    rows = list(metric_rows)
    if not rows:
        return {}
    keys = rows[0].keys()
    result: dict[str, float | None] = {}
    for key in keys:
        vals = [row[key] for row in rows if row.get(key) is not None]
        result[key] = sum(vals) / len(vals) if vals else None
    return result

