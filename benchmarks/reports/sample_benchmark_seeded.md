# AI+X Recommendation Benchmark Report

- generated_at: `2026-08-01T11:34:11.075373+00:00`
- queries: `3`
- articles: `6`
- judgments: `7`

## Aggregate Metrics

| Ranker | Precision@5 | Recall@20 | nDCG@10 | MRR | IrrelevantRate@10 | JudgedRate@10 |
|---|---:|---:|---:|---:|---:|---:|
| keyword | 0.267 | 1.000 | 1.000 | 1.000 | 0.167 | 0.389 |
| quality | 0.267 | 1.000 | 0.511 | 0.344 | 0.167 | 0.389 |
| hybrid | 0.267 | 1.000 | 1.000 | 1.000 | 0.167 | 0.389 |

## Next Recommendations

- 当前按 nDCG@10 看，最佳 baseline 是 `keyword`，后续改造应以它作为离线对照组。
- 增加 embedding recall：用研究方向召回语义相关但关键词不重合的论文，重点观察 Recall@20。
- 增加 LLM rerank：只重排 Top 50-100 候选，重点观察 nDCG@10 与 IrrelevantRate@10。
- 积累用户反馈训练集：把 useful/star/read/irrelevant 映射到偏好标签，为 learning-to-rank 准备特征。
- 上线前保留 quality_score 与 freshness 作为 guardrail，避免纯语义召回带来旧文或低质量噪音。

## Notes

- Labels: `core=3`, `inspiring=2`, `skim=1`, `irrelevant=0`.
- Precision/Recall/MRR treat `core` and `inspiring` as relevant.
- Unjudged Top-K items count as non-relevant for Precision/nDCG, while IrrelevantRate only counts explicit `irrelevant` labels.
- The LitSearch adapter is intentionally a local-file interface; the sample benchmark does not download external data.
