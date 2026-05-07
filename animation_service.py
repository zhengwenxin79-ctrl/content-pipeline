"""
论文图动画化服务
功能：arXiv/bioRxiv PDF 自动下载 → 图片提取 → Qwen-VL-Max 识别结构 → DeepSeek-V3 生成 HTML 动画
按需调用，结果缓存到 article_animations 表。
"""

import os
import re
import base64
import json
import hashlib
import requests
from typing import Optional, List

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")

# 最小机制图尺寸（像素），太小的图跳过
MIN_IMG_WIDTH  = 300
MIN_IMG_HEIGHT = 200
# 单次最多处理几张图（防止费用失控）
MAX_IMAGES_PER_PDF = 6


# ── PDF 下载 ───────────────────────────────────────────────────────────────────

def derive_pdf_url(article_url: str) -> Optional[str]:
    """
    从文章页面 URL 推导直接 PDF 下载地址。
    支持 arXiv、bioRxiv、medRxiv、PubMed Central。
    无法识别的来源返回 None。
    """
    if not article_url:
        return None
    u = article_url.strip().rstrip("/")

    # arXiv: https://arxiv.org/abs/2401.12345  →  https://arxiv.org/pdf/2401.12345.pdf
    m = re.match(r"https?://arxiv\.org/abs/([^\s?#]+)", u)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"

    # bioRxiv / medRxiv: 末尾加 .full.pdf
    if re.match(r"https?://(www\.)?(biorxiv|medrxiv)\.org/content/", u):
        base = re.sub(r"\.(abstract|full|full\.pdf)$", "", u)
        return base + ".full.pdf"

    # PubMed Central: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456/
    m = re.match(r"https?://www\.ncbi\.nlm\.nih\.gov/pmc/articles/(PMC\d+)/?", u)
    if m:
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{m.group(1)}/pdf/"

    return None


def download_pdf(pdf_url: str, timeout: int = 30) -> bytes:
    """下载 PDF，返回原始字节。失败抛出异常。"""
    headers = {"User-Agent": "Mozilla/5.0 (research bot; contact: research@example.com)"}
    resp = requests.get(pdf_url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    if "application/pdf" not in resp.headers.get("Content-Type", "") and not resp.content[:4] == b"%PDF":
        raise ValueError(f"响应不是 PDF（Content-Type: {resp.headers.get('Content-Type')}）")
    return resp.content


# ── PDF 图片提取 ───────────────────────────────────────────────────────────────

def extract_images_from_pdf(pdf_bytes: bytes) -> List[bytes]:
    """
    用 PyMuPDF 从 PDF 中提取图片，过滤掉太小的图（logo、图标等）。
    返回图片 PNG/JPEG 字节列表，最多 MAX_IMAGES_PER_PDF 张。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("请安装 pymupdf：pip install pymupdf")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    seen_hashes = set()

    for page in doc:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue

            w, h = base_image.get("width", 0), base_image.get("height", 0)
            if w < MIN_IMG_WIDTH or h < MIN_IMG_HEIGHT:
                continue

            img_bytes = base_image["image"]
            # 去重（同一张图可能出现在多页）
            digest = hashlib.md5(img_bytes).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            images.append(img_bytes)
            if len(images) >= MAX_IMAGES_PER_PDF:
                break
        if len(images) >= MAX_IMAGES_PER_PDF:
            break

    doc.close()
    return images


def image_hash(img_bytes: bytes) -> str:
    return hashlib.md5(img_bytes).hexdigest()


# ── Qwen-VL-Max 识图 ──────────────────────────────────────────────────────────

_QWEN_PROMPT = """请分析这张图片，判断它是否是医学或生物学**机制图**（展示分子通路、信号传导、细胞过程、药物作用机制等流程/网络图）。

【不是机制图的情况，直接返回】
如果是以下类型，返回 {"skip": true, "reason": "..."}：
- 统计图表（柱状图、折线图、散点图、ROC曲线等）
- 医学影像（MRI、CT、病理切片、超声图像等）
- 实验结果图（Western blot、凝胶电泳等）
- 表格、流程表、研究设计图
- 照片

【是机制图时，返回以下 JSON】
{
  "skip": false,
  "title": "机制简称（中文，10字以内）",
  "nodes": [
    {"id": "n1", "label": "节点英文原名", "label_zh": "中文名", "type": "molecule|protein|cell|organ|process|drug", "x": 0.3, "y": 0.2}
  ],
  "edges": [
    {"from": "n1", "to": "n2", "label": "作用", "type": "activate|inhibit|bind|transform|express"}
  ],
  "overall_description": "一句话描述整体机制（中文）"
}

节点坐标 x/y 为相对位置（0.0-1.0），从图中元素的视觉位置估算，左上角为(0,0)。
节点类型说明：molecule=小分子/代谢物, protein=蛋白质/受体/酶, cell=细胞类型, organ=器官/组织, process=生理/病理过程, drug=药物/抑制剂。
边的类型：activate=激活/促进, inhibit=抑制/阻断, bind=结合, transform=转化/产生, express=表达/分泌。

只输出 JSON，不要任何说明文字。"""


def _compress_image(image_bytes: bytes, max_side: int = 1200, quality: int = 85) -> bytes:
    """将图片压缩到 max_side 长边以内，减少 API 传输量。"""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        fmt = "JPEG" if image_bytes[:3] == b"\xff\xd8\xff" else "PNG"
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return image_bytes  # 压缩失败则原图传输


def analyze_image_with_qwen(image_bytes: bytes) -> dict:
    """
    调用 Qwen-VL-Max 分析图片结构。
    返回 graph dict，或 {"skip": True, "reason": "..."}。
    失败时抛出异常。
    """
    from openai import OpenAI

    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY 未配置")

    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    # 压缩图片降低传输量，加快响应速度
    image_bytes = _compress_image(image_bytes)
    b64 = base64.b64encode(image_bytes).decode()
    mime = "image/jpeg"  # 压缩后统一 JPEG

    resp = client.chat.completions.create(
        model="qwen-vl-max",
        timeout=90,
        max_tokens=2500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": _QWEN_PROMPT},
            ],
        }],
    )

    text = resp.choices[0].message.content.strip()
    return _extract_json(text)


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON，容忍前后有多余文字或代码块标记。"""
    # 去掉代码块标记
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]  # 去掉第一行 ```json 或 ```
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    # 先尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 找第一个 { 到最后一个 } 之间的内容
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从模型输出中提取有效 JSON，原始内容前200字：{text[:200]}")


# ── DeepSeek-V3 生成 HTML 动画（两步法）─────────────────────────────────────────
# 第一步：只生成 SVG 渲染框架 + 交互代码（不含知识库，节省 tokens）
# 第二步：单独生成节点知识 JSON，注入到 HTML 的占位符处

_FRAME_PROMPT_PREFIX = """你是医疗AI领域的前端工程师，精通 SVG 动画和交互设计。
根据以下机制图 JSON 生成**完整可运行的 HTML 页面**，知识库由外部注入。

## 机制图数据
```json
"""

_FRAME_PROMPT_SUFFIX = """
```

## 严格技术规范

### 布局
- 顶部工具栏：标题（左）+ 「▶ 播放」id=playBtn + 「▲ 折叠」id=collapseBtn（右）
- 主区域：左栏 SVG 60% + 右栏解释面板 40%，flex 横排
- 折叠时隐藏主区域，按钮变「▼ 展开」

### SVG
- id="mainSvg"，viewBox="0 0 800 420"
- 节点颜色：protein=#667eea, molecule=#48bb78, cell=#9f7aea, drug=#f6ad55, process=#4299e1, organ=#ed8936, 默认=#a0aec0
- 节点：圆角矩形 rx=10，宽110 高36，中间显示 label（白色13px），下方显示 label_zh（白色9px）
- 连线：贝塞尔曲线，activate=绿 inhibit=红 其他=蓝，带箭头 marker
- 节点坐标：x/y 乘以 (800-80) 后加 40，即 mapX = 40 + n.x*720，mapY = 40 + n.y*340
- **入场动画**：节点初始 opacity=0，用 setTimeout 每隔 150ms 设 opacity=1 + transform scale(1)，所有节点出现后再绘制连线

### 知识库接口（关键）
在 JS 最顶部声明：
```js
window.KNOWLEDGE_PLACEHOLDER = {};
```
点击节点时调用 showNodeInfo(nodeId)，读取 window.KNOWLEDGE_PLACEHOLDER[nodeId]，
若存在则展示 desc / clinical / ai 三段，否则显示 label_zh。

### 右栏面板 id="infoPanel"
默认显示 overall_description 文字，点击节点后更新为三段卡片：
📖 名词解释 → desc
🏥 临床转化 → clinical
💻 AI建模 → ai

### 样式
背景 #f8fafc，字体 -apple-system 等，卡片圆角12px，阴影 0 2px 8px rgba(0,0,0,.08)

## 输出
直接输出完整 HTML（从 <!DOCTYPE html> 开始到 </html> 结束），不要任何说明，不要代码块标记。"""

_KNOWLEDGE_PROMPT = """根据以下生物学节点列表，为每个节点生成简洁准确的中文知识库。

节点列表（JSON）：
%s

输出格式——纯 JSON 对象，key 为节点 id，value 包含三个字段：
{
  "n1": {
    "desc": "名词解释（1-2句，专业准确）",
    "clinical": "临床转化阶段 + 代表药物（1句）",
    "ai": "1个真实开源数据集名称 + 1句建模思路"
  }
}

要求：每个字段不超过 80 字。只输出 JSON，不要任何说明文字。"""


def _clean_html(text: str) -> str:
    text = text.strip()
    if text.startswith("```html"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def generate_animation_html(graph_json: dict) -> str:
    """
    两步调用 DeepSeek-V3：
    1. 生成 SVG 渲染框架 HTML（不含知识库）
    2. 生成节点知识 JSON
    将知识 JSON 注入 HTML 中的占位符，返回完整 HTML。
    """
    from openai import OpenAI

    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY 未配置")

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    # ── 步骤 1：生成渲染框架 ──────────────────────────────────────
    graph_str = json.dumps(graph_json, ensure_ascii=False, indent=2)
    frame_prompt = _FRAME_PROMPT_PREFIX + graph_str + _FRAME_PROMPT_SUFFIX

    resp1 = client.chat.completions.create(
        model="deepseek-chat",
        timeout=180,
        max_tokens=6000,
        temperature=0.2,
        messages=[{"role": "user", "content": frame_prompt}],
    )
    html = _clean_html(resp1.choices[0].message.content)

    # 检查 HTML 是否完整（必须有结束标签）
    if "</html>" not in html.lower():
        raise ValueError("HTML 渲染框架不完整，DeepSeek 输出被截断")

    # ── 步骤 2：生成节点知识 JSON ─────────────────────────────────
    nodes_brief = [
        {"id": n["id"], "label": n["label"], "label_zh": n.get("label_zh", ""), "type": n.get("type", "")}
        for n in graph_json.get("nodes", [])
    ]
    knowledge_prompt = _KNOWLEDGE_PROMPT % json.dumps(nodes_brief, ensure_ascii=False, indent=2)

    resp2 = client.chat.completions.create(
        model="deepseek-chat",
        timeout=120,
        max_tokens=4000,
        temperature=0.2,
        messages=[{"role": "user", "content": knowledge_prompt}],
    )
    knowledge_text = resp2.choices[0].message.content.strip()
    try:
        knowledge = _extract_json(knowledge_text)
    except Exception:
        knowledge = {}  # 知识库生成失败不影响动画展示

    # ── 注入知识库 ────────────────────────────────────────────────
    knowledge_js = json.dumps(knowledge, ensure_ascii=False)
    html = html.replace(
        "window.KNOWLEDGE_PLACEHOLDER = {};",
        f"window.KNOWLEDGE_PLACEHOLDER = {knowledge_js};"
    )
    return html


def _fallback_html(graph: dict) -> str:
    """DeepSeek 调用失败时的降级：静态节点列表展示。"""
    nodes = graph.get("nodes", [])
    title = graph.get("title", "机制图")
    desc  = graph.get("overall_description", "")

    color_map = {
        "protein": "#667eea", "molecule": "#48bb78", "cell": "#9f7aea",
        "drug": "#f6ad55", "process": "#4299e1", "organ": "#ed8936",
    }

    nodes_html = "".join([
        f'<span style="display:inline-block;margin:4px;padding:6px 14px;'
        f'border-radius:20px;background:{color_map.get(n.get("type",""), "#a0aec0")};'
        f'color:white;font-size:13px">{n.get("label_zh") or n.get("label","")}</span>'
        for n in nodes
    ])

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body{{font-family:-apple-system,"PingFang SC",sans-serif;padding:16px;
       background:#f8fafc;color:#2d3748;margin:0}}
  h3{{font-size:15px;font-weight:600;margin-bottom:8px}}
  .desc{{font-size:13px;color:#4a5568;margin-bottom:14px;line-height:1.6}}
  .note{{margin-top:14px;font-size:12px;color:#a0aec0;border-top:1px solid #e2e8f0;padding-top:10px}}
</style>
</head><body>
<h3>{title}</h3>
<p class="desc">{desc}</p>
<div style="margin-bottom:6px;font-size:12px;color:#a0aec0;font-weight:600">涉及节点</div>
<div>{nodes_html}</div>
<p class="note">动画生成暂时失败，显示简化版。刷新页面或稍后重试可重新生成。</p>
</body></html>"""


# ── 完整流程入口 ───────────────────────────────────────────────────────────────

def process_image(image_bytes: bytes) -> dict:
    """
    单张图片完整流程：Qwen 识别 → DeepSeek 生成 HTML。

    返回值之一：
      {"ok": True,  "html": str, "graph": dict}           成功
      {"ok": False, "skipped": True, "reason": str}       非机制图，跳过
      {"ok": False, "error": str, "fallback_html": str}   失败但有降级页面
    """
    # 1. Qwen 识图
    try:
        graph = analyze_image_with_qwen(image_bytes)
    except Exception as e:
        return {"ok": False, "error": f"Qwen 识图失败：{e}"}

    if graph.get("skip"):
        return {"ok": False, "skipped": True, "reason": graph.get("reason", "不是机制图")}

    nodes = graph.get("nodes", [])
    if len(nodes) < 2:
        return {"ok": False, "skipped": True, "reason": "节点数量不足，可能不是机制图"}

    # 2. DeepSeek 生成 HTML
    try:
        html = generate_animation_html(graph)
        return {"ok": True, "html": html, "graph": graph}
    except Exception as e:
        fallback = _fallback_html(graph)
        return {"ok": False, "error": f"DeepSeek 生成失败：{e}", "fallback_html": fallback, "graph": graph}


def process_article_pdf(article_url: str, progress_cb=None) -> List[dict]:
    """
    文章 URL 完整流程：推导 PDF URL → 下载 → 提取图片 → 逐张处理。
    progress_cb(msg: str) 用于向前端报告进度（可选）。

    返回每张图的处理结果列表（含 image_hash、ok、html 等字段）。
    """
    def _cb(msg):
        if progress_cb:
            progress_cb(msg)

    # 推导 PDF URL
    pdf_url = derive_pdf_url(article_url)
    if not pdf_url:
        return [{"ok": False, "error": "该来源不支持自动下载 PDF，请手动上传图片"}]

    # 下载 PDF
    _cb(f"📥 正在下载论文 PDF...")
    try:
        pdf_bytes = download_pdf(pdf_url)
    except Exception as e:
        return [{"ok": False, "error": f"PDF 下载失败：{e}（URL: {pdf_url}）"}]

    # 提取图片
    _cb("🖼️ 正在提取论文图片...")
    try:
        images = extract_images_from_pdf(pdf_bytes)
    except Exception as e:
        return [{"ok": False, "error": f"图片提取失败：{e}"}]

    if not images:
        return [{"ok": False, "error": "PDF 中未找到符合尺寸要求的图片"}]

    _cb(f"🔍 找到 {len(images)} 张图，开始分析...")

    # 逐张处理
    results = []
    for i, img_bytes in enumerate(images):
        _cb(f"🤖 Qwen 分析第 {i+1}/{len(images)} 张图...")
        result = process_image(img_bytes)
        result["image_hash"] = image_hash(img_bytes)
        result["image_index"] = i
        results.append(result)

    return results
