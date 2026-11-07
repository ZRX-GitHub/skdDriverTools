"""驱动入口（网页驱动 / 客户端软件）的回归测试。

    python -m unittest discover -s tests -v

只测「怎么判断、怎么取文案、路径怎么拼」，不真的去启动 G HUB、
也不去翻注册表（那部分在真机上由 tools 目录里的脚本覆盖）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import driver  # noqa: E402
import monitor as M  # noqa: E402


def _info(kind: str = "", connected: bool = True) -> M.MouseInfo:
    return M.MouseInfo(connected=connected, extra={"kind": kind} if kind else {})


class KindTests(unittest.TestCase):

    def test_logitech_is_software(self) -> None:
        self.assertEqual(driver.kind_of(_info("logitech")), driver.SOFTWARE)

    def test_crociris_is_web(self) -> None:
        self.assertEqual(driver.kind_of(_info("crociris")), driver.WEB)

    def test_missing_kind_falls_back_to_web(self) -> None:
        self.assertEqual(driver.kind_of(_info()), driver.WEB)

    def test_none_info_is_web(self) -> None:
        self.assertEqual(driver.kind_of(None), driver.WEB)


class TextAndIconTests(unittest.TestCase):

    def test_texts(self) -> None:
        self.assertEqual(driver.button_text(driver.WEB), "打开官方网页驱动")
        self.assertEqual(driver.button_text(driver.SOFTWARE), "打开官方软件驱动")

    def test_icons_differ(self) -> None:
        # 文案换了图标也得换，否则两个按钮看上去还是同一个东西
        self.assertNotEqual(driver.button_icon(driver.WEB), driver.button_icon(driver.SOFTWARE))


class PathTests(unittest.TestCase):

    def test_join_returns_empty_when_missing(self) -> None:
        self.assertEqual(driver._join(os.path.join(tempfile.gettempdir(), "no-such-dir-xyz"),
                                      "lghub.exe"), "")

    def test_join_finds_existing_exe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "lghub.exe")
            open(exe, "wb").close()
            self.assertEqual(driver._join(tmp, "lghub.exe"), exe)

    def test_exe_from_install_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder, exe = driver._exe_from(tmp)
            self.assertEqual((folder, exe), (tmp, ""))

    def test_exe_from_exe_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "lghub.exe")
            open(exe, "wb").close()
            folder, found = driver._exe_from(exe)
            self.assertEqual(found, exe)
            self.assertEqual(folder, tmp)

    def test_exe_from_uninstaller_points_at_folder(self) -> None:
        # 注册表里常见的是 uninstall.exe，只能取它所在目录再拼 lghub.exe
        with tempfile.TemporaryDirectory() as tmp:
            un = os.path.join(tmp, "uninstall.exe")
            open(un, "wb").close()
            self.assertEqual(driver._exe_from(un), (tmp, ""))

    def test_exe_from_quoted_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "lghub.exe")
            open(exe, "wb").close()
            self.assertEqual(driver._exe_from(f'"{exe}"')[1], exe)


class WebUrlTests(unittest.TestCase):

    def test_ghub_url_is_https(self) -> None:
        self.assertTrue(driver.GHUB_URL.startswith("https://"))


if __name__ == "__main__":
    unittest.main()
