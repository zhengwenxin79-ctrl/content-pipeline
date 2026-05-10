---
name: medai-digest
description: 医疗AI每日情报系统 - 论文推送、小红书笔记、漫画思维导图深度阅读
metadata: {"openclaw":{"requires":{"bins":["python3"]},"always":true}}
---

# 医疗AI每日情报 Skill

为用户提供医疗AI领域的每日论文推送和漫画式深度阅读体验。

**⚠️ 核心原则：每次生成情报前，必须先用 `summarize` 检查并补全摘要，再用 `render` 渲染。绝不可跳过摘要补全直接渲染！发送情报时，必须输出 `message` 字段的完整内容，禁止省略、截断或用"共X篇"概括。SVG 图片中所有文字必须使用 `<tspan>` 分行 + `dy` 控制行距（相邻行 dy ≥ 20px），禁止多个 `<text>` 重叠放置。**

## 首次使用配置

用户需要设置环境变量（在 openclaw.json 的 skills.entries.medai-digest.env 中）：

```json
{
  "MEDAI_DB": "/path/to/pipeline/corpus/corpus.db"
}
```

如果用户已有 content-pipeline 项目，指向其 `corpus/corpus.db` 即可。
如果是全新用户，先执行初始化：
```bash
python3 {baseDir}/medai.py init
```
然后通过 pipeline 的 `python3 main.py fetch && python3 main.py score` 导入数据。

可选环境变量：
- 无需额外 API key，摘要生成和深度分析由龙虾自身模型完成

## 核心能力

### 1. 每日情报推送

当用户说"今日情报"、"看看今天有什么"、"推一下"、"daily"时触发。

**⚠️ 必须严格按以下步骤顺序执行，不可跳过！**

**Step 0（必做）: 刷新数据**

从 `MEDAI_DB` 环境变量推算项目根目录（`MEDAI_DB` 路径中 `corpus/corpus.db` 的上两级目录），用项目的 venv Python 运行抓取命令：

macOS / Linux:
```bash
{项目根目录}/.venv/bin/python3 {项目根目录}/main.py fetch
```

Windows:
```cmd
{项目根目录}\.venv\Scripts\python {项目根目录}\main.py fetch
```

如果 fetch 失败（如未安装依赖），跳过，使用数据库已有数据继续。

然后检查是否有未评分的新文章：
```bash
python3 {baseDir}/medai.py score --limit 20
```
如果 `count > 0`，**你为每篇文章评分**（1-10分）并分类（"顶刊论文"/"大组动态"/"商业落地"/"开源项目"），然后保存：
```bash
python3 {baseDir}/medai.py save-score '[{"id":1,"score":8.5,"category":"顶刊论文"}]'
```

**Step 1（必做）: 补全文章摘要**

运行以下命令，查看是否有文章缺少一句话摘要：
```bash
python3 {baseDir}/medai.py summarize --days 7
```

- 如果返回 `count` 为 0，跳过本步，直接进入 Step 2。
- 如果返回 `count > 0`，**你必须为每篇文章各写一句中文摘要**（40字以内，格式：[做了什么]+[关键结论]），然后一次性保存：
```bash
python3 {baseDir}/medai.py save-summary '[{"id":文章ID,"summary":"你写的摘要"}]'
```
**注意：save-summary 的参数必须是合法 JSON 数组，每项包含 id 和 summary 两个字段。**

**Step 2（必做）: 渲染并发送**

确认摘要补全后，执行（会自动使用用户已保存的模板样式）：
```bash
python3 {baseDir}/medai.py render --days 7
```
返回 JSON，取 `message` 字段**完整**发给用户，禁止省略、截断或用"共X篇"概括。如果文章数为 0，尝试扩大 `--days 30`。

**如果返回的 JSON 中包含 `missing_summaries` 字段**，说明还有文章缺摘要，必须先回到 Step 1 补全，再重新 render。

如果需要结构化数据（让用户选序号），用：
```bash
python3 {baseDir}/medai.py digest --days 7
```

### 2. 论文漫画详解

当用户回复数字（如"1"、"第一篇"）或说"详细看看这篇"时触发：

**Step 1: 获取论文**
```bash
python3 {baseDir}/medai.py article <ID>
```

**Step 2: 深度分析**
```bash
python3 {baseDir}/medai.py analyze <ID>
```
如果返回 `cached: true`，直接使用 `analysis` 字段。
如果返回 `cached: false`，根据返回的 `content`，**你自己按以下7个维度写一份300-500字的深度分析**（每段用emoji标题开头）：
🔬 核心问题、⚡ 方法创新、📊 关键结果、🗄️ 数据集、💻 代码/模型、💡 价值判断、⚠️ 局限性
然后保存：
```bash
python3 {baseDir}/medai.py save-analysis <ID> "你的分析文本"
```

**Step 3: 生成 SVG 漫画**

拿到 `title`、`content`、`deep_analysis` 后，**你必须生成一个 SVG 文件**，用 exec 工具将 SVG 代码写入文件，然后发给用户。

**绝对不要用文字+emoji代替漫画！** 必须输出完整的 SVG XML 代码并转换为 PNG 图片。
**绝对不要输出文字版思维导图！** 只能输出 PNG 图片。

执行流程：
1. 根据论文内容构思漫画布局
2. 用 exec 工具将完整 SVG 代码写入文件：
```bash
cat > /tmp/medai_comic.svg << 'SVGEOF'
<svg ...>完整SVG代码</svg>
SVGEOF
```
3. 用 exec 工具将 SVG 转为 PNG 图片：
```bash
python3 {baseDir}/medai.py svg2png /tmp/medai_comic.svg
```
4. 将生成的 PNG 图片 `/tmp/medai_comic.png` 发送给用户（用图片发送功能，不是文件）

**必须是学术可视化海报风格（Visual Abstract），兼具专业性和视觉吸引力！** 严格遵循以下规范：

#### 整体定位
参考顶级期刊（Nature/Lancet/NEJM）的 Visual Abstract 风格：严谨的数据可视化 + 清晰的逻辑流 + 适度的视觉装饰。不是幼稚漫画，是学术海报。

#### 整体布局
- 画布 700px 宽，高度 1100-1400px
- 顶部标题横幅（深色背景 + 白色标题），底部信息栏
- 中间用细线分隔为 4-5 个区块，不要用粗黑框的漫画格子
- 区块之间有清晰的信息层级，用编号或箭头引导阅读顺序

#### 配色方案（学术感）
- **主色调**：深海蓝 #1B2A4A（标题栏、重点边框）
- **辅助色**：钢蓝 #4A7FB5、石板灰 #607D8B
- **数据色**：翡翠绿 #2E7D32（正面结果）、琥珀橙 #E65100（警告/局限）、深紫 #5C3D8F（方法创新）
- **背景**：纯白 #FFFFFF + 浅灰区块交替 #F5F7FA
- **文字**：深黑 #1A1A1A（正文）、白色 #FFFFFF（深色背景上的文字）

#### 学术视觉元素（必须有！）
1. **数据图表区**：用 SVG 画简化的统计图表
   - 柱状图：对比实验组和对照组的指标（用 rect + 渐变色）
   - ROC 曲线：用 path 画简化的 AUC 曲线 + 对角虚线
   - 表格：用 rect 画 2-3 行的精简数据表，交替行背景色
   - 数字突出：关键指标（AUC、准确率等）用大号加粗 + 色块背景
2. **方法流程图**：用方框 + 箭头展示技术架构
   - 每个步骤用圆角矩形，填充渐变色
   - 步骤间用带箭头的连接线
   - 关键技术名称用加粗标注
3. **角色辅助说明**（克制使用，不喧宾夺主）
   - 左下角或右下角放一个小的研究者角色（50-60px 高）
   - 角色旁加标注框（不是对话气泡），写 1-2 句总结性评语
   - 角色风格：简约线条画，不要太卡通

#### 内容结构（从上到下，总高度必须填满 viewBox 1200px，不留黑边）
1. **标题横幅**（h=120px）：深蓝背景 + 白色论文标题（中文，20-24px）+ 来源标签 + 评分徽章（圆形，金色边框）
2. **研究背景**（h=120px）：左侧用大号问号图标 + 右侧 2-3 句问题陈述 + 研究目标
3. **方法架构**（h=220px）：技术流程图（数据→模型→评估），关键创新点用紫色高亮框标注
4. **核心结果**（h=280px，最大区域）：数据图表（柱状图或 ROC 曲线）+ 关键数字用大号突出 + 置信区间或 p 值
5. **临床意义**（h=120px）：简洁的要点列表（每项一行，左对齐绿色圆点）+ 局限性（橙色圆点）
6. **底栏**（h=60px）：浅灰背景 + 来源期刊图标 + DOI/链接 + 发表日期

**所有区块必须紧密衔接，不留空白。底栏 y=860，底栏高度60px，整张图底部 y=920。**

#### ⚠️ 文字排版铁律（防止重叠错位的核心规则）

**每一条 text 元素必须遵守以下规则：**

1. **多行文字用 `<tspan>` + `dy`，禁止分行写多个 `<text>`**：
```xml
<!-- ✅ 正确：tspan 分行，dy 控制行距 -->
<text x="30" y="340" font-size="14" fill="#1A1A1A">
  <tspan x="30" dy="0">第一行文字</tspan>
  <tspan x="30" dy="22">第二行文字</tspan>
  <tspan x="30" dy="22">第三行文字</tspan>
</text>

<!-- ❌ 错误：多个 text 重叠 -->
<text x="30" y="340">第一行</text>
<text x="30" y="340">第二行</text>
```

2. **所有 text 必须设置 `y` 坐标，禁止省略**，且相邻元素 `y` 差值 ≥ 20px

3. **图表内部文字用 `text-anchor="middle"` 居中**，x 设为图表区域的水平中心

4. **柱状图**：每个柱子宽度 40-50px，柱子之间间距 ≥ 20px，柱子高度按数据比例缩放（最高柱子 = 150px），Y轴标签放在柱子左侧，柱顶写数值。禁止把所有柱子画成一样高。

#### 字体和排版规范
- 标题：bold 22-24px，白色（深色背景）或深黑（浅色背景）
- 小节标题：bold 16px，深蓝色
- 正文：14px，深黑色 #1A1A1A，行高 1.5（tspan dy=22）
- 数据数字：bold 28-36px，带色块背景标签
- 注释/脚注：12px，灰色 #666
- 所有英文学术术语保留原文（如 AUC-ROC、p<0.001、Cross-Validation）
- **文字行间距：相邻 tspan 的 dy 值不得小于 20px**

#### 装饰（克制，学术感）
- 标题栏可以加细微的网格或 DNA 双螺旋暗纹（低透明度）
- 区块分隔用 1px 细线 + 小圆点或菱形装饰
- 关键数字旁加小趋势箭头（↑ ↓）
- 底栏加微渐变效果

#### SVG 模板骨架

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 700 920" width="700">
  <!-- 满铺白色背景，防止黑边 -->
  <rect width="700" height="920" fill="white"/>
  <defs>
    <linearGradient id="headerGrad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#1B2A4A"/>
      <stop offset="100%" stop-color="#2C3E6B"/>
    </linearGradient>
    <linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#4A7FB5"/>
      <stop offset="100%" stop-color="#2E5A8B"/>
    </linearGradient>
    <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
      <path d="M0,0 L0,6 L8,3 z" fill="#607D8B"/>
    </marker>
  </defs>
  <style>
    .title { font: bold 22px "PingFang SC", sans-serif; fill: white; }
    .section-title { font: bold 16px "PingFang SC", sans-serif; fill: #1B2A4A; }
    .body { font: 14px "PingFang SC", sans-serif; fill: #1A1A1A; }
    .small { font: 12px "PingFang SC", sans-serif; fill: #666; }
    .metric { font: bold 28px "PingFang SC", sans-serif; fill: white; }
    .label { font: bold 13px "PingFang SC", sans-serif; fill: white; }
    .box-label { font: bold 12px "PingFang SC", sans-serif; fill: #1A1A1A; }
    .divider { stroke: #D0D5DD; stroke-width: 1; }
  </style>

  <!-- 标题横幅 h=120, y=0 -->
  <rect width="700" height="120" fill="url(#headerGrad)"/>
  <text class="title" x="30" y="50">论文标题（中文翻译）</text>
  <text class="small" x="30" y="80">来源: Nature Medicine | 评分: 9.2 | 2026</text>
  <!-- 评分徽章 -->
  <circle cx="640" cy="50" r="26" fill="#F5A623" stroke="white" stroke-width="2"/>
  <text x="640" y="56" font-size="13" font-weight="bold" text-anchor="middle" fill="white">9.2</text>

  <!-- 研究背景 h=110, y=120 -->
  <rect y="120" width="700" height="110" fill="#FAFAFA"/>
  <line class="divider" x1="20" y1="120" x2="680" y2="120"/>
  <text class="section-title" x="30" y="146">🔬 研究背景</text>
  <text class="body" x="30" y="170">
    <tspan x="30" dy="0">背景：......</tspan>
    <tspan x="30" dy="22">目标：......</tspan>
  </text>

  <!-- 方法架构 h=200, y=230 -->
  <rect y="230" width="700" height="200" fill="white"/>
  <line class="divider" x1="20" y1="230" x2="680" y2="230"/>
  <text class="section-title" x="30" y="258">⚡ 方法架构</text>
  <!-- 流程图框：宽130px，高44px，间距15px，框内文字用 tspan dy=30 居中 -->
  <rect x="40" y="278" width="130" height="44" rx="8" fill="url(#barGrad)"/>
  <text class="label" x="105" y="304" text-anchor="middle">📥 数据</text>
  <line x1="170" y1="300" x2="190" y2="300" stroke="#607D8B" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="195" y="278" width="130" height="44" rx="8" fill="url(#barGrad)"/>
  <text class="label" x="260" y="304" text-anchor="middle">🔧 特征</text>
  <line x1="325" y1="300" x2="345" y2="300" stroke="#607D8B" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="350" y="278" width="130" height="44" rx="8" fill="#5C3D8F"/>
  <text class="label" x="415" y="304" text-anchor="middle">🧠 模型</text>
  <line x1="480" y1="300" x2="500" y2="300" stroke="#607D8B" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="505" y="278" width="130" height="44" rx="8" fill="#2E7D32"/>
  <text class="label" x="570" y="304" text-anchor="middle">📈 评估</text>
  <!-- 创新点标注框，要足够大容纳文字 -->
  <rect x="40" y="335" width="280" height="70" rx="6" fill="#EDE7F6" stroke="#5C3D8F" stroke-width="1.5"/>
  <text class="box-label" x="52" y="358">
    <tspan x="52" dy="0">⚡ 关键创新</tspan>
    <tspan x="52" dy="20">DeepSpeed-Inference 加速推理</tspan>
    <tspan x="52" dy="20">3.8×Speedup | 内存占用降低</tspan>
  </text>

  <!-- 核心结果 h=240, y=430 -->
  <rect y="430" width="700" height="240" fill="#FAFAFA"/>
  <line class="divider" x1="20" y1="430" x2="680" y2="430"/>
  <text class="section-title" x="30" y="458">📊 核心结果</text>
  <!-- 柱状图：底部 y=640，Y轴高度=120px，最高柱=120px -->
  <!-- 柱子宽50px，间距30px，3根柱子 -->
  <rect x="80" y="520" width="50" height="120" fill="#4A7FB5" rx="3"/>
  <text class="body" x="105" y="515" text-anchor="middle">Baseline</text>
  <rect x="160" y="490" width="50" height="150" fill="#2E7D32" rx="3"/>
  <text class="body" x="185" y="485" text-anchor="middle">Ours</text>
  <rect x="240" y="505" width="50" height="135" fill="#607D8B" rx="3"/>
  <text class="body" x="265" y="500" text-anchor="middle">SOTA</text>
  <!-- Y轴 -->
  <line x1="60" y1="520" x2="60" y2="640" stroke="#999" stroke-width="1"/>
  <text class="small" x="55" y="518" text-anchor="end">100</text>
  <text class="small" x="55" y="558" text-anchor="end">75</text>
  <text class="small" x="55" y="598" text-anchor="end">50</text>
  <text class="small" x="55" y="638" text-anchor="end">25</text>
  <!-- 关键指标突出 -->
  <rect x="380" y="480" width="270" height="140" rx="8" fill="#E8F5E9" stroke="#2E7D32" stroke-width="2"/>
  <text class="metric" x="515" y="528" text-anchor="middle" fill="#2E7D32">AUC 0.89</text>
  <text class="body" x="395" y="558">
    <tspan x="395" dy="0">● p &lt; 0.001 vs Baseline</tspan>
    <tspan x="395" dy="22">● 95% CI: [0.86, 0.92]</tspan>
    <tspan x="395" dy="22">● 在 3 个外部数据集验证</tspan>
  </text>

  <!-- 临床意义 h=120, y=670 -->
  <rect y="670" width="700" height="120" fill="white"/>
  <line class="divider" x1="20" y1="670" x2="680" y2="670"/>
  <text class="section-title" x="30" y="698">💡 临床意义</text>
  <text class="body" x="30" y="724">
    <tspan x="30" dy="0">● 可整合入现有 EHR 系统，辅助早期诊断</tspan>
    <tspan x="30" dy="22">● 对创伤后癫痫高危人群筛查有重要价值</tspan>
  </text>
  <text class="body" x="30" y="774" fill="#E65100">
    <tspan x="30" dy="0">⚠️ 局限：仅验证于回顾性数据，前瞻性研究待开展</tspan>
  </text>

  <!-- 底栏 h=50, y=790 -->
  <rect y="790" width="700" height="50" fill="#F5F7FA"/>
  <text class="small" x="30" y="818">来源: Nature Medicine | DOI: 10.xxxx/xxxxx | 2026</text>
</svg>
```

将 SVG 写入 `/tmp/medai_comic.svg`，然后运行 `python3 {baseDir}/medai.py svg2png /tmp/medai_comic.svg` 转为 PNG，将 PNG 图片发送给用户。

### 3. 模板切换

用户说"换个风格"时，识别关键词：
- "经典" / "邮件" → classic
- "卡片" / "飞书" → card
- "简约" → minimal
- "杂志" → magazine

执行：
```bash
python3 {baseDir}/medai.py prefs set '{"template_style":"magazine"}'
python3 {baseDir}/medai.py render --days 1
```
偏好会保存到数据库，后续新会话也会自动使用此样式。

### 4. 设置研究方向

用户说"我关注影像诊断"、"设置研究方向"时：
```bash
python3 {baseDir}/medai.py set-interests "影像诊断,大模型,病理"
```
关键词逗号分隔，保存后「今日情报」会优先推送匹配的文章。无匹配时显示全部。

### 5. 关键词搜索

用户说"搜一下XX"时：
```bash
python3 {baseDir}/medai.py search "关键词" --days 7
```

### 5. 深度分析（文字版）

用户说"分析一下"时：
```bash
python3 {baseDir}/medai.py analyze <ID>
```

返回 `analysis` 字段，7 维度结构化文本，直接发给用户。

## 漫画风格

- "手绘" → stroke-dasharray="5,3"
- "赛博朋克" → 背景 #1a1a2e，霓虹 #00ff88 + #ff00ff
- "简约" → 实线黑白

## 定时推送

用户说"每天早上8点推送情报"、"设置定时任务"时，使用龙虾的 cron 功能：

```
每天早上 8:30 自动推送医疗AI每日情报，执行完整的今日情报流程（刷新数据→补摘要→渲染→发送）
```

龙虾会自动创建定时任务。用户也可以在 `~/.openclaw/scheduled_tasks.json` 中手动管理。

## 注意事项

1. 文章列表在对话上下文中保持，用户说序号可直接引用
2. SVG 全中文，英文标题自行翻译
3. 摘要生成和深度分析均由龙虾自身模型完成，无需外部 API key
4. 小红书笔记需要主项目的 XHS_COOKIE，独立部署时暂不支持
