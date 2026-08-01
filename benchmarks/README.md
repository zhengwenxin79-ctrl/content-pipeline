# AI+X 交叉研究雷达 Benchmark

这个目录把推荐系统评估从一次性脚本升级为可复现 benchmark。

## 快速运行

```bash
python3 -m benchmarks.run_benchmark \
  --db corpus/corpus.db \
  --queries data/eval_queries.sample.jsonl
```

默认会运行三条 baseline：

- `keyword`：BM25-like 关键词相关性，透明、可解释。
- `quality`：按 `articles.quality_score` 排序，检验全局质量分本身是否足够。
- `hybrid`：关键词相关性 + `quality_score` + freshness + artifact bonus。

输出包括：

- 控制台聚合表格。
- `benchmarks/reports/*.json` 机器可读报告。
- `benchmarks/reports/*.md` 项目复盘报告。

## Query JSONL 格式

每行一个 query：

```json
{"id":"q_robotics_vla","title":"具身智能中的视觉语言动作模型","direction":"机器人操作中的 vision-language-action model、任务规划、泛化和真实世界评估","keywords":["vision-language-action","robotics"],"expected_domains":["Robotics","Machine Learning"],"negative_keywords":["industrial automation marketing"]}
```

必填字段：

- `id`
- `title`
- `direction`

推荐字段：

- `keywords`
- `expected_domains`
- `negative_keywords`

## 人工标注格式

SQLite 表 `eval_judgments` 和可选 JSONL 标注文件都使用同一套标签：

- `core`：核心相关，值得精读。
- `inspiring`：非核心但有明确迁移启发。
- `skim`：可略读，有弱相关信息。
- `irrelevant`：无关或噪音。

可选 JSONL：

```json
{"query_id":"q_robotics_vla","article_id":123,"label":"core","note":"直接讨论 VLA 机器人操作"}
```

运行时追加：

```bash
python3 -m benchmarks.run_benchmark \
  --judgments-jsonl data/my_eval_judgments.jsonl
```

## 指标口径

- `Precision@K`：Top-K 中 `core/inspiring` 的比例；未标注项按非相关处理，避免虚高。
- `Recall@K`：Top-K 命中的相关标注数 / 该 query 所有相关标注数。
- `nDCG@K`：`core=3`、`inspiring=2`、`skim=1`、`irrelevant=0`。
- `MRR`：第一个 `core/inspiring` 的倒数排名。
- `IrrelevantRate@K`：Top-K 中显式标为 `irrelevant` 的比例。
- `JudgedRate@K`：Top-K 已标注覆盖率，用来判断当前指标可信度。

## LitSearch / SciRepEval 接入预留

`benchmarks.datasets.LitSearchAdapter` 现在只定义接口，不下载数据。后续接入方式：

1. 本地准备 LitSearch/SciRepEval topic、corpus、qrel 文件。
2. 子类化 `LitSearchAdapter`，把 topic 转成 `EvalQuery`。
3. 把论文元数据转成 `Article`。
4. 把 qrel 等级映射到 `core/inspiring/skim/irrelevant`。
5. 复用 `run_benchmark.py` 的 ranker 和 metrics，对公开数据与自建 query set 做同口径对比。

## 下一步推荐系统改造

- embedding recall：提升语义召回，主要看 `Recall@20`。
- LLM rerank：对 Top 50-100 候选重排，主要看 `nDCG@10` 和 `IrrelevantRate@10`。
- learning-to-rank：把 keyword、quality、freshness、artifact、用户反馈等特征学习成排序模型。
- user feedback training set：把 `star/useful/read/irrelevant` 转为弱监督标签，持续扩充自建 benchmark。

