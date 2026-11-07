"""头部「N台设备」必须与设备弹窗列出来的行数一致。

v1.4.26 加了键盘 / 耳机配件：弹窗里追加两行，头部却还在数鼠标，于是出现
「弹窗 5 行、标题写 3台设备」。修法不是把 3 改成某个常数，而是让两处共用
`main.device_menu_rows()` 这一份行数据——以后再加品类也不会分叉。

这里锁三件事：
  1. 行过滤 / 排序（在线鼠标在前、在线配件在后、离线一律不进）；
  2. 头部计数把配件算进去，中英文都对，且只有配件时标题仍可点开；
  3. `_sync()` 和 `_show_device_menu()` 都走 `_device_rows()`，不许再各算各的。
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

import i18n  # noqa: E402
import main as M  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _mice() -> list[dict]:
    return [
        {"key": "crociris", "label": "CROCIRIS X7 Ultra Max", "available": True,
         "current": True},
        {"key": "logitech", "label": "Logitech G502 HERO", "available": True,
         "current": False},
        {"key": "basic", "label": "狼途 USB Optical Mouse", "available": True,
         "current": False},
        # 拔掉的鼠标：不该进弹窗，也不该算进「N台设备」
        {"key": "offline", "label": "拔掉的鼠标", "available": False,
         "current": False},
    ]


def _acc() -> list[dict]:
    return [
        {"key": "kbd:09DA:FA44", "label": "血手幽灵 B930", "available": True,
         "kind": "keyboard", "switchable": False, "wired": True},
        {"key": "audio:x", "label": "血手幽灵 耳机", "available": True,
         "kind": "headset", "switchable": False, "wired": True},
        {"key": "kbd:offline", "label": "拔掉的键盘", "available": False,
         "kind": "keyboard", "switchable": False},
    ]


class RowSourceTests(unittest.TestCase):

    def test_only_online_rows_and_mice_first(self) -> None:
        rows = M.device_menu_rows(_mice(), _acc())
        self.assertEqual([r["key"] for r in rows],
                         ["crociris", "logitech", "basic",
                          "kbd:09DA:FA44", "audio:x"])
        self.assertEqual(len(rows), 5)

    def test_empty_sources(self) -> None:
        self.assertEqual(M.device_menu_rows([], []), [])
        self.assertEqual(M.device_menu_rows(_mice(), []), _mice()[:3])

    def test_sources_are_not_mutated(self) -> None:
        mice, acc = _mice(), _acc()
        M.device_menu_rows(mice, acc)
        self.assertEqual(len(mice), 4)
        self.assertEqual(len(acc), 3)

    def test_window_method_merges_both_sources(self) -> None:
        fake = SimpleNamespace(monitor=SimpleNamespace(devices=lambda: _mice()),
                               accessory=SimpleNamespace(rows=lambda: _acc()))
        rows = M.MouseGlassWindow._device_rows(fake)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[3]["kind"], "keyboard")

    def test_accessory_failure_does_not_break_mice(self) -> None:
        def boom() -> list[dict]:
            raise OSError("枚举炸了")
        fake = SimpleNamespace(monitor=SimpleNamespace(devices=lambda: _mice()),
                               accessory=SimpleNamespace(rows=boom))
        self.assertEqual(len(M.MouseGlassWindow._device_rows(fake)), 3)

    def test_no_accessory_attribute(self) -> None:
        fake = SimpleNamespace(monitor=SimpleNamespace(devices=lambda: _mice()))
        self.assertEqual(len(M.MouseGlassWindow._device_rows(fake)), 3)

    def test_header_and_menu_share_one_path(self) -> None:
        """防回归：两处都必须取 `_device_rows()`，不许一个只数鼠标。"""
        sync = inspect.getsource(M.MouseGlassWindow._sync)
        menu = inspect.getsource(M.MouseGlassWindow._show_device_menu)
        self.assertIn("set_devices(self._device_rows())", sync)
        self.assertIn("rows = self._device_rows()", menu)


class HeaderCountTests(unittest.TestCase):

    def setUp(self) -> None:
        self._lang = i18n.lang()
        i18n.set_lang(i18n.ZH)

    def tearDown(self) -> None:
        i18n.set_lang(self._lang)

    def _header(self, rows: list[dict]) -> M.Header:
        h = M.Header()
        h.resize(M.CARD_W, M.CARD_H)
        h.set_devices(rows)
        h.set_state("CROCIRIS", "X7 Ultra Max", "已连接", True)
        return h

    def _texts(self, h: M.Header) -> list[str]:
        return [t for t, _ in h._status_segments()]

    def test_count_includes_accessories(self) -> None:
        h = self._header(M.device_menu_rows(_mice(), _acc()))
        self.assertEqual(h._available(), 5)
        self.assertIn("5台设备", self._texts(h))
        self.assertNotIn("3台设备", " ".join(self._texts(h)))

    def test_count_is_english_too(self) -> None:
        i18n.set_lang(i18n.EN)
        h = self._header(M.device_menu_rows(_mice(), _acc()))
        self.assertIn("5 devices", self._texts(h))

    def test_only_mice_still_counts_mice(self) -> None:
        h = self._header(M.device_menu_rows(_mice(), []))
        self.assertIn("3台设备", self._texts(h))

    def test_accessories_only_still_opens_menu(self) -> None:
        """一台鼠标都没有、只有键盘耳机时，标题依然能点开清单。"""
        h = self._header(M.device_menu_rows([], _acc()))
        self.assertEqual(h._available(), 2)
        self.assertIn("2台设备", self._texts(h))
        self.assertTrue(h._title_clickable())

    def test_no_devices_has_no_count_segment(self) -> None:
        h = self._header([])
        self.assertEqual(h._available(), 0)
        self.assertNotIn("0台设备", " ".join(self._texts(h)))
        self.assertFalse(h._title_clickable())


if __name__ == "__main__":
    unittest.main()
