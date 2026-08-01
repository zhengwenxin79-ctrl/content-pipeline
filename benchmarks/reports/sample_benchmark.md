# AI+X Recommendation Benchmark Report

- generated_at: `2026-08-01T11:33:38.307726+00:00`
- queries: `3`
- articles: `0`
- judgments: `0`

## Aggregate Metrics

| Ranker | Precision@5 | Recall@20 | nDCG@10 | MRR | IrrelevantRate@10 | JudgedRate@10 |
|---|---:|---:|---:|---:|---:|---:|
| keyword | N/A | N/A | N/A | N/A | N/A | N/A |
| quality | N/A | N/A | N/A | N/A | N/A | N/A |
| hybrid | N/A | N/A | N/A | N/A | N/A | N/A |

## Next Recommendations

- 优先积累 30-50 个真实 query，每个 query 标注 20-50 篇候选文章。
- 把当前 benchmark 作为标注验收工具，先看 JudgedRate@10，再看排序指标。
- 接入 embedding recall 后，用同一 query set 对比 keyword / hybrid / embedding 的 Recall@20。

## Notes

- Labels: `core=3`, `inspiring=2`, `skim=1`, `irrelevant=0`.
- Precision/Recall/MRR treat `core` and `inspiring` as relevant.
- Unjudged Top-K items count as non-relevant for Precision/nDCG, while IrrelevantRate only counts explicit `irrelevant` labels.
- The LitSearch adapter is intentionally a local-file interface; the sample benchmark does not download external data.
