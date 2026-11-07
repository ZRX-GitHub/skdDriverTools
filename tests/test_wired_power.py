"""纯有线鼠标的「电量」怎么显示：一律写「USB供电」，不写百分比。

用户要求两处都改：

1. 设备弹窗里，有线鼠标那一行不再写 "100%"，改成绿色的「USB供电」；
2. 主页面电量环，有线鼠标把环画满 100%，环心写「USB供电」。

为什么不能留着 100%：有线机型读到的 100 是兜底值（罗技 `WIRED_BATTERY`），
不是电池电量；写个 100% 会让人以为「这块电池是满的」，而它压根没电池。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

import devcaps  # noqa: E402
import i18n  # noqa: E402
import monitor as mon  # noqa: E402
import theme  # noqa: E402
import widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])

WIRED_ROW = {"key": "b", "label": "Logitech G502 HERO", "brand": "Logitech",
             "model": "G502 HERO", "available": True, "current": True,
             "battery": 100, "wired": True}
WIRELESS_ROW = {"key": "a", "label": "CROCIRIS X7 Ultra Max", "brand": "CROCIRIS",
                "model": "X7 Ultra Max", "available": True, "current": False,
                "battery": 57, "wired": False}


class RingTests(unittest.TestCase):
    def _ring(self) -> W.BatteryRing:
        ring = W.BatteryRing()
        ring.resize(200, 200)
        return ring

    def test_wired_is_usb_power(self) -> None:
        ring = self._ring()
        # 狼途这类：连电池都读不到（level=None），照样是 USB 供电
        ring.set_state(None, False, True, wired=True)
        self.assertTrue(ring.usb_power())

    def test_wired_with_fake_battery_is_usb_power(self) -> None:
        """罗技有线机型会报一个兜底的 100 —— 同样按 USB 供电显示。"""
        ring = self._ring()
        ring.set_state(100, False, True, wired=True)
        self.assertTrue(ring.usb_power())

    def test_wireless_is_not(self) -> None:
        ring = self._ring()
        ring.set_state(57, False, True, wired=False)
        self.assertFalse(ring.usb_power())

    def test_charging_is_not(self) -> None:
        """无线鼠标插着线在充电：有真实电量在涨，照常显示百分比。"""
        ring = self._ring()
        ring.set_state(43, True, True, wired=True)
        self.assertFalse(ring.usb_power())

    def test_disconnected_is_not(self) -> None:
        ring = self._ring()
        ring.set_state(None, False, False, wired=True)
        self.assertFalse(ring.usb_power())

    def test_paints_without_value(self) -> None:
        """读不到电量的有线鼠标也要能画出来（以前走「休眠中」那一支）。"""
        ring = self._ring()
        ring.set_state(None, False, True, wired=True)
        pixmap = ring.grab()
        self.assertFalse(pixmap.isNull())


class DeviceMenuTests(unittest.TestCase):
    def _menu(self, rows) -> W.DeviceMenu:
        return W.DeviceMenu(rows, theme.LIGHT, low=15)

    def test_wired_text(self) -> None:
        menu = self._menu([WIRED_ROW])
        self.assertEqual(menu.battery_text(WIRED_ROW), "USB供电")

    def test_wired_text_english(self) -> None:
        menu = self._menu([WIRED_ROW])
        i18n.set_lang(i18n.EN)
        try:
            self.assertEqual(menu.battery_text(WIRED_ROW), "USB powered")
        finally:
            i18n.set_lang(i18n.ZH)

    def test_wireless_still_percentage(self) -> None:
        menu = self._menu([WIRELESS_ROW])
        self.assertEqual(menu.battery_text(WIRELESS_ROW), "57%")

    def test_no_reading_draws_nothing(self) -> None:
        row = dict(WIRELESS_ROW, battery=None, wired=False)
        menu = self._menu([row])
        self.assertEqual(menu.battery_text(row), "")

    def test_column_fits_usb_label(self) -> None:
        """电量列要按「USB供电」量：按 "100%" 量的话这行字会被压到型号上。"""
        from PySide6.QtGui import QFontMetricsF

        menu = self._menu([WIRED_ROW])
        need = QFontMetricsF(menu._batt_font).horizontalAdvance(i18n.T("USB供电"))
        self.assertGreaterEqual(menu._batt_w, need)

    def test_renders_mixed_rows(self) -> None:
        menu = self._menu([WIRELESS_ROW, WIRED_ROW])
        self.assertFalse(menu.grab().isNull())


class _Stub:
    """顶替真实 monitor：只回一个快照，不碰硬件。"""

    def __init__(self, **kw) -> None:
        self._info = mon.MouseInfo(**kw)

    def snapshot(self) -> mon.MouseInfo:
        return self._info


class HubRowsTests(unittest.TestCase):
    def test_devices_marks_wired(self) -> None:
        hub = mon.DeviceHub()
        hub.sources = {
            hub.CROCIRIS: _Stub(connected=True, brand="CROCIRIS", model="X7",
                                name="X7 Ultra Max", battery=57, is_wireless=True),
            hub.LOGITECH: _Stub(connected=True, brand="Logitech", model="G502 HERO",
                                name="G502 HERO", battery=100, is_wireless=False),
        }
        rows = {r["key"]: r for r in hub.devices()}
        self.assertFalse(rows[hub.CROCIRIS]["wired"])
        self.assertTrue(rows[hub.LOGITECH]["wired"])

    def test_offline_row_is_not_wired(self) -> None:
        hub = mon.DeviceHub()
        hub.sources = {
            hub.CROCIRIS: _Stub(connected=False),
            hub.LOGITECH: _Stub(connected=False, is_wireless=False),
        }
        rows = {r["key"]: r for r in hub.devices()}
        self.assertFalse(rows[hub.LOGITECH]["wired"])


if __name__ == "__main__":
    unittest.main()
