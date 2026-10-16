"""mentor —— 资料来源接地的学习工作台。

对标 NotebookLM：把一组材料变成可追问、可引用、可复习的笔记本。
差异点在"智能 UI"：回答不是一段长文，而是由模型按封闭组件协议
（见 protocol.WIDGET_TYPES）即时编排的一块块界面，前端只负责渲染。
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
