"""
动画功能端到端测试脚本
用法：
  1. 准备一张机制图（PNG/JPG），放到本项目目录，默认文件名 test_image.png
  2. 确认 .env 中已配置 DASHSCOPE_API_KEY 和 DEEPSEEK_API_KEY
  3. 运行：python test_animation.py [图片路径]
  4. 测试完成后会在当前目录生成 test_animation_output.html，用浏览器打开验证

可选参数：
  --pdf <url>   直接测试 arXiv/bioRxiv PDF 自动下载流程（需要网络）
"""

import os
import sys
import json
from pathlib import Path

# 加载 .env
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import animation_service

SEP = "─" * 60


def check_env():
    missing = []
    if not os.environ.get("DASHSCOPE_API_KEY"):
        missing.append("DASHSCOPE_API_KEY")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        missing.append("DEEPSEEK_API_KEY")
    if missing:
        print(f"❌ 缺少环境变量：{', '.join(missing)}")
        print("   请在 .env 文件中添加，格式：KEY=value")
        sys.exit(1)
    print("✓ 环境变量检查通过")


def test_single_image(image_path: str):
    print(f"\n{SEP}")
    print(f"[测试] 单图处理：{image_path}")
    print(SEP)

    path = Path(image_path)
    if not path.exists():
        print(f"❌ 文件不存在：{image_path}")
        sys.exit(1)

    img_bytes = path.read_bytes()
    print(f"✓ 图片读取成功，大小：{len(img_bytes)//1024} KB")

    # 计算 hash
    h = animation_service.image_hash(img_bytes)
    print(f"✓ 图片 MD5：{h}")

    # Step 1: Qwen 识图
    print(f"\n[Step 1] 调用 Qwen-VL-Max 识别图片结构...")
    try:
        graph = animation_service.analyze_image_with_qwen(img_bytes)
        print(f"✓ Qwen 返回结果：")
        print(json.dumps(graph, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"❌ Qwen 调用失败：{e}")
        sys.exit(1)

    if graph.get("skip"):
        print(f"\n⚠ Qwen 判断这不是机制图：{graph.get('reason')}")
        print("  换一张机制图（流程图、通路图）重试")
        sys.exit(0)

    nodes_count = len(graph.get("nodes", []))
    edges_count = len(graph.get("edges", []))
    print(f"\n✓ 识别到 {nodes_count} 个节点，{edges_count} 条连线")
    print(f"  标题：{graph.get('title')}")
    print(f"  描述：{graph.get('overall_description')}")

    if nodes_count < 2:
        print("⚠ 节点数量不足，跳过动画生成")
        sys.exit(0)

    # Step 2: 生成交互 HTML（原图叠加热区模式）
    print(f"\n[Step 2] 生成原图交互式 HTML（DeepSeek 生成知识卡片）...")
    print("  (预计 10-30 秒，请耐心等待...)")
    try:
        html = animation_service.generate_animation_html(graph, image_bytes=img_bytes)
        print(f"✓ HTML 生成成功，大小：{len(html)//1024} KB")
    except Exception as e:
        print(f"⚠ 生成失败（{e}），使用降级方案...")
        html = animation_service._fallback_html(graph)
        print(f"✓ 降级 HTML 生成成功")

    # 保存结果
    output_path = Path(__file__).parent / "test_animation_output.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"\n{SEP}")
    print(f"✅ 测试完成！")
    print(f"   输出文件：{output_path}")
    print(f"   用浏览器打开查看效果：")
    print(f"   open {output_path}")
    print(SEP)

    # 自动打开浏览器（macOS）
    try:
        import subprocess
        subprocess.Popen(["open", str(output_path)])
    except Exception:
        pass


def test_pdf_url(pdf_url: str):
    print(f"\n{SEP}")
    print(f"[测试] PDF 自动下载流程：{pdf_url}")
    print(SEP)

    # 测试 URL 推导
    derived = animation_service.derive_pdf_url(pdf_url)
    if not derived:
        print(f"❌ 无法推导 PDF URL，该来源不支持自动下载")
        sys.exit(1)
    print(f"✓ 推导 PDF URL：{derived}")

    # 下载 PDF
    print("\n[Step 1] 下载 PDF...")
    try:
        pdf_bytes = animation_service.download_pdf(derived)
        print(f"✓ PDF 下载成功，大小：{len(pdf_bytes)//1024} KB")
    except Exception as e:
        print(f"❌ PDF 下载失败：{e}")
        sys.exit(1)

    # 提取图片
    print("\n[Step 2] 提取图片...")
    try:
        images = animation_service.extract_images_from_pdf(pdf_bytes)
        print(f"✓ 提取到 {len(images)} 张符合尺寸要求的图片")
    except Exception as e:
        print(f"❌ 图片提取失败：{e}")
        sys.exit(1)

    if not images:
        print("⚠ 未找到图片，PDF 可能以矢量图形式存储")
        sys.exit(0)

    # 处理第一张图（测试用）
    print(f"\n[Step 3] 处理第 1 张图（共 {len(images)} 张）...")
    result = animation_service.process_image(images[0])

    if result.get("skipped"):
        print(f"⚠ 第 1 张图被跳过：{result.get('reason')}")
        print("  尝试第 2 张图..." if len(images) > 1 else "  没有更多图片了")
        if len(images) > 1:
            result = animation_service.process_image(images[1])

    if result.get("ok"):
        html = result["html"]
        output_path = Path(__file__).parent / "test_animation_output.html"
        output_path.write_text(html, encoding="utf-8")
        print(f"\n✅ 测试完成！输出：{output_path}")
        try:
            import subprocess
            subprocess.Popen(["open", str(output_path)])
        except Exception:
            pass
    elif result.get("fallback_html"):
        output_path = Path(__file__).parent / "test_animation_output.html"
        output_path.write_text(result["fallback_html"], encoding="utf-8")
        print(f"\n⚠ 使用降级方案，输出：{output_path}")
        print(f"  失败原因：{result.get('error')}")
    else:
        print(f"❌ 处理失败：{result.get('error')}")


def main():
    check_env()

    args = sys.argv[1:]

    if "--pdf" in args:
        idx = args.index("--pdf")
        if idx + 1 >= len(args):
            print("❌ --pdf 参数后需要跟 URL")
            sys.exit(1)
        test_pdf_url(args[idx + 1])
        return

    # 默认：单图测试
    image_path = args[0] if args else "test_image.png"

    if not Path(image_path).exists():
        print(f"❌ 找不到测试图片：{image_path}")
        print()
        print("使用方法：")
        print("  # 测试单张图片（机制图）")
        print("  python test_animation.py 你的图片.png")
        print()
        print("  # 测试 arXiv 论文自动 PDF 下载")
        print("  python test_animation.py --pdf https://arxiv.org/abs/2401.00001")
        sys.exit(1)

    test_single_image(image_path)


if __name__ == "__main__":
    main()
