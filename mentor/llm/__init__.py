"""Provider 装配：默认走离线，配好 key 就升级到真实模型。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .base import GenerateRequest, Provider, ProviderError
from .offline import OfflineProvider
from .openai_compat import OpenAICompatProvider

__all__ = ["GenerateRequest", "Provider", "ProviderError", "OfflineProvider",
           "OpenAICompatProvider", "build_provider", "load_config", "provider_info",
           "config_error"]

log = logging.getLogger(__name__)

# 读配置时出的错，按路径记。坏配置**不能静默**：那会让人以为模型在工作，实际跑的是离线兜底。
_CONFIG_ERRORS: dict[str, str] = {}


def _config_path(path: str | Path | None = None) -> Path:
    """配置文件的解析规则（显式路径 > MENTOR_CONFIG > 仓库根的 config.json）。"""
    if path:
        return Path(path)
    return Path(os.environ.get("MENTOR_CONFIG")
                or Path(__file__).resolve().parents[2] / "config.json")


def config_error(path: str | Path | None = None) -> str | None:
    """某个配置文件上一次读取的错误（没有就是 None）。

    按路径记而不是记一个全局值：`build_provider()` 会再读一次配置，全局单值会被那次
    成功的读取冲掉，错误就再也说不出来了。
    """
    return _CONFIG_ERRORS.get(str(_config_path(path)))


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """读 `mentor/config.json`（可用 MENTOR_CONFIG 指定别的路径）。

    文件不存在 → 空配置（正常）。文件存在但读不出来 / JSON 坏了 / 顶层不是对象 →
    仍然返回空配置（不能因为一个坏文件让程序起不来），但会把原因记进 `config_error()`
    并打一条 warning，好让 `doctor`、`/api/health` 把这些话说出来。
    """
    target = _config_path(path)
    key = str(target)
    if not target.exists():
        _CONFIG_ERRORS.pop(key, None)
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _CONFIG_ERRORS[key] = f"{target} 读取失败（{type(exc).__name__}: {exc}）"
        log.warning("配置文件有问题，已忽略：%s", _CONFIG_ERRORS[key])
        return {}
    if not isinstance(data, dict):
        _CONFIG_ERRORS[key] = f"{target} 的顶层不是 JSON 对象（是 {type(data).__name__}）"
        log.warning("配置文件有问题，已忽略：%s", _CONFIG_ERRORS[key])
        return {}
    _CONFIG_ERRORS.pop(key, None)
    return data


def build_provider(name: str | None = None, config: dict[str, Any] | None = None) -> Provider:
    """`auto`（默认）：有可用配置就用真实模型，否则离线。

    显式要求 `openai` 但没配好时抛错——静默降级成离线会让人以为模型在工作。
    """
    config = config if config is not None else load_config()
    chosen = (name or os.environ.get("MENTOR_PROVIDER") or config.get("provider") or "auto").lower()
    if chosen in ("offline", "none", "local"):
        return OfflineProvider()
    if chosen in ("openai", "auto", "compatible"):
        provider = OpenAICompatProvider.from_env(config)
        if provider.available():
            return provider
        if chosen == "openai":
            # 显式要 openai 却给不出可用配置：必须报错，不能静默变离线。
            # 如果配置**文件**本身读不出来，把原因也带上，否则用户会去翻环境变量。
            hint = f"；另外配置文件有问题：{config_error()}" if config_error() else ""
            raise ProviderError("配置了 openai provider，但缺少 base_url / api_key / model" + hint)
    return OfflineProvider()


def provider_info(provider: Provider | None = None) -> dict[str, Any]:
    provider = provider or build_provider()
    info: dict[str, Any] = {"name": provider.name, "available": provider.available()}
    if isinstance(provider, OpenAICompatProvider):
        info["model"] = provider.model
        info["base_url"] = provider.base_url
        info["stream"] = provider.stream
    # 配置文件坏了这件事必须能被界面/doctor 看见，否则"离线"会被误读成"没配模型"
    error = config_error()
    if error:
        info["config_error"] = error
    return info
