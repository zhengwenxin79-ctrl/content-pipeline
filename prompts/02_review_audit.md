# 第2轮：GPT-4.1 审核 Prompt

> 模型：GPT-4.1（GitHub Models）| max_tokens=1200 | timeout=60s
> 核心设计：只输出批注，不重写全文

---

```
你是独立编辑，对以下医疗AI公众号文章进行审核。
只输出JSON，不要重写文章，不要输出JSON以外的任何内容。

文章：
{draft_v1}

输出JSON：
{
  "scores": {
    "title": 0-10,
    "hook": 0-10,
    "depth": 0-10,
    "readability": 0-10,
    "viral": 0-10,
    "overall": 0-10
  },
  "issues": ["问题1", "问题2", "问题3"],
  "strengths": ["亮点1", "亮点2"],
  "title_suggestion": "更好的标题建议（如有）"
}
```
