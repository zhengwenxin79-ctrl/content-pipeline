"""
CV 节点检测 PoC：用 OpenCV 在论文图上找矩形/方框节点。
目标：验证传统 CV 能否准确定位流程图节点（不依赖 LLM 给坐标）。

用法:
  python tests/cv_poc/detect_boxes.py [--page N] [--pdf path.pdf]
默认渲染 mpnet.pdf 第 1 页

输出:
  tests/cv_poc/out_raw.png        — 原图
  tests/cv_poc/out_edges.png      — 边缘检测中间结果
  tests/cv_poc/out_detected.png   — 检测到的所有矩形框（粗略）
  tests/cv_poc/out_filtered.png   — 过滤后的候选节点（带编号）
"""
import sys
import os
import re
from pathlib import Path

import cv2
import fitz  # PyMuPDF
import numpy as np


HERE = Path(__file__).parent


def render_pdf_page(pdf_path: Path, page_num: int = 0, dpi: int = 200) -> np.ndarray:
    """PDF 页面 → numpy BGR 图像"""
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    doc.close()
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def detect_figure_regions(pdf_path: Path, page_num: int, render_dpi: int = 200):
    """
    用 PyMuPDF 的文字块定位识别 figure 区域：
    figure 通常出现在 "没有大量正文" 的连续区域。
    返回 [(x1, y1, x2, y2), ...] 像素坐标（render_dpi 下）

    策略：找到 "Figure N" 或 "Fig. N" 文字所在 y 坐标，
    figure 区域通常在它上方一段连续无文字密集区。
    """
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    page_w, page_h = page.rect.width, page.rect.height
    scale = render_dpi / 72

    # 用 textpage 拿所有文字块及其位置
    blocks = page.get_text("blocks")  # [(x0, y0, x1, y1, text, block_no, block_type), ...]
    text_blocks = [b for b in blocks if b[6] == 0 and len(b[4].strip()) > 5]

    # 找到 figure caption（以 "Fig" 开头的文字块）
    fig_captions = []
    for b in text_blocks:
        text = b[4].strip()
        if re.match(r"(?i)^(Figure|Fig\.|Fig\b)\s*\d+", text):
            fig_captions.append(b)  # caption 顶部 y0 即为 figure 底部

    if not fig_captions:
        # 没找到 caption，返回整页
        doc.close()
        return [(0, 0, int(page_w * scale), int(page_h * scale))]

    figure_regions = []
    for cap in fig_captions:
        cap_x0, cap_y0, cap_x1, cap_y1 = cap[:4]
        # figure 上边界：往上找最近的"大片空白" 或 页面顶部 / 上一个 caption 之后
        upper_y = 0
        for b in text_blocks:
            bx0, by0, bx1, by1 = b[:4]
            # 在 caption 上方且是较短的文本（如标题/换行符），跳过
            if by1 < cap_y0 - 10 and by1 > upper_y:
                # 是不是大段正文？大段正文则把它作为 figure 上边界
                if len(b[4].strip()) > 100:
                    upper_y = by1
        # figure 区域：左右大致跨整页（论文图通常占 1-2 栏）
        x0 = 0
        x1 = page_w
        y0 = upper_y
        y1 = cap_y0

        # 转像素坐标
        figure_regions.append((
            int(x0 * scale), int(y0 * scale),
            int(x1 * scale), int(y1 * scale)
        ))

    doc.close()
    return figure_regions


def detect_rectangular_boxes(img: np.ndarray,
                              min_area_ratio: float = 0.0006,
                              max_area_ratio: float = 0.30,
                              min_side_px: int = 28):
    """
    检测图中矩形节点。返回 [(x, y, w, h, score), ...]

    优化点：
      1. 加最小边长（35px）—— 过滤色块碎片
      2. interior 密度判断 —— 区分空心节点 vs 实心色块/波形/文字段
      3. NMS 反向：嵌套时保留**大框**（容器优先，如 Encoder > 内部 Conv 色条）
    """
    H, W = img.shape[:2]
    total_area = H * W

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 100)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)
    cv2.imwrite(str(HERE / "out_edges.png"), closed)

    contours, _ = cv2.findContours(closed, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if area < total_area * min_area_ratio:
            continue
        if area > total_area * max_area_ratio:
            continue
        if w < min_side_px or h < min_side_px:
            continue
        ratio = w / h if h > 0 else 0
        if ratio < 0.25 or ratio > 6:
            continue

        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
        is_rect = 4 <= len(approx) <= 8

        # interior 分析：取框内部（去掉边框 8px 内边距）的像素特征
        pad = 8
        ix0, iy0 = x + pad, y + pad
        ix1, iy1 = x + w - pad, y + h - pad
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        interior_edges = closed[iy0:iy1, ix0:ix1]
        interior_gray  = gray[iy0:iy1, ix0:ix1]

        # 1) 内部边缘密度：节点内部应该稀疏（只有 label 文字），波形/文字段会很密
        edge_density = interior_edges.mean() / 255.0  # 0-1
        # 2) 内部颜色方差：实心色块方差极低（几乎单色），节点框内大部分是白底
        color_std = float(interior_gray.std())
        # 3) 整体边缘密度（含边框）
        overall_density = closed[y:y+h, x:x+w].mean() / 255.0

        # 评分（宽松版本：CV 召回优先，SoM 阶段再让 Qwen 筛）
        score = 0.0
        if is_rect:                                   score += 0.30
        if 0.4 <= ratio <= 4.0:                       score += 0.15
        if edge_density < 0.18:                       score += 0.25  # 内部相对干净
        if 8 < color_std < 90:                        score += 0.15  # 不是纯色块
        if 0.015 <= overall_density <= 0.25:          score += 0.15  # 整体边缘密度合理

        # 硬性过滤：极端纹理（密集波形/段落）丢
        if edge_density > 0.40:
            continue
        # 硬性过滤：纯色块丢（color_std 极低 = 单色填充）
        if color_std < 5:
            continue

        boxes.append((x, y, w, h, score))

    # 宽松阈值：召回优先
    boxes = [b for b in boxes if b[4] >= 0.4]
    # NMS：嵌套时保留大框（容器优先），重叠 IoU > 0.4 也合并
    boxes = nms_boxes(boxes, iou_threshold=0.4)

    return boxes


def nms_boxes(boxes, iou_threshold=0.4):
    """
    NMS：嵌套关系下，仅当容器是"紧密包裹"（1.5x ~ 6x 大小）时合并；
    远超此范围的容器（如 panel 边界、整张 figure）不算合并，子节点保留。
    """
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: -(b[2] * b[3]))  # 大框先
    keep = []
    for b in boxes:
        x1, y1, w1, h1, s1 = b
        suppressed = False
        for k in keep:
            x2, y2, w2, h2, s2 = k
            area1, area2 = w1 * h1, w2 * h2
            # k 完全包含 b
            contained = (x2 <= x1 and y2 <= y1 and
                         x2 + w2 >= x1 + w1 and y2 + h2 >= y1 + h1)
            if contained:
                ratio = area2 / max(area1, 1)
                # 容器是当前框 1.5x~6x ：紧密包裹 → 合并（保留容器，删 b）
                # 容器超过 6x：是 panel 边界，不算合并
                if 1.5 <= ratio <= 6.0:
                    suppressed = True
                    break
                else:
                    continue  # 不算合并，继续检查下一个 k
            # 高度重叠（非嵌套）：取 score 更高的
            ix = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
            iy = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
            inter = ix * iy
            union = area1 + area2 - inter
            iou = inter / union if union > 0 else 0
            if iou > iou_threshold:
                suppressed = True
                break
        if not suppressed:
            keep.append(b)
    return keep


def draw_boxes(img: np.ndarray, boxes, with_numbers=True, color_by_score=True):
    """在原图上画检测框 + 编号，返回新图"""
    out = img.copy()
    for i, (x, y, w, h, score) in enumerate(boxes, start=1):
        # 颜色：score 高=绿色，低=橙色
        if color_by_score:
            if score >= 0.8:   color = (0, 200, 0)
            elif score >= 0.5: color = (0, 165, 255)
            else:              color = (100, 100, 200)
        else:
            color = (0, 200, 0)
        cv2.rectangle(out, (x, y), (x+w, y+h), color, 2)
        if with_numbers:
            label = f"{i}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            # 编号背景
            cv2.rectangle(out, (x, y), (x+tw+8, y+th+10), color, -1)
            cv2.putText(out, label, (x+4, y+th+4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return out


def main():
    pdf = HERE / "mpnet.pdf"
    page = 0
    args = sys.argv[1:]
    if "--pdf" in args:
        pdf = Path(args[args.index("--pdf") + 1])
    if "--page" in args:
        page = int(args[args.index("--page") + 1])

    if not pdf.exists():
        print(f"❌ PDF not found: {pdf}")
        sys.exit(1)

    print(f"渲染 {pdf.name} 第 {page+1} 页 @ 200 DPI ...")
    img = render_pdf_page(pdf, page, dpi=200)
    print(f"  尺寸: {img.shape[1]}x{img.shape[0]}")
    cv2.imwrite(str(HERE / "out_raw.png"), img)

    # Step 1: 用 PDF 文本结构定位 figure 区域
    print("\n[Step 1] 定位 figure 区域")
    fig_regions = detect_figure_regions(pdf, page, render_dpi=200)
    for i, (x1, y1, x2, y2) in enumerate(fig_regions, 1):
        print(f"  figure {i}: ({x1},{y1}) -> ({x2},{y2}), {x2-x1}x{y2-y1}px")

    # 可视化 figure 区域
    fig_overlay = img.copy()
    for x1, y1, x2, y2 in fig_regions:
        cv2.rectangle(fig_overlay, (x1, y1), (x2, y2), (0, 0, 255), 4)
    cv2.imwrite(str(HERE / "out_figure_region.png"), fig_overlay)

    # Step 2: 在每个 figure 区域内做 CV 检测
    print("\n[Step 2] CV 节点检测（仅 figure 区域内）")
    all_boxes = []
    for x1, y1, x2, y2 in fig_regions:
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        boxes = detect_rectangular_boxes(crop)
        # 转回整页坐标
        for x, y, w, h, s in boxes:
            all_boxes.append((x + x1, y + y1, w, h, s))

    print(f"  检测到 {len(all_boxes)} 个候选矩形框")
    high_score = [b for b in all_boxes if b[4] >= 0.7]
    print(f"  高置信度 (score≥0.7): {len(high_score)} 个")

    # Step 3: 可视化所有 + 高置信度
    out_all = draw_boxes(img, all_boxes, with_numbers=True, color_by_score=True)
    cv2.imwrite(str(HERE / "out_detected.png"), out_all)

    out_filtered = draw_boxes(img, high_score, with_numbers=True, color_by_score=False)
    cv2.imwrite(str(HERE / "out_filtered.png"), out_filtered)

    print(f"\n✅ 输出：")
    print(f"  {HERE / 'out_raw.png'}      — 原图")
    print(f"  {HERE / 'out_edges.png'}    — 边缘检测")
    print(f"  {HERE / 'out_detected.png'} — 全部候选（颜色按 score 分级：绿>橙>淡红）")
    print(f"  {HERE / 'out_filtered.png'} — 仅高置信度（score≥0.7）")
    print(f"\n现在打开这两张图肉眼检查：")
    print(f"  open {HERE / 'out_detected.png'}")
    print(f"  open {HERE / 'out_filtered.png'}")

    # 自动打开
    try:
        import subprocess
        subprocess.Popen(["open", str(HERE / "out_detected.png")])
        subprocess.Popen(["open", str(HERE / "out_filtered.png")])
    except Exception:
        pass


if __name__ == "__main__":
    main()
