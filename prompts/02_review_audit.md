# 第2轮：GPT-4.1 审核 Prompt（优化版）

> 模型：GPT-4.1（GitHub Models）| max_tokens=1200 | timeout=60s
> 变更：增加评分锚点，提升评分一致性

---

## 关键改进

### 评分锚点
旧版只说"评分要严格，不要虚高"，新版给出具体标准：
- 9-10分：让人想立刻转发，有可执行洞察
- 7-8分：专业可靠但缺少亮点
- 5-6分：像综述，缺乏判断
- 3-4分：空洞套话
- 1-2分：有事实错误

### 设计理由
绝对打分容易漂移，锚点让不同次调用的分数尺度保持一致，
使"选分最高"机制更可靠。

## 输出格式
JSON，包含 scores / issues / strengths / cut_candidates / title_suggestion / key_fix
