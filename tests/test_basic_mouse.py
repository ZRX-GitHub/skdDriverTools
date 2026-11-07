"""狼途（以及同类「没有厂商通道」的鼠标）的识别与只读展示。

这类设备在 HID 里只有一条标准鼠标集合：没有厂商用途页、没有 output /
feature 报表，连读都被系统挡住（mouhid 独占）。所以验收标准不是「能不能改」，
而是三条：
  1. 能被认出来，按品牌列进设备列表（而不是干脆看不见）；
  2. 数值一律留空、绝不编造，界面上说清「软件读不到 / 用按键调」；
  3. 卡片不可点、不弹编辑框、不给手型光标。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

import config  # noqa: E402
import devcaps  # noqa: E402
import driver  # noqa: E402
import hid_win  # noqa: E402
import hidbrands  # noqa: E402
import i18n  # noqa: E402
import monitor as mon  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _col(vid: int, pid: int, product: str, usage_page: int = 0x0001,
         usage: int = 0x0002, path: str = "") -> hid_win.HidCollection:
    return hid_win.HidCollection(
        path=path or f"\\\\?\\hid#vid_{vid:04x}&pid_{pid:04x}#test",
        vendor_id=vid, product_id=pid, usage_page=usage_page, usage=usage,
        input_len=6, product=product, instance=path,
    )


class IdentifyTests(unittest.TestCase):
    def test_langtu_vid(self) -> None:
        info = hidbrands.identify(0x30FA, 0x0400, "USB Optical Mouse")
        self.assertIsNotNone(info)
        self.assertEqual(info["brand"], "狼途")
        self.assertEqual(info["model"], "USB Optical Mouse")
        self.assertEqual((info["vid"], info["pid"]), (0x30FA, 0x0400))

    def test_unknown_vid_returns_none(self) -> None:
        """认不出的 VID 不许瞎认——标错品牌比不显示更糟。"""
        self.assertIsNone(hidbrands.identify(0x046D, 0xC08B, "G502 HERO"))
        self.assertIsNone(hidbrands.identify(0, 0, ""))

    def test_model_falls_back_to_vid_pid(self) -> None:
        info = hidbrands.identify(0x30FA, 0x0400, "")
        self.assertEqual(info["model"], "30FA:0400")


class BrandMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = mon.hid_win.enumerate_hid
        self.dev = mon.BrandHidMonitor(0.5)

    def tearDown(self) -> None:
        mon.hid_win.enumerate_hid = self._orig

    def _enumerate(self, cols):
        mon.hid_win.enumerate_hid = lambda: list(cols)

    def test_picks_whitelisted_device(self) -> None:
        self._enumerate([
            _col(0x046D, 0xC08B, "G502 HERO"),
            _col(0x30FA, 0x0400, "USB Optical Mouse"),
        ])
        self.dev._connect()
        info = self.dev.snapshot()
        self.assertTrue(info.connected)
        self.assertEqual(info.brand, "狼途")
        self.assertEqual(info.model, "USB Optical Mouse")
        self.assertFalse(info.is_wireless, "有线设备不能被当成无线")
        self.assertEqual(info.connection, "USB 有线")
        self.assertEqual(info.extra.get("kind"), "basic")

    def test_no_value_is_invented(self) -> None:
        """读不到就是读不到：不留「100% / 1000 Hz」这种编出来的数。"""
        self._enumerate([_col(0x30FA, 0x0400, "USB Optical Mouse")])
        self.dev._connect()
        info = self.dev.snapshot()
        self.assertIsNone(info.dpi)
        self.assertIsNone(info.report_rate)
        self.assertIsNone(info.battery)
        self.assertEqual(info.dpi_levels, 0)
        self.assertEqual(info.dpi_table, [])

    def test_caps_are_readonly(self) -> None:
        self._enumerate([_col(0x30FA, 0x0400, "USB Optical Mouse")])
        self.dev._connect()
        caps = self.dev.snapshot().caps
        self.assertEqual(caps.source, "hw")
        self.assertFalse(caps.dpi_editable)
        self.assertFalse(caps.rate_editable)
        self.assertFalse(caps.levels_editable)

    def test_write_commands_refused(self) -> None:
        self._enumerate([_col(0x30FA, 0x0400, "USB Optical Mouse")])
        self.dev._connect()
        for result in (self.dev.set_dpi(1600), self.dev.set_report_rate(1000),
                       self.dev.set_dpi_levels([800, 1600])):
            self.assertFalse(result[0])
            self.assertIn("通道", result[1])

    def test_missing_device_reports_offline(self) -> None:
        self._enumerate([_col(0x046D, 0xC08B, "G502 HERO")])
        self.dev._connect()
        self.assertFalse(self.dev.snapshot().connected)

    def test_only_mouse_collections_are_considered(self) -> None:
        """键盘 / 厂商集合不能顶替鼠标：VID 对但用途页不对，不算。"""
        self._enumerate([
            _col(0x30FA, 0x0400, "USB Optical Mouse", usage_page=0x0006, usage=0x0002),
            _col(0x30FA, 0x0400, "USB Optical Mouse", usage_page=0x0001, usage=0x0006),
        ])
        self.dev._connect()
        self.assertFalse(self.dev.snapshot().connected)

    def test_drop_clears_state(self) -> None:
        self._enumerate([_col(0x30FA, 0x0400, "USB Optical Mouse")])
        self.dev._connect()
        self.dev._drop()
        info = self.dev.snapshot()
        self.assertFalse(info.connected)
        self.assertIsNone(info.dpi)


class HubTests(unittest.TestCase):
    def test_three_sources_with_separate_caches(self) -> None:
        hub = mon.DeviceHub(1.0, {"crociris": {"model": "X7"},
                                  "logitech": {"model": "G502 HERO"},
                                  "basic": {"model": "USB Optical Mouse"}},
                            "basic")
        self.assertEqual(hub.selected, "basic")
        self.assertEqual(hub.sources[hub.CROCIRIS].cache.get("model"), "X7")
        self.assertEqual(hub.sources[hub.LOGITECH].cache.get("model"), "G502 HERO")
        self.assertEqual(hub.sources[hub.BASIC].cache.get("model"), "USB Optical Mouse")
        self.assertEqual(set(hub.cache_snapshot()), {"crociris", "logitech", "basic"})

    def test_devices_row_uses_brand_and_model(self) -> None:
        hub = mon.DeviceHub(1.0, None, "crociris")
        dev = hub.sources[hub.BASIC]
        dev._publish(connected=True, brand="狼途", model="USB Optical Mouse",
                     name="USB Optical Mouse")
        row = [r for r in hub.devices() if r["key"] == "basic"][0]
        self.assertEqual(row["brand"], "狼途")
        self.assertEqual(row["model"], "USB Optical Mouse")
        self.assertTrue(row["available"])
        self.assertIsNone(row["battery"], "有线设备没有电量可显示")

    def test_devices_row_translates_brand(self) -> None:
        hub = mon.DeviceHub(1.0, None, "crociris")
        hub.sources[hub.BASIC]._publish(connected=True, brand="狼途",
                                        model="USB Optical Mouse")
        i18n.set_lang(i18n.EN)
        try:
            row = [r for r in hub.devices() if r["key"] == "basic"][0]
            self.assertEqual(row["brand"], "LANGTU")
            self.assertEqual(row["label"], "LANGTU USB Optical Mouse")
        finally:
            i18n.set_lang(i18n.ZH)

    def test_select_basic_persists(self) -> None:
        cfg = config.Config()
        cfg.device = "basic"
        self.assertIn(cfg.device, ("crociris", "logitech", "basic"))


class DriverTests(unittest.TestCase):
    def test_kind_none_for_basic(self) -> None:
        info = mon.MouseInfo(extra={"kind": "basic"})
        self.assertEqual(driver.kind_of(info), driver.NONE)
        self.assertIn("按键", driver.button_text(driver.NONE))

    def test_kind_unchanged_for_other_brands(self) -> None:
        self.assertEqual(driver.kind_of(mon.MouseInfo(extra={"kind": "crociris"})),
                         driver.WEB)
        self.assertEqual(driver.kind_of(mon.MouseInfo(extra={"kind": "logitech"})),
                         driver.SOFTWARE)

    def test_open_driver_does_nothing_without_app(self) -> None:
        info = mon.MouseInfo(extra={"kind": "basic"})
        self.assertEqual(driver.open_driver(info, "https://example.com"), (False, ""))


class BadgeTests(unittest.TestCase):
    def test_badge_says_on_device(self) -> None:
        import widgets as W

        card = W.InfoCard("灵敏度")
        card.set_support_app("按键")
        self.assertEqual(card.support_label(), "按键调节")
        card.set_support_app("G HUB")
        self.assertEqual(card.support_label(), "由 G HUB 支持")
        card.set_support_app("")
        self.assertEqual(card.support_label(), "")

    def test_badge_english(self) -> None:
        import widgets as W

        card = W.InfoCard("Sensitivity")
        card.set_support_app("按键")
        i18n.set_lang(i18n.EN)
        try:
            self.assertEqual(card.support_label(), "On device")
        finally:
            i18n.set_lang(i18n.ZH)


class LanguageTests(unittest.TestCase):
    def test_strings_translated(self) -> None:
        i18n.set_lang(i18n.EN)
        try:
            self.assertEqual(i18n.T("狼途"), "LANGTU")
            self.assertEqual(i18n.T("按键调节"), "On device")
            self.assertEqual(i18n.T("无法读取"), "Can't read")
            self.assertIn("official app", i18n.T("无官方软件 · 按键调节"))
            self.assertEqual(i18n.T("USB供电"), "USB powered")
        finally:
            i18n.set_lang(i18n.ZH)

    def test_zh_is_identity(self) -> None:
        for text in ("狼途", "按键调节", "无法读取", "USB供电"):
            self.assertEqual(i18n.T(text), text)


if __name__ == "__main__":
    unittest.main()
