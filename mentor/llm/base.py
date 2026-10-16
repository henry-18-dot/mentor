"""Provider 抽象：谁都可以来接，但界面只认组件协议。

- `offline`：确定性生成器，没有 API key 也能跑通全链路（默认）。
- `openai`：任何 OpenAI 兼容的 /chat/completions 端点（DeepSeek、百炼、自建 vLLM…）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """provider 不可用或返回不合法内容。调用方据此回退到 offline。"""


@dataclass
class GenerateRequest:
    """一次生成的全部输入。模型只被允许看这些，不允许看别的。"""

    question: str
    intent: str
    layout: str
    planned: list[str]
    context: list[dict] = field(default_factory=list)
    keyphrases: list[str] = field(default_factory=list)
    material: dict[str, Any] = field(default_factory=dict)
    language: str = "auto"
    # 同一笔记本里最近的几个问题：让模型知道这是"追问"，而不是孤立一问
    history: list[str] = field(default_factory=list)

    def brief(self, max_chars: int = 1400) -> str:
        """给模型看的检索摘要，控制长度，避免长材料把上下文撑爆。"""
        blocks: list[str] = []
        for item in self.context:
            text = item["text"]
            if len(text) > max_chars // max(1, len(self.context)):
                text = text[: max_chars // max(1, len(self.context))] + "…"
            head = item["title"] or item["locator"]
            blocks.append(f"[{item['n']}] {item['locator']} · {head}\n{text}")
        return "\n\n".join(blocks)


class Provider:
    name = "base"

    def available(self) -> bool:
        return False

    def generate(self, request: GenerateRequest) -> list[dict[str, Any]]:
        raise ProviderError("provider 未实现")

    def generate_stream(self, request: GenerateRequest):
        """逐个产出组件。默认实现就是一次性生成后依次吐出。

        真正的流式 provider（见 openai_compat）会重写它，这样界面上组件是
        边生成边出现的，而不是等全部算完再一起画。
        """
        for widget in self.generate(request):
            yield widget

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Provider {self.name} available={self.available()}>"
