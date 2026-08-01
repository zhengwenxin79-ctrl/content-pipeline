# P8 Benchmark 与推荐评估框架复盘

## 本阶段目标

把 P7 的 benchmark 脚手架升级为可复现、可扩展、可对比的推荐评估框架。核心目标不是马上追求最高分，而是让项目能回答一个更重要的问题：推荐系统到底比简单 baseline 好多少。

## 做了什么

### 1. 审核旧脚手架

旧 `scripts/evaluate_recommendations.py` 已经具备三个正确方向：

- 用 JSONL 表达真实研究方向。
- 用 `eval_judgments` 保存人工标注。
- 输出 `Precision@5` 和 `nDCG@10`。

但它存在两个会让指标偏乐观的问题：

- `Precision@5` 只除以已标注的 Top-K 项，未标注推荐被跳过，容易把不完整标注误读成高精度。
- `nDCG@10` 的 ideal ranking 只来自当前 Top-K 的 gains，而不是该 query 的完整标注集合，排序错误会被低估。

### 2. 新增 `benchmarks/` 模块

新增文件：

- `benchmarks/metrics.py`
- `benchmarks/datasets.py`
- `benchmarks/rankers.py`
- `benchmarks/run_benchmark.py`
- `benchmarks/README.md`

框架拆成四层：

- metrics：统一指标口径。
- datasets：加载 JSONL query、SQLite 文章、SQLite/JSONL 人工标注。
- rankers：实现 baseline 排序器。
- runner：运行 benchmark 并输出报告。

### 3. 支持三条 baseline

- `keyword`：BM25-like baseline，适合作为透明可解释对照组。
- `quality`：只看 `quality_score`，检验全局质量分是否能独立支撑推荐。
- `hybrid`：关键词相关性 + quality_score + freshness + artifact bonus，并对 negative keywords 做惩罚。

### 4. 完善指标

当前支持：

- `Precision@K`
- `Recall@K`
- `nDCG@K`
- `MRR`
- `IrrelevantRate@K`
- `JudgedRate@K`

标注映射：

- `core=3`
- `inspiring=2`
- `skim=1`
- `irrelevant=0`

其中 `core/inspiring` 计为相关。未标注 Top-K 在 Precision/nDCG 中按非相关处理，避免指标虚高；`IrrelevantRate@K` 只统计显式标注为 `irrelevant` 的项目。

### 5. 扩展 query 与 DB schema

`data/eval_queries.sample.jsonl` 增加：

- `expected_domains`
- `negative_keywords`

`db.py` 中 `eval_queries` 表也补充这两个字段，并提供旧库迁移。

### 6. 保留旧入口

保留 `scripts/evaluate_recommendations.py`，但它现在转调：

```bash
python3 -m benchmarks.run_benchmark
```

旧参数 `--top-k` 继续可用，映射为新报告里的 preview Top-K。

### 7. 输出报告

runner 默认输出：

- 控制台聚合表格。
- JSON 报告：便于后续做趋势追踪或 CI 对比。
- Markdown 报告：便于项目复盘、论文推荐系统实验记录和简历展示。

## 为什么这么做

推荐系统改造容易陷入“模型越来越复杂，但不知道有没有变好”。这个 benchmark 框架先固定评估协议，再允许后续加入 embedding recall、LLM rerank、learning-to-rank。这样每次算法升级都可以和 keyword、quality、hybrid baseline 做同口径对比。

## 优点

- 不依赖联网下载数据，sample 环境可直接运行。
- 不引入重型依赖，仅使用 Python 标准库。
- 指标口径比旧脚本更保守，更适合作为项目证据。
- query、judgment、ranker、report 解耦，便于接入 LitSearch / SciRepEval / 自建标注集。
- `JudgedRate@K` 能提醒当前指标可信度，避免小样本过度解读。

## 代价和不足

- BM25-like baseline 是轻量实现，不等同于完整搜索引擎 BM25。
- 还没有真实公开数据 adapter，只预留了 LitSearch 接口。
- 没有人工标注时，核心指标会输出 `N/A`，这是诚实结果，不是系统故障。
- hybrid 权重目前是经验权重，后续应通过标注集或用户反馈学习。

## 如何接入 LitSearch / SciRepEval

建议后续实现一个本地 adapter：

1. 把公开 topic 转成 `EvalQuery`。
2. 把 corpus 元数据转成 `Article`。
3. 把 qrel 等级映射到 `core/inspiring/skim/irrelevant`。
4. 复用 `benchmarks.run_benchmark` 的 ranker 与 metrics。
5. 将公开 benchmark 与自建 query set 分开报告，避免混淆产品真实场景和公开任务场景。

## 下一步推荐系统改造建议

1. embedding recall：补足关键词不重合但语义相关的论文，优先观察 `Recall@20`。
2. LLM rerank：对 Top 50-100 候选做精排，优先观察 `nDCG@10` 和 `IrrelevantRate@10`。
3. learning-to-rank：用 keyword、quality、freshness、artifact、source、用户反馈等特征训练轻量排序模型。
4. user feedback training set：将 `star/useful/read/irrelevant` 映射成弱监督标签，持续扩充自建 benchmark。
5. 标注规范化：每个 query 至少标注 20-50 篇候选，优先覆盖 Top 20 和明显负例。

## 简历/项目介绍表达

> 为 AI+X 跨领域论文推荐系统设计并实现可复现 benchmark 框架，支持自建 JSONL query set、人工相关性标注、BM25-like/quality/hybrid baseline 对比，并输出 Precision@5、Recall@20、nDCG@10、MRR、Irrelevant Rate 等指标报告，为 embedding recall、LLM rerank 和 learning-to-rank 迭代提供量化评估闭环。

## 复盘

这一阶段的价值是把“推荐看起来还不错”推进成“推荐质量可以被复现实验讨论”。短期内它会暴露数据和标注不足；长期看，这正是推荐系统从 demo 走向可信产品必须补上的地基。

