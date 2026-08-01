"""Baseline rankers for offline recommendation benchmarks."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from .datasets import Article, EvalQuery


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}|[\u4e00-\u9fff]{2,}")
ARTIFACT_PATTERNS = (
    "github.com", "gitlab.com", "code", "source code", "dataset", "data set",
    "benchmark", "leaderboard", "huggingface.co", "model weights", "supplementary",
)


@dataclass
class RankedArticle:
    article: Article
    score: float
    features: dict[str, float | int | str | list[str]]


def tokenize(text: str | None) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(text or "")]


def query_terms(query: EvalQuery) -> list[str]:
    terms: list[str] = []
    for part in [query.title, query.direction, *query.keywords, *query.expected_domains]:
        terms.extend(tokenize(part))
    seen = set()
    return [t for t in terms if not (t in seen or seen.add(t))]


def negative_terms(query: EvalQuery) -> set[str]:
    terms: set[str] = set()
    for part in query.negative_keywords:
        terms.update(tokenize(part))
    return terms


def article_text(article: Article) -> str:
    return " ".join([
        article.title,
        article.content[:2500],
        article.source_name,
        article.source,
        article.category,
        article.domain_tags,
        article.tags,
        article.url,
    ])


def _jsonish_terms(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            return [str(v) for v in loaded]
    except Exception:
        pass
    return [raw]


def artifact_bonus(article: Article) -> float:
    haystack = f"{article.url} {article.content[:1200]} {article.tags} {article.domain_tags}".lower()
    return 1.0 if any(pattern in haystack for pattern in ARTIFACT_PATTERNS) else 0.0


def freshness_score(article: Article, now: datetime | None = None) -> float:
    raw = article.published_at or article.fetched_at
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    current = now or datetime.now(timezone.utc)
    age_days = max((current - dt.astimezone(timezone.utc)).total_seconds() / 86400, 0.0)
    return math.exp(-age_days / 21.0)


class BaseRanker:
    name = "base"

    def rank(self, query: EvalQuery, articles: list[Article]) -> list[RankedArticle]:
        raise NotImplementedError


class KeywordBM25Ranker(BaseRanker):
    name = "keyword"

    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def rank(self, query: EvalQuery, articles: list[Article]) -> list[RankedArticle]:
        q_terms = query_terms(query)
        neg_terms = negative_terms(query)
        docs = [tokenize(article_text(article)) for article in articles]
        avg_len = sum(len(doc) for doc in docs) / len(docs) if docs else 0.0
        doc_freq = Counter()
        for doc in docs:
            doc_freq.update(set(doc))
        n_docs = max(len(docs), 1)
        ranked: list[RankedArticle] = []
        for article, doc in zip(articles, docs):
            tf = Counter(doc)
            score = 0.0
            overlaps: list[str] = []
            for term in q_terms:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                overlaps.append(term)
                idf = math.log(1 + (n_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                denom = freq + self.k1 * (1 - self.b + self.b * (len(doc) / max(avg_len, 1.0)))
                score += idf * (freq * (self.k1 + 1)) / denom
            neg_hits = sorted(neg_terms & set(doc))
            score -= len(neg_hits) * 1.5
            domain_hit = _domain_hit(query, article)
            if domain_hit:
                score += 0.75
            ranked.append(RankedArticle(article, score, {
                "bm25": round(score, 6),
                "overlap_terms": overlaps[:12],
                "negative_hits": neg_hits[:8],
                "domain_hit": int(domain_hit),
            }))
        return sorted(ranked, key=lambda r: (r.score, r.article.quality_score, r.article.fetched_at), reverse=True)


class QualityScoreRanker(BaseRanker):
    name = "quality"

    def rank(self, query: EvalQuery, articles: list[Article]) -> list[RankedArticle]:
        ranked = []
        for article in articles:
            score = float(article.quality_score or 0.0)
            ranked.append(RankedArticle(article, score, {
                "quality_score": round(score, 4),
                "freshness": round(freshness_score(article), 4),
            }))
        return sorted(ranked, key=lambda r: (r.score, r.article.fetched_at), reverse=True)


class HybridRanker(BaseRanker):
    name = "hybrid"

    def __init__(self):
        self.keyword_ranker = KeywordBM25Ranker()

    def rank(self, query: EvalQuery, articles: list[Article]) -> list[RankedArticle]:
        keyword_ranked = self.keyword_ranker.rank(query, articles)
        max_bm25 = max((max(r.score, 0.0) for r in keyword_ranked), default=0.0)
        by_id = {r.article.id: r for r in keyword_ranked}
        ranked: list[RankedArticle] = []
        for article in articles:
            kw = by_id[article.id]
            bm25_norm = max(kw.score, 0.0) / max_bm25 if max_bm25 > 0 else 0.0
            quality_norm = min(max(float(article.quality_score or 0.0) / 10.0, 0.0), 1.0)
            fresh = freshness_score(article)
            artifact = artifact_bonus(article)
            negative_penalty = min(len(kw.features.get("negative_hits", [])) * 0.12, 0.3)
            score = (
                0.55 * bm25_norm
                + 0.25 * quality_norm
                + 0.12 * fresh
                + 0.08 * artifact
                - negative_penalty
            )
            features = {
                **kw.features,
                "bm25_norm": round(bm25_norm, 4),
                "quality_norm": round(quality_norm, 4),
                "freshness": round(fresh, 4),
                "artifact_bonus": artifact,
                "negative_penalty": round(negative_penalty, 4),
            }
            ranked.append(RankedArticle(article, score, features))
        return sorted(ranked, key=lambda r: (r.score, r.article.quality_score, r.article.fetched_at), reverse=True)


def _domain_hit(query: EvalQuery, article: Article) -> bool:
    if not query.expected_domains:
        return False
    domain_text = " ".join([
        article.source_name,
        article.source,
        article.category,
        " ".join(_jsonish_terms(article.domain_tags)),
        article.title,
    ]).lower()
    return any(domain.lower() in domain_text for domain in query.expected_domains)


def get_rankers(names: list[str] | None = None) -> list[BaseRanker]:
    available: dict[str, BaseRanker] = {
        "keyword": KeywordBM25Ranker(),
        "quality": QualityScoreRanker(),
        "hybrid": HybridRanker(),
    }
    if not names or names == ["all"]:
        return list(available.values())
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"Unknown ranker(s): {', '.join(unknown)}. Available: {', '.join(available)}")
    return [available[name] for name in names]

