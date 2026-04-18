# 第1轮：DeepSeek 初稿生成 Prompt（优化版）

> 模型：DeepSeek | max_tokens=3500 | temperature=0.75 | timeout=120s
> 变更：system/user 拆分，增加素材萃取前置步骤

---

## 架构变更

**旧流程：** 原始素材 → 初稿 prompt → 生成
**新流程：** 原始素材 → 素材萃取（提取核心发现+关键事实+决策参考） → 初稿 prompt → 生成

## 设计理由

1. **素材萃取**解决"模型逐篇复述"问题——先找到跨材料的共同线索，再围绕它写作
2. **System/User 分离**——角色定位和禁用词放 system（稳定规则），写作任务放 user（变化内容）
3. **temperature=0.75**——配合并行3篇，增加多样性，让3篇真正不同
4. **max_tokens=3500**——给中文 tokenization 留够空间，避免截断

## Prompt 结构

- System: 角色定位 + 写作铁律 + 禁用词
- User: 素材萃取要点 + 原始素材 + 写作任务（7条） + 标题要求
