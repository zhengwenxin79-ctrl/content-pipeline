# 论文图节点检测优化研究

**日期**: 2026-05-10
**触发**: 用户反馈机制图动画节点位置不准（节点标在公式/正文上、重复 label、坐标飘 5-15%）
**当前结论**: 暂不引入新方案，靠 prompt 强化 + 后处理兜底；surya 路线技术可行但服务器规格不够，待有 GPU/付费用户后再启动

---

## 1. 问题描述

现有 `animation_service.py` 用 Qwen-VL-Max 单模型完成「机制图识别 + 节点定位 + 中文标注」。
真实运行中观察到：

- 节点圆点常落在正文段落、公式编号、caption 上
- 同一个节点被多次标记（如 Encoder 出现 3-4 次）
- 子图 (b)(c) 流形可视化的节点几乎全错位
- 「整页 PDF 渲染 → Qwen 给整页坐标」这条路有结构性问题

## 2. 三种探索路线及结果

### 方案 A：纯 CV 节点检测（OpenCV findContours）

**做法**: 用 OpenCV 边缘检测 + 轮廓拟合找矩形节点。

**结论**: ❌ 不可行（独立使用）

| 优点 | 缺点 |
|---|---|
| 坐标像素级准确 | 不能区分"节点矩形"和"段落框/公式块/装饰矩形" |
| 不依赖 LLM | 流形、曲面、散点图完全检不到 |
| 速度快 | 调参在 0/117/4 之间反复横跳，难找平衡 |

**实测数据** (MPNet 论文 Fig.1):
- 真节点 ~22 个
- 调参三轮：0 / 117 / 4 / 20 个候选
- 即使调到 20 个候选，仍混着 panel 边界、色块、波形片段

### 方案 B：CV + Qwen Set-of-Mark (SoM)

**做法**: CV 找候选框 → 编号叠加到原图 → Qwen 看带编号的图，回答"#N 是什么节点"。
理论上 Qwen 只做语义分类（强项），CV 给坐标（强项）。

**结论**: ❌ 不可行 ——**Qwen 在 SoM 阶段会编造 label**

实测：20 个 CV 候选，Qwen **接收 100%**，但对照原图：

| 编号 | Qwen 给的 label | 实际位置 | 判断 |
|---|---|---|---|
| #1 | 高频率信号 | (a) 子图标签框 | ❌ 编造 |
| #2 | 低频率信号 | High freq 输入波形 | ❌ 张冠李戴 |
| #15-20 | S_pooled、S_i、H_low | 在公式区，不在 figure 里 | ❌ 严重编造 |

**根因**: Qwen-VL 是强语言模型，**靠先验知识脑补**了每个编号"应该"是什么，而非老实读编号位置上的内容。先验越强，幻觉越严重。

### 方案 C：Surya layout + OCR ⭐

**做法**:
1. Surya layout 模型精确定位 figure 区域
2. Surya OCR 在 figure 内识别所有文字 + 像素级 bbox
3. OCR 文字位置即节点位置
4. 把"纯文本列表"喂给 Qwen 做翻译 + type 分类（无图，零幻觉）

**结论**: ✅ 技术上可行，但**服务器跑不动**

实测 (MPNet Fig.1, Mac MPS):

| 步骤 | 耗时 | 准确度 |
|---|---|---|
| Layout 检测 figure 区域 | 3.2s | ✅ 像素级 |
| OCR 26 个文字片段 | 14s | ✅ 真节点全识别（Encoder/Spatial Conv/Temporal Conv/Fusion Node/...) |
| **合计** | **~20s** (Mac MPS) | 高 |

**服务器约束**:
- 当前: 阿里云 ECS, 2 vCPU / 2 GiB / 40GB
- Surya 模型需 ~2 GiB 内存（与 server.py 共存会 OOM）
- CPU 推理预估 60-90s/页（同步阻塞 HTTP 服务）
- 升级到带 GPU 的实例 ≈ 5-10 倍现价（¥600+/月）

## 3. 当前阶段决策

**保持 Qwen 全包路线**，做两件零成本零风险的优化：

### 3.1 提示词强化
在 `_QWEN_PROMPT_TMPL` 加 4 条硬约束：
1. 节点 x/y 必须严格落在 diagram_region 内
2. 同一 label 不重复出现
3. 任意两节点距离至少 0.05
4. 节点数 5-15 个为佳，超 20 个说明在标装饰

### 3.2 后处理兜底过滤
新增 `_enforce_node_constraints(nodes, dr)`：
- 越界节点（落在 dr 外，含 5% 容差）直接丢弃
- 重复 label 只保留第一个
- NaN/超 0~1 范围异常坐标丢弃

调用位置：`_build_overlay_html` 中 Qwen 输出后立刻过滤；Pass-2 校准后再过滤一次。

## 4. 何时回来重启此项目

满足**任一**条件即可重新评估 Surya 方案：

| 触发条件 | 原因 |
|---|---|
| 服务器升级到 ≥4 GiB 内存 | Surya 模型加载有空间 |
| 配置 GPU（即使是 T4） | 推理 5s 以内不阻塞 HTTP |
| 付费用户 ≥ 50 人 | 有营收支撑基础设施投入 |
| 出现真实用户对节点不准的具体抱怨 | 证明这是真痛点而非 PM 完美主义 |

## 5. 复现实验

测试代码全部留在 `tests/cv_poc/` 下，**未删除**：

| 文件 | 用途 |
|---|---|
| `tests/cv_poc/mpnet.pdf` | 测试输入（MPNet, arXiv 2605.05212） |
| `tests/cv_poc/detect_boxes.py` | 方案 A：纯 CV 检测 |
| `tests/cv_poc/som_pipeline.py` | 方案 B：CV+Qwen SoM（验证幻觉） |
| `tests/cv_poc/test_surya.py` | 方案 C：Surya layout |
| `tests/cv_poc/test_surya_ocr.py` | 方案 C：Surya layout + OCR |
| `tests/cv_poc/out_*.png` | 各方案的可视化产物 |

重启项目时第一步：在更强的服务器上跑 `test_surya_ocr.py` 验证产能与速度。

## 6. 关键提示词调整记录（不要回滚）

`animation_service.py:_QWEN_PROMPT_TMPL` 加的 4 条硬约束 + `_enforce_node_constraints` 过滤函数解决了**60-70% 的视觉别扭**，是这一阶段唯一应做的修改。
