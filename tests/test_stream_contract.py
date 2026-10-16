"""流协议一致性测试（Kimi 建议 #3.1）。

界面是"边到边渲染"的，所以真正的风险不在 happy path，而在流的异常形态：
provider 抛错、组件乱序、类型重复、未知类型、畸形数据、引用越界、超长输出。
这里用一批"坏 provider"逼这些形态出现，断言两件事：

1. **事件流的结构不变式**：plan 第一、retrieval 第二、surface 在 done 之前、
   widget 序号单调、组件 id 不重复；
2. **最终态永远合法**：不管流怎么坏，surface 都能过 `surface_problems`，
   且正文引用不越界——界面永远不会拿到一份自相矛盾的界面描述。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor.llm.base import Provider, ProviderError
from mentor.protocol import WIDGET_TYPES, surface_problems
from mentor.store import Store
from mentor.tutor import Tutor, _surface_from_dict

from .test_pipeline import sample_corpus


class ScriptedProvider(Provider):
    """按剧本产出组件，可以在中途抛错。"""

    name = "scripted"

    def __init__(self, script: list[object]) -> None:
        self.script = script

    def available(self) -> bool:
        return True

    def generate_stream(self, request):
        for item in self.script:
            if isinstance(item, Exception):
                raise item
            yield item


def good_answer(markdown: str = "要点 [1]") -> dict:
    return {"type": "answer", "data": {"markdown": markdown, "confidence": "high"}, "cites": [1]}


class StreamContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "stream.db")
        sources, chunks = sample_corpus()
        self.notebook = self.store.create_notebook("流协议")
        self.store.attach(self.notebook.id, sources, chunks)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def run_script(self, script: list[object]) -> list[dict]:
        tutor = Tutor(self.store, ScriptedProvider(script))
        return list(tutor.ask_stream(self.notebook.id, "BPE 是怎么训练的？", "explore"))

    # ------------------------------------------------------------ 不变式
    def assert_invariants(self, events: list[dict]) -> dict:
        names = [event["event"] for event in events]
        self.assertEqual(names[0], "plan", names)
        self.assertEqual(names[1], "retrieval", names)
        self.assertEqual(names[-1], "done", names)
        self.assertIn("surface", names)
        self.assertLess(names.index("surface"), names.index("done"))

        indices = [event["data"]["index"] for event in events if event["event"] == "widget"]
        self.assertEqual(indices, sorted(indices), f"组件序号非单调：{indices}")
        self.assertEqual(len(indices), len(set(indices)), f"组件序号重复：{indices}")

        ids = [event["data"]["widget"]["id"] for event in events if event["event"] == "widget"]
        self.assertEqual(len(ids), len(set(ids)), f"组件 id 重复：{ids}")

        surface = [e for e in events if e["event"] == "surface"][0]["data"]["surface"]
        parsed = _surface_from_dict(surface)
        self.assertEqual(surface_problems(parsed), [], "最终 surface 违约")
        for widget in parsed.widgets:
            self.assertIn(widget.type, WIDGET_TYPES)
            self.assertIn(widget.slot, ("primary", "support", "evidence", "aside"))
        return surface

    # ------------------------------------------------------------ 坏形态
    def test_normal_script(self) -> None:
        events = self.run_script([good_answer(), {"type": "sources", "data": {"items": [
            {"n": 1, "locator": "a.py:1", "title": "T", "snippet": "s", "source_id": "src_lecture"}]}}])
        self.assert_invariants(events)

    def test_provider_dies_immediately_falls_back_offline(self) -> None:
        events = self.run_script([ProviderError("boom")])
        surface = self.assert_invariants(events)
        self.assertTrue(surface["degraded"])
        self.assertTrue(any("boom" in note for note in surface["notes"]), surface["notes"])

    def test_provider_dies_mid_stream_keeps_partial_and_fills(self) -> None:
        events = self.run_script([
            good_answer("部分内容 [1]"),
            {"type": "quote", "data": {"text": "原文", "locator": "a.py:1", "source_id": "src_lecture"}},
            ProviderError("stream cut"),
        ])
        surface = self.assert_invariants(events)
        types = [w["type"] for w in surface["widgets"]]
        self.assertIn("answer", types)
        self.assertIn("quote", types)
        self.assertTrue(surface["degraded"])

    def test_unknown_widget_type_is_dropped_with_note(self) -> None:
        events = self.run_script([good_answer(), {"type": "attention_flow", "data": {"x": 1}}])
        surface = self.assert_invariants(events)
        self.assertNotIn("attention_flow", [w["type"] for w in surface["widgets"]])
        self.assertTrue(any("attention_flow" in note for note in surface["notes"]), surface["notes"])

    def test_malformed_widgets_are_dropped(self) -> None:
        events = self.run_script([
            good_answer(),
            {"type": "quiz", "data": {}},                        # 缺 questions
            {"type": "cheatsheet", "data": {"items": []}},         # 空列表
            "不是字典",                                             # 类型错误
            None,
        ])
        surface = self.assert_invariants(events)
        self.assertIn("answer", [w["type"] for w in surface["widgets"]])

    def test_duplicate_types_keep_first(self) -> None:
        events = self.run_script([good_answer("第一版 [1]"), good_answer("第二版 [1]")])
        surface = self.assert_invariants(events)
        answers = [w for w in surface["widgets"] if w["type"] == "answer"]
        self.assertEqual(len(answers), 1)
        self.assertIn("第一版", answers[0]["data"]["markdown"])

    def test_out_of_range_citations_are_stripped(self) -> None:
        events = self.run_script([good_answer("真的 [1] 假的 [9]")])
        surface = self.assert_invariants(events)
        answer = [w for w in surface["widgets"] if w["type"] == "answer"][0]
        self.assertIn("[1]", answer["data"]["markdown"])
        self.assertNotIn("[9]", answer["data"]["markdown"])

    def test_answer_without_any_citation_gets_one(self) -> None:
        events = self.run_script([good_answer("没有任何引用的一句话。")])
        surface = self.assert_invariants(events)
        answer = [w for w in surface["widgets"] if w["type"] == "answer"][0]
        self.assertRegex(answer["data"]["markdown"], r"\[\d+\]")

    def test_huge_payload_is_trimmed_not_crashing(self) -> None:
        big = {"type": "cheatsheet", "data": {"items": [
            {"term": f"术语{i}", "definition": "定义" * 20} for i in range(500)]}}
        events = self.run_script([good_answer(), big])
        surface = self.assert_invariants(events)
        cheatsheet = [w for w in surface["widgets"] if w["type"] == "cheatsheet"][0]
        self.assertLessEqual(len(cheatsheet["data"]["items"]), 14)

    def test_stream_with_no_widgets_still_produces_surface(self) -> None:
        events = self.run_script([])
        surface = self.assert_invariants(events)
        self.assertTrue(surface["widgets"])

    def test_protocol_version_is_advertised(self) -> None:
        events = self.run_script([good_answer()])
        plan = [e for e in events if e["event"] == "plan"][0]["data"]
        self.assertIn("protocol", plan)
        self.assertIn("slots", plan)
        for name in plan["planned"]:
            self.assertIn(name, WIDGET_TYPES)


if __name__ == "__main__":
    unittest.main()
