# medai-digest — 医疗AI每日情报 Skill

## 快速开始

### 1. 克隆项目仓库

```bash
git clone https://github.com/zhengwenxin79-ctrl/content-pipeline.git
cd content-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 main.py init
```

Windows 用户将 `source .venv/bin/activate` 替换为 `.venv\Scripts\activate`。

### 2. 导入 Skill 到龙虾

```bash
cp -r openclaw-skills/medai-digest/ ~/.openclaw/skills/medai-digest/
```

### 3. 配置数据库路径

编辑 `~/.openclaw/openclaw.json`，在 `skills.entries` 中添加：

```json
{
  "skills": {
    "entries": {
      "medai-digest": {
        "env": {
          "MEDAI_DB": "/你的路径/content-pipeline/corpus/corpus.db"
        }
      }
    }
  }
}
```

将路径替换为你的实际路径。重启龙虾即可使用。

---

## 使用方式

在龙虾对话中：
- **"今日情报"** — 自动抓取最新论文 → 龙虾评分 → 生成摘要 → 推送
- **"我关注影像诊断和大模型"** — 设置研究方向，只推送匹配的论文
- **"第1篇"** — 用学术海报风格详解某篇论文
- **"换个风格"** — 切换排版样式（卡片/杂志/经典/简约），跨会话保留
- **"搜一下影像"** — 关键词搜索
- **"每天早上8点推送情报"** — 设置定时自动推送

---

## 工作原理

当用户说"今日情报"时，龙虾会按以下顺序执行：

1. **Step 0 — 刷新数据**：调用项目的 `main.py fetch` 从 16+ RSS 源抓取最新文章
2. **Step 1 — 评分 + 摘要**：龙虾自身模型对新文章评分（1-10分）并分类，同时为缺少摘要的文章生成一句话总结
3. **Step 2 — 渲染推送**：按用户偏好的样式（卡片/杂志/经典/简约）和研究方向过滤后，推送情报

评分、摘要、深度分析均由龙虾自身模型完成，**无需 DeepSeek API key**。如果项目未安装依赖，会跳过抓取步骤，使用数据库中已有数据。

---

## 依赖说明

| 组件 | 用途 |
|------|------|
| content-pipeline 项目 | RSS 抓取（需 clone + pip install），仅 fetch 命令需要 |
| Python 3.8+ | medai.py 仅用标准库，无第三方依赖 |
| librsvg | SVG 转 PNG 需要（`brew install librsvg` / `apt install librsvg2-bin`） |

---

## 文件结构

```
medai-digest/
├── SKILL.md      # 龙虾 Skill 指令
├── medai.py      # CLI 工具（零依赖）
├── config.json   # 配置模板
└── README.md    # 本文件
```

---

## medai.py 命令一览

| 命令 | 说明 |
|------|------|
| `init` | 初始化数据库（含字段迁移） |
| `render --days 7` | 渲染情报消息 |
| `digest --days 7` | 输出结构化 digest（供选序号） |
| `article <ID>` | 获取单篇文章完整数据 |
| `score --limit 20` | 列出需要评分的文章 |
| `save-score '[{"id":1,"score":8.5,"category":"顶刊论文"}]'` | 保存评分结果 |
| `summarize --days 7` | 列出缺少摘要的文章 |
| `save-summary '[{"id":1,"summary":"..."}]'` | 保存摘要 |
| `analyze <ID>` | 获取深度分析（或生成7维度报告） |
| `save-analysis <ID> "分析文本"` | 保存深度分析 |
| `set-interests "影像诊断,大模型"` | 设置研究方向关键词 |
| `search "关键词" --days 7` | 关键词搜索 |
| `prefs get/set` | 读取/设置用户偏好 |
| `svg2png <file.svg>` | SVG 转 PNG 图片 |

---

## 常见问题

**Q：龙虾推送的情报没有摘要怎么办？**
龙虾会在渲染前自动为缺少摘要的文章生成总结。如仍缺失，说"重新生成摘要"。

**Q：切换样式后新会话又变回卡片？**
样式偏好已保存到数据库，render 会自动读取。如未生效，检查 MEDAI_DB 路径是否正确。

**Q：漫画没有生成图片？**
需要安装 librsvg：`brew install librsvg`（macOS）或 `apt install librsvg2-bin`（Linux）。

**Q：想设置每天自动推送？**
跟龙虾说"每天早上8点推送情报"即可，龙虾会自动创建定时任务。

**Q：只想看我关注的论文？**
跟龙虾说"我关注影像诊断和大模型"，龙虾会保存研究方向，后续情报优先推送匹配文章。
