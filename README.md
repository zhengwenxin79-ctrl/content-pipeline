# 医疗AI每日情报系统

自动抓取医疗AI领域顶刊论文、机构动态、商业落地资讯，经 DeepSeek 评分筛选后，通过三轮生成流程（初稿→审核→润色）产出可直接发布的微信公众号文章。

## 功能

- 自动抓取 16 个 RSS 源（Nature Medicine、Lancet Digital Health、NEJM AI、arXiv 等顶刊 + 行业媒体）
- 接入 wewe-rss 订阅微信公众号，作为竞品参考
- DeepSeek 对文章打质量分（0-10），只保留医疗 × AI 双属性文章
- 三轮生成：DeepSeek 并行生成3篇初稿 → GPT-4.1 审核打分 → DeepSeek 润色终稿
- 质量门槛：最优初稿低于 7.0 分自动补生成2篇，从5篇中选最优
- Web 界面：每日情报浏览、文章选择、一键生成、草稿管理

## 环境要求

- Python 3.9+
- DeepSeek API Key（[申请地址](https://platform.deepseek.com/)）
- GitHub Token（用于调用 GitHub Models 的 GPT-4.1，[申请地址](https://github.com/settings/tokens)，需勾选 Models 权限）
- Docker（可选，用于 wewe-rss 订阅微信公众号）

## 安装

```bash
git clone https://github.com/zhengwenxin79-ctrl/content-pipeline.git
cd content-pipeline

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## 配置

### 1. 设置环境变量

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key"
export GITHUB_TOKEN="your_github_token"
```

建议写入 `~/.zshrc` 或 `~/.bashrc` 永久生效：

```bash
echo 'export DEEPSEEK_API_KEY="your_deepseek_api_key"' >> ~/.zshrc
echo 'export GITHUB_TOKEN="your_github_token"' >> ~/.zshrc
source ~/.zshrc
```

### 2. 初始化数据库

```bash
mkdir -p corpus
python3 main.py fetch   # 首次运行会自动建表
```

### 3. （可选）接入 wewe-rss 订阅微信公众号

参考 [WEWE_RSS_SETUP.md](WEWE_RSS_SETUP.md) 部署 wewe-rss，然后在 `config.yaml` 中填入公众号的 feed_path。

## 使用

### 启动 Web 界面

```bash
python3 server.py
# 访问 http://localhost:8888
```

### 命令行操作

```bash
# 抓取所有 RSS 源
python3 main.py fetch

# DeepSeek 评分（筛选医疗×AI文章）
python3 main.py score

# 生成今日情报摘要
python3 main.py digest

# 推荐标题候选
python3 main.py titles
```

### 完整每日流程

```bash
python3 main.py fetch && python3 main.py score && python3 main.py digest
python3 server.py   # 打开界面选文章生成公众号文章
```

## 项目结构

```
content-pipeline/
├── server.py              # Web 服务器 + 所有 API + 前端页面
├── main.py                # CLI 入口
├── analyze.py             # DeepSeek 评分与摘要生成
├── db.py                  # SQLite 操作
├── config.yaml            # RSS 源配置
├── requirements.txt
├── scrapers/
│   ├── rss.py             # RSS 抓取 + wewe-rss 接入
│   ├── github_scraper.py  # GitHub 医疗AI项目抓取
│   ├── wechat.py          # 微信公众号处理
│   ├── enrich.py          # 内容增强
│   └── manual.py          # 手动录入
├── prompts/               # 三轮生成的 Prompt 文档
│   ├── 01_draft_generation.md
│   ├── 02_review_audit.md
│   └── 03_final_polish.md
└── data/
    └── my_posts_template.md   # 历史文章导入模板
```

## 数据说明

- 数据库文件 `corpus/corpus.db` 不含在仓库中，首次运行自动创建
- 文章评分阈值：>= 6.5 分进入每日情报展示，< 6.5 分过滤
- 生成质量门槛：初稿最优分 >= 7.0 才进入润色，否则补生成2篇
