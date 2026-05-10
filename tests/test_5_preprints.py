"""
跑 5 篇真实预印本走完整 process_article_pdf 流程，验证 P0 改动。
输出每篇的 HTML 到 test_outputs/，便于浏览器查看动画质量。
"""
import os, sys, json, time, traceback
from pathlib import Path

ROOT = Path(__file__).parent
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import animation_service

OUT_DIR = ROOT / "test_outputs"
OUT_DIR.mkdir(exist_ok=True)

# 5 篇覆盖不同类型的预印本（来自 corpus.db 的高分文章）
PAPERS = [
    ("CARE-ECG_因果Agent",      "https://arxiv.org/abs/2604.10420"),
    ("RPG-SAM_医学分割",        "https://arxiv.org/abs/2603.07436"),
    ("FMASH_中医基础模型",      "https://arxiv.org/abs/2503.05167"),
    ("EEG_双曲多模态",          "https://arxiv.org/abs/2604.12579"),
    ("Glaucoma_眼底筛查",       "https://arxiv.org/abs/2604.12351"),
]

def run_one(name: str, url: str) -> dict:
    print(f"\n{'='*70}\n[{name}] {url}\n{'='*70}")
    progress = []
    def cb(m):
        print(f"  · {m}")
        progress.append(m)

    t0 = time.time()
    try:
        results = animation_service.process_article_pdf(url, progress_cb=cb)
    except Exception as e:
        traceback.print_exc()
        return {"name": name, "url": url, "error": str(e), "elapsed": time.time()-t0}
    elapsed = time.time() - t0

    summary = {
        "name": name, "url": url, "elapsed": round(elapsed, 1),
        "n_results": len(results), "results": []
    }
    for i, r in enumerate(results):
        item = {"i": i, "ok": r.get("ok"), "skipped": r.get("skipped", False),
                "error": r.get("error", ""), "reason": r.get("reason", ""),
                "image_index": r.get("image_index", -1)}
        graph = r.get("graph") or {}
        if graph:
            item["title"] = graph.get("title")
            item["nodes"] = len(graph.get("nodes", []))
            item["edges"] = len(graph.get("edges", []))
            item["has_dr"] = bool(graph.get("diagram_region"))
        if r.get("ok") and r.get("html"):
            out_path = OUT_DIR / f"{name}_{i}.html"
            out_path.write_text(r["html"], encoding="utf-8")
            item["html_kb"] = len(r["html"]) // 1024
            item["html_path"] = str(out_path)
        summary["results"].append(item)

    # 是否触发了强制识别 fallback
    summary["used_fallback"] = any("强制识别" in m for m in progress)
    return summary


def main():
    if not os.environ.get("DASHSCOPE_API_KEY") or not os.environ.get("DEEPSEEK_API_KEY"):
        print("❌ 缺少 DASHSCOPE_API_KEY 或 DEEPSEEK_API_KEY"); sys.exit(1)

    all_summaries = []
    for name, url in PAPERS:
        try:
            all_summaries.append(run_one(name, url))
        except Exception as e:
            traceback.print_exc()
            all_summaries.append({"name": name, "url": url, "fatal": str(e)})

    # 汇总
    print("\n" + "="*70)
    print("汇总")
    print("="*70)
    for s in all_summaries:
        if "fatal" in s:
            print(f"💥 {s['name']}: {s['fatal']}"); continue
        n_ok    = sum(1 for r in s["results"] if r.get("ok"))
        n_skip  = sum(1 for r in s["results"] if r.get("skipped"))
        n_err   = sum(1 for r in s["results"] if r.get("error") and not r.get("skipped"))
        fb      = " [fallback]" if s.get("used_fallback") else ""
        print(f"\n📄 {s['name']}  ({s['elapsed']}s){fb}")
        print(f"   ok={n_ok} skipped={n_skip} err={n_err}")
        for r in s["results"]:
            tag = "✅" if r.get("ok") else ("⚠️ " if r.get("skipped") else "❌")
            line = f"   {tag} idx={r.get('image_index')}"
            if r.get("title"):
                line += f" title={r['title']!r} nodes={r.get('nodes')} edges={r.get('edges')}"
            if r.get("html_path"):
                line += f"  → {r['html_path']}"
            if r.get("error"):
                line += f"  err={r['error'][:80]}"
            if r.get("reason"):
                line += f"  reason={r['reason'][:80]}"
            print(line)

    # 写到 JSON
    (OUT_DIR / "summary.json").write_text(
        json.dumps(all_summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总 JSON: {OUT_DIR / 'summary.json'}")
    print(f"HTML 输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
