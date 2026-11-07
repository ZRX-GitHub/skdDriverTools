"""双语层：品牌名、设置行、翻译覆盖率。

用户要求「适配英文，其中品牌名三颗豆 = SKD，设置页把关闭时最小化那一行换成
语言切换」。这里锁三件事：

* 英文界面里品牌必须是 **SKD**，不是拼音也不是中文；
* 设置页的 6 行固定是「开机启动 / 置顶 / 语言 / 主题 / 低电量提醒 / 阈值」，
  「关闭时最小化」变成固定行为、不再占开关位；
* 语言行的分段控件能点到、行内文字区不是可点区域（否则整行都像能点）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import config  # noqa: E402
import devcaps  # noqa: E402
import i18n  # noqa: E402
import main as M  # noqa: E402
import setup_core  # noqa: E402


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


class TranslateTests(unittest.TestCase):
    def tearDown(self) -> None:
        i18n.set_lang("zh")

    def test_brand_is_skd(self) -> None:
        i18n.set_lang("en")
        self.assertEqual(i18n.T("三颗豆"), "SKD")
        self.assertEqual(i18n.T("三颗豆驱动管理"), "SKD Driver Manager")
        self.assertEqual(i18n.T("三颗豆 · 未连接"), "SKD · Not connected")

    def test_chinese_is_identity(self) -> None:
        i18n.set_lang("zh")
        self.assertEqual(i18n.T("设置"), "设置")
        self.assertEqual(i18n.T("三颗豆"), "三颗豆")

    def test_unknown_text_passes_through(self) -> None:
        """字典里没有的条目原样返回：漏译是「没翻」，不是空白控件。"""
        i18n.set_lang("en")
        self.assertEqual(i18n.T("CROCIRIS X7 Ultra Max"), "CROCIRIS X7 Ultra Max")

    def test_unknown_language_falls_back_to_chinese(self) -> None:
        i18n.set_lang("fr")
        self.assertEqual(i18n.lang(), "zh")
        i18n.set_lang("EN")
        self.assertEqual(i18n.lang(), "en")

    def test_devices_count_plural(self) -> None:
        i18n.set_lang("zh")
        self.assertEqual(i18n.devices(2), "2台设备")
        i18n.set_lang("en")
        self.assertEqual(i18n.devices(1), "1 device")
        self.assertEqual(i18n.devices(2), "2 devices")

    def test_stage_text(self) -> None:
        i18n.set_lang("zh")
        self.assertEqual(i18n.stage(2, 4), "第 2 档 / 共 4 档")
        self.assertEqual(i18n.stages_total(4), "共 4 档")
        i18n.set_lang("en")
        self.assertEqual(i18n.stage(2, 4), "Stage 2 / 4")
        self.assertEqual(i18n.stages_total(4), "4 stages")


class SettingsRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def test_language_replaces_minimize_row(self) -> None:
        keys = [k for k, _t in M.SettingsPanel.ROWS]
        self.assertIn("language", keys)
        self.assertNotIn("tray", keys)
        self.assertEqual(len(keys), 6)

    def test_every_row_title_is_translated(self) -> None:
        i18n.set_lang("en")
        try:
            for _key, title in M.SettingsPanel.ROWS:
                self.assertNotEqual(i18n.T(title), title, f"{title} 没翻译")
        finally:
            i18n.set_lang("zh")

    def test_all_close_to_tray_defaults_on(self) -> None:
        """关闭按钮固定「最小化到托盘」：默认值和读配置都必须是 True。"""
        self.assertTrue(config.Config().close_to_tray)

    def test_language_row_hand_area(self) -> None:
        _app()
        panel = M.SettingsPanel(config.Config())
        panel.isVisible = lambda: True          # type: ignore[assignment]
        y = panel.rows_top(panel.row_index("language")) + panel.ROW_H / 2
        # 左侧文字区不可点（整行不切换），分段控件上可以是手型
        self.assertFalse(panel.wants_hand(panel.mapToGlobal(
            QPoint(int(panel.ROW_X + 20), int(y)))))
        seg = panel.lang_seg.geometry()
        self.assertTrue(panel.wants_hand(panel.mapToGlobal(seg.center())))

    def test_segment_has_two_languages(self) -> None:
        _app()
        panel = M.SettingsPanel(config.Config())
        self.assertEqual(panel.lang_seg._items, ["中文", "English"])


class HintTests(unittest.TestCase):
    def tearDown(self) -> None:
        i18n.set_lang("zh")

    def test_dpi_hint_english(self) -> None:
        caps = devcaps.DeviceCaps(dpi_min=50, dpi_max=64000, dpi_step=1)
        i18n.set_lang("zh")
        self.assertEqual(caps.dpi_hint(), "可调 50–64000 DPI\n步进 1 DPI")
        i18n.set_lang("en")
        self.assertEqual(caps.dpi_hint(), "Adj. 50–64000 DPI\nStep 1 DPI")

    def test_rate_hint_english(self) -> None:
        caps = devcaps.DeviceCaps(rate_values=[125, 250, 500, 1000])
        i18n.set_lang("en")
        self.assertEqual(caps.rate_hint(), "Adj. 125–1000 Hz")
        caps.rate_values = [1000]
        self.assertEqual(caps.rate_hint(), "Only 1000 Hz")


class InstallerTests(unittest.TestCase):
    """安装器双语：默认语言跟随系统、切换后整窗换语言、选的语言写进主程序配置。"""

    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def tearDown(self) -> None:
        i18n.set_lang("zh")

    # -------------------------------------------------------------- 默认语言
    def test_langid_only_english_is_english(self) -> None:
        # 0x0409 = en-US，0x0809 = en-GB，0x0C09 = en-AU
        for langid in (0x0409, 0x0809, 0x0C09):
            self.assertEqual(i18n._lang_from_langid(langid), "en", hex(langid))

    def test_langid_other_languages_fall_back_to_chinese(self) -> None:
        """用户明确要求：系统不是中英文就默认中文（不是英文）。"""
        # 0x0411 日文 / 0x0407 德文 / 0x040C 法文 / 0x0412 韩文
        for langid in (0x0411, 0x0407, 0x040C, 0x0412):
            self.assertEqual(i18n._lang_from_langid(langid), "zh", hex(langid))

    def test_langid_chinese_variants(self) -> None:
        # 0x0804 简中 / 0x0404 繁中 / 0x0C04 港中
        for langid in (0x0804, 0x0404, 0x0C04):
            self.assertEqual(i18n._lang_from_langid(langid), "zh", hex(langid))

    def test_initial_language_follows_system_when_fresh(self) -> None:
        import installer

        with unittest.mock.patch.object(setup_core, "read_config_language", return_value=""), \
                unittest.mock.patch.object(i18n, "system_default_lang", return_value="en"):
            self.assertEqual(installer.initial_language(), "en")
        with unittest.mock.patch.object(setup_core, "read_config_language", return_value=""), \
                unittest.mock.patch.object(i18n, "system_default_lang", return_value="zh"):
            self.assertEqual(installer.initial_language(), "zh")

    def test_initial_language_keeps_previous_choice(self) -> None:
        """升级安装不能把用户上次选的英文打回中文。"""
        import installer

        with unittest.mock.patch.object(setup_core, "read_config_language", return_value="en"), \
                unittest.mock.patch.object(i18n, "system_default_lang", return_value="zh"):
            self.assertEqual(installer.initial_language(), "en")

    # -------------------------------------------------------------- 覆盖率
    def test_every_literal_in_installer_is_translated(self) -> None:
        """安装器里凡是用 T(...) 包起来的中文，字典里必须真有译文。"""
        import re

        with open(os.path.join(ROOT, "src", "installer.py"), encoding="utf-8") as fh:
            src = fh.read()
        literals = set(re.findall(r'i18n\.T\(\s*f?"([^"]+)"', src))
        self.assertTrue(literals)
        missing = sorted(t for t in literals if t not in i18n.TABLE)
        self.assertEqual(missing, [], f"这些安装器文案没有英文：{missing}")

    def test_rows_and_buttons_translated(self) -> None:
        import installer

        i18n.set_lang("en")
        for _key, title, hint in installer.ROWS:
            self.assertNotEqual(i18n.T(title), title, f"{title} 没翻译")
            self.assertNotEqual(i18n.T(hint), hint, f"{hint} 没翻译")
        for text in ("更改…", "安装", "取消", "请稍候", "重试", "完成",
                     "正在安装…", "安装完成，正在启动…", "安装位置",
                     "安装到当前用户，无需管理员权限",
                     "将安装到新位置，旧目录里的旧版本会被清理",
                     "检测到已安装版本，继续将覆盖更新",
                     "界面语言，安装完成后立即生效", "语言"):
            self.assertNotEqual(i18n.T(text), text, f"{text} 没翻译")

    # -------------------------------------------------------------- 界面
    def test_window_retranslates_on_switch(self) -> None:
        import installer

        win = installer.SetupWindow()
        try:
            win._on_language(1)                       # 切到 English
            self.assertEqual(i18n.lang(), "en")
            self.assertIn("Install SKD", win.windowTitle())
            self.assertEqual(win.btn_ok._text, "Install")
            self.assertEqual(win.btn_cancel._text, "Cancel")
            self.assertEqual(win.btn_browse._text, "Browse…")
            self.assertEqual(win.lang_seg.index(), 1)

            win._on_language(0)                       # 切回中文
            self.assertEqual(i18n.lang(), "zh")
            self.assertIn("安装 三颗豆", win.windowTitle())
            self.assertEqual(win.btn_ok._text, "安装")
            self.assertEqual(win.btn_cancel._text, "取消")
        finally:
            win.close()

    def test_language_row_fits_inside_panel(self) -> None:
        """语言行 + 按钮 + 底部提示都得在面板高度之内（改高度时别忘了它们）。"""
        import installer

        win = installer.SetupWindow()
        try:
            self.assertLess(installer.HINT_Y + 18, installer.PANEL_H)
            self.assertLess(installer.BTN_Y + 38, installer.HINT_Y)
            self.assertLess(installer.LANG_Y + 44, installer.BTN_Y)
            self.assertLess(win.lang_seg.geometry().right(), installer.PANEL_W)
        finally:
            win.close()

    def test_ok_button_text_follows_state(self) -> None:
        """安装中/失败/完成后主按钮换字，切语言也得跟着换。"""
        import installer

        win = installer.SetupWindow()
        try:
            win._ok_key = "重试"
            win.btn_ok.set_text("重试")
            win._on_language(1)
            self.assertEqual(win.btn_ok._text, "Retry")
        finally:
            win.close()


class ConfigLanguageTests(unittest.TestCase):
    """安装器把语言写进主程序配置：只动 language 一个键。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="skd_cfg_")
        self._patch = unittest.mock.patch.object(
            setup_core, "config_dir", side_effect=lambda: self.tmp)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_keeps_other_keys(self) -> None:
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"x": 120, "y": 40, "low_battery_threshold": 25}, fh)
        self.assertTrue(setup_core.write_config_language("en"))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["language"], "en")
        self.assertEqual(data["x"], 120)
        self.assertEqual(data["low_battery_threshold"], 25)

    def test_write_creates_config_when_missing(self) -> None:
        self.assertTrue(setup_core.write_config_language("zh"))
        self.assertEqual(setup_core.read_config_language(), "zh")

    def test_write_rejects_unknown_language(self) -> None:
        self.assertFalse(setup_core.write_config_language("fr"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "config.json")))

    def test_read_ignores_garbage(self) -> None:
        path = os.path.join(self.tmp, "config.json")
        for payload in ('{"language": "fr"}', "not json", '["zh"]'):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(payload)
            self.assertEqual(setup_core.read_config_language(), "", payload)


if __name__ == "__main__":
    unittest.main()
