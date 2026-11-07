"""主按钮文案回归：断开 → 重新连上，按钮必须从「重新扫描」切回「打开官方网页驱动」。

v1.4.9 修的坑：`_sync()` 在断线分支里把按钮文字改成「重新扫描」，却没有让
`_driver_kind` 这个缓存失效；`_apply_driver()` 见「驱动形态没变」就直接 return，
于是设备重新连上后按钮永远停在「重新扫描」，点下去却还是打开网页驱动 ——
文案和动作对不上。

这里不建真窗口（托盘/设备都太重），直接借 `MouseGlassWindow` 的两个方法，
用假 self 跑：`_show_rescan_button()` 与 `_apply_driver()` 只碰
`_driver_kind` / `btn_primary` / `_act_hub` 三样东西。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)


class _FakeButton:
    def __init__(self) -> None:
        self.text = ""
        self.icon = ""
        self.tip = ""
        self.enabled = True
        self.writes = 0

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.writes += 1
        self.text = text

    def set_icon(self, name: str) -> None:
        self.icon = name

    def set_enabled_state(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def setToolTip(self, tip: str) -> None:  # noqa: N802  (Qt 命名)
        self.tip = tip


class _FakeWindow:
    """只提供两个方法要用到的属性。"""

    def __init__(self) -> None:
        self.btn_primary = _FakeButton()
        self._driver_kind = ""


class PrimaryButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import main  # 重量级导入放这里，别拖慢整个 discover

        cls.main = main

    def setUp(self) -> None:
        self.win = _FakeWindow()

    def _disconnect(self) -> None:
        self.main.MouseGlassWindow._show_rescan_button(self.win)

    def _connect(self, info=None) -> None:
        self.main.MouseGlassWindow._apply_driver(self.win, info)

    def test_disconnect_shows_rescan(self) -> None:
        self._disconnect()
        self.assertEqual(self.win.btn_primary.text, "重新扫描")
        self.assertEqual(self.win.btn_primary.icon, "refresh")
        self.assertEqual(self.win.btn_primary.tip, "重新扫描设备")
        # 缓存必须失效，否则下面那步会被早退挡掉
        self.assertEqual(self.win._driver_kind, "")

    def test_reconnect_restores_driver_label(self) -> None:
        """先连上 → 拔掉 → 再插上：文案要回到「打开官方网页驱动」。"""
        self._connect()
        self.assertEqual(self.win.btn_primary.text, "打开官方网页驱动")
        self._disconnect()
        self._connect()
        self.assertEqual(self.win.btn_primary.text, "打开官方网页驱动")
        self.assertEqual(self.win.btn_primary.icon, "link")
        self.assertEqual(self.win.btn_primary.tip, "在浏览器中打开网页驱动")
        self.assertEqual(self.win._driver_kind, "web")

    def test_repeated_sync_does_not_rewrite_button(self) -> None:
        """每 2 秒一次 sync，形态没变时不该反复改按钮（避免无谓重绘）。"""
        self._connect()
        before = self.win.btn_primary.writes
        for _ in range(5):
            self._connect()
        self.assertEqual(self.win.btn_primary.writes, before)

    def test_no_official_app_disables_button(self) -> None:
        """没有官方软件的设备（狼途这类）：按钮写「无官方软件」并禁用。"""
        import monitor as mon

        info = mon.MouseInfo(connected=True, extra={"kind": "basic"})
        self._connect(info)
        self.assertEqual(self.win.btn_primary.text, "无官方软件 · 按键调节")
        self.assertFalse(self.win.btn_primary.enabled)
        self.assertIn("按键", self.win.btn_primary.tip)

    def test_switching_away_reenables_button(self) -> None:
        """从「无官方软件」切回有网页驱动的设备，按钮必须重新可用。"""
        import monitor as mon

        self._connect(mon.MouseInfo(connected=True, extra={"kind": "basic"}))
        self.assertFalse(self.win.btn_primary.enabled)
        self._connect(mon.MouseInfo(connected=True, extra={"kind": "crociris"}))
        self.assertTrue(self.win.btn_primary.enabled)
        self.assertEqual(self.win.btn_primary.text, "打开官方网页驱动")


if __name__ == "__main__":
    unittest.main()
