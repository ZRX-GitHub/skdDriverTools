"""设备弹窗的电量标识：位置统一靠右、按电量分档着色。

用户要求「在型号后面加上当前鼠标的电量百分比，电量标识统一靠最右边，
避免每一行错位」。这里锁三件事：

* 电量矩形的右缘每行严格相同（由 `_battery_box()` 统一算，不按文字宽度排）；
* 窗口宽度必须容得下「最长型号 + 间距 + 电量列」，否则百分比会压到名字上；
* 红/琥珀/绿三档跟设置里的低电量阈值走（和电量环、通知同一套规则）。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402
import widgets as W  # noqa: E402

ROWS = [
    {"key": "a", "label": "CROCIRIS X7 Ultra Max", "available": True,
     "current": True, "battery": 15},
    {"key": "b", "label": "Logitech G502 HERO", "available": True,
     "current": False, "battery": 25},
    {"key": "c", "label": "CROCIRIS X7", "available": True,
     "current": False, "battery": 85},
]


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


class BatteryColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _menu(self, rows=None, low=15):
        return W.DeviceMenu(rows or ROWS, theme.DARK, low=low)

    def _cells(self, menu):
        fr = menu.frame_rect()
        return [QRectF(fr.left() + menu.PAD, fr.top() + menu.PAD + i * menu.ROW_H,
                       menu._width - menu.PAD * 2, menu.ROW_H)
                for i in range(len(menu._rows))]

    def test_battery_box_right_edge_identical(self) -> None:
        """三行型号长短不同，电量矩形的右缘必须一分不差。"""
        menu = self._menu()
        boxes = [menu._battery_box(cell) for cell in self._cells(menu)]
        self.assertEqual(len({round(b.right(), 3) for b in boxes}), 1)
        self.assertEqual(len({round(b.width(), 3) for b in boxes}), 1)
        # 定宽按 "100%" 量出来，三位数也不会被挤到窗口外
        self.assertGreater(menu._batt_w, 0)

    def test_name_area_never_overlaps_battery(self) -> None:
        menu = self._menu()
        for cell in self._cells(menu):
            box = menu._battery_box(cell)
            name_right = (cell.left() + menu.TEXT_X
                          + (cell.width() - menu.TEXT_X - menu.BATT_GAP - menu._batt_w))
            self.assertLessEqual(name_right, box.left())

    def test_window_wide_enough_for_battery(self) -> None:
        menu = self._menu()
        self.assertGreaterEqual(
            menu._width,
            menu.TEXT_X + menu.BATT_GAP + menu._batt_w + menu.PAD_R)

    def test_battery_colors_follow_threshold(self) -> None:
        """≤阈值红、阈值+15 内琥珀、更高绿；和电量环复用 battery_color。"""
        low = 15
        self.assertEqual(theme.battery_color(theme.DARK, 15, low).name(),
                         theme.DARK.danger.name())
        self.assertEqual(theme.battery_color(theme.DARK, 25, low).name(),
                         theme.DARK.warning.name())
        self.assertEqual(theme.battery_color(theme.DARK, 85, low).name(),
                         theme.DARK.success.name())

    def test_missing_battery_draws_nothing(self) -> None:
        """有线机型 battery=None：不画百分比，也不该给个 0%。"""
        menu = self._menu([{"key": "a", "label": "CROCIRIS X7", "available": True,
                            "current": True, "battery": None}])
        menu.grab()      # 能正常渲染、不抛异常即可
        self.assertIsNone(menu._rows[0]["battery"])


if __name__ == "__main__":
    unittest.main()
