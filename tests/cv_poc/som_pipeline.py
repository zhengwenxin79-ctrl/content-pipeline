"""
Set-of-Mark 完整流水线：
  1. PDF → 渲染页面
  2. 文本结构定位 figure 区域
  3. CV 检测候选矩形（高召回）
  4. 在原图上叠加编号 → Qwen-VL 筛选 + 中文 label
  5. 输出 (坐标准的 CV bbox + 语义清晰的 Qwen 标签)

用法:
  python tests/cv_poc/som_pipeline.py [--page N]
"""
import sys
import os
import json
import base64
import io
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from detect_boxes import (
    render_pdf_page, detect_figure_regions, detect_rectangular_boxes,
)

# 加载 .env
_env = HERE.parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")


def draw_numbered_marks(img: np.ndarray, boxes) -> np.ndarray:
    """
    SoM 标记：在每个 box 中心位置画一个**显眼的彩色编号圆圈**，
    Qwen 视觉模型对这种 mark 识别率最高（参考 microsoft/SoM 论文）。
    """
    out = img.copy()
    # 调色板（高对比度，避免和图本身的色彩冲突）
    palette = [
        (255,  60,  60), ( 60, 200,  60), ( 60,  60, 255), (255, 165,   0),
        (200,  60, 200), (  0, 180, 180), (255,  20, 147), ( 50, 205,  50),
    ]

    for i, (x, y, w, h, _) in enumerate(boxes, start=1):
        color = palette[(i - 1) % len(palette)]
        # 框：细线（便于视觉对照）
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        # 编号圆圈：放在框左上角
        cx, cy = x + 16, y + 16
        cv2.circle(out, (cx, cy), 14, color, -1)
        cv2.circle(out, (cx, cy), 14, (255, 255, 255), 2)
        # 文字
        text = str(i)
        font = cv2.FONT_HERSHEY_SIMPLEX
        fs = 0.55 if i < 10 else 0.45
        (tw, th), _ = cv2.getTextSize(text, font, fs, 2)
        cv2.putText(out, text, (cx - tw // 2, cy + th // 2),
                    font, fs, (255, 255, 255), 2, cv2.LINE_AA)
    return out


_QWEN_SOM_PROMPT = """你正在分析一张论文机制图/流程图。我已用 OpenCV 检测了候选区域，**每个候选框的左上角都有一个彩色编号圆圈**。

请你完成两件事：
1. **筛选**：判断每个编号区域是否是一个真正的"流程图节点"（即流程图里方框、模块、子组件、数据/输入/输出节点）
2. **标注**：对真节点输出 label 原文（英文或符号原文）+ label_zh（中文含义）+ type

【真节点标准】
- 是一个**模块/组件/数据流单位**，不是装饰、空白、文字段落、整张子图边界
- 例：Encoder、Spatial Conv、High frequency 输入、Fusion Node、Tangent Space

【非节点（应过滤）】
- panel 整体边界（如 "(a)" 这种子图标签框、整个子图外边框）
- 装饰性元素（坐标轴、图注、纯背景色块）
- 重复检测（同一个真节点被标了两次）

只输出 JSON：
{
  "title": "整张图的中文简称（10字以内）",
  "overall_description": "一句话说明整张图的核心机制（中文）",
  "nodes": [
    {
      "mark_id": 3,
      "label": "Encoder",
      "label_zh": "编码器",
      "type": "module"
    }
  ]
}

类型 type 取值：input / output / module / process / data / molecule / protein / cell / organ / drug

不在 nodes 列表里的编号即视为"过滤掉"。
"""


def call_qwen_som(marked_image: np.ndarray, n_marks: int) -> dict:
    """调用 Qwen-VL-Max 做 SoM 筛选 + 标注"""
    import requests
    # PIL encode → base64
    pil = Image.fromarray(cv2.cvtColor(marked_image, cv2.COLOR_BGR2RGB))
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=88)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    if not DASHSCOPE_API_KEY:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")

    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
               "Content-Type": "application/json"}
    payload = {
        "model": "qwen-vl-max",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text",
                 "text": _QWEN_SOM_PROMPT + f"\n\n本图共 {n_marks} 个编号候选。"},
            ],
        }],
        "temperature": 0,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


def main():
    pdf = HERE / "mpnet.pdf"
    page = 0
    args = sys.argv[1:]
    if "--pdf" in args:
        pdf = Path(args[args.index("--pdf") + 1])
    if "--page" in args:
        page = int(args[args.index("--page") + 1])

    print(f"[1] 渲染 {pdf.name} 第 {page+1} 页 ...")
    img = render_pdf_page(pdf, page, dpi=200)

    print(f"[2] 定位 figure 区域 ...")
    fig_regions = detect_figure_regions(pdf, page, render_dpi=200)
    print(f"    {len(fig_regions)} 个 figure 区域")

    print(f"[3] CV 候选检测 ...")
    all_boxes = []
    for x1, y1, x2, y2 in fig_regions:
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        boxes = detect_rectangular_boxes(crop)
        for x, y, w, h, s in boxes:
            all_boxes.append((x + x1, y + y1, w, h, s))
    print(f"    {len(all_boxes)} 个候选")

    if not all_boxes:
        print("❌ 没有候选框，无法进入 SoM 阶段")
        sys.exit(1)

    # 限制最多 30 个，太多会让 Qwen 困惑
    all_boxes = sorted(all_boxes, key=lambda b: -b[4])[:30]

    print(f"[4] 在原图上叠加 {len(all_boxes)} 个编号标记 ...")
    marked = draw_numbered_marks(img, all_boxes)
    cv2.imwrite(str(HERE / "out_marked.png"), marked)

    print(f"[5] 调用 Qwen-VL-Max 做 SoM 筛选 + 标注 ...")
    try:
        result = call_qwen_som(marked, n_marks=len(all_boxes))
    except Exception as e:
        print(f"❌ Qwen 调用失败: {e}")
        sys.exit(1)

    print(f"\n=== Qwen 返回 ===")
    print(f"标题: {result.get('title', '')}")
    print(f"描述: {result.get('overall_description', '')}")
    nodes_kept = result.get("nodes", [])
    print(f"\n保留 {len(nodes_kept)} / {len(all_boxes)} 个节点：")
    for n in nodes_kept:
        print(f"  #{n.get('mark_id'):>2}  [{n.get('type', '')}]  "
              f"{n.get('label_zh', '')}  ({n.get('label', '')})")

    # 输出最终图：只画被保留的节点 + 中文标签
    print(f"\n[6] 渲染最终结果图 ...")
    final = img.copy()
    kept_marks = {n["mark_id"]: n for n in nodes_kept}
    palette = [(255,60,60),(60,200,60),(60,60,255),(255,165,0),
               (200,60,200),(0,180,180),(255,20,147),(50,205,50)]

    for i, (x, y, w, h, _) in enumerate(all_boxes, start=1):
        if i not in kept_marks:
            continue
        n = kept_marks[i]
        color = palette[(i - 1) % len(palette)]
        cv2.rectangle(final, (x, y), (x + w, y + h), color, 3)
        # 节点标签（中文）：写在框上方
        label = n.get("label_zh", "") or n.get("label", "")
        # PIL 写中文（cv2 不支持中文）
        pil_img = Image.fromarray(cv2.cvtColor(final, cv2.COLOR_BGR2RGB))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(pil_img)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 18)
        except Exception:
            font = ImageFont.load_default()
        tx, ty = x, max(0, y - 24)
        bbox = draw.textbbox((tx, ty), label, font=font)
        draw.rectangle(bbox, fill=tuple(color))
        draw.text((tx, ty), label, fill=(255, 255, 255), font=font)
        final = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    cv2.imwrite(str(HERE / "out_final.png"), final)

    print(f"\n✅ 输出文件：")
    print(f"   {HERE / 'out_marked.png'}  — CV 候选 + 编号（喂给 Qwen 的图）")
    print(f"   {HERE / 'out_final.png'}   — Qwen 筛选后 + 中文标签（最终结果）")

    try:
        import subprocess
        subprocess.Popen(["open", str(HERE / "out_marked.png")])
        subprocess.Popen(["open", str(HERE / "out_final.png")])
    except Exception:
        pass


if __name__ == "__main__":
    main()
