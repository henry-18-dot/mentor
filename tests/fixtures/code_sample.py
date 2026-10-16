"""示例模块（自造）：用来验证其它 .py 按 def / class 切块。

这个 docstring 本身应该成为一个独立的 docstring 块。
"""
import math


def first(value: int) -> int:
    """把输入加一。"""
    return value + 1


def second(value: int) -> int:
    """把输入乘二。"""
    return first(value) * 2


class Helper:
    """一个很小的工具类。"""

    scale = 3

    def apply(self, value: int) -> int:
        """按 scale 放大。"""
        return second(value) * self.scale


def _touches_math() -> float:
    return math.pi
