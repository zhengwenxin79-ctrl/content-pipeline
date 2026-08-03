# Bench Analysis Project Progress

## 项目名称

Bench Analysis Pipeline

## 名称意义

`Bench` 指各类用于评估模型、Agent 或工作流能力的 benchmark。`Analysis Pipeline` 强调这个项目不是一次性写总结，而是一条可复用、可追踪、可复盘的分析管道。

项目希望把散落在论文、官网、GitHub、Hugging Face、leaderboard 和数据集页面里的信息，整理成统一的结构化分析结果，并导出人可读的 HTML 报告。

## 当前定位

当前阶段定位为内部 demo 和稳态产品雏形：

- 先给内部使用，帮助我们批量调研 Bench。
- 架构上预留未来给外部用户使用的可能。
- 核心动作是多 Bench batch 分析。
- 第一版支持 `with_web`，更接近真实系统。
- 主要输出是可导出的 HTML 报告，同时保留 JSON 和 raw cache。

## 核心目标

系统最终要回答五个问题：

1. 这个 Bench 是什么？
2. 它评估什么能力？
3. 它的数据和任务长什么样？
4. 它怎么评分，评分是否可信？
5. 它的模型结果和使用价值如何？

## 执行过程记录

### 2026-08-02：明确需求

初始需求来自一组 Bench 名称：

- APEX
- GDPval
- FAB
- SpreadsheetBench v2
- FinSearchComp
- OneMillion-Bench
- IBFE

讨论后明确：目标不是分析某一个 Bench，而是做一个通用 Bench 理解系统。

### 2026-08-02：完成最小 MVP

实现了第一版离线 MVP：

- seed catalog
- name / aliases 解析
- capability tagging
- `BenchProfile` JSON 输出
- 单 Bench HTML 报告
- 多 Bench 横向能力矩阵
- `IBFE` 低置信度歧义处理

### 2026-08-03：补充自动化管道模块

新增五个管道模块：

- `source_discovery.py`：自动发现候选来源。
- `fetch.py`：下载网页、PDF、GitHub README。
- `extract.py`：抽取候选字段。
- `results.py`：抽取候选模型分数。
- `reconcile.py`：合并来源、记录冲突和可靠性备注。

当前这些模块仍是启发式实现，适合 MVP 演示和后续迭代，不应视为最终抽取质量。

### 2026-08-03：确定稳态方向

讨论后决定不追求快速网页 demo，而是稳态建设：

- batch-first：核心动作是批量分析多个 Bench。
- evidence-first：报告字段需要来源、证据和置信度。
- job-first：每次分析需要可追踪 job 记录。
- export-first：主要输出是可导出的 HTML 报告。

### 2026-08-03：开始执行 Milestone 1

新增任务系统骨架：

- `job_store.py`：SQLite job store，记录 `jobs`、`bench_runs`、`job_steps`。
- `job_runner.py`：batch-first runner，按 step 执行每个 Bench。
- CLI 新增 `job-run`、`job-list`、`job-show`。
- 每个 job 输出到独立目录：`bench_analysis_outputs/jobs/{job_id}/`。
- 每个 Bench 独立执行，一个 Bench 的失败不会阻塞其它 Bench。

已验证：

```bash
python3 -m bench_analysis job-run "GDPval" "IBFE" "Unknown Bench" --no-web --output-dir /tmp/bench_analysis_jobs_smoke
python3 -m bench_analysis job-list --output-dir /tmp/bench_analysis_jobs_smoke
python3 -m bench_analysis job-show 20260803-112349-nicvd --output-dir /tmp/bench_analysis_jobs_smoke
python3 -m bench_analysis job-run "GDPval" --discovery-limit 4 --fetch-limit 2 --output-dir /tmp/bench_analysis_jobs_web_smoke
```

验证结果：

- 离线 batch job 成功生成 3 个 Bench 报告和 batch `index.html`。
- SQLite 中可以查询 job、bench_run 和 step 状态。
- `with_web` job 成功完成，并因 raw 抓取错误/字段冲突标记为 `completed_with_warnings`。
- `GDPval` web run 抽到 4 个 sources、2 个 raw document 记录、13 条候选字段、2 条候选模型分数、4 条冲突。

### 2026-08-03：执行 Milestone 1.1

新增 batch manifest 与导出摘要：

- `job_manifest.py`：从 SQLite job 记录和 `profile.json` 汇总 `job.json`。
- `job.json` 记录 job 状态、选项、bench run、step 状态、输出路径和 warning/error 计数。
- batch `index.html` 升级为 Job Summary + Bench Run Summary + 能力矩阵。
- CLI `job-run` / `job-show` 会显示 `job.json` 路径。

这一步的意义：

- 未来 Web UI 可以直接读取 `job.json` 做任务详情页。
- 导出的 HTML 不再只是矩阵，而是一个完整 batch report。
- 项目复盘时能快速看到哪些 Bench 有 raw 抓取失败、冲突和缺失字段。

已验证：

```bash
python3 -m bench_analysis job-run "GDPval" "FAB" "IBFE" --no-web --output-dir /tmp/bench_analysis_manifest_smoke2
python3 -m bench_analysis job-run "GDPval" --discovery-limit 4 --fetch-limit 2 --output-dir /tmp/bench_analysis_manifest_web_smoke
```

验证结果：

- 离线 job 生成 `job.json` 和增强版 `index.html`。
- `IBFE` 因缺失核心字段被标记为 `completed_with_warnings`。
- 联网 GDPval job 生成 `job.json`，统计到 4 个 sources、2 个 raw document、13 条候选字段、2 条候选模型分数、4 条冲突。
- 联网 GDPval job 因 1 个 raw fetch failure 和 4 个冲突，被标记为 `completed_with_warnings`。

### 2026-08-03：执行 Milestone 1.2

目标：稳定输出目录规范和 warning/error 语义。

新增与调整：

- `job_paths.py`：集中定义 job 输出路径和 SQLite 默认路径。
- `job.json` 增加 `manifest_version`、`generated_at`、`artifacts` 和 `output_layout`。
- manifest 同时保留绝对路径和相对 artifacts，方便未来 Web UI 与导出流程读取。
- warning/error 语义收敛：
  - `error`：bench run 或 pipeline step 失败。
  - `warning`：任务完成但需要复查，例如身份歧义、未解析身份、缺失核心字段、raw 抓取失败、字段冲突。
- `job.json` 增加 `warnings_by_type` 和 `errors_by_type`。
- batch `index.html` 增加 Warning / Error Types 区块。

这一步的意义：

- 后续 Web UI 可以稳定依赖 manifest schema。
- 黄色/红色状态语义更清楚，不会把可恢复的抓取失败和真正 pipeline 崩溃混在一起。
- 每个 batch report 都能解释 warning 来自哪里。

已验证：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis job-run "GDPval" "FAB" "IBFE" --no-web --output-dir /tmp/bench_analysis_m12_offline
python3 -m bench_analysis job-run "GDPval" --discovery-limit 4 --fetch-limit 2 --output-dir /tmp/bench_analysis_m12_web
python3 -m bench_analysis batch --output-dir /tmp/bench_analysis_m12_legacy
```

验证结果：

- 离线 job 生成 manifest version `1.1`。
- manifest 中包含 `artifacts` 和 `output_layout`。
- `IBFE` 的 `ambiguous_identity` 和 `missing_core_field` 被归为 warning，error 为 0。
- 联网 GDPval job 中 `raw_fetch_failure` 和 `field_conflict` 被归为 warning，error 为 0。
- 旧 `batch` 命令仍可正常生成 7 个静态报告。

### 2026-08-03：执行 Milestone 2.1

目标：稳定 source layer 和 raw cache 的基础状态。

新增与调整：

- `RawDocument` 增加 `source_url`、`cache_status`、`fetched_at`。
- `fetch.py` 增加 raw cache index：`raw/raw_index.json`。
- fetch 成功后写入 cache index；同一 raw 目录下再次抓取相同 source 可以返回 `cache_status=hit`。
- manifest 增加 raw source 状态：
  - `raw_success_count`
  - `raw_failures_count`
  - `raw_cache_hits_count`
  - `source_statuses`
- batch `index.html` 增加 Raw Fail 和 Cache Hit 列。

这一步的意义：

- source layer 不再只是“抓了/没抓”，而是能说明每个 raw document 从哪个 source 来、是否失败、是否来自缓存。
- 后续做 source 审阅、重跑、去重、增量更新时有结构化基础。

### 2026-08-03：执行 Milestone 3.1

目标：让报告结果具备更明确的复查状态。

新增与调整：

- manifest 为每个 Bench run 增加 `review_status`：
  - `ready`
  - `review_recommended`
  - `needs_human_review`
  - `failed_review`
- `review_status` 基于 error、warning 和缺失核心字段计算。
- batch `index.html` 增加 Review 列。

这一步的意义：

- 使用者不需要读完整 JSON，也能看到哪个 Bench 可以直接看，哪个需要人工复查。
- 为未来 Web UI 的筛选、排序和人工确认流程打底。

### 2026-08-03：执行 Milestone 4.1

目标：完成第一个可用的本地 Web UI 工作台。

新增与调整：

- `web_app.py`：基于 Python 标准库的本地 Web UI。
- CLI 新增：

```bash
python3 -m bench_analysis web --host 127.0.0.1 --port 8765
```

Web UI 支持：

- New Batch 页面：输入多个 Bench 名。
- `with_web` 开关。
- discovery/fetch limit 设置。
- Recent Jobs / Jobs 页面。
- Job Detail 页面：查看 job 状态、bench run 状态、step 状态。
- Artifact 链接：打开 batch `index.html`、`job.json`、单 Bench `report.html` 和 `profile.json`。
- running job 页面自动刷新。

已验证：

```bash
python3 -m bench_analysis web --host 127.0.0.1 --port 8765 --output-dir /tmp/bench_analysis_webui_smoke2
```

使用 HTTP 请求验证：

- 首页返回 200。
- POST `/jobs` 可以创建 batch job。
- Job detail 页面返回 200。
- `/artifact/{job_id}/job.json` 返回 200。
- `/artifact/{job_id}/index.html` 返回 200。
- `/artifact/{job_id}/gdpval/report.html` 返回 200。

验证 job：

```text
20260803-132700-d8znw
```

验证结果：

- `GDPval` 和 `IBFE` 均完成，状态为 `completed_with_warnings`。
- `GDPval` 的 review status 为 `review_recommended`。
- `IBFE` 的 review status 为 `needs_human_review`。
- job summary 中 warning/error 类型统计正常，error 为 0。

### 2026-08-03：执行 Paper Analysis v1

背景：

根据师兄的 Bench 论文笔记，我们把系统目标从“Bench 元信息卡片”升级为“Benchmark Paper Analysis Report”。报告不只回答“它是谁、测什么、数据多少”，还要回答论文级理解问题：

- 核心问题与动机是什么？
- 它主要考核 agent / 大模型哪方面能力？
- 它如何通过 benchmark 设计实现评估？
- rubric / gold / scoring protocol 是什么？
- 不同模型的主要结果和能力差异是什么？
- 论文结论是什么？
- 模型失败原因是什么？

新增与调整：

- `schema.py` 新增：
  - `BenchmarkDesign`
  - `RubricScoring`
  - `PaperAnalysis`
- `BenchProfile` 增加 `paper_analysis` 字段。
- `catalog.py` 为 3 个训练 Bench 补充 seed 级论文分析：
  - `GDPval`
  - `FAB`
  - `SpreadsheetBench v2`
- 单 Bench HTML 报告升级为论文分析模板：
  - Core Question & Motivation
  - Evaluated Capability
  - Benchmark / Task Design
  - Rubric, Gold & Scoring
  - Model Results
  - Main Findings & Conclusions
  - Failure Modes
  - Reliability / Reproducibility Notes
- batch `index.html` 增加：
  - Core Gap
  - Failure Modes
- manifest 增加 `missing_paper_analysis_field` warning。
- Web UI Job Detail 增加 Core Gap 列。

这一步的意义：

- HTML 报告更接近组会可展示的 Bench 论文阅读笔记。
- 系统从 metadata extraction 向 paper-note-style extraction 转型。
- 后续 LLM-backed extraction 可以围绕 `PaperAnalysis` schema 增强，而不用重新设计报告结构。

已验证：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis job-run "GDPval" "FAB" "SpreadsheetBench v2" "IBFE" --no-web --output-dir /tmp/bench_analysis_paper_v1
python3 -m bench_analysis batch --output-dir /tmp/bench_analysis_paper_legacy
python3 -m bench_analysis web --host 127.0.0.1 --port 8766 --output-dir /tmp/bench_analysis_paper_web
```

验证结果：

- `GDPval`、`FAB`、`SpreadsheetBench v2` 的 `paper_analysis.core_question` 和 `gap_claimed` 已进入 `profile.json`。
- 单 Bench HTML 中出现 `Core Question & Motivation`、`Benchmark / Task Design`、`Rubric, Gold & Scoring`、`Failure Modes`。
- batch `index.html` 中出现 `Core Gap` 和 `Failure Modes`。
- `IBFE` 因缺失论文分析字段被标记为 `needs_human_review`。
- Web UI 可以打开 job detail 和单 Bench paper-analysis 报告。

### 2026-08-03：设计天蓝色 Research Brief 视觉原型

根据用户反馈，高密度海报风阅读成本偏高，因此新增一个更清爽的 Research Brief 原型：

- 不模仿师兄的高密度三栏海报。
- 使用浅色背景和天蓝色视觉系统。
- 首页先展示 Executive Summary、Key Metrics、Quick Verdict。
- 正文采用主内容 + 右侧判断栏的两栏结构。
- Sources、Artifacts 等证据放到 Evidence Appendix。
- 新增 `brief_render.py`，与主 `report.html` 渲染器分离。
- CLI 新增 `brief-prototype`，默认生成 `APEX`、`OneMillion-Bench`、`SpreadsheetBench v2` 三个样板。

输出：

```text
bench_analysis_outputs/research_briefs/
  index.html
  apex.html
  onemillion-bench.html
  spreadsheetbench-v2.html
```

已验证：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis brief-prototype
```

### 2026-08-03：执行 Milestone 4.1：中文 Research Brief 输出设计

目标：

- 将天蓝色 Research Brief 从视觉样例升级成中文可读研究简报模板。
- 明确区分“原型样例”和正式 pipeline 输出。
- 不覆盖英文原始数据；新增中文展示层，底部仍保留英文 source / artifact / evidence 以便追溯。

新增与调整：

- `brief_localization.py`：新增中文展示层映射 `ZH_BRIEF_OVERRIDES`。
- 覆盖 3 个原型样例：
  - `APEX`
  - `OneMillion-Bench`
  - `SpreadsheetBench v2`
- `brief_render.py` 改成优先读取中文 localized brief；没有中文内容则 fallback 到英文 `paper_analysis` 或显示“待复核”。
- Research Brief 顶部增加状态条：
  - `原型样例`
  - 说明内容来自 seed profile / 人工整理，尚未代表完整自动抽取结果。
- Brief index 增加：
  - `视觉与结构原型`
  - 说明当前页面用于验证中文 Research Brief 的信息架构与视觉风格。
- CLI `brief-prototype` 增加 `--lang zh-CN`。

输出：

```text
bench_analysis_outputs/research_briefs/
  index.html
  apex.html
  onemillion-bench.html
  spreadsheetbench-v2.html
```

已验证：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis brief-prototype --lang zh-CN --output-dir /tmp/bench_research_briefs
python3 -m bench_analysis job-run "GDPval" "IBFE" --no-web --output-dir /tmp/brief_regression_job
python3 -m bench_analysis batch --output-dir /tmp/brief_regression_batch
```

验证结果：

- 成功生成 `apex.html`、`onemillion-bench.html`、`spreadsheetbench-v2.html` 和 `index.html`。
- 页面固定 UI 为中文。
- 三个样例主体为中文 Research Brief。
- 页面顶部明确标注“原型样例”。
- index 明确标注“视觉与结构原型”。
- source / artifact 保留英文原始链接。
- 无中文内容的字段显示“待复核”。
- 不影响现有 `job-run` 和 `batch` 命令。

## 当前 Milestone

Milestone 4：Web 工作台和导出。

目标：

- Web UI 可以创建 batch job。
- Web UI 可以查看 job/bench/step 状态。
- Web UI 可以打开导出的 HTML/JSON。
- Web UI 和 CLI 共用 job runner、SQLite job store 和 `job.json` manifest。

## 待复盘问题

- `with_web` 默认抓取范围应该多大，才能在速度和信息完整度之间平衡？
- source discovery 是否需要人工确认步骤？
- HTML 报告是单文件导出，还是一个目录 bundle？
- 什么时候引入 LLM-backed extraction？
- 模型结果何时从候选抽取升级为 verified leaderboard/parser 结果？

### 2026-08-03：执行阶段 A：PaperAnalysis 自动抽取 v1

目标：

- 把师兄的 Bench 论文笔记模板自动化。
- 从 paper/source text 中抽取：
  - core question
  - motivation
  - benchmark design
  - rubric / gold / scoring
  - model results
  - conclusions
  - failure modes
- 每个自动字段保留 source snippet，写入 `paper_analysis.evidence`。

新增与调整：

- 新增 `paper_analysis_extract.py`。
- 新增 job step：`extract_paper_analysis`。
- `pipeline.py` 和 `job_runner.py` 都接入 PaperAnalysis v1，避免 CLI 单 bench 和 batch job 分叉。
- `reconcile.py` 新增 PaperAnalysis merge 逻辑：
  - seed 中已有字段优先保留。
  - web 抽取字段只补空字段。
  - evidence snippets 追加保存。
  - reliability notes 标注 heuristic extraction，需要人工复核。
- `render.py` 增加 `PaperAnalysis Evidence` 表格。

阶段检查：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis job-run "GDPval" --discovery-limit 4 --fetch-limit 2 --output-dir /tmp/stage_abcd_with_web_20260803a
```

检查结果：

- job `20260803-164505-iktrv` 完成，状态为 `completed_with_warnings`。
- step 链路包含并完成：
  - `extract_paper_analysis:completed`
- `gdpval/profile.json` 中：
  - `paper_analysis.evidence` 数量为 13。
  - `localized_brief.status` 为 `formal`。
- 复盘：
  - v1 是规则抽取，适合先打通结构、证据和复核链路。
  - 抽取质量仍需要后续 LLM-backed extraction 和更强 section parser 增强。

### 2026-08-03：执行阶段 B：Leaderboard / Table Parser v1

目标：

- 让模型结果从“普通候选句子”升级为“表格优先、候选可复核”。
- 抽取字段包含：
  - model
  - metric
  - score
  - source_url
  - date
  - candidate / verified 状态

新增与调整：

- `schema.py` 扩展 `ModelResult`：
  - `date`
  - `source_type`
  - `extraction_method`
  - `verification_status`
- `results.py` 新增：
  - markdown table parser
  - whitespace text table parser
  - line regex fallback
  - extractor 层去重
- `reconcile.py` 去重时优先保留 `verified` table row。
- `job_manifest.py` 增加：
  - `verified_model_results_count`
  - `candidate_model_results_count`
- `render.py` 的模型结果表增加 Status 和 Method 列。

阶段检查：

```bash
python3 - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from bench_analysis.results import extract_model_results
from bench_analysis.schema import RawDocument
with TemporaryDirectory() as td:
    p=Path(td)/'leaderboard.txt'
    p.write_text('| Model | Pass@1 |\\n| --- | --- |\\n| GPT-4o | 72.5% |\\n| Claude 3.5 Sonnet | 0.681 |\\n', encoding='utf-8')
    results=extract_model_results([RawDocument(url='https://example.com/leaderboard', type='leaderboard', path=str(p), text_path=str(p), fetched_at='2026-08-03T00:00:00Z')])
    for r in results:
        print(r.model, r.metric, r.score, r.verification_status, r.extraction_method, f'{r.confidence:.2f}')
PY
```

检查结果：

- `GPT-4o score 72.5% verified markdown_table 0.84`
- `Claude 3.5 score 0.681 verified markdown_table 0.78`
- 修复了一个重要误抽问题：`Claude 3.5 Sonnet` 中的 `3.5` 不再被当成分数。

复盘：

- v1 已经支持 candidate vs verified 标注。
- 当前 verified 表示“来自 paper/leaderboard 的表格形态抽取”，还不是人工确认。
- 后续需要加入真正的 PDF table engine、HTML DOM table parser 和人工 verified 状态。

### 2026-08-03：执行阶段 C：中文 Brief 自动生成 v1

目标：

- 把人工中文 override 逐步升级为 pipeline 输出。
- 新增 `localized_brief` schema。
- 从 `paper_analysis` 自动生成中文展示层。
- 保留英文 evidence。
- 支持 `prototype` / `formal` 两种状态。

新增与调整：

- `schema.py` 新增 `LocalizedBrief`。
- `BenchProfile` 新增 `localized_brief`。
- `brief_localization.py` 新增 `generate_localized_brief()`：
  - 有中文 override 时优先使用人工样例。
  - 无 override 时，从 `paper_analysis` 生成中文框架表达。
  - evidence URL 写入 `evidence_refs`。
- `reconcile.py` 在合并结束后生成 `formal` localized brief。
- `job_runner.py` 在每个 job 自动输出：
  - `briefs/{bench_slug}.html`
  - `briefs/index.html`
- `brief_render.py` 支持：
  - `正式分析报告`
  - `原型样例`
  - evidence snippets 附录。

阶段检查：

```bash
python3 -m bench_analysis job-run "GDPval" "IBFE" --no-web --output-dir /tmp/stage_abcd_no_web_20260803a
```

检查结果：

- job `20260803-164426-ffda9` 完成，状态为 `completed_with_warnings`。
- 输出包含：
  - `briefs/gdpval.html`
  - `briefs/ibfe.html`
  - `briefs/index.html`
- `gdpval/profile.json` 中：
  - `localized_brief.status = formal`
  - `localized_brief.language = zh-CN`

复盘：

- v1 的中文 brief 已经成为 pipeline 正式输出。
- 对没有人工 override 的 bench，当前是中文框架 + 英文 evidence 原句，仍不是高质量中文翻译。
- 后续阶段需要引入 LLM 生成中文摘要，并要求每段绑定 evidence snippet。

### 2026-08-03：执行阶段 D：Web UI 产品化 v1

目标：

- 让内部成员可以稳定使用 Web UI 做 batch 分析、复盘和导出。

新增与调整：

- `web_app.py` 增加：
  - full batch rerun
  - rerun single Bench
  - source review
  - evidence review
  - export zip
  - history job search
  - report compare
  - job detail 更清晰的 action 区
- `job_manifest.py` 增加 brief artifact 路径：
  - `briefs/index.html`
  - `briefs/{bench_slug}.html`

阶段检查：

```bash
python3 -m bench_analysis web --host 127.0.0.1 --port 8767 --output-dir /tmp/stage_d_webui_20260803a
```

HTTP 验证：

- `GET /` 返回 200。
- `POST /jobs` 创建 job：
  - `20260803-164702-d3odv`
- `GET /jobs/20260803-164702-d3odv` 返回 200。
- `GET /jobs/20260803-164702-d3odv/sources` 返回 200。
- `GET /jobs/20260803-164702-d3odv/compare` 返回 200。
- `GET /artifact/20260803-164702-d3odv/export.zip` 返回 200，大小 24661 bytes。
- `GET /jobs?query=GDPval` 返回 200。
- `POST /jobs/20260803-164702-d3odv/rerun` 创建新 job：
  - `20260803-164723-0thwx`
- `POST /jobs/20260803-164702-d3odv/rerun-bench` 创建单 Bench job：
  - `20260803-164723-tefrr`

检查结果：

- 原 job、整批复跑 job、单 Bench 复跑 job 均完成，状态为 `completed_with_warnings`。
- `export.zip` 中包含：
  - `job.json`
  - `index.html`
  - `briefs/index.html`

复盘：

- Web UI 已从“能跑 batch”升级到“能复跑、复查、对比、导出”的内部 demo。
- 当前 source/evidence review 仍是只读页面。
- 下一步如果继续做稳，应增加人工确认状态、field-level accept/reject 和 confirmed source set。

### 2026-08-03：Web UI 主界面中文化

目标：

- 将 Web 工作台固定界面从英文切换为中文，和中文 Research Brief 的使用场景保持一致。

新增与调整：

- `web_app.py` 中文化：
  - 首页
  - 历史任务页
  - 任务详情页
  - 来源与证据复核页
  - 报告横向对比页
  - 错误页与表单校验文案
- 增加状态显示映射：
  - `completed` -> `已完成`
  - `completed_with_warnings` -> `完成，有警告`
  - `running` -> `运行中`
  - `failed` -> `失败`
- 增加步骤显示映射：
  - `discover_sources` -> `发现来源`
  - `fetch_raw` -> `抓取原始资料`
  - `extract_paper_analysis` -> `抽取论文分析`
  - `render_report` -> `生成报告`

已验证：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis web --host 127.0.0.1 --port 8768 --output-dir /tmp/webui_zh_smoke_20260803a
```

HTTP smoke test：

- 首页包含 `Bench 分析工作台`、`新建批量分析`、`最近任务`。
- 历史页包含 `历史任务`、`按任务 ID、状态或 Bench 名搜索`。
- 任务详情页包含 `Bench 运行结果`、`执行步骤`、`复跑整个批次`、`打开中文简报`。
- 来源复核页包含 `来源与证据复核`、`来源列表`、`原始资料抓取`、`论文分析证据`。
- 对比页包含 `报告横向对比`、`核心问题`、`评测缺口`、`失败模式`。

复盘：

- Web UI 固定文案已基本中文化。
- 底层枚举和 JSON 字段仍保留英文，避免破坏 pipeline 和 manifest。
- 报告内容中的英文 evidence/snippet 继续保留，用于追溯原文证据。

### 2026-08-03：Batch Report 静态页面中文化

目标：

- 将 `/artifact/{job_id}/index.html` 对应的批量导出报告中文化。
- 解决 Web 工作台已中文，但导出的 batch `index.html` 仍显示英文标题和表头的问题。

新增与调整：

- `render.py` 增加中文状态、复核建议、warning/error 类型映射。
- `render_job_index()` 中文化：
  - 页面标题：`Bench 批量分析报告`
  - 任务摘要：任务 ID、状态、Bench 数、警告、错误
  - 分析选项：联网发现资料、来源发现上限、抓取资料上限
  - 任务产物：任务清单 JSON、批量 HTML 报告、中文简报首页
  - Bench 运行摘要表头
  - 失败记录表头与空状态
- `render_index()` 的 overview 表头也同步中文化。

已验证：

```bash
python3 -m compileall bench_analysis
```

并重新生成当前 job 的静态 batch 报告：

```text
bench_analysis_outputs/jobs/20260803-205926-i7e78/index.html
```

HTTP smoke test：

- 页面包含 `Bench 批量分析报告`
- 页面包含 `警告 / 错误类型`
- 页面包含 `任务产物`
- 页面包含 `中文简报首页`
- 页面包含 `Bench 运行摘要`
- 页面包含 `失败记录`

复盘：

- 工作台页面和 batch 静态导出页现在都已中文化。
- 单 Bench `report.html` 里仍保留部分英文论文模板字段名和 evidence 原文，这是为了对齐 schema 与英文 source，可在下一阶段继续做“单 Bench 论文报告全中文化”。

### 2026-08-03：阶段 B 加强版：arXiv PDF 与 GDPval 模型结果抽取

目标：

- 解决 GDPval 报告中“候选模型分数为空”的问题。
- 对 arXiv 自动抓 PDF，而不是只抓 `/abs/` 摘要页。
- 用 PDF 文本布局抽取实验表。
- 对官方页面抓取失败增加 fallback。
- 对 GDPval 的 Elo / expert preference / win rate 类指标做专门适配。

新增与调整：

- `fetch.py`：
  - arXiv `/abs/{id}` 自动优先抓取 `https://arxiv.org/pdf/{id}`。
  - PDF 文本抽取优先使用系统 `pdftotext -layout`，尽量保留表格列结构。
  - OpenAI 官方页增加 reader fallback candidate。
- `results.py`：
  - 扩展模型名识别：`Nex-N2`、`NexForge`、`Claude Opus 4.1` 等。
  - 扩展指标识别：`Elo`、`expert preference`、`human preference`、`win rate`。
  - 新增 GDPval Elo 专门抽取模式。
  - 增加 Bench-aware 过滤：标题/URL 不匹配当前 Bench 的相关论文，只保留上下文明确写当前 Bench 的专门指标，避免把 Terminal-Bench 等无关表格抽进 GDPval。
  - 修复版本号误抽：`Claude Opus 4.1`、`fig. 8.5` 不再被当作模型分数。
- `job_runner.py` / `pipeline.py`：
  - `extract_model_results()` 传入当前 `bench_name`，让 parser 能做 Bench-aware filtering。
- `render.py`：
  - 空模型结果表提示改为“本次没有抽到结构化模型分数行”，明确不等于论文没有模型结果。

已验证：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis job-run "GDPval" --discovery-limit 6 --fetch-limit 4 --output-dir bench_analysis_outputs
```

验证 job：

```text
20260803-211117-pntgy
```

验证结果：

- GDPval raw cache 成功抓到：
  - OpenAI GDPval 官方页
  - `https://arxiv.org/pdf/2510.04374v1`
  - `https://arxiv.org/pdf/2607.14186v5`
  - `https://arxiv.org/pdf/2512.06196v1`
- `gdpval/profile.json` 中模型结果从 0 增加到 7：
  - 4 条 verified table rows：
    - `gpt-4o | 12.5%`
    - `o4-mini | 29.1%`
    - `o3 | 35.2%`
    - `gpt-5 | 39.0%`
  - 3 条 GDPval Elo candidate rows：
    - `Qwen3.5 | 1338 Elo`
    - `Qwen3.5 | 1585 Elo`
    - `Nex-N2 | 1585 Elo`
- 静态报告链接已验证候选模型分数表正常渲染：
  - `/artifact/20260803-211117-pntgy/gdpval/report.html`

复盘：

- 这一步证明“候选模型分数为空”不是 GDPval 没有结果，而是之前 pipeline 没抓 PDF / 没有 PDF 表格解析。
- 当前 PDF table parser 仍是文本布局级别，不是真正的结构化 PDF table engine。
- `verified` 仍表示“来自匹配 Bench 的 paper/leaderboard 表格形态抽取”，不是人工最终确认。
- 下一步可以继续做：
  - PDF table cell 级 parser。
  - 表头传播，让 `score` 自动改成更准确的 metric 名。
  - 人工 verified / rejected review workflow。

### 2026-08-03：Milestone 4.2：前端 UI 信息架构重排

背景：

- 用户反馈页面之间“有点杂乱，没有顺序”。
- 复盘后确认问题不在于缺功能，而在于页面角色和阅读路径不清晰：
  - 工作台、任务状态、批量报告、单 Bench 报告、中文简报、证据页混在一起。
  - 单 Bench report 把论文分析和 pipeline debug 放在同一主阅读流。
  - Batch index 更像状态表，而不是研究对比报告。

目标：

- 让系统阅读路径变成：

```text
工作台首页
  -> 任务控制台
  -> 批量对比报告
  -> 单 Bench 论文分析报告
  -> 证据与调试附录
```

新增与调整：

- `web_app.py`：
  - 首页新增清晰流程：
    - 输入 Bench
    - 运行 batch
    - 看批量对比
    - 深读单个 Bench
    - 复核证据
  - 首页表单旁新增“阅读顺序”说明。
  - Job Detail 改为“任务控制台”。
  - 任务控制台顶部新增“推荐下一步”：
    - 打开中文简报
    - 打开批量对比报告
    - 来源与证据复核
    - 报告横向对比
    - 导出 zip
    - 打开任务清单 JSON
  - 执行步骤改名为“执行日志”，明确普通阅读可以跳过。

- `render.py`：
  - 新增静态报告 UI 样式：
    - hero
    - toc
    - section-head
    - two-col
    - callout
    - appendix
  - Batch `index.html` 改为“Bench 批量对比报告”：
    - 本批次覆盖能力
    - Bench 横向对比
    - 复核队列
    - 任务产物
    - 失败记录
  - 单 Bench `report.html` 改为“单 Bench 论文分析报告”：
    - 核心问题与动机
    - 能力评估定位
    - Benchmark 怎么设计
    - Gold、Rubric 与评分协议
    - 模型结果与分数表
    - 主要发现与论文结论
    - 失败模式
    - 我们的使用判断
    - 证据与调试附录
  - 将 raw cache、自动抽取字段、冲突、legacy notes 下沉到附录。

已重新生成：

```text
bench_analysis_outputs/jobs/20260803-211117-pntgy/
bench_analysis_outputs/jobs/20260803-205926-i7e78/
```

已验证：

```bash
python3 -m compileall bench_analysis
python3 -m bench_analysis web --host 127.0.0.1 --port 8765
```

HTTP smoke test：

- `/jobs/20260803-211117-pntgy` 包含：
  - `任务控制台`
  - `推荐下一步`
  - `Bench 运行结果`
  - `执行日志`
- `/artifact/20260803-211117-pntgy/index.html` 包含：
  - `Bench 批量对比报告`
  - `本批次覆盖能力`
  - `Bench 横向对比`
  - `复核队列`
  - `任务产物`
  - `失败记录`
- `/artifact/20260803-211117-pntgy/gdpval/report.html` 包含：
  - `单 Bench 论文分析报告`
  - `核心问题与动机`
  - `能力评估定位`
  - `Benchmark 怎么设计`
  - `Gold、Rubric 与评分协议`
  - `模型结果与分数表`
  - `证据与调试附录`

复盘：

- 这一步没有新增抽取能力，而是让已有能力按自然研究流程呈现。
- 工作台负责“运行和入口”；batch report 负责“横向比较”；single report 负责“论文级理解”；附录负责“证据和调试”。
- 下一步可以继续做：
  - 单 Bench report 全中文 schema 标签。
  - Evidence snippet 与 seed/manual summary 的来源区分。
  - 可折叠附录和 field-level review 操作。
