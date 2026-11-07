"""设置浮层 / 设备菜单的手型光标判据。

用户报「设置页面里鼠标移到可点的地方不变手型」。成因是浮窗打开期间，主窗口
每 30ms 会用 Win32 SetCursor 强推一次形状；设置浮层是非激活的 Tool 窗口，
自己的 WM_SETCURSOR 抢不过它，于是浮层里刚设好的手型立刻被按回箭头。

修法是让「哪里可点」只由一份判定给出（`wants_hand`），轮询和浮层共用。
这里锁住这份判据：开关行整行算、分段控件只在控件上算、灰掉的电量行不算。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import config  # noqa: E402
import main as M  # noqa: E402
import theme  # noqa: E402
import widgets as W  # noqa: E402


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


class SettingsHandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _panel(self, battery_ok: bool = True):
        panel = M.SettingsPanel(config.Config())
        # 不真开窗：只要 mapToGlobal/mapFromGlobal 的坐标换算，判据本身不依赖渲染
        panel.isVisible = lambda: True          # type: ignore[assignment]
        panel._battery_ok = battery_ok
        return panel

    def _at(self, panel, x: float, y: float) -> bool:
        return panel.wants_hand(panel.mapToGlobal(QPoint(int(x), int(y))))

    def test_switch_row_is_clickable_anywhere(self) -> None:
        """开关行整行都能点（点行文字也能拨开关）→ 整行都该是手型。"""
        panel = self._panel()
        i = panel.row_index("autostart")
        top = panel.rows_top(i)
        for x in (panel.ROW_X + 8, panel.ROW_X + panel.ROW_W / 2, panel.ROW_X + panel.ROW_W - 8):
            self.assertTrue(self._at(panel, x, top + panel.ROW_H / 2), f"x={x}")

    def test_segment_row_hands_only_on_the_control(self) -> None:
        """主题行：左侧文字区不可点，右侧分段控件可点。"""
        panel = self._panel()
        row = panel.rows_top(panel.row_index("theme")) + panel.ROW_H / 2
        self.assertFalse(self._at(panel, panel.ROW_X + 20, row))
        seg = panel.theme_seg.geometry()
        self.assertTrue(self._at(panel, seg.center().x(), seg.center().y()))

    def test_threshold_segment_is_clickable(self) -> None:
        panel = self._panel()
        seg = panel.threshold_seg.geometry()
        self.assertTrue(self._at(panel, seg.center().x(), seg.center().y()))

    def test_battery_rows_are_dead_when_wired(self) -> None:
        """纯有线鼠标：低电量那两行整块置灰，手型也不能给。"""
        panel = self._panel(battery_ok=False)
        for key in panel.BATTERY_KEYS:
            y = panel.rows_top(panel.row_index(key)) + panel.ROW_H / 2
            self.assertFalse(self._at(panel, panel.ROW_X + 20, y), key)
        seg = panel.threshold_seg.geometry()
        self.assertFalse(self._at(panel, seg.center().x(), seg.center().y()))

    def test_outside_rows_is_arrow(self) -> None:
        """标题区和底部电量图例都不是可点区域。"""
        panel = self._panel()
        self.assertFalse(self._at(panel, panel.ROW_X + 20, panel.TITLE_Y))
        self.assertFalse(self._at(panel, panel.ROW_X + 20, panel.CARD.bottom() - 12))
        self.assertFalse(self._at(panel, 2, 2))


class DeviceMenuHandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    ROWS = [
        {"key": "a", "label": "CROCIRIS X7 Ultra Max", "available": True,
         "current": True, "battery": 15},
        {"key": "b", "label": "Logitech G502 HERO", "available": False,
         "current": False, "battery": None},
    ]

    def _menu(self):
        menu = W.DeviceMenu(self.ROWS, theme.DARK, low=15)
        menu.isVisible = lambda: True           # type: ignore[assignment]
        return menu

    def _row_center(self, menu, index: int) -> QPoint:
        fr = menu.frame_rect()
        return QPoint(int(fr.left() + menu.PAD + 20),
                      int(fr.top() + menu.PAD + index * menu.ROW_H + menu.ROW_H / 2))

    def test_available_row_takes_hand(self) -> None:
        menu = self._menu()
        self.assertTrue(menu.wants_hand(menu.mapToGlobal(self._row_center(menu, 0))))

    def test_unavailable_row_stays_arrow(self) -> None:
        """未连接的设备点了没反应，光标不该骗人。"""
        menu = self._menu()
        self.assertFalse(menu.wants_hand(menu.mapToGlobal(self._row_center(menu, 1))))


if __name__ == "__main__":
    unittest.main()
