"""批量测试今日推荐论文的动画提取效果"""
import os, sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))

env_path = pathlib.Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import animation_service
animation_service.DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

papers = [
    ("CuraView 多智能体幻觉检测",       "2605.03476"),
    ("Atomic Fact-Checking 临床信任",   "2605.03916"),
    ("EQUITRIAGE 性别偏见审计",         "2605.03998"),
    ("FMECA 患者安全框架",              "2605.04085"),
    ("MapPFN 因果扰动图",               "2601.21092"),
    ("PABLO 黑盒优化",                  "2601.22382"),
]

def base_url(arxiv_id):
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

results = []
for name, arxiv_id in papers:
    url = base_url(arxiv_id)
    print(f"\n{'='*58}")
    print(f"📄 {name} ({arxiv_id})")
    t0 = time.time()

    try:
        pdf = animation_service.download_pdf(url, connect_timeout=12, total_timeout=25)
        print(f"   下载 {len(pdf)//1024}KB", end="  ")
    except Exception as e:
        print(f"   ❌ 下载失败: {e}")
        results.append((name, "❌下载失败", "-"))
        continue

    try:
        pages = animation_service.extract_images_from_pdf(pdf)
        print(f"渲染 {len(pages)} 页")
    except Exception as e:
        print(f"   ❌ 渲染失败: {e}")
        results.append((name, "❌渲染失败", "-"))
        continue

    found = False
    for i, img in enumerate(pages):
        print(f"   → 第{i+1}页 ", end="", flush=True)
        try:
            g = animation_service.analyze_image_with_qwen(img)
        except Exception as e:
            print(f"❌ {e}")
            time.sleep(1)
            continue

        if g.get("skip"):
            reason = g.get("reason", "")[:45]
            print(f"⏭ {reason}")
        else:
            nodes = g.get("nodes", [])
            print(f"✅ 识别！title=【{g.get('title','')}】 nodes={len(nodes)}")
            found = True
            break
        time.sleep(0.8)

    elapsed = time.time() - t0
    status = f"✅ 第{i+1}页找到" if found else "⚠ 未找到"
    results.append((name, status, f"{elapsed:.0f}s"))

print(f"\n\n{'='*58}")
print(f"{'论文':<28} {'结果':<14} 耗时")
print("-"*58)
for name, status, t in results:
    print(f"{name:<28} {status:<14} {t}")

ok = sum(1 for _, s, _ in results if s.startswith("✅"))
print(f"\n通过率: {ok}/{len(results)}")
