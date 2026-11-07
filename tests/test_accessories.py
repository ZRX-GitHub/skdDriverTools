"""键盘 / 耳机这类「非鼠标配件」的识别与只展示。

用户的要求是三条：
  1. 配件只出现在设备弹窗里，**不能切换**（点它不响应）；
  2. 有线写「USB供电」，无线写当前电量——读不到就如实写「无法读取」；
  3. 品牌名前面加品类图标（鼠标 / 键盘 / 耳机）。

这里顺带锁死一个容易踩的坑：血手 B930 自带一个标准鼠标集合，如果品牌
表只管「叫什么」不管「是什么」，它会以一台假鼠标的身份混进设备列表。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import accessories as acc  # noqa: E402
import hid_win  # noqa: E402
import hidbrands  # noqa: E402
import monitor as mon  # noqa: E402
import theme  # noqa: E402
import widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])

B930_MOUSE_COL = hid_win.HidCollection(
    path=r"\\?\hid#vid_09da&pid_fa44&mi_01#test",
    vendor_id=0x09DA, product_id=0xFA44,
    usage_page=0x0001, usage=0x0002, input_len=7,
)
B930_KBD_COL = hid_win.HidCollection(
    path=r"\\?\hid#vid_09da&pid_fa44&mi_00#test",
    vendor_id=0x09DA, product_id=0xFA44,
    usage_page=0x0001, usage=0x0006, input_len=21,
)


class IdentifyTests(unittest.TestCase):
    def test_b930_is_keyboard(self) -> None:
        """09DA:FA44 认成血手幽灵 B930，且品类必须是键盘。"""
        info = hidbrands.identify(0x09DA, 0xFA44, "")
        self.assertIsNotNone(info)
        self.assertEqual(info["brand"], "血手幽灵")
        self.assertEqual(info["model"], "B930")
        self.assertEqual(info["kind"], "keyboard")

    def test_unknown_pid_of_known_vid_defaults_to_mouse(self) -> None:
        """同厂商的其它 PID 默认仍是鼠标（血手也卖鼠标）。"""
        info = hidbrands.identify(0x09DA, 0x1068, "Bloody A90")
        self.assertEqual(info["kind"], "mouse")

    def test_brand_monitor_ignores_keyboard(self) -> None:
        """键盘的鼠标集合不许被当成鼠标捡进设备列表。"""
        orig = mon.hid_win.enumerate_hid
        mon.hid_win.enumerate_hid = lambda: [B930_MOUSE_COL]
        try:
            dev = mon.BrandHidMonitor(0.5)
            dev._connect()
            self.assertFalse(dev.snapshot().connected)
        finally:
            mon.hid_win.enumerate_hid = orig

    def test_brand_monitor_still_takes_real_mouse(self) -> None:
        orig = mon.hid_win.enumerate_hid
        mon.hid_win.enumerate_hid = lambda: [
            hid_win.HidCollection(
                path=r"\\?\hid#vid_30fa&pid_0400#test",
                vendor_id=0x30FA, product_id=0x0400,
                usage_page=0x0001, usage=0x0002, input_len=6,
                product="USB Optical Mouse"),
        ]
        try:
            dev = mon.BrandHidMonitor(0.5)
            dev._connect()
            self.assertTrue(dev.snapshot().connected)
        finally:
            mon.hid_win.enumerate_hid = orig


class HeadsetRowTests(unittest.TestCase):
    def test_bloody_headset_name(self) -> None:
        """耳机固件把产品名写在音频终端描述符里：名字里带品牌。"""
        row = acc.row_from_endpoint(
            "幽灵血手耳机", r"{1}.USB\VID_0D8C&PID_0012&MI_00\7&1FB4197D&0&0000")
        self.assertIsNotNone(row)
        self.assertEqual(row["kind"], "headset")
        self.assertEqual(row["brand"], "血手幽灵")
        self.assertEqual(row["model"], "耳机")
        self.assertEqual(row["key"], "acc:headset:0D8C:0012")

    def test_row_is_not_switchable(self) -> None:
        """用户明确要求：配件只展示，点了不能切。"""
        row = acc.row_from_endpoint("幽灵血手耳机", r"USB\VID_0D8C&PID_0012&MI_00\7&1")
        self.assertFalse(row["switchable"])
        self.assertFalse(row["current"])

    def test_wired_power(self) -> None:
        """USB 耳机是有线取电：走「USB供电」，电量位置不空着。"""
        row = acc.row_from_endpoint("幽灵血手耳机", r"USB\VID_0D8C&PID_0012&MI_00\7&1")
        self.assertTrue(row["wired"])

    def test_unknown_brand_is_skipped(self) -> None:
        """认不出品牌就不显示——杂牌耳机挂一排「USB Audio Device」是噪音。"""
        self.assertIsNone(acc.row_from_endpoint(
            "USB Audio Device", r"USB\VID_0D8C&PID_0012&MI_00\7&1"))

    def test_non_usb_path_is_skipped(self) -> None:
        """主板板载声卡（HDAUDIO）没有 USB 身份，不进列表。"""
        self.assertIsNone(acc.row_from_endpoint(
            "幽灵血手耳机", r"{1}.HDAUDIO\FUNC_01&VEN_1002&DEV_AA01\7&3A7F8BF&0&0001"))

    def test_model_from_name_when_not_generic(self) -> None:
        """产品名里有真型号就别扔：G501 这种要原样保留。"""
        row = acc.row_from_endpoint(
            "血手幽灵 G501", r"USB\VID_0D8C&PID_0012&MI_00\7&1")
        self.assertEqual(row["model"], "G501")

    def test_dedup_by_vid_pid(self) -> None:
        """一台耳机可能有多个播放端点，列表里只能出现一次。"""
        rows = acc.headsets()
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))


class KeyboardRowTests(unittest.TestCase):
    def test_keyboard_rows_from_real_machine(self) -> None:
        """真机上插着血手 B930：应当扫出一行键盘。"""
        rows = [r for r in acc.keyboards() if r["kind"] == "keyboard"]
        self.assertTrue(rows)
        row = rows[0]
        self.assertEqual((row["brand"], row["model"]), ("血手幽灵", "B930"))
        self.assertTrue(row["key"].startswith("acc:keyboard:"))

    def test_scan_never_contains_mouse_kind(self) -> None:
        """配件扫描只出键盘和耳机，鼠标永远走 DeviceHub 那一路。"""
        for row in acc.scan():
            self.assertIn(row["kind"], ("keyboard", "headset"))


class MonitorThreadTests(unittest.TestCase):
    def test_rows_snapshot(self) -> None:
        mon_ = acc.AccessoryMonitor(0.05)
        self.assertEqual(mon_.rows(), [])
        mon_.start()
        try:
            for _ in range(40):
                if mon_.rows():
                    break
                mon_._stop.wait(0.05)
            self.assertTrue(mon_.rows())   # 真机上至少有血手键盘
        finally:
            mon_.stop()


class MenuInteractionTests(unittest.TestCase):
    """弹窗交互：配件行不亮悬停底衬、不给手型、点了不发切换信号。"""

    ROWS = [
        {"key": "m", "label": "Logitech G502 HERO", "brand": "Logitech",
         "model": "G502 HERO", "available": True, "current": True,
         "battery": None, "wired": True, "kind": "mouse", "switchable": True},
        {"key": "acc:keyboard:09DA:FA44", "label": "血手幽灵 B930",
         "brand": "血手幽灵", "model": "B930", "available": True, "current": False,
         "battery": None, "wired": True, "kind": "keyboard", "switchable": False},
    ]

    @classmethod
    def setUpClass(cls) -> None:
        _APP

    def _menu(self):
        return W.DeviceMenu(list(self.ROWS), theme.DARK)

    def _pos(self, menu, idx: int) -> QPointF:
        fr = menu.frame_rect()
        y = fr.top() + menu.PAD + idx * menu.ROW_H + menu.ROW_H / 2
        return QPointF(menu.SHADOW + 40, y)

    def test_hand_only_on_switchable(self) -> None:
        menu = self._menu()
        menu.show()
        try:
            self.assertTrue(menu.wants_hand(menu.mapToGlobal(
                self._pos(menu, 0).toPoint())))
            self.assertFalse(menu.wants_hand(menu.mapToGlobal(
                self._pos(menu, 1).toPoint())))
        finally:
            menu.hide()

    def test_click_on_accessory_emits_nothing(self) -> None:
        menu = self._menu()
        got: list[str] = []
        menu.chosen.connect(got.append)
        menu.show()
        try:
            QTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
            QTest.mouseClick(menu, Qt.LeftButton, pos=self._pos(menu, 1).toPoint())
            self.assertEqual(got, [])
            self.assertTrue(menu.isVisible())   # 也没把菜单收掉
        finally:
            menu.hide()

    def test_click_on_mouse_emits_key(self) -> None:
        menu = self._menu()
        got: list[str] = []
        menu.chosen.connect(got.append)
        menu.show()
        try:
            QTest = __import__("PySide6.QtTest", fromlist=["QTest"]).QTest
            QTest.mouseClick(menu, Qt.LeftButton, pos=self._pos(menu, 0).toPoint())
            self.assertEqual(got, ["m"])
        finally:
            menu.hide()


class BatteryTextTests(unittest.TestCase):
    def _menu(self):
        return W.DeviceMenu([
            {"key": "m", "label": "m", "available": True, "current": True},
        ], theme.DARK)

    def test_wired_accessory_shows_usb_power(self) -> None:
        menu = self._menu()
        self.assertEqual(menu.battery_text({"wired": True, "kind": "headset",
                                            "battery": None}), "USB供电")

    def test_wireless_accessory_without_reading(self) -> None:
        """无线却读不到电量：如实写「无法读取」，不许空着也不许编百分比。"""
        menu = self._menu()
        self.assertEqual(menu.battery_text({"wired": False, "kind": "keyboard",
                                            "battery": None}), "无法读取")

    def test_wireless_mouse_without_reading_stays_blank(self) -> None:
        """鼠标保持原样：读不到就留白，不天天挂一句废话。"""
        menu = self._menu()
        self.assertEqual(menu.battery_text({"wired": False, "kind": "mouse",
                                            "battery": None}), "")

    def test_wireless_with_reading_shows_percent(self) -> None:
        menu = self._menu()
        self.assertEqual(menu.battery_text({"wired": False, "kind": "headset",
                                            "battery": 73}), "73%")

    def test_battery_box_wide_enough_for_new_text(self) -> None:
        """「无法读取」比「100%」宽：列宽必须按最宽的算，右缘才不挤。"""
        menu = self._menu()
        self.assertGreaterEqual(menu._batt_w, 40.0)


if __name__ == "__main__":
    unittest.main()
