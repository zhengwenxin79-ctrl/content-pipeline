"""
论文图动画化服务
功能：arXiv/bioRxiv PDF 自动下载 → 图片提取 → Qwen-VL-Max 识别结构 → DeepSeek-V3 生成 HTML 动画
按需调用，结果缓存到 article_animations 表。
"""

import os
import re
import html as _html
import base64
import json
import hashlib
import requests
from typing import Optional, List

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")

# 从 PDF 中最多渲染多少页（扫描上限）
MAX_IMAGES_PER_PDF = 8
# 找到几张机制图后停止（通常 1 张就够）
MAX_ANIM_RESULTS   = 1


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


def download_pdf(pdf_url: str, connect_timeout: int = 20,
                 total_timeout: int = 90, max_mb: int = 20) -> bytes:
    """下载 PDF，返回原始字节。超时或过大均抛出异常。"""
    import time
    headers = {"User-Agent": "Mozilla/5.0 (research bot; contact: research@example.com)"}
    resp = requests.get(pdf_url, headers=headers,
                        timeout=(connect_timeout, connect_timeout), stream=True)
    resp.raise_for_status()

    deadline = time.time() + total_timeout
    max_bytes = max_mb * 1024 * 1024
    chunks = []
    received = 0
    for chunk in resp.iter_content(chunk_size=65536):
        if time.time() > deadline:
            raise TimeoutError(f"PDF 下载超时（>{total_timeout}s），网络较慢请稍后重试或手动上传截图")
        if chunk:
            chunks.append(chunk)
            received += len(chunk)
            if received > max_bytes:
                raise ValueError(f"PDF 文件超过 {max_mb}MB，跳过")

    data = b"".join(chunks)
    if data[:4] != b"%PDF":
        raise ValueError(f"响应不是有效 PDF（Content-Type: {resp.headers.get('Content-Type')}）")
    return data


# ── PDF 图片提取 ───────────────────────────────────────────────────────────────

def extract_images_from_pdf(pdf_bytes: bytes) -> List[bytes]:
    """
    将 PDF 每页渲染为完整图片（而非仅提取嵌入光栅图）。
    这样矢量机制图、复合图形都能被 Qwen 看到。
    返回每页的 JPEG 字节列表，最多 MAX_IMAGES_PER_PDF 页。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("请安装 pymupdf：pip install pymupdf")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []

    for page_num in range(min(MAX_IMAGES_PER_PDF, len(doc))):
        page = doc[page_num]
        # 150 DPI 渲染，足够 Qwen 识别细节又不超限
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("jpeg", jpg_quality=85)
        images.append(img_bytes)

    doc.close()
    return images


def extract_page_captions(pdf_bytes: bytes, max_pages: int = None) -> list:
    """
    从 PDF 每页提取 Figure/Fig. 开头的图注文字。
    返回与 extract_images_from_pdf 等长的列表，无图注时为空字符串。

    Bug D 修复：原正则用 re.DOTALL 会让 .{10,300} 跨段落贪婪抓取，
    把多面板图（Fig 1a/b/c）后续段落甚至下一图注混成一段。
    现按段落（连续两行换行）切块，找到含 "Figure N" 的段落，选最长的一条。
    """
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n = min(max_pages or MAX_IMAGES_PER_PDF, len(doc))
    captions = []
    head_pat = re.compile(r'(?im)^\s*(?:Figure|Fig\.|Fig\b)\s*\d+\s*[.:|\-]?')
    for i in range(n):
        text = doc[i].get_text()
        # 按空行切段（PDF 文本中段落间一般会有空行）
        paragraphs = re.split(r'\n\s*\n', text)
        candidates = [p for p in paragraphs if head_pat.search(p)]
        if not candidates:
            captions.append("")
            continue
        # 选最长的一条作为信息量最大的 caption；多余空白合并、截断到 500 字符
        best = max(candidates, key=len)
        caption = re.sub(r'\s+', ' ', best).strip()[:500]
        captions.append(caption)
    doc.close()
    return captions


def image_hash(img_bytes: bytes) -> str:
    return hashlib.md5(img_bytes).hexdigest()


# ── Qwen-VL-Max 识图 ──────────────────────────────────────────────────────────

_QWEN_PROMPT_TMPL = """你是论文图分析专家。请判断这张图片是否包含**流程图/架构图/机制图**。{caption_section}
⚠️ 重要前提：此图可能是**整页PDF渲染**，即同一张图里既有大段文字又有图形。
**判断标准只看图形部分**，不管文字占多少比例——只要页面中存在符合条件的图形区域，就应该识别它。

【✅ 应该识别（只要图形区域里有方框+箭头，就识别）】
- 系统/模型架构图（神经网络结构、pipeline流程、模块连接图）
- 研究流程图（数据筛选漏斗图、实验设计、研究方法示意图）
- 临床/生物机制图（信号通路、疾病机制、药物作用）
- 数据流程图（数据处理步骤、标注流程、训练流程）
- 任何「方框+箭头」图，哪怕节点只是数字（如 N=78→N=51→N=24）或阶段名称（Phase 1→Phase 2）

【❌ 跳过（这些才跳过）】
- 纯统计图：柱状图、折线图、散点图、ROC曲线、热力图（没有流程箭头）
- 医学影像：X光、MRI、CT、病理切片、超声
- 实验结果凝胶图、印迹图
- 纯文字/表格页（完全没有图形元素）
- 自然照片、截图

【跳过时】{{"skip": true, "reason": "说明为什么不是流程/架构图"}}

【识别到图形时，返回】
{{
  "skip": false,
  "title": "图的中文简称（10字以内）",
  "diagram_region": {{"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}},
  "nodes": [
    {{"id": "n1", "label": "节点原文（英文或数字）", "label_zh": "中文含义（结合图注解释符号语义）", "type": "process|module|data|input|output|molecule|protein|cell|organ|drug", "x": 0.35, "y": 0.25}}
  ],
  "edges": [{{"from": "n1", "to": "n2", "label": "关系", "type": "activate|inhibit|transform|bind|express"}}],
  "overall_description": "一句话说明整体流程或机制（中文）"
}}

【节点标注规则】
- diagram_region：图形区域（不含图注caption文字）在整张图中的边界框（0.0~1.0）
- 节点 x/y：该节点方框中心在**整张图片**中的绝对坐标（左上角=0,0，右下角=1,1）
- label_zh：若节点是数学符号（如 Y^obs、f_θ），**必须**结合图注说明其语义，例如 Y^obs→"观测细胞表达矩阵"
- 对于数字流程框（如 N=78），label 写"N=78"，label_zh 写步骤含义（如"初始识别"）

只输出 JSON，不要任何说明文字。"""

# fallback 强制识别：不允许 skip，强制从图中提取图形结构
_QWEN_FALLBACK_PROMPT = """你是论文图分析专家。这张图片来自科研论文PDF页面渲染。{caption_section}⚠️ **强制识别模式**：你必须从图中找出任何可识别的图形元素（方框、圆圈、箭头、节点、流程线），不允许返回 skip。
即使图形不完全符合标准机制图，也要分析页面中最主要的图形区域并返回结构化信息。

【节点标注规则】
- diagram_region：图形区域（不含图注文字）在整张图中的边界框（0.0~1.0）
- 节点 x/y：节点中心在整张图片中的绝对坐标（左上角=0,0，右下角=1,1）
- label_zh：若图注中有对应说明，必须结合图注解释符号语义
- 至少返回 2 个节点

返回格式（纯 JSON，不要任何说明文字）：
{{
  "skip": false,
  "title": "图的中文简称（10字以内）",
  "diagram_region": {{"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}},
  "nodes": [
    {{"id": "n1", "label": "节点原文", "label_zh": "中文含义", "type": "process|module|data|input|output|molecule|protein|cell|organ|drug", "x": 0.35, "y": 0.25}}
  ],
  "edges": [{{"from": "n1", "to": "n2", "label": "关系", "type": "activate|inhibit|transform|bind|express"}}],
  "overall_description": "一句话说明整体流程或机制（中文）"
}}"""


def _build_qwen_prompt(caption: str = "", fallback: bool = False) -> str:
    if caption:
        section = f"\n\n【图注参考（Figure caption）】\n{caption}\n请结合图注理解图形含义，并用图注中的描述填充 label_zh 和 overall_description。\n\n"
    else:
        section = "\n\n"
    tmpl = _QWEN_FALLBACK_PROMPT if fallback else _QWEN_PROMPT_TMPL
    return tmpl.format(caption_section=section)


def _crop_to_diagram_region(image_bytes: bytes, dr: dict,
                             nodes: list, pad: float = 0.03) -> tuple:
    """
    按 diagram_region 裁剪图片并同步转换节点坐标。
    pad 为额外留白比例（避免节点热区被裁掉边缘）。
    裁剪区域过小（<20% 页面）或失败时原样返回。
    """
    import copy
    x1 = max(0.0, dr.get("x1", 0) - pad)
    y1 = max(0.0, dr.get("y1", 0) - pad)
    x2 = min(1.0, dr.get("x2", 1) + pad)
    y2 = min(1.0, dr.get("y2", 1) + pad)
    w, h = x2 - x1, y2 - y1

    # 区域已接近全页，或范围异常时跳过裁剪
    if w > 0.9 and h > 0.9:
        return image_bytes, nodes
    if w <= 0 or h <= 0:
        return image_bytes, nodes

    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        W, H = img.size
        cropped = img.crop((int(x1 * W), int(y1 * H), int(x2 * W), int(y2 * H)))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=90)

        # 节点坐标从全页归一化 → 裁剪图归一化
        new_nodes = []
        for n in copy.deepcopy(nodes):
            n["x"] = max(0.01, min(0.99, (n["x"] - x1) / w))
            n["y"] = max(0.01, min(0.99, (n["y"] - y1) / h))
            new_nodes.append(n)

        return buf.getvalue(), new_nodes
    except Exception:
        return image_bytes, nodes


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


def analyze_image_with_qwen(image_bytes: bytes, caption: str = "",
                             fallback: bool = False) -> dict:
    """
    调用 Qwen-VL-Max 分析图片结构。
    caption 传入图注文字时，用于辅助判断图类型和解释数学符号节点。
    fallback=True 时使用强制识别 prompt（不允许 skip）。
    返回 graph dict，或 {"skip": True, "reason": "..."}。
    """
    from openai import OpenAI

    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY 未配置")

    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    image_bytes = _compress_image(image_bytes)
    b64 = base64.b64encode(image_bytes).decode()
    mime = "image/jpeg"

    # Bug G：Qwen 偶发 Connection error / 5xx，瞬时网络抖动应重试一次再放弃。
    import time as _time
    last_err = None
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model="qwen2.5-vl-72b-instruct",
                timeout=90,
                temperature=0,
                max_tokens=4000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": _build_qwen_prompt(caption, fallback=fallback)},
                    ],
                }],
            )
            text = resp.choices[0].message.content.strip()
            return _extract_json(text)
        except Exception as e:
            last_err = e
            if attempt == 0:
                print(f"[qwen] 调用失败，3秒后重试一次：{e}")
                _time.sleep(3)
                continue
            raise
    raise last_err  # 不会到达，类型补全


# ── Pass-2 坐标校准 ────────────────────────────────────────────────────────────

_VERIFY_PROMPT = """这张图片中已识别出若干节点，下方列出了每个节点的估算坐标（x/y 为整图相对位置，左上角=0,0，右下角=1,1）。

已识别节点及其估算坐标：
{nodes_list}

请仔细对照图片，完成以下任务：
1. 检查每个节点坐标是否准确指向该标签/图形元素的视觉中心
2. 若坐标偏差超过 0.03，给出修正后的 x/y
3. 若某节点在图中完全找不到，标记 "missing": true

返回格式——纯 JSON 对象，key 为节点 id：
{{
  "n1": {{"x": 0.35, "y": 0.42}},
  "n2": {{"x": 0.65, "y": 0.18}},
  "n3": {{"missing": true}}
}}

只输出 JSON，不要任何说明文字。"""


def _verify_node_positions(image_bytes: bytes, nodes: list) -> list:
    """
    Pass-2 坐标校准：将原图 + Pass-1 估算坐标一起发给 Qwen，
    让它对照图片逐个核对并修正偏差超过 0.03 的坐标。
    失败时静默返回原始节点列表（不中断流程）。
    """
    from openai import OpenAI
    if not DASHSCOPE_API_KEY or not nodes:
        return nodes

    # 构建节点描述（只传 id、label、label_zh、当前 x/y）
    nodes_desc = "\n".join([
        f'- id={n["id"]}: "{n.get("label_zh") or n["label"]}" ({n["label"]})  '
        f'x={n["x"]:.3f}, y={n["y"]:.3f}'
        for n in nodes
    ])
    prompt = _VERIFY_PROMPT.format(nodes_list=nodes_desc)

    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    b64 = base64.b64encode(_compress_image(image_bytes)).decode()

    try:
        resp = client.chat.completions.create(
            model="qwen2.5-vl-72b-instruct",
            timeout=90,
            temperature=0,
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        corrections = _extract_json(resp.choices[0].message.content.strip())
    except Exception as e:
        print(f"[verify] Qwen 校准调用失败，保留原坐标：{e}")
        return nodes

    # 应用修正
    refined = []
    changed = 0
    for n in nodes:
        fix = corrections.get(n["id"])
        if not fix or fix.get("missing"):
            refined.append(n)
            continue
        new_x = fix.get("x", n["x"])
        new_y = fix.get("y", n["y"])
        if abs(new_x - n["x"]) > 0.01 or abs(new_y - n["y"]) > 0.01:
            changed += 1
        refined.append({**n, "x": new_x, "y": new_y})

    print(f"[verify] 校准完成，{changed}/{len(nodes)} 个节点坐标被修正")
    return refined


def _normalize_node_ids(graph: dict) -> dict:
    """
    去重 Qwen 返回的节点 id，重复的加 _2/_3 后缀以保证 DOM id 唯一。
    edges 中的引用按首次出现的 id 解析（重复节点在边图中不被引用，但仍能在 UI 上渲染和点击）。
    """
    nodes = graph.get("nodes", [])
    if not nodes:
        return graph
    seen = {}
    new_nodes = []
    for n in nodes:
        nid = n.get("id") or f"n{len(new_nodes)+1}"
        if nid in seen:
            seen[nid] += 1
            nid = f"{nid}_{seen[nid]}"
        else:
            seen[nid] = 1
        new_nodes.append({**n, "id": nid})
    return {**graph, "nodes": new_nodes}


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


# ── 节点轻量斥力（避免热区重叠）─────────────────────────────────────────────────

def _repel_overlapping_nodes(nodes: list, min_dist: float = 0.045) -> list:
    """
    overlay 模式专用：只在两节点距离过近时沿连线方向轻微推开，
    不做整体拉伸，保留节点与原图的位置对应关系。
    min_dist=0.045 约对应 800px 图宽下 36px 视觉间距，可保证 14px 圆点不互相覆盖。
    """
    import copy
    nodes = copy.deepcopy(nodes)
    if len(nodes) < 2:
        return nodes

    for _ in range(50):
        moved = False
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                dx = nodes[j]["x"] - nodes[i]["x"]
                dy = nodes[j]["y"] - nodes[i]["y"]
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < min_dist:
                    if dist < 1e-6:
                        # 完全重合：沿 x 轴拆开
                        nodes[j]["x"] += min_dist
                        moved = True
                        continue
                    push = (min_dist - dist) / 2 + 0.002
                    ux, uy = dx / dist, dy / dist
                    nodes[i]["x"] -= ux * push
                    nodes[i]["y"] -= uy * push
                    nodes[j]["x"] += ux * push
                    nodes[j]["y"] += uy * push
                    moved = True
        if not moved:
            break

    for n in nodes:
        n["x"] = max(0.02, min(0.98, n["x"]))
        n["y"] = max(0.02, min(0.98, n["y"]))
    return nodes


# ── DeepSeek-V3 生成 HTML 动画（两步法）─────────────────────────────────────────
# 第一步：只生成 SVG 渲染框架 + 交互代码（不含知识库，节省 tokens）
# 第二步：单独生成节点知识 JSON，注入到 HTML 的占位符处

_FRAME_PROMPT_PREFIX = """你是医疗AI领域的前端工程师，精通 SVG 动画和交互设计。
根据以下机制图 JSON 生成**完整可运行的 HTML 页面**，知识库由外部注入。

## 机制图数据
```json
"""

# suffix 模板，{VW}/{VH}/{MAPX}/{MAPY} 由代码动态替换
_FRAME_PROMPT_SUFFIX_TPL = """
```

## 严格技术规范

### 布局
- 顶部工具栏：标题（左）+ 「▶ 播放」id=playBtn + 「▲ 折叠」id=collapseBtn（右）
- 主区域：左栏 SVG 60% + 右栏解释面板 40%，flex 横排
- 折叠时隐藏主区域，按钮变「▼ 展开」

### SVG
- id="mainSvg"，viewBox="0 0 {VW} {VH}"，width="100%"，height 自适应容器
- 节点颜色：protein=#667eea, molecule=#48bb78, cell=#9f7aea, drug=#f6ad55, process=#4299e1, organ=#ed8936, 默认=#a0aec0
- 节点：圆角矩形 rx=10，宽120 高40，第一行显示 label（白色12px bold），第二行显示 label_zh（白色9px）
- 连线：贝塞尔曲线，activate=绿(#48bb78) inhibit=红(#fc8181) 其他=蓝(#667eea)，带箭头 marker，strokeWidth=1.5
- **节点坐标换算（严格按此公式）**：mapX = {PAD} + n.x * {MAPX}，mapY = {PAD} + n.y * {MAPY}
  节点矩形左上角：(mapX - 60, mapY - 20)；连线起止点为矩形中心 (mapX, mapY)
- **入场动画**：节点初始 opacity=0 scale(0.5)，用 setTimeout 每隔 120ms 依次设 opacity=1 scale(1)，transition 200ms ease，所有节点出现后再绘制连线（连线也做 opacity 0→1 动画，strokeDasharray 实现描线效果）

### 知识库接口（关键）
在 JS 最顶部声明：
```js
window.KNOWLEDGE_PLACEHOLDER = {};
```
点击节点时调用 showNodeInfo(nodeId)，读取 window.KNOWLEDGE_PLACEHOLDER[nodeId]，
若存在则展示 desc / clinical / ai 三段，否则显示 label_zh。
节点 hover 时 cursor:pointer，被点中时描边变白加粗。

### 右栏面板 id="infoPanel"
默认显示 overall_description 文字，点击节点后更新为三段卡片：
📖 名词解释 → desc
🏥 临床转化 → clinical
💻 AI建模 → ai

### 样式
背景 #f8fafc，字体 -apple-system 等，卡片圆角12px，阴影 0 2px 8px rgba(0,0,0,.08)

## 输出
直接输出完整 HTML（从 <!DOCTYPE html> 开始到 </html> 结束），不要任何说明，不要代码块标记。"""

_KNOWLEDGE_PROMPT_BASE = """你是一位论文精读助手。根据节点列表%s，为每个节点生成帮助读者快速理解该图的中文解释。

节点列表（JSON）：
%s

【输出格式】纯 JSON 对象，key 为节点 id，每个节点包含两个字段：
{
  "n1": {
    "desc": "是什么：专业准确的解释。若节点名称含「类型/类别/步骤/阶段/方法」等可枚举概念，必须用①②③…逐条列出核心条目并加1-2字说明（≤200字）；否则1-2句概括（≤80字）",
    "role": "在本文的作用：结合%s说明该节点在论文核心方法/贡献中的具体职责（≤100字）"
  }
}

【要求】每个节点都必须输出，不能遗漏。只输出 JSON，不要任何说明文字。"""


def _build_knowledge_prompt(nodes_brief: list, abstract: str = "") -> str:
    nodes_json = json.dumps(nodes_brief, ensure_ascii=False, indent=2)
    if abstract and abstract.strip():
        ctx_hint = "和以下论文摘要"
        role_hint = "论文摘要"
        abstract_section = f"\n论文摘要：\n{abstract.strip()}\n"
    else:
        ctx_hint = ""
        role_hint = "图中信息"
        abstract_section = ""
    return _KNOWLEDGE_PROMPT_BASE % (ctx_hint, nodes_json + abstract_section, role_hint)


def _clean_html(text: str) -> str:
    text = text.strip()
    if text.startswith("```html"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _fetch_knowledge(graph_json: dict, abstract: str = "") -> dict:
    """调用 DeepSeek 生成节点知识卡片，失败返回空 dict。"""
    from openai import OpenAI
    if not DEEPSEEK_API_KEY:
        return {}
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    nodes_brief = [
        {"id": n["id"], "label": n["label"], "label_zh": n.get("label_zh", ""), "type": n.get("type", "")}
        for n in graph_json.get("nodes", [])
    ]
    prompt = _build_knowledge_prompt(nodes_brief, abstract)
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat", timeout=120, max_tokens=6000, temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_json(resp.choices[0].message.content.strip())
    except Exception:
        return {}


def generate_animation_html(graph_json: dict, image_bytes: bytes = None,
                            abstract: str = "") -> str:
    """
    主入口：原图叠加交互热区模式（image_bytes 存在时）。
    abstract 传入论文摘要时，知识卡片将结合论文内容生成论文专属解读。
    """
    knowledge = _fetch_knowledge(graph_json, abstract)
    if image_bytes:
        return _build_overlay_html(graph_json, image_bytes, knowledge)
    return _fallback_html(graph_json)


def _build_overlay_html(graph_json: dict, image_bytes: bytes, knowledge: dict) -> str:
    """
    在原图上叠加带文字标签的可点击热区，右侧显示知识卡片。
    利用 diagram_region 修正坐标，使标签更精准。
    """
    title = graph_json.get("title", "机制图")
    desc  = graph_json.get("overall_description", "")
    nodes = graph_json.get("nodes", [])

    # Pass-2 坐标校准：在整页图坐标系下，让 Qwen 对照原图修正偏差
    nodes = _verify_node_positions(image_bytes, nodes)

    # 利用 diagram_region 做坐标映射：Qwen 的 x/y 是整图坐标，直接使用
    # 按 diagram_region 裁剪图片，避免显示整页 PDF 留下大片空白
    dr = graph_json.get("diagram_region", {"x1": 0, "y1": 0, "x2": 1, "y2": 1})
    image_bytes, nodes = _crop_to_diagram_region(image_bytes, dr, nodes)

    # 裁剪图坐标系下做轻量斥力，避免热区圆点重叠
    nodes = _repel_overlapping_nodes(nodes)

    img_b64 = base64.b64encode(image_bytes).decode()
    mime = "image/jpeg"

    color_map = {
        "protein": "#667eea", "molecule": "#48bb78", "cell": "#9f7aea",
        "drug": "#f6ad55",   "process": "#4299e1",  "organ": "#ed8936",
        "module": "#667eea", "data": "#48bb78", "operation": "#f6ad55",
        "input": "#68d391",  "output": "#fc8181",
    }

    # Bug C：转义所有外部字段，防止 Qwen/DeepSeek 输出含 < & 等字符破坏 HTML 或注入
    def _esc_str(v):
        return _html.escape(str(v)) if v is not None else ""

    def _esc_node(n):
        return {**n,
                "label":    _esc_str(n.get("label", "")),
                "label_zh": _esc_str(n.get("label_zh", "")),
                "type":     _esc_str(n.get("type", ""))}

    nodes_safe = [_esc_node(n) for n in nodes]
    knowledge_safe = {
        k: {kk: _esc_str(vv) for kk, vv in (v or {}).items()}
        for k, v in (knowledge or {}).items()
    }
    title_safe = _esc_str(title)
    desc_safe  = _esc_str(desc).replace("\n", "<br>")

    # JSON 嵌入到 <script> 中，需把 </ 转义防止字符串中出现 </script> 提前关闭
    def _safe_json(obj):
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    nodes_js     = _safe_json(nodes_safe)
    knowledge_js = _safe_json(knowledge_safe)
    color_map_js = _safe_json(color_map)
    dr_js        = _safe_json(dr)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f8fafc;color:#2d3748}}
.toolbar{{display:flex;align-items:center;gap:10px;padding:10px 16px;
          background:#f3ebff;border-bottom:1px solid #e9d8fd}}
.toolbar-title{{font-size:14px;font-weight:600;color:#553c9a;flex:1}}
.play-btn{{background:#6b46c1;color:white;border:none;padding:6px 16px;
           border-radius:8px;cursor:pointer;font-size:12px;font-weight:500}}
.play-btn:hover{{background:#553c9a}}
.collapse-btn{{background:none;border:1px solid #d6bcfa;color:#6b46c1;padding:5px 12px;
               border-radius:8px;cursor:pointer;font-size:12px}}
.main{{display:flex;height:calc(100vh - 46px);overflow:hidden}}
.img-scroll{{flex:0 0 62%;overflow:auto;background:#e2e8f0}}
.img-positioner{{position:relative;display:inline-block;min-width:100%}}
.img-positioner img{{display:block;width:100%}}
/* 热区：默认小圆点，hover 展开标签 */
.hotspot{{position:absolute;transform:translate(-50%,-50%);cursor:pointer;
          width:14px;height:14px;border-radius:50%;
          border:2px solid rgba(255,255,255,0.9);
          box-shadow:0 1px 4px rgba(0,0,0,.3);
          transition:all .15s ease;z-index:10}}
.hotspot:hover,.hotspot.active{{width:auto;height:auto;border-radius:20px;
          padding:3px 8px 3px 5px;display:flex;align-items:center;gap:4px;
          white-space:nowrap;font-size:11px;font-weight:600;color:white;
          box-shadow:0 2px 10px rgba(0,0,0,.4);z-index:20}}
.hotspot .hs-label{{display:none;line-height:1.3}}
.hotspot:hover .hs-label,.hotspot.active .hs-label{{display:inline}}
.hotspot .hs-dot{{width:7px;height:7px;border-radius:50%;
                  background:rgba(255,255,255,.85);flex-shrink:0;display:none}}
.hotspot:hover .hs-dot,.hotspot.active .hs-dot{{display:inline-block}}
.hotspot.active{{box-shadow:0 0 0 2.5px white,0 2px 10px rgba(0,0,0,.45)}}
@keyframes hs-pulse{{0%,100%{{box-shadow:0 0 0 0 currentColor}}
                     50%{{box-shadow:0 0 0 4px transparent}}}}
.hotspot.anim{{animation:hs-pulse 1.1s ease-in-out infinite}}
.info-panel{{flex:1;padding:16px;overflow-y:auto;border-left:1px solid #e9d8fd;
             background:white}}
.panel-default{{color:#4a5568;font-size:13px;line-height:1.7}}
.node-header{{display:flex;align-items:center;gap:8px;margin-bottom:14px}}
.node-dot{{width:14px;height:14px;border-radius:50%;flex-shrink:0}}
.node-name{{font-size:15px;font-weight:700;color:#2d3748}}
.node-en{{font-size:11px;color:#a0aec0;margin-top:2px}}
.card{{background:#fdf9ff;border:1px solid #e9d8fd;border-radius:12px;
       padding:12px 14px;margin-bottom:10px;font-size:13px;line-height:1.7}}
.card-label{{font-size:11px;font-weight:600;color:#6b46c1;margin-bottom:5px}}
.tip{{font-size:12px;color:#a0aec0;margin-top:10px;text-align:center}}
/* 节点目录 */
.dir-section{{border-bottom:1px solid #e9d8fd;padding-bottom:10px;margin-bottom:12px}}
.dir-title{{font-size:11px;font-weight:700;color:#a0aec0;letter-spacing:.05em;
            text-transform:uppercase;margin-bottom:6px}}
.dir-grid{{display:flex;flex-wrap:wrap;gap:5px}}
.dir-item{{display:flex;align-items:center;gap:5px;padding:4px 8px;border-radius:8px;
           cursor:pointer;font-size:12px;color:#4a5568;border:1px solid #e2e8f0;
           background:white;transition:all .15s;white-space:nowrap}}
.dir-item:hover{{background:#faf5ff;border-color:#d6bcfa;color:#553c9a}}
.dir-item.dir-active{{background:#6b46c1;color:white;border-color:#6b46c1}}
.dir-item.dir-active .dir-dot{{background:rgba(255,255,255,.8)}}
.dir-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
/* 知识卡片区 */
#nodeCard{{min-height:60px}}
</style>
</head>
<body>
<div class="toolbar">
  <span class="toolbar-title">🔬 {title_safe}</span>
  <button class="play-btn" id="playBtn" onclick="togglePlay()">▶ 逐步播放</button>
  <button class="collapse-btn" id="collapseBtn" onclick="toggleCollapse()">▲ 折叠</button>
</div>
<div class="main" id="mainArea">
  <div class="img-scroll" id="imgScroll">
    <div class="img-positioner" id="imgPos">
      <img src="data:{mime};base64,{img_b64}" id="mainImg" onload="initHotspots()" alt="{title_safe}">
      <div id="hotspotsLayer" style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none"></div>
    </div>
  </div>
  <div class="info-panel" id="infoPanel">
    <!-- 节点目录（始终可见） -->
    <div class="dir-section">
      <div class="dir-title">节点目录 · 点击查看详解</div>
      <div class="dir-grid" id="dirGrid"></div>
    </div>
    <!-- 知识卡片区 -->
    <div id="nodeCard">
      <div class="panel-default">
        <strong style="font-size:14px;display:block;margin-bottom:8px">整体描述</strong>
        {desc_safe}
      </div>
    </div>
  </div>
</div>
<script>
const NODES = {nodes_js};
const KNOWLEDGE = {knowledge_js};
const COLOR_MAP = {color_map_js};
const DR = {dr_js};

let activeId = null, playing = false, playTimer = null, playIdx = 0;

function initHotspots() {{
  const img   = document.getElementById('mainImg');
  const layer = document.getElementById('hotspotsLayer');
  layer.style.pointerEvents = 'auto';
  layer.innerHTML = '';

  const W = img.offsetWidth;
  const H = img.offsetHeight;

  // 节点目录（只建一次）
  const grid = document.getElementById('dirGrid');
  if (grid && !grid.dataset.built) {{
    grid.dataset.built = '1';
    NODES.forEach(n => {{
      const color = COLOR_MAP[n.type] || '#a0aec0';
      const item = document.createElement('div');
      item.className = 'dir-item';
      item.id = 'dir-' + n.id;
      item.innerHTML = `<span class="dir-dot" style="background:${{color}}"></span>${{n.label_zh || n.label}}`;
      item.onclick = () => showNode(n.id);
      grid.appendChild(item);
    }});
  }}

  // 图片上的圆点（resize 时重建像素坐标）
  NODES.forEach(n => {{
    const el = document.createElement('div');
    el.className = 'hotspot';
    el.id = 'hs-' + n.id;
    const color = COLOR_MAP[n.type] || '#a0aec0';
    el.style.background = color + 'dd';
    el.style.left = (n.x * W).toFixed(1) + 'px';
    el.style.top  = (n.y * H).toFixed(1) + 'px';
    el.innerHTML  = `<span class="hs-dot"></span><span class="hs-label">${{(n.label_zh||n.label).slice(0,10)}}</span>`;
    el.title = (n.label_zh || n.label) + ' (' + n.label + ')';
    el.onclick = () => showNode(n.id);
    layer.appendChild(el);
  }});
}}

window.addEventListener('resize', () => {{
  const img = document.getElementById('mainImg');
  if (img && img.complete) initHotspots();
}});

function showOverview() {{
  activeId = null;
  NODES.forEach(n => {{
    const el = document.getElementById('hs-' + n.id);
    if (el) el.classList.remove('active','anim');
  }});
  // 只重置右侧卡片区，节点目录保持不动
  document.getElementById('nodeCard').innerHTML = `
    <div class="panel-default" style="margin-bottom:0">
      <strong style="font-size:14px;display:block;margin-bottom:8px">整体描述</strong>
      {desc_safe}
    </div>`;
  // 清除目录高亮
  document.querySelectorAll('.dir-item').forEach(el => el.classList.remove('dir-active'));
}}

function showNode(id) {{
  if (activeId === id) {{ showOverview(); return; }}

  NODES.forEach(n => {{
    const el = document.getElementById('hs-' + n.id);
    if (el) el.classList.remove('active','anim');
  }});
  const hs = document.getElementById('hs-' + id);
  if (hs) {{
    hs.classList.add('active');
    // 滚动图片区让圆点进入视口
    hs.scrollIntoView({{behavior:'smooth', block:'center', inline:'nearest'}});
  }}
  activeId = id;

  // 目录高亮
  document.querySelectorAll('.dir-item').forEach(el => el.classList.remove('dir-active'));
  const dirEl = document.getElementById('dir-' + id);
  if (dirEl) dirEl.classList.add('dir-active');

  const n = NODES.find(x => x.id === id);
  if (!n) return;
  const k = KNOWLEDGE[id] || {{}};
  const color = COLOR_MAP[n.type] || '#a0aec0';
  const fmt = s => s ? s.replace(/\n/g, '<br>') : '';

  document.getElementById('nodeCard').innerHTML = `
    <div class="node-header">
      <div class="node-dot" style="background:${{color}}"></div>
      <div>
        <div class="node-name">${{n.label_zh || n.label}}</div>
        <div class="node-en">${{n.label}} · ${{n.type || ''}}</div>
      </div>
    </div>
    ${{k.desc ? `<div class="card"><div class="card-label">📖 是什么</div>${{fmt(k.desc)}}</div>` : ''}}
    ${{k.role ? `<div class="card"><div class="card-label">🔗 通路中的作用</div>${{fmt(k.role)}}</div>` : ''}}
    ${{!k.desc && !k.role ? `<div class="card">${{n.label_zh || n.label}}</div>` : ''}}
  `;
}}

function togglePlay() {{
  if (playing) {{
    playing = false;
    clearInterval(playTimer);
    document.getElementById('playBtn').textContent = '▶ 逐步播放';
    NODES.forEach(n => document.getElementById('hs-'+n.id)?.classList.remove('anim'));
  }} else {{
    playing = true;
    playIdx = 0;
    document.getElementById('playBtn').textContent = '⏹ 停止';
    playTimer = setInterval(() => {{
      if (playIdx >= NODES.length) {{
        playing = false;
        clearInterval(playTimer);
        document.getElementById('playBtn').textContent = '▶ 逐步播放';
        return;
      }}
      const n = NODES[playIdx++];
      showNode(n.id);
      const el = document.getElementById('hs-' + n.id);
      if (el) el.classList.add('anim');
    }}, 1400);
  }}
}}

let collapsed = false;
function toggleCollapse() {{
  collapsed = !collapsed;
  document.getElementById('mainArea').style.display = collapsed ? 'none' : 'flex';
  document.getElementById('collapseBtn').textContent = collapsed ? '▼ 展开' : '▲ 折叠';
}}
</script>
</body></html>"""


def _fallback_html(graph: dict) -> str:
    """DeepSeek 调用失败时的降级：静态节点列表展示。"""
    nodes = graph.get("nodes", [])
    title = _html.escape(graph.get("title", "机制图"))
    desc  = _html.escape(graph.get("overall_description", ""))

    color_map = {
        "protein": "#667eea", "molecule": "#48bb78", "cell": "#9f7aea",
        "drug": "#f6ad55", "process": "#4299e1", "organ": "#ed8936",
    }

    nodes_html = "".join([
        f'<span style="display:inline-block;margin:4px;padding:6px 14px;'
        f'border-radius:20px;background:{color_map.get(n.get("type",""), "#a0aec0")};'
        f'color:white;font-size:13px">'
        f'{_html.escape(n.get("label_zh") or n.get("label","") or "")}</span>'
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

def process_image(image_bytes: bytes, abstract: str = "", caption: str = "",
                  fallback: bool = False) -> dict:
    """
    单张图片完整流程：Qwen 识别 → DeepSeek 生成 HTML。
    caption 传入图注文字时，Qwen 用其辅助判断图类型和解释符号节点。
    fallback=True 时使用强制识别 prompt，不允许 skip。
    """
    try:
        graph = analyze_image_with_qwen(image_bytes, caption=caption, fallback=fallback)
    except Exception as e:
        return {"ok": False, "error": f"Qwen 识图失败：{e}"}

    if graph.get("skip"):
        return {"ok": False, "skipped": True, "reason": graph.get("reason", "不是机制图")}

    if len(graph.get("nodes", [])) < 2:
        return {"ok": False, "skipped": True, "reason": "节点数量不足，可能不是机制图"}

    # Bug F：Qwen 偶发返回重复节点 id，去重以保证前端 DOM 唯一
    graph = _normalize_node_ids(graph)

    try:
        html = generate_animation_html(graph, image_bytes=image_bytes, abstract=abstract)
        return {"ok": True, "html": html, "graph": graph}
    except Exception as e:
        fallback_html = _fallback_html(graph)
        return {"ok": False, "error": f"HTML 生成失败：{e}", "fallback_html": fallback_html, "graph": graph}


def _extract_abstract(pdf_bytes: bytes) -> str:
    """从 PDF 文本中自动提取 Abstract 段落，失败返回空字符串。"""
    try:
        import fitz, re
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for i, page in enumerate(doc):
            if i >= 3:
                break
            text += page.get_text()
        doc.close()

        # 匹配 Abstract 到下一个大段落标题之间的内容
        m = re.search(
            r'(?i)\bAbstract\b[\s\.\-:]*\n?(.*?)(?=\n\s*\n\s*[A-Z][A-Za-z\s]{2,20}\n|\n1[\.\s]|\Z)',
            text, re.DOTALL
        )
        if m:
            snippet = re.sub(r'\s+', ' ', m.group(1)).strip()
            return snippet[:2000]
    except Exception:
        pass
    return ""


def process_article_pdf(article_url: str, progress_cb=None,
                        abstract: str = "") -> List[dict]:
    """
    文章 URL 完整流程：推导 PDF URL → 下载 → 提取图片 → 逐张处理。
    abstract 传入时知识卡片将结合论文摘要生成论文专属解读。
    """
    def _cb(msg):
        if progress_cb:
            progress_cb(msg)

    # 推导 PDF URL
    pdf_url = derive_pdf_url(article_url)
    if not pdf_url:
        return [{"ok": False, "error": "该来源不支持自动下载 PDF，请手动上传图片"}]

    # 下载前 HEAD 请求获取文件大小，给用户预估时间
    # 注意：requests.head 默认不跟重定向，需显式 allow_redirects=True
    try:
        head = requests.head(pdf_url, timeout=8, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (research bot)"})
        size_bytes = int(head.headers.get("Content-Length", 0))
        if size_bytes > 50_000:  # 至少 50KB 才算是真实 PDF 大小，过滤掉重定向 HTML
            size_mb = size_bytes / 1024 / 1024
            est_sec = max(10, int(size_mb * 8))  # 粗估：约1MB/s
            _cb(f"📥 正在下载论文 PDF（{size_mb:.1f} MB，预计 {est_sec} 秒）...")
        else:
            _cb("📥 正在下载论文 PDF...")
    except Exception:
        _cb("📥 正在下载论文 PDF...")

    try:
        pdf_bytes = download_pdf(pdf_url)
    except (TimeoutError, requests.Timeout, requests.ConnectionError) as e:
        # Bug A：原本只 catch Python 内置 TimeoutError，
        # 但 requests 实际抛 requests.exceptions.ReadTimeout/ConnectTimeout/ConnectionError，
        # 这些都应该触发重试一次。
        _cb(f"⏳ 下载{'超时' if isinstance(e, (TimeoutError, requests.Timeout)) else '连接失败'}，正在重试（第2次，延长至120s）...")
        try:
            pdf_bytes = download_pdf(pdf_url, connect_timeout=30, total_timeout=120)
        except Exception as e2:
            return [{"ok": False, "error": f"PDF 下载失败（重试后仍失败）：{e2}（URL: {pdf_url}）"}]
    except Exception as e:
        return [{"ok": False, "error": f"PDF 下载失败：{e}（URL: {pdf_url}）"}]

    # 自动提取摘要（用户未手动提供时）
    if not abstract:
        _cb("📋 自动提取论文摘要...")
        abstract = _extract_abstract(pdf_bytes)

    # 提取图片
    _cb("🖼️ 正在提取论文图片...")
    try:
        images = extract_images_from_pdf(pdf_bytes)
    except Exception as e:
        return [{"ok": False, "error": f"图片提取失败：{e}"}]

    if not images:
        return [{"ok": False, "error": "PDF 中未找到符合尺寸要求的图片"}]

    # 提取每页图注
    _cb("📝 提取图注文字...")
    try:
        captions = extract_page_captions(pdf_bytes, max_pages=len(images))
    except Exception:
        captions = [""] * len(images)

    _cb(f"🔍 找到 {len(images)} 张候选图，逐张扫描机制图...")

    # 逐张扫描：跳过统计图/实验图，找到机制图即停止
    results = []
    skipped = 0
    fallback_candidates = []  # (img_bytes, caption, index) — 包含 skipped 和 errored，用于 fallback
    for i, img_bytes in enumerate(images):
        caption = captions[i] if i < len(captions) else ""
        _cb(f"🤖 Qwen 扫描第 {i+1}/{len(images)} 张图"
            + (f"（已跳过 {skipped} 张非机制图）" if skipped else "")
            + ("（含图注）" if caption else "") + "...")
        result = process_image(img_bytes, abstract=abstract, caption=caption)
        result["image_hash"] = image_hash(img_bytes)
        result["image_index"] = i

        if result.get("skipped"):
            skipped += 1
            fallback_candidates.append((img_bytes, caption, i))
            continue  # 不是机制图，继续往后找

        if not result.get("ok"):
            # error（Qwen/HTML 生成异常）也作为 fallback 候选；保留 result 以便最终展示错误信息
            fallback_candidates.append((img_bytes, caption, i))
            results.append(result)
            continue

        results.append(result)
        if len([r for r in results if r.get("ok")]) >= MAX_ANIM_RESULTS:
            break  # 已找到足够的机制图，停止

    # Bug B：原条件 `if not results` 会被 error 项填满，导致 fallback 永远不触发。
    # 改为：只要没有任何 ok 结果，就对候选图（含 skipped 和 errored）做强制识别。
    has_ok = any(r.get("ok") for r in results)
    if not has_ok and fallback_candidates:
        _cb(f"⚡ 未识别到标准机制图，对候选图进行强制识别（共 {len(fallback_candidates)} 张）...")
        for img_bytes, caption, i in fallback_candidates[:3]:
            _cb(f"🔄 强制识别第 {i+1} 张图...")
            result = process_image(img_bytes, abstract=abstract, caption=caption, fallback=True)
            result["image_hash"] = image_hash(img_bytes)
            result["image_index"] = i
            if not result.get("skipped"):
                results.append(result)
                if result.get("ok"):
                    break  # 找到一个可用结果即止

    if not results:
        return [{"ok": False, "skipped": True,
                 "reason": f"扫描了全部 {len(images)} 张图，均为统计图/实验图，未找到机制图",
                 "image_hash": "", "image_index": 0}]

    return results
