"""配置文件读不出来时，必须**说出来**，不能静默退回离线。

曾经的行为：`load_config()` 吞掉 `JSONDecodeError`/`OSError` 直接返回 `{}` ——
于是 `MENTOR_CONFIG` 指向一个写坏的 JSON 时，程序照常启动、照常回答问题，
只是默默用了离线兜底。用户会以为"模型在工作"或"我没配模型"，而真相是**配置没读进来**。
（同一个项目里 `build_provider("openai")` 对"缺字段"是会报错的，说明作者在意这件事，
只是坏文件的路径绕过了那道检查。）
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mentor.llm import build_provider, config_error, load_config, provider_info
from mentor.llm.base import ProviderError


class ConfigErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._saved = os.environ.pop("MENTOR_CONFIG", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["MENTOR_CONFIG"] = self._saved
        else:
            os.environ.pop("MENTOR_CONFIG", None)
        self.tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_is_not_an_error(self) -> None:
        load_config(self.root / "没有这个文件.json")
        self.assertIsNone(config_error(), "文件不存在是正常情况，不该算错误")

    def test_valid_config_is_not_an_error(self) -> None:
        path = self.write("ok.json", json.dumps({"provider": "offline", "note": "x"}))
        data = load_config(path)
        self.assertEqual(data.get("provider"), "offline")
        self.assertIsNone(config_error())

    def test_broken_json_is_reported(self) -> None:
        path = self.write("broken.json", '{"provider": "openai",}')
        data = load_config(path)
        self.assertEqual(data, {}, "坏配置退回空配置，程序不该起不来")
        error = config_error(path)
        self.assertIsNotNone(error, "坏配置必须留下痕迹")
        self.assertIn("broken.json", error)
        self.assertIn("JSONDecodeError", error)

    def test_non_object_config_is_reported(self) -> None:
        path = self.write("list.json", "[1, 2, 3]")
        self.assertEqual(load_config(path), {})
        self.assertIn("顶层不是 JSON 对象", config_error(path) or "")

    def test_valid_config_clears_previous_error(self) -> None:
        bad = self.write("bad.json", "{oops")
        load_config(bad)
        self.assertIsNotNone(config_error(bad))
        # 同一个路径后来被修正：错误要清掉
        bad.write_text('{"provider": "offline"}', encoding="utf-8")
        self.assertEqual(load_config(bad).get("provider"), "offline")
        self.assertIsNone(config_error(bad), "同一个文件修好后不该还留着旧错误")

    def test_provider_info_exposes_the_error(self) -> None:
        """真实用法：MENTOR_CONFIG 指向坏文件 —— provider_info 必须带着错误。"""
        path = self.write("broken.json", "{不是 JSON")
        os.environ["MENTOR_CONFIG"] = str(path)
        load_config()
        info = provider_info()
        self.assertIn("config_error", info)
        self.assertIn("broken.json", info["config_error"])

    def test_explicit_openai_request_never_silently_falls_back(self) -> None:
        """显式要 openai 时，坏配置 + 缺字段都必须报错，并把配置问题写进原因。"""
        path = self.write("broken.json", "{不是 JSON")
        os.environ["MENTOR_CONFIG"] = str(path)
        with self.assertRaises(ProviderError) as ctx:
            build_provider("openai")
        message = str(ctx.exception)
        self.assertIn("openai", message)
        self.assertIn("broken.json", message, "报错里要带上配置文件的线索，否则用户会去翻环境变量")

    def test_auto_still_works_with_broken_config(self) -> None:
        """auto 不能因为一个坏文件就起不来，但错误要留在 provider_info 里。"""
        path = self.write("broken.json", "{不是 JSON")
        os.environ["MENTOR_CONFIG"] = str(path)
        provider = build_provider("auto")
        self.assertTrue(provider.available())
        self.assertIn("config_error", provider_info(provider))

    def test_doctor_reports_broken_config(self) -> None:
        from mentor.cli import _doctor_checks

        path = self.write("broken.json", "{不是 JSON")
        os.environ["MENTOR_CONFIG"] = str(path)
        report = {"checks": True, "python": "3.11.9", "ingest": "ok",
                  "ui": {"index": True}, "provider": provider_info()}
        problems = _doctor_checks(report)
        self.assertTrue(any("配置文件" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
