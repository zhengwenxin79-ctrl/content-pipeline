# 医疗AI每日情报系统

> 参考项目：[Daily-Digest-Assistant](https://github.com/yzbcs/Daily-Digest-Assistant)

自动抓取医疗AI领域顶刊论文、机构动态、商业落地资讯，经 DeepSeek 评分筛选后，通过三轮生成流程（初稿→审核→润色）产出可直接发布的微信公众号文章。支持用户注册登录，游客可浏览全部情报，注册用户可收藏、生成文章、管理草稿。

## 功能

- 自动抓取 16 个 RSS 源（Nature Medicine、Lancet Digital Health、NEJM AI、arXiv 等顶刊 + 行业媒体）
- DeepSeek 对文章打质量分（0-10），只保留医疗 × AI 双属性文章
- 三轮生成：DeepSeek 并行生成3篇初稿 → GPT-4.1 审核打分 → DeepSeek 润色终稿
- 质量门槛：最优初稿低于 7.0 分自动补生成2篇，从5篇中选最优
- 关键词订阅：用户填写邮箱+关键词，每日自动推送匹配文章+小红书热门笔记
- **用户系统**：邮箱注册/登录，游客可浏览所有情报，登录后解锁收藏、生成、草稿等操作功能
- Web 界面：每日情报浏览、文章选择、一键生成、草稿管理、关键词订阅管理

## 环境要求

- Python 3.9+
- Node.js 18+（小红书签名加密需要）
- DeepSeek API Key（[申请地址](https://platform.deepseek.com/)）
- GitHub Token（用于调用 GitHub Models 的 GPT-4.1，[申请地址](https://github.com/settings/tokens)，需勾选 Models 权限）

## 本地安装

```bash
git clone https://github.com/zhengwenxin79-ctrl/content-pipeline.git
cd content-pipeline

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
npm install crypto-js
```

## 本地配置

### 1. 设置环境变量

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key"
export GITHUB_TOKEN="your_github_token"
export MAIL_SENDER="your_qq_email@qq.com"
export MAIL_PASSWD="your_qq_smtp_auth_code"
export XHS_COOKIE="your_xiaohongshu_cookie"   # 从浏览器F12复制，约30天过期
```

### 2. 启动

```bash
python3 server.py
# 访问 http://localhost:8888
```

数据库会在首次启动时自动创建。

---

## 部署到 Render（免费）

### 第一步：Fork 仓库

点击右上角 **Fork**，Fork 到自己的 GitHub 账号。

### 第二步：注册 Render

打开 [render.com](https://render.com)，用 GitHub 账号登录。

### 第三步：新建 Web Service

1. 点 **New** → **Web Service**
2. 选择 Fork 后的 `content-pipeline` 仓库
3. 配置如下：

| 字段 | 值 |
|------|-----|
| Language | Python |
| Branch | main |
| Root Directory | （留空） |
| Start Command | `python server.py` |
| Instance Type | Free |

### 第四步：配置环境变量

在 **Environment** 页面添加：

| 变量名 | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `GITHUB_TOKEN` | GitHub Token（GPT-4.1审核用） |
| `MAIL_SENDER` | 发件QQ邮箱 |
| `MAIL_PASSWD` | QQ邮箱SMTP授权码 |
| `XHS_COOKIE` | 小红书Cookie（登录后从F12复制） |
| `AUTH_SALT` | （可选）用户密码哈希盐，不设则使用默认值 |

### 第五步：部署

点 **Deploy Web Service**，等待构建完成（约3分钟），即可访问分配的域名。

> ⚠️ 注意：Render 免费版没有持久化存储，每次重新部署数据库会清空。数据库文件仅在当次运行期间保留。如需持久化，升级到 Starter（$7/月）并挂载 Disk。

---

## 项目结构

```
content-pipeline/
├── server.py              # Web 服务器 + 所有 API + 前端页面（含用户认证）
├── main.py                # CLI 入口
├── analyze.py             # DeepSeek 评分与摘要生成
├── db.py                  # SQLite 操作（含用户表、自动迁移）
├── mailer.py              # 邮件推送模块
├── config.yaml            # RSS 源配置
├── requirements.txt
├── Procfile               # Render 启动配置
├── scrapers/
│   ├── rss.py             # RSS 抓取
│   ├── xhs_fetcher.py     # 小红书关键词抓取
│   ├── xhs_pc_apis.py     # 小红书 API 封装
│   ├── xhs_util.py        # 小红书签名工具
│   ├── xhs_cookie_util.py # Cookie 解析
│   ├── xhs_static/        # JS 加密文件
│   ├── enrich.py          # 内容增强
│   └── manual.py          # 手动录入
├── prompts/               # 三轮生成的 Prompt 文档
└── data/
    └── my_posts_template.md
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.9，标准库 `http.server`（无框架） |
| 数据库 | SQLite（5张表：articles / users / subscriptions / drafts / title_suggestions） |
| AI | DeepSeek API（评分+生成）、GitHub Models GPT-4.1（审核） |
| 抓取 | feedparser（RSS）、requests + BeautifulSoup（网页） |
| 认证 | SHA-256 密码哈希，内存 session token（30天有效期 cookie） |
| 前端 | 纯 HTML/CSS/JS，内联于 server.py |
| 部署 | Render Web Service（免费层） |

## 已知问题 / 待修复

- [ ] Render 免费版数据每次重部署后丢失，需接入持久化存储
- [ ] 小红书 Cookie 约30天过期，需手动更新环境变量
- [ ] 生成文章功能在免费版 CPU（0.1核）下较慢
- [ ] 定时推送邮件在 Render 免费版（会休眠）下无法保证准时

## 数据说明

- 文章评分阈值：>= 6.5 分进入每日情报展示
- 生成质量门槛：初稿最优分 >= 7.0 才进入润色，否则补生成2篇
