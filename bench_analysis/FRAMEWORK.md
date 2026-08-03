# Bench Analysis Pipeline Framework

## 目标

这个项目要做的不是单个 Bench 的人工总结，而是一个通用的 Bench 理解系统。

系统输入一个 Bench 名字，自动或半自动收集资料，并从固定维度生成结构化分析结果：

```text
Bench 名字
  -> 身份识别
  -> 能力理解
  -> 数据与任务分析
  -> 评测方法分析
  -> 模型结果整理
  -> JSON + HTML + 横向对比矩阵
```

核心问题是：

```text
这个 Bench 是什么？
它测什么？
它怎么测？
它的结果可靠吗？
它对我们有没有用？
```

## 五个评估维度

### 1. Bench 基本身份

这个维度解决“它是谁”的问题。

需要评估：

- 正式名称
- 别名和缩写
- 发布机构或作者
- 发布时间
- 论文地址
- 项目页
- GitHub
- Hugging Face / Dataset
- Leaderboard
- 是否存在撞名或歧义

示例输出：

```json
{
  "name": "FAB",
  "aliases": ["Finance Agent Benchmark", "FAB v2"],
  "organization": "Vals AI",
  "status": "resolved",
  "confidence": 0.91
}
```

对使用者的帮助：

- 防止把 Bench 认错。
- 处理 `FAB`、`APEX`、`IBFE` 这类高风险缩写。
- 帮用户快速定位论文、官网、代码和数据入口。

### 2. 能力评估范围

这个维度解决“它测什么能力”的问题。

需要评估：

- 是否测金融能力
- 是否测表格能力
- 是否测搜索检索
- 是否测长流程 Agent
- 是否测真实工作产出
- 是否测多步推理
- 是否测工具调用
- 是否测专家级任务

示例输出：

```json
{
  "capability_tags": [
    "finance_research",
    "search_and_retrieval",
    "tool_use",
    "long_horizon_agent"
  ]
}
```

对使用者的帮助：

- 不用读完整论文，就能判断这个 Bench 是否适合自己的模型或 Agent。
- 想测 Excel Agent，可以优先看 `SpreadsheetBench v2`。
- 想测金融搜索，可以优先看 `FinSearchComp`。
- 想测真实职业任务，可以优先看 `GDPval` 或 `APEX`。
- 想测金融分析 Agent，可以优先看 `FAB`。

### 3. 数据与任务设计

这个维度解决“题目长什么样”的问题。

需要评估：

- 任务数量
- 数据来源
- 任务样例
- 输入输出格式
- 是否多轮
- 是否需要工具
- 是否包含文件
- 是否包含表格、PDF、网页、图片等模态
- 数据是否公开
- 是否能够复现

示例输出：

```json
{
  "dataset_size": "321 tasks",
  "task_format": "multi-sheet spreadsheet workflow tasks",
  "task_categories": [
    "generation",
    "debugging",
    "visualization"
  ],
  "data_access": "public"
}
```

对使用者的帮助：

- 判断这个 Bench 是真实任务还是玩具任务。
- 判断自己能不能拿来跑实验。
- 判断任务难点来自哪里，比如长流程、工具调用、专业知识、数据检索或表格操作。

### 4. 评测方法与指标

这个维度解决“它怎么打分”的问题。

需要评估：

- 评测指标是什么
- 是 exact match、accuracy、win rate，还是 rubric score
- 是自动评分还是人工评分
- 是否使用 LLM judge
- 是否有专家标注
- 是否有 human baseline
- 是否能稳定复现
- 是否存在评分偏差风险

示例输出：

```json
{
  "evaluation_method": "Expert blind comparison against human deliverables",
  "metrics": ["win rate", "rubric score"],
  "judge_type": "human expert + automated grader"
}
```

对使用者的帮助：

- 避免只看分数，但不知道分数靠不靠谱。
- 判断评估是否公平、稳定、可复现。
- 如果用了 LLM judge，需要进一步确认 judge 模型、rubric 和人工校验机制。

### 5. 模型表现与使用价值

这个维度解决“这个 Bench 的结果有什么用”的问题。

需要评估：

- 哪些模型跑过
- 模型分数是多少
- SOTA 是谁
- 开源模型和闭源模型差距
- 有没有 human baseline
- 分数来自官网、论文还是第三方
- 结果是否更新
- 这个 Bench 当前还有没有区分度

示例输出：

```json
{
  "leaderboard": [
    {
      "model": "model_name",
      "score": "xx",
      "metric": "accuracy",
      "source": "leaderboard_url"
    }
  ],
  "usefulness": "Good for comparing finance agents with tool use"
}
```

对使用者的帮助：

- 快速知道当前模型水平。
- 判断这个 Bench 是否值得采用。
- 如果所有模型都接近满分，说明区分度可能不够。
- 如果只有少量模型跑过，说明结果参考价值有限。
- 如果 leaderboard 更新频繁，说明它适合追踪模型进展。

## 最终输出

### 1. 结构化 JSON

给机器用，方便后续入库、检索、对比和自动更新。

建议结构：

```json
{
  "name": "SpreadsheetBench v2",
  "identity": {},
  "capability": {},
  "data_tasks": {},
  "evaluation": {},
  "model_results": {},
  "reliability": {}
}
```

### 2. HTML 单页报告

给人看，适合快速阅读和展示。

报告内容包括：

- 这个 Bench 是什么
- 它评估什么能力
- 任务和数据长什么样
- 它怎么评分
- 哪些模型跑过
- 有哪些来源
- 有哪些不确定信息或缺失信息

### 3. 横向对比矩阵

把多个 Bench 放在同一张表里比较。

示例：

| Bench | 真实工作 | 金融 | 表格 | 搜索 | Agent | 数据公开 | 有榜单 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GDPval | yes | partial | partial | no | partial | partial | yes |
| FAB | yes | yes | partial | yes | yes | yes | yes |
| SpreadsheetBench v2 | yes | partial | yes | no | yes | yes | unknown |

这个矩阵是展示价值最高的部分，因为它说明系统不是在做单个 Bench 总结，而是在做通用 Bench 理解。

## 数据库管理

数据库管理不是 MVP 第一版必须完成的功能，但如果这个系统要从 demo 变成可持续使用的 Bench 分析系统，就需要数据库。

原因是 Bench 分析不是一次性文本生成，而是会不断积累、更新和比较的信息。

### 为什么需要数据库

#### 1. 保存 Bench 档案

每个 Bench 都会有一份结构化 profile：

```text
name
aliases
domain
capability_tags
task_format
evaluation_method
sources
confidence
```

如果只有少量 Bench，用 JSON 文件就够了。但 Bench 数量变多以后，查询、筛选、更新和横向比较会变得麻烦。数据库可以把这些档案统一管理起来。

#### 2. 保存来源记录

系统会抓取和整理很多来源：

```text
论文
官网
GitHub
Hugging Face
leaderboard
博客
arXiv
```

数据库可以记录：

```text
url
source_type
抓取时间
是否成功
可信度
对应哪个 Bench
```

这样后续就能知道每个结论来自哪里、什么时候抓取、来源是否可靠。

#### 3. 保存 raw 抓取状态

raw 文件本身可以放在本地文件夹里，比如：

```text
raw/project_page.html
raw/paper.pdf
raw/github_readme.md
raw/dataset_card.md
```

数据库负责记录这些 raw 文件的索引和状态：

```text
文件路径
抓取时间
文件 hash
解析状态
是否过期
对应来源 URL
```

这样可以支持复查、复现和增量更新。

#### 4. 保存模型分数

模型结果是最适合放进数据库的部分。

一个 Bench 可能有很多模型结果：

```text
model
score
metric
split
date
source_url
```

数据库可以支持后续查询：

```text
哪些 Bench 上 GPT 系列模型跑过？
哪个模型在金融 Bench 表现最好？
哪些 Bench 有 human baseline？
哪些 Bench 的 leaderboard 最近更新过？
```

#### 5. 支持横向比较

这个项目的重点不是单个 Bench，而是跨 Bench 对比。

数据库可以支持：

```text
找出所有金融类 Bench
找出所有表格类 Bench
找出所有数据公开的 Bench
找出所有有 leaderboard 的 Bench
找出所有适合测 Agent 的 Bench
```

#### 6. 支持更新和版本管理

Bench 信息会不断变化：

```text
leaderboard 更新
论文版本更新
GitHub 更新
dataset 开放状态变化
新模型分数加入
```

数据库可以记录历史版本，而不是每次生成报告都覆盖掉旧结果。

### 什么时候不需要数据库

如果只是最早的静态 MVP：

```text
7 个 Bench
手工 seed
生成 JSON
生成 HTML
```

那么暂时不需要数据库，`profile.json` 就够用。

### 什么时候需要数据库

当系统进入 batch-first 阶段，就应该增加数据库：

```text
Bench 数量超过 20 个
需要自动更新
需要搜索筛选
需要保存模型分数
需要记录每次抓取结果
需要比较不同时间的 leaderboard
需要保留 batch job 状态
需要知道每个 step 是否失败
```

### 推荐路线

第一阶段，静态 MVP：

```text
JSON 文件
```

第二阶段，batch-first 内部系统：

```text
SQLite / DuckDB
```

适合本地开发、单人使用、快速查询和批量分析。

第三阶段，产品化：

```text
PostgreSQL
```

适合多人使用、Web UI、持续更新和长期版本管理。

### 一句话概括

数据库是为了让 Bench 分析结果可以长期保存、更新、查询和横向比较；但最小版本先用 JSON 就够了。

## 对使用者的价值

这个系统最终要帮助使用者回答这些问题：

- 我应该用哪个 Bench 测自己的模型？
- 这个 Bench 测的是不是我关心的能力？
- 这个 Bench 的数据是否公开，能不能复现？
- 这个 Bench 的评分方式是否可信？
- 哪些模型已经跑过，当前表现如何？
- 这个 Bench 是否还有区分度？
- 这个 Bench 和其他 Bench 有什么区别？

## MVP 对应关系

当前 MVP 已经实现：

- Bench 基本身份的 seed catalog
- name / slug / aliases 解析
- 能力标签化
- JSON 输出
- HTML 单页报告
- 多 Bench 横向能力矩阵
- train/test Bench 划分
- IBFE 低置信度歧义处理
- 启发式 source discovery
- raw cache：网页、PDF、GitHub README
- 启发式字段抽取
- 候选模型分数抽取
- 多来源冲突记录
- SQLite job store
- batch job / bench run / step 状态记录

当前 MVP 暂未成熟：

- 论文表格的稳定结构化抽取
- 动态 leaderboard 抽取
- verified model result parser
- LLM-backed 字段级 evidence 抽取
- Web 工作台

## 稳态建设路线图

我们不把网页当作主项目，而是先搭一个可持续演进的 Bench 分析系统。网页只是入口和审阅界面；真正的核心资产是任务系统、证据数据、raw cache、reconciliation 逻辑和可导出的 HTML 报告。

系统应采用 batch-first、evidence-first 的路线：

```text
输入一批 Bench
  -> 创建 Batch Job
  -> 为每个 Bench 创建 Bench Run
  -> 按 step 记录状态
  -> 发现候选来源
  -> 抓取 raw artifacts
  -> 抽取字段级 evidence
  -> reconcile 成 profile
  -> 生成 HTML 报告与横向矩阵
  -> 保留 job / raw / JSON / HTML 以便复盘
```

### Milestone 1：数据模型和任务系统

目标：先把批量任务做扎实，让每一次分析都可追踪、可复盘、可恢复。

要实现：

- SQLite job store，记录 `job`、`bench_run`、`step`。
- 每个 batch job 有独立输出目录。
- 每个 Bench 独立运行，单个失败不影响整批任务。
- 每个 step 有状态：`pending`、`running`、`completed`、`failed`、`skipped`。
- CLI 和未来 Web UI 共用同一个 job runner。
- 每个 Bench 输出 `profile.json` 和 `report.html`。
- 每个 batch 输出横向对比 `index.html`。

验收标准：

- 输入 3-10 个 Bench 名，可以生成一个 job。
- job 可以在 SQLite 中查到。
- 每个 Bench 的执行状态和 step 状态可查询。
- 其中一个 Bench 失败时，其它 Bench 继续运行。
- 输出目录能看到 batch 总览页和每个 Bench 的报告。

### Milestone 2：可靠 source layer

目标：让联网收集资料可控、可复查，而不是依赖一次性搜索结果。

要实现：

- source discovery 按类型拆分：官方页、论文、GitHub、Hugging Face、leaderboard。
- raw cache 去重，避免重复下载。
- 每个 source 记录 relevance score、source type、抓取时间、抓取状态。
- 抓取失败记录错误，不中断整个 job。
- 对 403、timeout、PDF 解析失败、动态网页等情况做显式标记。

验收标准：

- 每个 resolved Bench 至少能稳定保留候选来源。
- GitHub README 和 Hugging Face dataset card 能稳定进入 raw cache。
- 失败 source 能在报告和数据库里看到原因。

### Milestone 3：抽取与 reconciliation

目标：让报告从“能生成”变成“可信”。

要实现：

- 字段级 evidence，每个核心字段尽量有来源 URL 和原文片段。
- 多来源冲突记录，不静默覆盖。
- source 权重规则：官方页/项目页 > 论文 > GitHub/HF > 第三方汇总。
- `ModelResult` schema：模型、指标、分数、来源、日期、置信度。
- leaderboard 和论文表格的专门 parser。

验收标准：

- 核心字段可以点回来源。
- 冲突字段出现在报告里。
- 模型分数区分 `candidate` 和 `verified`。

### Milestone 4：Web 工作台和导出

目标：让内部用户不用命令行也能运行 batch 分析和审阅报告。

要实现：

- New Batch 页面：输入多个 Bench 名和分析选项。
- Jobs 页面：查看历史任务。
- Job Detail 页面：查看每个 Bench 的 step 进度。
- Bench Report 页面：审阅五维度分析、sources、raw cache、evidence、conflicts。
- Export 页面：打开/下载 batch HTML、单 Bench HTML、JSON bundle。

验收标准：

- 网页能创建 batch job。
- 网页能看到运行进度和失败原因。
- 网页能打开导出的 HTML 报告。
- 输出报告可以直接分享或归档。

## 下一步实现建议

优先级从高到低：

1. 完成 Milestone 1：SQLite job store、batch runner、step 状态和独立输出目录。
2. 稳定 Milestone 2：source discovery、raw cache 和抓取失败记录。
3. 深化 Milestone 3：字段级 evidence、模型分数表格抽取和冲突处理。
4. 实现 Milestone 4：Web 工作台和 HTML/JSON 导出。

## 一句话概括

我们从身份、能力、数据任务、评测方法、模型结果五个维度分析每个 Bench。系统以 batch job 为核心，沉淀 raw source、字段级证据、结构化 JSON、单 Bench HTML 报告和多 Bench 横向对比矩阵。这样使用者可以快速判断一个 Bench 测什么、靠不靠谱、能不能复现、适不适合拿来评估自己的模型。
