# P12 新概念冒头信号精准度修复复盘

## 问题现象

线上“AI+X 近期冒头信号”出现了明显不相关的医学标题碎片，例如：

- `Weight Loss Older`
- `Loss Older Patients`
- `Older Patients Persistent`
- `Patients Persistent Atrial`
- `Due Glomerular Diseases`
- `Remote Multicomponent Rehabilitation`

这些内容来自普通临床医学论文，不是 AI+X 交叉研究概念，也不是值得作为产品亮点展示的“新概念”。

## 底层原因

这不是 DeepSeek API 缺失导致的识别不准。

原逻辑里，DeepSeek 只负责给已经选出的候选概念生成中文解释。真正决定“谁能成为新概念候选”的，是 `detect_emerging_concepts()` 里的规则：

- 从近 14 天文章标题里抽 2-gram / 3-gram；
- 和前 46 天基线比较；
- 如果最近重复出现且基线低，就计算 breakout 分；
- 再用 OpenAlex 按标题搜索做概念年龄校验。

问题在于候选生成前缺少两个门槛：

- 没有先判断文章是否属于 AI+X / AI 方法相关语料；
- 没有判断抽出来的 n-gram 是不是一个“方法/技术概念”，还是普通标题碎片。

因此普通 JAMA 临床论文只要标题重复，就会被误认为是“近期冒头概念”。

## 做了什么

- 新增文章级 AI+X 过滤：
  - AI / ML / CV / NLP / Robotics / Statistics & ML 等 `domain_tags` 可通过；
  - `arXiv cs.*`、`stat.ML`、`eess.IV`、`NEJM AI`、`Medical Image Analysis` 等来源可通过；
  - 标题、正文或标签中包含 AI/计算方法相关术语可通过。
- 新增短语级概念过滤：
  - 候选短语必须包含 AI/方法相关词，如 `foundation`、`multimodal`、`neural`、`segmentation`、`retrieval`、`embedding` 等；
  - 纯临床碎片，如 `older patients`、`weight loss`、`glomerular diseases` 会被过滤。
- 新增重叠候选去重：
  - 同一批代表论文抽出的多个高度重叠短语，只保留一个。
- 缓存版本升级：
  - 旧的 `emerging_concepts` 缓存会被自动跳过，避免线上继续展示旧误报。
- 前端文案从“近期冒头信号”改为“AI+X 近期冒头信号”，明确它先经过 AI/计算方法过滤。

## 优点

- 明显降低普通临床论文标题碎片进入“新概念”的概率。
- 用户看到的冒头信号更符合“AI+X 交叉研究雷达”的产品定位。
- 旧缓存自动失效，线上部署后无需手动清库也能重新计算。
- 规则仍然轻量，不依赖额外模型或新增 API 成本。

## 代价和不足

- 规则会更保守，部分“数字健康但标题没有明显 AI 术语”的内容可能被过滤。
- 仍然是启发式规则，不是语义级概念发现。
- 同义词归并和概念层级判断还比较弱，例如同一个方向的不同表述仍可能分散。
- OpenAlex 仍只是标题年龄校验，不能证明概念首次出现时间。

## 后续建议

- 用 embedding 聚类替代纯 n-gram，把同义概念聚合后再展示。
- 引入负向医学标题碎片词表的线上反馈机制。
- 为每个冒头信号展示“为什么判定为 AI+X”，例如命中的 AI 方法词、来源标签或代表论文证据。
- 增加后台指标：冒头信号点击率、隐藏率、不相关反馈率。

## 简历/项目介绍表达

> 修复 AI+X 研究雷达中新概念检测的误报问题：将原先基于标题 n-gram 与时间窗口的冒头检测，升级为“AI+X 语料过滤 + 方法概念短语过滤 + 重叠候选去重 + 缓存版本失效”的轻量规则系统，显著降低普通临床论文标题碎片被误判为新概念的概率，提升研究雷达的可信度和产品定位一致性。

