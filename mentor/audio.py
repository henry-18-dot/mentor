"""音频概览：对标 NotebookLM 的 Audio Overview。

分两步，故意解耦：
1. **脚本文本** 永远能生成（离线 provider 就能产出 `audio_script` 组件），先落盘成 `.md`；
2. **合成语音** 需要 OpenAI 兼容的 `/audio/speech` 端点，没配置就明确报错，
   不会假装有音频。

配置：`MENTOR_TTS_BASE` / `MENTOR_TTS_KEY` / `MENTOR_TTS_MODEL`（默认 `tts-1`）/ `MENTOR_TTS_VOICE`（默认 `alloy`）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .llm.base import ProviderError

DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"


def script_to_text(segments: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    for seg in segments:
        speaker = str(seg.get("speaker", "")).strip()
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        lines.append(f"{speaker}：{text}" if speaker else text)
    return "\n\n".join(lines)


def script_to_markdown(question: str, segments: Iterable[dict[str, Any]]) -> str:
    body = script_to_text(segments)
    return f"# 音频概览脚本\n\n主题：{question}\n\n{body}\n"


def export_script(question: str, segments: Iterable[dict[str, Any]], out_path: str | Path) -> Path:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script_to_markdown(question, segments), encoding="utf-8")
    return target


def tts_config(config: dict[str, Any] | None = None) -> dict[str, str]:
    config = config or {}

    def pick(key: str, env: str, default: str = "") -> str:
        return str(config.get(key) or os.environ.get(env) or default)

    return {
        "base_url": pick("tts_base_url", "MENTOR_TTS_BASE").rstrip("/"),
        "api_key": pick("tts_api_key", "MENTOR_TTS_KEY"),
        "model": pick("tts_model", "MENTOR_TTS_MODEL", DEFAULT_MODEL),
        "voice": pick("tts_voice", "MENTOR_TTS_VOICE", DEFAULT_VOICE),
    }


def tts_available(config: dict[str, Any] | None = None) -> bool:
    cfg = tts_config(config)
    return bool(cfg["base_url"] and cfg["api_key"])


def synthesize(segments: Iterable[dict[str, Any]], out_path: str | Path, *,
               config: dict[str, Any] | None = None, timeout: float = 120.0) -> Path:
    """把脚本合成成 mp3。未配置 TTS 时抛 ProviderError，调用方据此只导出脚本。"""
    cfg = tts_config(config)
    if not tts_available(config):
        raise ProviderError("未配置 MENTOR_TTS_BASE / MENTOR_TTS_KEY，只能先导出脚本文本")
    text = script_to_text(segments)
    if not text:
        raise ProviderError("脚本为空")
    payload = {"model": cfg["model"], "input": text[:4000], "voice": cfg["voice"],
               "response_format": "mp3"}
    endpoint = cfg["base_url"]
    if not endpoint.endswith("/audio/speech"):
        endpoint = f"{endpoint}/audio/speech"
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['api_key']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # pragma: no cover - 依赖网络
            audio = resp.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - 依赖网络
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ProviderError(f"TTS HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # pragma: no cover - 依赖网络
        raise ProviderError(f"TTS 请求失败：{exc}") from exc
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio)
    return target
