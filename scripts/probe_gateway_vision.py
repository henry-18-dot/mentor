"""对本地网关做一次"谁能看图 + 谁便宜"的实测。

对同一张 UI 截图，依次问每个候选模型同一个问题，打印：
状态码 / 延迟 / 首句回答；再读网关的 / 统计看实际命中了哪个上游模型。
"""

from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = "http://127.0.0.1:4000"
KEY = "local-gateway-key"
IMAGE = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Henry_1\AppData\Local\Temp\ui-review-900.png")
MODELS = sys.argv[2].split(",") if len(sys.argv) > 2 else [
    "deepseek-flash", "deepseek-v4-pro", "kimi-for-coding",
]
PROMPT = ("这是一张软件界面截图。请只用一句话指出它最明显的视觉或信息层级问题，"
          "要求具体到区域（不要泛泛而谈）。")


def call(model: str) -> dict:
    payload = {
        "model": model,
        "max_tokens": 300,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(IMAGE.read_bytes()).decode()}},
            ],
        }],
    }
    request = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        elapsed = time.time() - started
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"model": model, "status": 200, "sec": round(elapsed, 1),
                "answer": (content or "").strip().replace("\n", " ")[:220],
                "usage": body.get("usage")}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        return {"model": model, "status": exc.code, "sec": round(time.time() - started, 1),
                "answer": detail}
    except Exception as exc:  # noqa: BLE001
        return {"model": model, "status": 0, "sec": round(time.time() - started, 1),
                "answer": f"{type(exc).__name__}: {exc}"}


def stats() -> dict:
    try:
        with urllib.request.urlopen(
                urllib.request.Request(f"{GATEWAY}/", headers={"Authorization": f"Bearer {KEY}"}),
                timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return {"byModel": data.get("stats", {}).get("byModel"),
                "errors": data.get("stats", {}).get("errors"),
                "quota": data.get("quota", {})}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def main() -> int:
    print(f"图片：{IMAGE.name}（{IMAGE.stat().st_size // 1024} KB）\n")
    before = stats()
    for model in MODELS:
        result = call(model)
        print(f"[{model}] HTTP {result['status']} · {result['sec']}s · {result.get('usage') or ''}")
        print(f"    {result['answer'][:200]}\n")
    after = stats()
    print("网关统计（byModel 变化即实际命中的上游模型）：")
    print("  before:", before.get("byModel"), "errors:", before.get("errors"))
    print("  after :", after.get("byModel"), "errors:", after.get("errors"))
    quota = after.get("quota") or {}
    print(f"  5 小时窗口已用 {quota.get('window5hUsedPercent')}%，"
          f"重置 {quota.get('window5hResetTime')}；月度 {quota.get('monthTotalUsedPercent')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
