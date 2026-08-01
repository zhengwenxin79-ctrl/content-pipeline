# P7 Benchmark 脚手架复盘

## 做了什么

- 新增 `eval_queries` 和 `eval_judgments` 表，用于沉淀真实研究方向和人工相关性标注。
- 新增 `data/eval_queries.sample.jsonl`，包含 3 个 AI+X 样例研究方向：
  - 医学影像报告生成中的幻觉评估
  - 材料发现中的主动学习
  - 具身智能中的视觉语言动作模型
- 新增 `scripts/evaluate_recommendations.py`，实现一个透明、可解释的关键词重叠 baseline。
- 评估脚本输出：
  - 每个 query 的 Top-K 推荐
  - overlap terms
  - 如果已有人工标注，输出 `Precision@5` 和 `nDCG@10`
- 在临时空库 `/tmp/content_pipeline_eval_p7.db` 上验证迁移和脚本能正常运行。

## 为什么这么做

如果对外说“精准推荐”，必须有评估证据。公开 benchmark 不能完全覆盖“每日跨领域研究雷达”，所以项目需要自建小型 benchmark：用真实用户研究方向、真实每日候选池和人工标注来评估推荐质量。

## 优点

- 不依赖外部模型或 API，任何环境都能先跑起来。
- baseline 透明，方便和后续 embedding/LLM rerank 对比。
- `eval_judgments` 能逐步积累人工标注，形成项目自己的评估资产。
- 输出结果适合做产品复盘，也适合简历里展示“可量化推荐系统评估”。

## 代价和不足

- 当前只是关键词重叠 baseline，不代表最终推荐算法水平。
- 空库下没有文章和标注，指标会显示 `N/A`。
- 需要后续人工标注 `core / inspiring / skim / irrelevant`，才能得到可信指标。
- 还没有接入 Google Scholar Alert、arXiv RSS、Semantic Scholar 等外部基线对比。

## 建议标注规范

- `core`：与用户课题核心相关，值得精读。
- `inspiring`：不是核心方向，但有明显跨领域迁移价值。
- `skim`：可略读，信息有用但不重要。
- `irrelevant`：无关或噪音。

## 简历/项目介绍表达

> 为跨领域论文推荐系统设计自建 benchmark 框架：构造真实 AI+X 研究方向集合，建立人工相关性标注表，并实现 Precision@5、nDCG@10 等指标的可复现评估脚本，为后续 embedding 召回和 LLM rerank 提供量化对比基线。

## 复盘

P7 的价值在于把“感觉推荐不错”变成“可以被验证”。早期系统不需要马上拥有大规模 benchmark，但必须从第一批真实用户开始积累 query、候选、标注和反馈，这是推荐产品形成壁垒的起点。
