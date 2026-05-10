import os, pathlib, time, base64
env_path = pathlib.Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import animation_service
animation_service.DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
from openai import OpenAI

def call_model(model_id, img_bytes):
    client = OpenAI(api_key=animation_service.DASHSCOPE_API_KEY,
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    compressed = animation_service._compress_image(img_bytes)
    b64 = base64.b64encode(compressed).decode()
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model_id, timeout=90, max_tokens=2500,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": animation_service._QWEN_PROMPT}]}])
    elapsed = time.time() - t0
    text = resp.choices[0].message.content.strip()
    try:
        g = animation_service._extract_json(text)
    except Exception:
        g = {"skip": True, "reason": "JSON解析失败"}
    return g, elapsed

tests = [
    ("✅应识别 Transformer架构", "cs_Atte_0.png"),
    ("✅应识别 BioGPT流程",      "medai_BioGPT_0.png"),
    ("✅应识别 REMEDI流程图",     "medai_REMEDI_1.png"),
    ("❌应跳过 医学影像MedSAM",  "med_MedSAM_0.png"),
    ("❌应跳过 统计图表",        "test_qbio_4.01852_0.png"),
]

MODELS = [
    ("qwen-vl-max (旧)",      "qwen-vl-max"),
    ("qwen2.5-vl-72b (新)",   "qwen2.5-vl-72b-instruct"),
]

header = f"{'图片':<26} {'模型':<22} {'结果':<8} {'节点数':<7} 耗时"
print(header)
print("-" * 72)

for label, fname in tests:
    img = pathlib.Path(fname).read_bytes()
    for mname, mid in MODELS:
        try:
            g, t = call_model(mid, img)
            if g.get("skip"):
                status = "⏭ 跳过"
                nodes = 0
                reason = g.get("reason", "")[:30]
            else:
                nodes = len(g.get("nodes", []))
                status = "✅ 识别"
                reason = g.get("title", "")[:20]
            print(f"{label:<26} {mname:<22} {status:<8} {nodes:<7} {t:.1f}s  {reason}")
        except Exception as e:
            print(f"{label:<26} {mname:<22} ERROR: {str(e)[:50]}")
        time.sleep(1)
    print()
