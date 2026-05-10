"""
测试整页 PDF 渲染 + Qwen 分类，覆盖不同论文类型。
用法: python3 test_pdf_pages.py
"""
import os, sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))

env_path = pathlib.Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import animation_service
animation_service.DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

# 不同类型的论文（arXiv PDF URL）
papers = [
    ("生物医学机制图",     "https://arxiv.org/pdf/2106.04799.pdf"),   # BioGPT
    ("CS 架构图",         "https://arxiv.org/pdf/2010.11929.pdf"),   # ViT
    ("研究流程/方法图",    "https://arxiv.org/pdf/2302.13971.pdf"),   # LLaMA
    ("分子生物通路",       "https://arxiv.org/pdf/2404.01219.pdf"),   # med paper
    ("多智能体框架",       "https://arxiv.org/pdf/2308.08155.pdf"),   # MetaGPT
]

def test_paper(name, pdf_url):
    print(f"\n{'='*55}")
    print(f"📄 {name}")
    print(f"   {pdf_url}")
    try:
        pdf_bytes = animation_service.download_pdf(pdf_url, connect_timeout=15, total_timeout=30)
        print(f"   下载完成 {len(pdf_bytes)//1024}KB")
    except Exception as e:
        print(f"   ❌ 下载失败: {e}")
        return

    try:
        pages = animation_service.extract_images_from_pdf(pdf_bytes)
        print(f"   渲染 {len(pages)} 页")
    except Exception as e:
        print(f"   ❌ 渲染失败: {e}")
        return

    found = False
    for i, img_bytes in enumerate(pages):
        print(f"   → 第 {i+1}/{len(pages)} 页 ({len(img_bytes)//1024}KB) ", end="", flush=True)
        try:
            graph = animation_service.analyze_image_with_qwen(img_bytes)
        except Exception as e:
            print(f"❌ Qwen错误: {e}")
            continue

        if graph.get("skip"):
            print(f"⏭ 跳过 ({graph.get('reason','')[:40]})")
        else:
            nodes = graph.get("nodes", [])
            print(f"✅ 识别！title={graph.get('title','')} nodes={len(nodes)}")
            found = True
            break
        time.sleep(0.5)

    if not found:
        print(f"   ⚠ 未找到机制图")

for name, url in papers:
    test_paper(name, url)
    time.sleep(1)

print("\n\n测试完成")
