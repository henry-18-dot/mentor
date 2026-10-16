"""视觉评审：把界面截图交给便宜的多模态模型，拿回"问题→改法"清单。

为什么要这个脚本：桌面 GUI 自动化（Kimi 客户端）要抢焦点、要真实键鼠，还要人工搬运截图；
而本机 DSH 网关 `http://127.0.0.1:4000` 提供了 OpenAI 兼容接口，`deepseek-flash` 直接吃图片，
一次评审约 ¥0.001，比 Kimi 会员额度更省、也不占 5 小时窗口。

用法：
    python scripts/visual_review.py runs/ui/selftest-1440.png runs/ui/selftest-390.png
    python scripts/visual_review.py shot.png --model deepseek-flash --out runs/kimi/vision-review.md
    python scripts/visual_review.py a.png b.png --prompt "只挑排版问题，5 条以内"

模型选择（网关别名，见 ~/.dsh/gateway/README.md）：
- `auto`（默认）先试 `kimi-for-coding`（Kimi 会员额度、审美最好、不额外计费），
  上游不通或额度用满时自动降级到 `deepseek-flash`（按量 ¥1/¥4，低谷半价，**稳定可看图**）。
- `deepseek-flash`  显式指定：永远可用，单张图约 ¥0.003，2~6 秒。
- `kimi-for-coding` / `kimi-for-coding-highspeed`  Kimi 会员额度内不加钱；受 5 小时窗口限制，
  且与 Claude Code 共用同一份额度，不建议频繁调用。
- **不要用 K3**（`k3` / `k3-256k` / `k3-1m`）：它是推理型模型，看图评审美是大材小用，
  且会把 max_tokens 大量花在 reasoning 上（实测 2000 token 全被吃掉、正文为空）。
- `deepseek-v4-pro` 同为 reasoner，一次成型需 `--max-tokens` ≥4000。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = "http://127.0.0.1:4000"
KEY = "local-gateway-key"
# auto 模式的偏好顺序：先贵一点但审美更好的 Kimi，再退回永远可用的便宜通道
AUTO_CHAIN = ["kimi-for-coding", "deepseek-flash"]
DEFAULT_MODEL = "auto"
DEFAULT_PROMPT = (
    "你是资深产品设计师。下面是一张真实软件界面截图（不是 mock）。"
    "请给出「问题 → 改法」清单：最多 8 条，按严重度排序，每条一句话并指出具体区域"
    "（用你在图里真的看到的东西，例如某处间距、对比度、字号、层级、对齐）；"
    "分四类标注【审美】【交互】【动效】【信息层级】；不要写无法验收的套话；最后给一句总评。"
)
# 估算用单价（元 / 1M tokens），低谷期 DeepSeek 为半价；仅用于提示量级
PRICE = {"deepseek-flash": (1.0, 4.0), "deepseek-v4-pro": (4.5, 13.5)}


def encode(path: Path, max_width: int, max_height: int) -> tuple[str, str]:
    """尽量压一下尺寸（有 Pillow 就用，没有就原图直传）。"""
    raw = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    try:
        import io

        from PIL import Image  # 可选依赖

        with Image.open(io.BytesIO(raw)) as img:
            if img.width > max_width or img.height > max_height:
                scale = min(max_width / img.width, max_height / img.height)
                resized = img.convert("RGB").resize(
                    (int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
                buffer = io.BytesIO()
                resized.save(buffer, format="PNG", optimize=True)
                return base64.b64encode(buffer.getvalue()).decode(), "image/png"
    except Exception:  # noqa: BLE001 - Pillow 缺失或图片异常都退回原图
        pass
    return base64.b64encode(raw).decode(), mime


def review(images: list[Path], model: str, prompt: str, max_tokens: int,
           max_width: int, max_height: int, timeout: float,
           effort: str = "low") -> dict:
    chain = AUTO_CHAIN if model == "auto" else [model]
    errors: list[str] = []
    for index, candidate in enumerate(chain):
        try:
            result = _review_once(images, candidate, prompt, max_tokens,
                                  max_width, max_height, timeout, effort)
        except SystemExit as exc:  # 上游不可用 → 换下一个
            errors.append(f"{candidate}: {exc}")
            continue
        result["fallbacks"] = errors
        return result
    raise SystemExit("所有候选模型都失败了：\n  " + "\n  ".join(errors))


def _review_once(images: list[Path], model: str, prompt: str, max_tokens: int,
                 max_width: int, max_height: int, timeout: float,
                 effort: str = "low") -> dict:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for path in images:
        data, mime = encode(path, max_width, max_height)
        content.append({"type": "text", "text": f"（图片：{path.name}）"})
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    payload = {"model": model, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": content}]}
    # 看图的活不需要长链推理：Kimi 系默认 off（走无思考版），否则它会把 max_tokens
    # 全花在 reasoning 上、正文返回空。
    # 注意 `thinking:false` 是 **Kimi 专属**字段：DeepSeek 上游只接受结构体，
    # 传布尔值会 422（实测 "thinking: invalid type: boolean"），所以只对 Kimi 系发送。
    kimi_like = model.startswith(("k3", "kimi", "claude"))
    if kimi_like:
        if effort == "off":
            payload["thinking"] = False
        elif effort:
            payload["reasoning_effort"] = effort
    request = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise SystemExit(f"网关返回 HTTP {exc.code}：{detail}")
    return {
        "model": model,
        "seconds": round(time.time() - started, 1),
        "usage": body.get("usage") or {},
        "text": ((body.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip(),
    }


def _setup_console() -> None:
    """Windows 控制台默认 GBK：脚本里有中文和 ¥，不切 UTF-8 会直接崩在 print 上。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", help="截图路径（可多张，一起交给同一次评审）")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--max-width", type=int, default=1100)
    parser.add_argument("--max-height", type=int, default=1400)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--effort", default="off", choices=["off", "low", "high", "max"],
                        help="看图的活默认 off（实测 low 仍会把 max_tokens 全花在 reasoning 上、正文为空；off 走无思考版）")
    parser.add_argument("--out", default=None, help="把评审结果写成 markdown")
    args = parser.parse_args()

    paths = [Path(p) for p in args.images]
    for path in paths:
        if not path.exists():
            raise SystemExit(f"找不到图片：{path}")

    result = review(paths, args.model, args.prompt, args.max_tokens,
                    args.max_width, args.max_height, args.timeout, args.effort)
    usage = result["usage"]
    pin, pout = PRICE.get(args.model, (0.0, 0.0))
    cost = usage.get("prompt_tokens", 0) / 1e6 * pin + usage.get("completion_tokens", 0) / 1e6 * pout

    print(f"模型 {result['model']}｜{result['seconds']}s｜"
          f"tokens in/out = {usage.get('prompt_tokens')}/{usage.get('completion_tokens')}"
          f"｜按公开单价估算 ¥{cost:.4f}")
    for note in result.get("fallbacks") or []:
        print(f"（已跳过：{note[:120]}）")
    print("-" * 60)
    print(result["text"] or "（模型没有输出正文：reasoner 类模型请把 --max-tokens 提到 4000+）")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        header = ("# 视觉评审（%s）\n\n图片：%s\n模型：%s｜%ss｜估算 ¥%.4f\n\n"
                  % (time.strftime("%Y-%m-%d %H:%M"), "、".join(p.name for p in paths),
                     result["model"], result["seconds"], cost))
        out.write_text(header + result["text"] + "\n", encoding="utf-8")
        print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
