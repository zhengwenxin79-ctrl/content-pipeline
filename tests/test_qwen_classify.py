"""
批量测试 Qwen 对不同类型图片的分类结果
用法: python3 test_qwen_classify.py
"""
import os, sys, glob, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

# 手动设置 key（从 .env 读）
env_path = pathlib.Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import animation_service
animation_service.DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

# 选取代表性测试图（每个前缀各取1张）
test_groups = {
    "CS-Attention":   "cs_Atte_0.png",
    "CS-Swin":        "cs_Swin_0.png",
    "CS-ViT":         "cs_ViT_0.png",
    "Med-BioViL":     "med_BioViL_0.png",
    "Med-LLaVA":      "med_LLaVA-_0.png",
    "Med-MedSAM":     "med_MedSAM_0.png",
    "MedAI-BioGPT":   "medai_BioGPT_0.png",
    "MedAI-GatorT":   "medai_GatorT_0.png",
    "MedAI-PathAs":   "medai_PathAs_0.png",
    "MedAI-REMEDI":   "medai_REMEDI_0.png",
    "Test-img":       "test_img_0.png",
    "Test-med":       "test_med_0.png",
    "Test-qbio":      "test_qbio_4.01852_0.png",
}

base = pathlib.Path(__file__).parent
results = []

for name, fname in test_groups.items():
    p = base / fname
    if not p.exists():
        print(f"[SKIP] {name}: 文件不存在")
        continue
    img_bytes = p.read_bytes()
    print(f"\n{'='*50}")
    print(f"测试: {name} ({fname})")
    try:
        graph = animation_service.analyze_image_with_qwen(img_bytes)
        if graph.get("skip"):
            status = "⏭ SKIP"
            detail = graph.get("reason", "")
            node_n = 0
        else:
            node_n = len(graph.get("nodes", []))
            status = "✅ OK" if node_n >= 2 else "⚠ 节点不足"
            detail = f"title={graph.get('title','')} nodes={node_n}"
        print(f"  {status}: {detail}")
        results.append((name, status, detail))
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        results.append((name, "❌ ERROR", str(e)[:80]))

print(f"\n\n{'='*60}")
print("汇总结果:")
print(f"{'图片':<20} {'结果':<10} 详情")
print("-"*60)
for name, status, detail in results:
    print(f"{name:<20} {status:<10} {detail}")

ok = sum(1 for _, s, _ in results if "OK" in s)
skip = sum(1 for _, s, _ in results if "SKIP" in s)
err = sum(1 for _, s, _ in results if "ERROR" in s)
print(f"\n总计: ✅ {ok} 成功识别  ⏭ {skip} 跳过  ❌ {err} 错误  (共 {len(results)} 张)")
