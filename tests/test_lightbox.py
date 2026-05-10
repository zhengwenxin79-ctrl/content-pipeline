"""
Lightbox 离线渲染测试 — 不调用 Qwen/DeepSeek，直接喂假 graph_json + 真图片，
验证 _build_overlay_html 生成的 HTML 是否能在浏览器中正常打开 lightbox。

用法:
  python test_lightbox.py [图片路径]
默认使用 cs_Atte_0.png
"""
import sys
import re
import subprocess
from pathlib import Path

# 跳过 _verify_node_positions 的 Qwen 调用：用 monkeypatch 短路
import animation_service
animation_service._verify_node_positions = lambda img, nodes: nodes
animation_service._fetch_knowledge = lambda graph, abstract="": (
    graph.get("overall_description", ""),
    {n["id"]: {"desc": f"测试卡片 - {n.get('label_zh', n['label'])}",
               "role": "用于离线验证 lightbox 渲染"}
     for n in graph.get("nodes", [])}
)

FAKE_GRAPH = {
    "title": "测试机制图（离线 lightbox 验证）",
    "overall_description": "这是一个用于测试的假机制图。点击右上角 🔍 放大原图按钮，或直接点击图片空白区域，应该弹出全屏 lightbox。",
    "diagram_region": {"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0},
    "nodes": [
        {"id": "n1", "label": "Input", "label_zh": "输入层", "type": "input",  "x": 0.2, "y": 0.3},
        {"id": "n2", "label": "Encoder", "label_zh": "编码器", "type": "module", "x": 0.5, "y": 0.5},
        {"id": "n3", "label": "Output", "label_zh": "输出层", "type": "output", "x": 0.8, "y": 0.7},
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
    ],
}


def main():
    img_path = Path(sys.argv[1] if len(sys.argv) > 1 else "cs_Atte_0.png")
    if not img_path.exists():
        print(f"❌ 找不到测试图片: {img_path}")
        print("   可用图片:")
        for p in sorted(Path(".").glob("*.png"))[:10]:
            print(f"     {p}")
        sys.exit(1)

    img_bytes = img_path.read_bytes()
    print(f"✓ 读取图片: {img_path} ({len(img_bytes)//1024} KB)")

    html = animation_service.generate_animation_html(FAKE_GRAPH, image_bytes=img_bytes)
    print(f"✓ HTML 生成: {len(html)//1024} KB")

    # 静态校验：lightbox 关键元素都存在
    checks = [
        ("lb-overlay class CSS",      r"\.lb-overlay\{"),
        ("lb-img class CSS",          r"\.lb-img\{"),
        ("lbOverlay element",          r'id="lbOverlay"'),
        ("lbImg element",              r'id="lbImg"'),
        ("openLightbox() function",   r"function openLightbox"),
        ("closeLightbox() function",  r"function closeLightbox"),
        ("lbZoom() function",         r"function lbZoom"),
        ("放大原图 button",            r"放大原图"),
        ("ESC key handler",            r"e\.key === 'Escape'"),
        ("wheel zoom handler",         r"addEventListener\('wheel'"),
        ("mousedown drag handler",     r"addEventListener\('mousedown'"),
        ("layer click → lightbox",    r"e\.target === layer"),
        ("cursor:zoom-in on main img", r"cursor:zoom-in"),
    ]
    print("\n[静态检查]")
    failed = []
    for name, pat in checks:
        ok = re.search(pat, html) is not None
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name}")
        if not ok:
            failed.append(name)

    out = Path("test_lightbox_output.html")
    out.write_text(html, encoding="utf-8")
    print(f"\n✓ 已写入 {out.absolute()}")

    if failed:
        print(f"\n❌ {len(failed)} 项检查未通过: {failed}")
        sys.exit(1)
    else:
        print(f"\n✅ 全部静态检查通过")
        print(f"\n手动验证清单（浏览器打开后逐项确认）:")
        print(f"  1. 工具栏出现「🔍 放大原图」按钮")
        print(f"  2. 主图 hover 时光标变成放大镜图标")
        print(f"  3. 点击「🔍 放大原图」→ 弹出全屏深色背景，图片居中")
        print(f"  4. 滚轮上下 → 图片缩放 +/-，右上角百分比变化")
        print(f"  5. 鼠标按住拖拽 → 图片跟随平移")
        print(f"  6. 按 ESC → 关闭 lightbox")
        print(f"  7. 点击图片之外的暗背景 → 关闭 lightbox")
        print(f"  8. 关闭后回到原视图，热区圆点仍可点击展开知识卡片")
        print(f"  9. 点击主图空白处（非热区）→ 也能弹出 lightbox")
        try:
            subprocess.Popen(["open", str(out)])
        except Exception:
            pass


if __name__ == "__main__":
    main()
