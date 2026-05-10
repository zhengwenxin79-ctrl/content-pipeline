"""
测试在 surya 切出来的 figure 区域内做 OCR。
思路：figure 内部的"节点"通常都是带文字的方框 → OCR 识别到的文字位置就是节点位置。
比 CV 找轮廓更可靠，比 Qwen 看图更不容易幻觉。
"""
import sys
from pathlib import Path

import fitz
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

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
    page = 1
    args = sys.argv[1:]
    if "--page" in args:
        page = int(args[args.index("--page") + 1])

    print(f"[1] 加载 PDF 第 {page+1} 页 ...")
    pil_img = render_pdf_page_pil(pdf, page, dpi=200)

    print(f"[2] 加载 surya 模型 ...")
    from surya.layout import batch_layout_detection
    from surya.detection import batch_text_detection
    from surya.ocr import run_ocr
    from surya.model.detection.model import load_model as det_load_model, load_processor as det_load_processor
    from surya.model.recognition.model import load_model as rec_load_model
    from surya.model.recognition.processor import load_processor as rec_load_processor
    from surya.settings import settings

    layout_model = det_load_model(checkpoint=settings.LAYOUT_MODEL_CHECKPOINT)
    layout_processor = det_load_processor(checkpoint=settings.LAYOUT_MODEL_CHECKPOINT)
    det_model = det_load_model()
    det_processor = det_load_processor()
    rec_model = rec_load_model()
    rec_processor = rec_load_processor()

    print(f"[3] Layout 检测找 figure 区域 ...")
    line_predictions = batch_text_detection([pil_img], det_model, det_processor)
    layout_predictions = batch_layout_detection([pil_img], layout_model, layout_processor, line_predictions)
    figs = [b for b in layout_predictions[0].bboxes if b.label in ("Picture", "Figure")]
    print(f"    {len(figs)} 个 figure")
    if not figs:
        print("❌ 没找到 figure")
        sys.exit(1)

    fx1, fy1, fx2, fy2 = [int(v) for v in figs[0].bbox]
    print(f"    figure: ({fx1},{fy1}) → ({fx2},{fy2})")

    print(f"[4] 在 figure 区域内做 OCR ...")
    crop = pil_img.crop((fx1, fy1, fx2, fy2))
    ocr_result = run_ocr([crop], [["en"]], det_model, det_processor, rec_model, rec_processor)
    text_lines = ocr_result[0].text_lines
    print(f"    识别到 {len(text_lines)} 个文字片段\n")

    # 打印每行文字 + 位置（相对于 figure crop）
    print(f"{'#':>3}  {'text':<35}  {'bbox':<28}")
    print("-" * 70)
    for i, t in enumerate(text_lines, 1):
        x1, y1, x2, y2 = [int(v) for v in t.bbox]
        # 转回原图坐标
        gx1, gy1, gx2, gy2 = x1 + fx1, y1 + fy1, x2 + fx1, y2 + fy1
        text_short = (t.text or "").strip()[:34]
        bbox_str = f"({gx1},{gy1})→({gx2},{gy2})"
        print(f"{i:>3}  {text_short:<35}  {bbox_str:<28}")

    # 可视化
    cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    # figure 边界（绿色粗框）
    cv2.rectangle(cv_img, (fx1, fy1), (fx2, fy2), (0, 200, 0), 4)
    # 写中文/英文用 PIL
    pil_overlay = Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_overlay)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 16)
    except Exception:
        font = ImageFont.load_default()

    palette = [(255,60,60),(60,150,255),(255,165,0),(150,60,200),
               (0,180,180),(255,20,147),(50,180,50),(200,100,50)]
    for i, t in enumerate(text_lines, 1):
        x1, y1, x2, y2 = [int(v) for v in t.bbox]
        gx1, gy1, gx2, gy2 = x1+fx1, y1+fy1, x2+fx1, y2+fy1
        color = palette[(i - 1) % len(palette)]
        draw.rectangle([gx1, gy1, gx2, gy2], outline=color, width=2)
        # 编号 + 短文本
        label = f"{i}: {(t.text or '').strip()[:18]}"
        bbox = draw.textbbox((gx1, max(0, gy1-18)), label, font=font)
        draw.rectangle(bbox, fill=color)
        draw.text((gx1, max(0, gy1-18)), label, fill=(255, 255, 255), font=font)

    out = HERE / "out_surya_ocr.png"
    pil_overlay.save(str(out))
    print(f"\n✅ 输出: {out}")
    try:
        import subprocess
        subprocess.Popen(["open", str(out)])
    except Exception:
        pass


if __name__ == "__main__":
    main()
