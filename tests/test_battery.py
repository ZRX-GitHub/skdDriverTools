"""低电量提醒相关配置的回归测试（纯函数，不写盘）。

    python -m unittest discover -s tests -v

阈值是用户自己在设置页选的，最大值/最小值必须有兜底：手改 config.json 写了
999 或者 "abc" 也不能让通知逻辑计算出离谱的区间。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import config  # noqa: E402
import theme  # noqa: E402


class ThresholdTests(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = config.Config()
        self.assertTrue(cfg.low_battery_notify)
        self.assertEqual(cfg.low_battery_threshold, 15)

    def test_clamp_keeps_valid_values(self) -> None:
        for value in (5, 10, 15, 20, 30, 50):
            self.assertEqual(config.clamp_threshold(value), value)

    def test_clamp_low(self) -> None:
        self.assertEqual(config.clamp_threshold(0), config.THRESHOLD_MIN)
        self.assertEqual(config.clamp_threshold(-30), config.THRESHOLD_MIN)

    def test_clamp_high(self) -> None:
        self.assertEqual(config.clamp_threshold(999), config.THRESHOLD_MAX)

    def test_clamp_garbage(self) -> None:
        for value in (None, "abc", "", object()):
            self.assertEqual(config.clamp_threshold(value), config.THRESHOLD_DEFAULT)

    def test_clamp_accepts_numeric_string(self) -> None:
        self.assertEqual(config.clamp_threshold("25"), 25)


class BatteryColorTests(unittest.TestCase):
    def test_red_below_threshold(self) -> None:
        self.assertEqual(theme.battery_color(theme.DARK, 20, 25).name(), theme.RED.lower())
        self.assertEqual(theme.battery_color(theme.DARK, 25, 25).name(), theme.RED.lower())

    def test_amber_window_follows_threshold(self) -> None:
        # 阈值 25 → 26..40 是琥珀，41 起步是绿
        self.assertEqual(theme.battery_color(theme.DARK, 30, 25).name(), theme.AMBER.lower())
        self.assertEqual(theme.battery_color(theme.DARK, 41, 25).name(), theme.GREEN.lower())

    def test_default_threshold_matches_legacy_behaviour(self) -> None:
        # 不传阈值时行为和旧版一致：15 红、30 黄、31 绿
        self.assertEqual(theme.battery_color(theme.DARK, 15).name(), theme.RED.lower())
        self.assertEqual(theme.battery_color(theme.DARK, 30).name(), theme.AMBER.lower())
        self.assertEqual(theme.battery_color(theme.DARK, 31).name(), theme.GREEN.lower())

    def test_none_is_neutral(self) -> None:
        self.assertEqual(theme.battery_color(theme.DARK, None).name(),
                         theme.DARK.text_tertiary.name())


if __name__ == "__main__":
    unittest.main()
