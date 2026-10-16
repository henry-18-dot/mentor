# 示例讲义（自造，非课程材料）
"""这是模块 docstring，用来验证讲义以第一个 `##` 标题命名。"""


def preamble():
    text("这段文字在第一个二级标题之前，section 应该为空。")
    image("images/before-heading.png", width=200)


def section_one():
    text("## 第一章：分词")
    text("Hello world")
    text("- 列表项一：字节级分词把文本切成 256 个取值。")
    text("- 列表项二：BPE 从字节出发反复合并最高频的相邻对。")
    text("BPE 的合并依据是**最高频相邻对**，merge the most common pair of adjacent tokens。")
    image("images/bpe-merge.png", width=400)


def section_two():
    text("## 第二章：注意力")
    text("注意力把序列里任意两个位置直接连起来，路径长度是 O(1)。")
    text("### 2.1 复杂度的代价")
    text("代价是 O(n^2) 的成对打分，长上下文时这是主要瓶颈。")
    link(title="Attention Is All You Need", url="https://arxiv.org/abs/1706.03762")
    text("缓存 KV 之后，解码每一步只算一个新 query。")
