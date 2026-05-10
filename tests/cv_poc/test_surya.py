"""
测试 surya layout 检测能力：能否精确定位 figure 区域？
能否给出每个文本块的位置（用于后续节点定位）？
"""
import sys
from pathlib import Path

import fitz
import numpy as np
import cv2
from PIL import Image

HERE = Path(__file__).parent


def render_pdf_page_pil(pdf_path: Path, page_num: int = 0, dpi: int = 200) -> Image.Image:
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def main():
    pdf = HERE / "mpnet.pdf"
    page = 1  # MPNet figure 在第 2 页
    args = sys.argv[1:]
    if "--page" in args:
        page = int(args[args.index("--page") + 1])

    print(f"[1] 加载 PDF 第 {page+1} 页 ...")
    pil_img = render_pdf_page_pil(pdf, page, dpi=200)
    print(f"    尺寸: {pil_img.size}")

    print(f"[2] 加载 surya layout 模型（首次会下载约 200MB）...")
    from surya.layout import batch_layout_detection
    from surya.detection import batch_text_detection
    from surya.model.detection.model import load_model, load_processor
    from surya.settings import settings

    layout_model = load_model(checkpoint=settings.LAYOUT_MODEL_CHECKPOINT)
    layout_processor = load_processor(checkpoint=settings.LAYOUT_MODEL_CHECKPOINT)
    det_model = load_model()
    det_processor = load_processor()

    print(f"[3] 跑 layout 检测 ...")
    line_predictions = batch_text_detection([pil_img], det_model, det_processor)
    predictions = batch_layout_detection([pil_img], layout_model, layout_processor, line_predictions)
    pred = predictions[0]
    print(f"    检测到 {len(pred.bboxes)} 个 layout 区域\n")

    # 打印所有区域类型 + 位置
    print(f"{'#':>3}  {'label':<14}  {'bbox':<28}  {'wxh':<12}")
    print("-" * 65)
    for i, b in enumerate(pred.bboxes, 1):
        x1, y1, x2, y2 = b.bbox
        w, h = int(x2 - x1), int(y2 - y1)
        bbox_str = f"({int(x1)},{int(y1)})→({int(x2)},{int(y2)})"
        print(f"{i:>3}  {b.label:<14}  {bbox_str:<28}  {w}x{h}")

    # 可视化：在原图画出每类区域，颜色编码
    cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    color_map = {
        "Picture":      (60, 200, 60),
        "Figure":       (60, 200, 60),
        "Text":         (160, 160, 160),
        "Caption":      (255, 165, 0),
        "Title":        (200, 60, 200),
        "SectionHeader":(200, 60, 200),
        "Formula":      (60, 60, 200),
        "List":         (100, 200, 200),
        "Table":        (255, 60, 60),
    }

    for i, b in enumerate(pred.bboxes, 1):
        x1, y1, x2, y2 = [int(v) for v in b.bbox]
        color = color_map.get(b.label, (128, 128, 128))
        cv2.rectangle(cv_img, (x1, y1), (x2, y2), color, 3)
        # label
        text = f"{i} {b.label}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(cv_img, (x1, y1), (x1 + tw + 8, y1 + th + 10), color, -1)
        cv2.putText(cv_img, text, (x1 + 4, y1 + th + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    out = HERE / "out_surya_layout.png"
    cv2.imwrite(str(out), cv_img)
    print(f"\n✅ 输出: {out}")

    # 找出 figure / picture 区域
    figs = [b for b in pred.bboxes if b.label in ("Picture", "Figure")]
    print(f"\nfigure 区域数: {len(figs)}")
    for f in figs:
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        print(f"  ({x1},{y1}) → ({x2},{y2}), {x2-x1}x{y2-y1}px")

    try:
        import subprocess
        subprocess.Popen(["open", str(out)])
    except Exception:
        pass


if __name__ == "__main__":
    main()
