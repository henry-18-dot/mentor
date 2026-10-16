"""Markdown 代码区屏蔽：引用清理与引用校验都不能进代码里改东西。

原来这套逻辑只写在 `ground.enforce()` 里，结果 `protocol.surface_problems()`
的新校验又踩了同一个坑（把代码里的 `a[0]` 当成引用 `[0]`）。抽成一个模块，
两处共用一份实现。

占位符带随机 nonce：万一正文里真的出现 `\\x00mentor-...-0\\x00` 这种字面量，
也不会被替换成代码块内容。
"""

from __future__ import annotations

import re
import uuid

# 闭合的围栏块
_FENCED_RE = re.compile(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", re.S)
# 只有开口、没有闭合的围栏（模型被截断时很常见）：屏蔽到文末
_OPEN_FENCE_RE = re.compile(r"(?:```|~~~)[^\n]*\n.*\Z", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _new_nonce() -> str:
    return uuid.uuid4().hex[:10]


def shield_code(markdown: str) -> tuple[str, list[str], str]:
    """把代码区换成占位符。返回 (屏蔽后的文本, 代码块列表, nonce)。"""
    blocks: list[str] = []
    nonce = _new_nonce()

    def stash(match: re.Match[str]) -> str:
        blocks.append(match.group(0))
        return f"\x00mentor-{nonce}-{len(blocks) - 1}\x00"

    text = _FENCED_RE.sub(stash, markdown or "")
    text = _OPEN_FENCE_RE.sub(stash, text)
    text = _INLINE_CODE_RE.sub(stash, text)
    return text, blocks, nonce


def unshield_code(text: str, blocks: list[str], nonce: str) -> str:
    for index, block in enumerate(blocks):
        text = text.replace(f"\x00mentor-{nonce}-{index}\x00", block)
    return text


def iter_code_blocks(markdown: str) -> list[str]:
    """只取代码区，用于"这段文字是不是代码"这类判断。"""
    _, blocks, _ = shield_code(markdown)
    return blocks
