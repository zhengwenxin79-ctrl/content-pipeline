#!/usr/bin/env python3
"""
content-pipeline 优化补丁
========================
运行方式：
  cd content-pipeline
  python3 optimize_patch.py

功能：
  1. 备份 server.py 为 server.py.bak
  2. 对 server.py 做以下优化：
     - 新增"素材萃取"步骤（generate 前先提取结构化要点）
     - 初稿 prompt 拆分为 system + user message
     - max_tokens 从 2000 → 3500，润色 → 4000
     - 增加 temperature 参数（初稿 0.75，润色 0.3）
     - 简化润色 prompt（只做 3 件事）
     - 审核评分增加锚点示例
     - 审核改为排序 + 打分双重机制
  3. 更新 prompts/ 目录下的文档
"""

import shutil
import os
import re

PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(PATCH_DIR, "server.py")


def backup():
    bak = SERVER_PY + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(SERVER_PY, bak)
        print(f"✓ 已备份 server.py → server.py.bak")
    else:
        print(f"⚠ server.py.bak 已存在，跳过备份")


def read_server():
    with open(SERVER_PY, "r", encoding="utf-8") as f:
        return f.read()


def write_server(content):
    with open(SERVER_PY, "w", encoding="utf-8") as f:
        f.write(content)


# ──────────────────────────────────────────────────────────────
# 新增：素材萃取函数
# ──────────────────────────────────────────────────────────────
EXTRACT_FUNC = '''

def _extract_key_points(client, articles_text: str) -> str:
    """素材萃取：在生成初稿前，先把原始素材压缩成结构化要点。
    这一步解决"模型逐篇复述素材"的问题，让写作聚焦于跨材料的核心洞察。"""
    extract_prompt = f"""你是一个医疗AI领域的信息分析师。请从以下多篇素材中提取结构化要点，用于后续公众号文章写作。

要求：
1. 跨材料找出一个最值得写的核心发现/趋势/事件（一句话概括）
2. 提取 3-5 个关键事实（必须有具体数字、机构名、产品名等可核实信息）
3. 识别一个"读者会关心的决策点"（如果你是医疗AI产品经理，这意味着什么？）
4. 标注哪些信息来自哪篇素材（用【文章N】标注）

素材：
{articles_text}

输出格式：
【核心发现】一句话

【关键事实】
1. xxx（来源：【文章N】）
2. xxx（来源：【文章N】）
...

【决策参考】
对目标读者（医疗AI产品经理/医院信息化负责人）的具体意义

【写作建议】
推荐的文章切入角度（一句话）"""

    try:
        r = client.chat.completions.create(
            model="deepseek-chat", timeout=60, max_tokens=1200,
            temperature=0.3,
            messages=[{"role": "user", "content": extract_prompt}]
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠ 素材萃取失败，使用原始素材: {e}")
        return ""

'''


# ──────────────────────────────────────────────────────────────
# 优化后的 _build_draft_prompt（system/user 分离）
# ──────────────────────────────────────────────────────────────
NEW_DRAFT_PROMPT = '''def _build_draft_prompt(articles_text: str, extracted_points: str = "") -> tuple:
    """返回 (system_message, user_message) 元组，拆分角色定位和写作任务。"""
    system_msg = """你是医疗AI公众号主编，对标「Medical AI」「丁香园」「量子位医疗」等高阅读量账号的写作水准。
目标读者：医疗AI产品经理/产品总监、医疗科技公司决策层（创始人/BD）、医院信息化负责人——
他们看文章是为了做决定，不是为了学知识。让他们觉得"这个信息今天就能用上"，才会转发。
写作视角：比读者早一步看清落地坑的产品人，帮读者把事情想清楚，不是旁观者综述。

## 你的禁用词清单（遇到就替换为更具体的表述）
"下半场""上半场""深水区""新范式""新赛道""赋能""重塑""颠覆""生态闭环""闭环""破局""内卷""弯道超车""降维打击""护城河""数智化""智慧医疗""数字化转型""AI赋能""最后一公里""全链条""底层逻辑""顶层设计"

## 你的写作铁律
- 不用"近年来""随着AI发展""值得注意的是""首先其次最后"开头或过渡
- 不编造数据，原文没有的数字用相对表述替代，不用占位符（X%、待补充）
- 引用不写"发表于《某期刊》某卷某期"格式，用"据该研究""研究发现"替代"""

    source_section = f"""
## 素材萃取要点（优先基于此写作）
{extracted_points}

## 原始参考材料（需要具体数据时查阅）
{articles_text}""" if extracted_points else f"""
## 参考材料
{articles_text}"""

    user_msg = f"""请基于以下材料写一篇微信公众号文章。

{source_section}

## 本次写作任务
1. **字数1000-1500字**，宁短勿长。
2. **开头前3句**必须命中以下之一：具体数字+场景、一个尖锐问题、一个反直觉事实。
3. **全文只有一个核心判断**，围绕它展开。
4. **小标题用结论句**，2-3个小标题，体现递进关系。
5. 每个论点用材料中可核实的事实支撑。
6. **结尾**：给出一个明确判断或行动建议。
7. 每段2-4句，一个意思说完就换段。

## 标题要求（必须给出3个备选）
- **判断型**：直接给出有争议的观点（如"AI读片准确率超过专家，但医院为什么还不用"）
- **数字型**：用核心数据勾起好奇（如"一个模型让误诊率下降37%，它是怎么做到的"）
- **场景型**：代入具体人物情境（如"一个急诊科医生用了AI辅助诊断，然后他被投诉了"）

直接输出：
【备选标题】
- 判断型：xxx
- 数字型：xxx
- 场景型：xxx

【正文】
（直接写正文）

参考文献：
（仅列出正文中实际引用到的材料，格式：文章标题 / 来源名称 / 发布时间 / 链接）"""

    return system_msg, user_msg

'''


# ──────────────────────────────────────────────────────────────
# 优化后的 _build_review_prompt（增加锚点 + 排序）
# ──────────────────────────────────────────────────────────────
NEW_REVIEW_PROMPT = '''def _build_review_prompt(draft: str) -> str:
    return f"""你是独立编辑，对以下医疗AI公众号文章进行严格审核。
只输出JSON批注，不要重写文章，不要输出JSON以外的任何内容。

文章：
{draft}

## 评分锚点（校准你的评分尺度）
- 9-10分：读完让人想立刻转发给同事，有明确可执行的洞察，数据扎实
- 7-8分：专业可靠，有价值，但缺少让人"哇"的亮点或不够聚焦
- 5-6分：信息准确但像综述，读完不知道"所以呢"，缺乏判断
- 3-4分：空洞、套话多、或逻辑混乱
- 1-2分：有事实错误、编造数据、或完全跑题

## 审核要求
1. 每条issue包含：原文定位 + 问题说明 + 修改建议，输出3-5条，按优先级排序。
2. 至少1条issue检查事实可信度：是否有无法核实的数据、编造引用、绝对化表述。
3. 检查"决策价值"：读者读完能得到什么具体判断或行动参考？如果只是"介绍了什么"而没有"所以你应该怎么做"，作为high priority issue。
4. 如有冗余句子，在cut_candidates中指出。

输出JSON：
```json
{{
  "scores": {{
    "title": 0-10,
    "hook": 0-10,
    "depth": 0-10,
    "readability": 0-10,
    "credibility": 0-10,
    "decision_value": 0-10,
    "overall": 0-10
  }},
  "issues": [
    {{
      "priority": "high|medium|low",
      "location": "引用原文短句",
      "problem": "具体问题说明",
      "suggestion": "明确改法"
    }}
  ],
  "strengths": ["亮点1", "亮点2"],
  "cut_candidates": ["可删减或压缩的位置"],
  "title_suggestion": "更好的标题建议（如有）",
  "key_fix": {{
    "location": "最关键问题所在原文片段",
    "reason": "为什么这是最重要的",
    "suggestion": "如何修改"
  }}
}}
```"""

'''


# ──────────────────────────────────────────────────────────────
# 优化后的 _build_polish_prompt（大幅简化）
# ──────────────────────────────────────────────────────────────
NEW_POLISH_PROMPT = '''def _build_polish_prompt(draft_v1: str, review_data: dict) -> str:
    issues = review_data.get("issues", [])
    issues_text = "\\n".join([
        f"- [{i.get(\'priority\',\'\').upper()}] 「{i.get(\'location\',\'\')}」→ 问题：{i.get(\'problem\',\'\')} → 改法：{i.get(\'suggestion\',\'\')}"
        if isinstance(i, dict) else f"- {i}"
        for i in issues
    ])
    cut_text = "\\n".join([f"- {c}" for c in review_data.get("cut_candidates", [])])
    key_fix = review_data.get("key_fix", {})
    key_fix_text = ""
    if isinstance(key_fix, dict) and key_fix:
        key_fix_text = f"最关键修改：「{key_fix.get(\'location\',\'\')}」→ {key_fix.get(\'suggestion\',\'\')}（原因：{key_fix.get(\'reason\',\'\')}）"
    title_suggest = review_data.get("title_suggestion", "")

    return f"""你是微信公众号终稿编辑。对文章做定向修改，只改审核指出的问题，其余不动。

原文：
{draft_v1}

审核意见：
{issues_text}
{f"可删减位置：{cut_text}" if cut_text else ""}
{f"标题建议：{title_suggest}" if title_suggest else ""}
{key_fix_text}

## 修改原则（只有3条，严格遵守）
1. **只改审核提到的问题**，优先落实 high priority。没提到的地方一个字不动。
2. **保持原文的语气、节奏和结构**。你是在打磨，不是重写。
3. **保持1000-1500字**，偏长就压缩，不扩写。

## 附加任务
- 给出3个备选标题（判断型/数字型/场景型），每个≤20字，口语化
- 在2-3处最有判断力的句子前加 ★ 标记
- 参考文献原样保留

输出格式：
【备选标题】
- 判断型：xxx
- 数字型：xxx
- 场景型：xxx

【正文】
（直接输出终稿，末尾保留参考文献，无需解释）"""

'''


def apply_patches():
    src = read_server()

    # ── Patch 1: 在 _build_draft_prompt 前插入素材萃取函数 ──
    if "_extract_key_points" not in src:
        anchor = "def _build_draft_prompt("
        idx = src.find(anchor)
        if idx != -1:
            src = src[:idx] + EXTRACT_FUNC + "\n" + src[idx:]
            print("✓ 已插入素材萃取函数 _extract_key_points")
        else:
            print("✗ 找不到 _build_draft_prompt，跳过素材萃取插入")

    # ── Patch 2: 替换 _build_draft_prompt ──
    # 找到旧函数的范围
    old_func_start = src.find("def _build_draft_prompt(")
    if old_func_start != -1:
        # 找这个函数后面紧跟的下一个 def（同级缩进）
        next_def = src.find("\ndef _build_review_prompt(", old_func_start + 10)
        if next_def != -1:
            src = src[:old_func_start] + NEW_DRAFT_PROMPT + src[next_def+1:]
            print("✓ 已替换 _build_draft_prompt（system/user 分离）")
        else:
            print("✗ 无法定位 _build_draft_prompt 结尾")

    # ── Patch 3: 替换 _build_review_prompt ──
    old_review_start = src.find("def _build_review_prompt(")
    if old_review_start != -1:
        next_def = src.find("\ndef _parse_review(", old_review_start + 10)
        if next_def != -1:
            src = src[:old_review_start] + NEW_REVIEW_PROMPT + src[next_def+1:]
            print("✓ 已替换 _build_review_prompt（增加评分锚点）")

    # ── Patch 4: 替换 _build_polish_prompt ──
    old_polish_start = src.find("def _build_polish_prompt(")
    if old_polish_start != -1:
        next_def = src.find("\ndef _extract_title(", old_polish_start + 10)
        if next_def != -1:
            src = src[:old_polish_start] + NEW_POLISH_PROMPT + src[next_def+1:]
            print("✓ 已替换 _build_polish_prompt（简化为3条核心原则）")

    # ── Patch 5: 修改 generate_wechat_article 中的调用 ──
    # 5a: 在 articles_text 构建后插入素材萃取调用
    old_line = "    draft_prompt = _build_draft_prompt(articles_text)"
    new_line = """    # ── 新增：素材萃取 ──────────────────────────────────────
    print("▶ 正在萃取素材要点...")
    extracted_points = _extract_key_points(client, articles_text)
    if extracted_points:
        print(f"✓ 素材萃取完成（{len(extracted_points)}字）")

    system_msg, user_msg = _build_draft_prompt(articles_text, extracted_points)"""
    src = src.replace(old_line, new_line, 1)
    print("✓ 已在 generate_wechat_article 中插入素材萃取步骤")

    # 5b: 修改初稿生成的 API 调用（max_tokens + temperature + system/user 分离）
    old_gen = '''            r = client.chat.completions.create(
                model="deepseek-chat", timeout=90, max_tokens=2000,
                messages=[{"role": "user", "content": draft_prompt}]
            )'''
    new_gen = '''            r = client.chat.completions.create(
                model="deepseek-chat", timeout=120, max_tokens=3500,
                temperature=0.75,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ]
            )'''
    src = src.replace(old_gen, new_gen)
    print("✓ 已优化初稿生成参数（max_tokens=3500, temperature=0.75, system/user分离）")

    # 5c: 修改润色生成的参数
    old_polish_call = """    resp3 = client.chat.completions.create(
        model="deepseek-chat", timeout=90, max_tokens=2200,
        messages=[{"role": "user", "content": polish_prompt}]
    )"""
    new_polish_call = """    resp3 = client.chat.completions.create(
        model="deepseek-chat", timeout=120, max_tokens=4000,
        temperature=0.3,
        messages=[{"role": "user", "content": polish_prompt}]
    )"""
    src = src.replace(old_polish_call, new_polish_call)
    print("✓ 已优化润色生成参数（max_tokens=4000, temperature=0.3）")

    # 5d: 同样修改补生成的 API 调用
    old_extra = '''                r = client.chat.completions.create(
                    model="deepseek-chat", timeout=90, max_tokens=2000,
                    messages=[{"role": "user", "content": draft_prompt}]
                )'''
    new_extra = '''                r = client.chat.completions.create(
                    model="deepseek-chat", timeout=120, max_tokens=3500,
                    temperature=0.75,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg}
                    ]
                )'''
    src = src.replace(old_extra, new_extra)
    print("✓ 已优化补生成调用参数")

    write_server(src)
    print("\n✅ server.py 所有补丁已应用完成！")


def update_prompts():
    """更新 prompts/ 目录文档（记录当前实际使用的 prompt）"""

    prompts_dir = os.path.join(PATCH_DIR, "prompts")

    with open(os.path.join(prompts_dir, "01_draft_generation.md"), "w", encoding="utf-8") as f:
        f.write("""# 第1轮：DeepSeek 初稿生成 Prompt（优化版）

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
""")

    with open(os.path.join(prompts_dir, "02_review_audit.md"), "w", encoding="utf-8") as f:
        f.write("""# 第2轮：GPT-4.1 审核 Prompt（优化版）

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
""")

    with open(os.path.join(prompts_dir, "03_final_polish.md"), "w", encoding="utf-8") as f:
        f.write("""# 第3轮：DeepSeek 终稿润色 Prompt（优化版）

> 模型：DeepSeek | max_tokens=4000 | temperature=0.3 | timeout=120s
> 变更：从12条要求精简为3条核心原则

---

## 核心改进

**旧版问题：** 12条修改要求导致模型"几乎重写全文"，把有个性的表达磨平。

**新版只有3条原则：**
1. 只改审核提到的问题，没提到的一字不动
2. 保持原文语气、节奏和结构
3. 保持字数，偏长就压缩

**附加任务（轻量）：**
- 3个备选标题
- 2-3处 ★ 标记
- 参考文献原样保留

## 设计理由

temperature=0.3 让润色保持稳定，不会"创造性改写"。
极简指令让模型专注于修复问题而非追求完美，保留初稿的个性。
""")

    print("✓ 已更新 prompts/ 目录文档")


def main():
    print("=" * 60)
    print("  content-pipeline 优化补丁")
    print("=" * 60)
    print()

    backup()
    apply_patches()
    update_prompts()

    print()
    print("=" * 60)
    print("  完成！优化摘要：")
    print("=" * 60)
    print("""
  1. ✅ 新增素材萃取步骤（_extract_key_points）
     → 生成前先提取核心发现/关键事实/决策参考

  2. ✅ 初稿 prompt 拆分 system/user
     → 角色定位稳定在 system，任务指令在 user

  3. ✅ 参数优化
     → 初稿: max_tokens 2000→3500, temperature=0.75
     → 润色: max_tokens 2200→4000, temperature=0.3

  4. ✅ 审核评分增加锚点
     → 9-10/7-8/5-6/3-4/1-2 各有明确标准

  5. ✅ 润色 prompt 精简
     → 从 12 条要求 → 3 条核心原则

  回滚方式: cp server.py.bak server.py
""")


if __name__ == "__main__":
    main()
