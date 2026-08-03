# P13 顶刊顶会优先与预印本分层策略复盘

## 背景

普通 arXiv 预印本数量大、更新快、噪声高。对 AI+X 交叉研究者来说，直接把大量预印本推到首页会带来两个问题：

- 读者需要花大量时间判断可信度和发表状态；
- 真正值得读的顶刊、顶会或已被接收的预印本容易被淹没。

因此推荐策略应从“尽量召回预印本”改为“优先展示经过 venue 信号过滤的高价值研究”。

## 做了什么

- 为 digest 中每篇文章动态附加发表层级字段：
  - `journal_top`
  - `conference_top`
  - `accepted_preprint`
  - `preprint`
  - `industry`
  - `github`
  - `other`
- 增加 venue 信号字段：
  - `publication_label`
  - `venue_signal`
  - `venue_confidence`
  - `venue_reason`
  - `publication_weight`
- 顶刊来源直接识别为 `journal_top`，包括 Nature Medicine、The Lancet Digital Health、NEJM AI、npj Digital Medicine、JAMA Network Open、Medical Image Analysis、IEEE TMI 等。
- 顶会信号识别包括 NeurIPS、ICLR、ICML、CVPR、MICCAI、ACL、EMNLP、KDD、AAAI、IJCAI、CHI、SIGIR 等。
- arXiv 预印本如果标题、摘要、来源或标签中出现顶会/顶刊信号，标为 `accepted_preprint`。
- 普通 arXiv 标为 `preprint`，在首页推荐中降权。
- 推荐综合排序加入发表层级权重：
  - 顶刊 `+1.2`
  - 顶会 `+1.1`
  - 已接收预印本 `+0.9`
  - 产业/开源小幅加权
  - 普通预印本 `-1.0`
- 今日必读只允许顶刊、顶会、已接收预印本优先进入；普通预印本只有在极高质量或极高个性化匹配时才进入。
- 补充候选中普通预印本最多展示 2 篇。
- 前端文章卡片增加标签：
  - 顶刊
  - 顶会
  - 已接收预印本
  - 普通预印本
  - 产业信号
  - 开源项目
- 空状态增加候选摘要，告诉用户当前抓取了多少候选、多少顶刊/顶会/预印本、多少 8 分以上文章。

## 为什么这么做

顶刊/顶会抓取往往比 arXiv 更难、更慢，但可信度、筛选价值和读者决策效率更高。预印本并不是完全没有价值，真正有价值的是：

- 已被顶会接收的预印本；
- 后续有 journal version 的预印本；
- 与用户方向高度匹配且方法新颖的预印本。

因此策略不是“去掉 arXiv”，而是把 arXiv 从主信息流降级为候选池，并把其中有发表/接收证据的文章重新提升。

## 优点

- 首页更符合研究者心智：优先看可信、高筛选强度来源。
- 降低普通预印本刷屏概率。
- 仍保留 arXiv 中真正有价值的顶会/已接收信号。
- 不新增数据库迁移，字段在 digest 层动态生成，改动成本低。
- 前端能解释为什么一篇文章被推荐或被降权。

## 代价和不足

- accepted preprint 识别目前是启发式规则，不能保证 100% 准确。
- arXiv metadata 里不一定包含最终接收 venue，可能漏掉一部分已接收论文。
- 顶会识别依赖标题/摘要/标签中出现 venue 名称。
- 普通预印本被明显降权后，探索性发现会减少。

## 后续建议

- 接入 Semantic Scholar / OpenAlex 以标题或 arXiv ID 查询 external IDs、venue、publication venue。
- 为 arXiv 增加 DOI / journal-ref / comments 字段解析。
- 建立“预印本 → 发表版本”映射缓存表，避免每次实时查询。
- 对顶会论文源增加官方 proceedings RSS/API，例如 OpenReview、PMLR、ACL Anthology。
- 在后台统计普通预印本、已接收预印本、顶刊、顶会的点击率和有用率，反向校准权重。

## 简历/项目介绍表达

> 针对 AI+X 论文雷达中普通预印本噪声高的问题，设计并实现顶刊/顶会优先的发表层级排序策略：动态识别顶刊、顶会、已接收预印本、普通预印本和产业信号，将 venue 证据纳入推荐综合分，并限制普通 arXiv 在首页候选中的占比，在保留前沿发现能力的同时显著提升推荐可信度和阅读性价比。

