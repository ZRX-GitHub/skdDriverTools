"""卡片交互回归：点回报率进编辑模块、不支持时的「由 XX 软件支持」角标。

三块：
* 点卡片 —— 可编辑的卡点一下就进编辑态；回报率也一样（以前点一下直接弹
  浮层、卡片毫无变化，用户报「点了像没反应」）；不可编辑的卡点了什么都不做。
* 角标 —— 只有不支持编辑的模块才挂，文案「由 G HUB 支持」/「via G HUB」，
  随语言切换；卡片本身不给手型光标、不给悬停高亮。
* 回报率编辑模块自带「取消」——回报率是选完即写、没有「应用」，少了它，
  DPI 也不可写时整窗会卡在编辑态出不来。

    python -m unittest tests.test_card_badge -v
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import devcaps  # noqa: E402
import i18n  # noqa: E402
import theme  # noqa: E402
import widgets as W  # noqa: E402


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


def _press(card: W.InfoCard) -> None:
    """真的点一下（送进 mousePressEvent，走完整事件链）。"""
    QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, QPoint(20, 40))


class CardClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _rate_card(self) -> W.InfoCard:
        card = W.InfoCard("回报率")
        card._pal = theme.DARK
        card.set_picker(True)
        card.set_pick_values([125, 250, 500, 1000])
        card.set_editable(True)
        return card

    def test_rate_card_click_enters_edit_module(self) -> None:
        """点回报率卡片 = 进编辑模块（卡片里出现「2000 Hz ▾」），不是直接弹浮层。"""
        card = self._rate_card()
        edits, picks = [], []
        card.edit_requested.connect(lambda: edits.append(1))
        card.picker_requested.connect(lambda: picks.append(1))
        _press(card)
        self.assertEqual(edits, [1], "点卡片要进编辑态")
        self.assertEqual(picks, [], "点卡片不该直接弹挡位浮层")

    def test_pick_button_opens_picker(self) -> None:
        """编辑模块里的下拉按钮才是弹挡位面板的入口。"""
        card = self._rate_card()
        picks = []
        card.picker_requested.connect(lambda: picks.append(1))
        card.btn_pick.click()
        self.assertEqual(picks, [1])

    def test_pick_box_has_own_cancel(self) -> None:
        card = self._rate_card()
        got = []
        card.edit_cancelled.connect(lambda: got.append(1))
        card.btn_rate_cancel.click()
        self.assertEqual(got, [1], "回报率模块必须能自己退出编辑")

    def test_editing_card_ignores_further_clicks(self) -> None:
        card = self._rate_card()
        caps = devcaps.DeviceCaps(rate_values=[125, 250, 500, 1000], rate_writable=True)
        card.start_edit(1000, caps, "rate")
        edits = []
        card.edit_requested.connect(lambda: edits.append(1))
        _press(card)
        self.assertEqual(edits, [], "已经在编辑态就别再发一次")

    def test_read_only_card_does_not_react(self) -> None:
        """不支持编辑时：点了不该有任何动静（用户明确要求不给点击动线）。"""
        card = W.InfoCard("回报率")
        card._pal = theme.DARK
        card.set_picker(True)
        card.set_editable(False)
        card.set_pick_values([])
        edits, picks = [], []
        card.edit_requested.connect(lambda: edits.append(1))
        card.picker_requested.connect(lambda: picks.append(1))
        _press(card)
        self.assertEqual((edits, picks), ([], []))

    def test_cursor_follows_editable(self) -> None:
        card = W.InfoCard("回报率")
        card.set_editable(False)
        self.assertEqual(card.cursor().shape(), Qt.ArrowCursor)
        card.set_editable(True)
        self.assertEqual(card.cursor().shape(), Qt.PointingHandCursor)
        # 读到挡位但写不回去 → 仍然是箭头（不可点）
        card.set_editable(False)
        card.set_pick_values([125, 250])
        self.assertEqual(card.cursor().shape(), Qt.ArrowCursor)


class SupportBadgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def tearDown(self) -> None:
        i18n.set_lang(i18n.ZH)

    def test_empty_means_no_badge(self) -> None:
        card = W.InfoCard("灵敏度")
        card.set_support_app("")
        self.assertEqual(card.support_label(), "")

    def test_logitech_badge_text(self) -> None:
        card = W.InfoCard("回报率")
        card.set_support_app("G HUB")
        self.assertEqual(card.support_label(), "由 G HUB 支持")
        self.assertEqual(card.support_app, "G HUB")

    def test_badge_follows_language(self) -> None:
        card = W.InfoCard("回报率")
        card.set_support_app("G HUB")
        i18n.set_lang(i18n.EN)
        self.assertEqual(card.support_label(), "via G HUB")
        self.assertEqual(i18n.T("回报率"), "Rate", "英文标题要短，才给角标留出位置")

    def test_official_app_badge(self) -> None:
        card = W.InfoCard("灵敏度")
        card.set_support_app("官方软件")
        self.assertEqual(card.support_label(), "由 官方软件 支持")
        i18n.set_lang(i18n.EN)
        self.assertEqual(card.support_label(), "via official app")

    def test_paint_with_badge_does_not_crash(self) -> None:
        """角标是画出来的：缩字号 + 省略号两条路径都要能走通。"""
        for app in ("G HUB", "官方软件"):
            for lang in (i18n.ZH, i18n.EN):
                i18n.set_lang(lang)
                card = W.InfoCard("回报率")
                card._pal = theme.DARK if lang == i18n.ZH else theme.LIGHT
                card.set_support_app(app)
                card.set_content("1000", "Hz", "1 ms")
                card.resize(113, 78)
                card.grab()          # 走一遍 paintEvent
                card.set_support_app("")
                card.grab()

    def test_badge_hidden_while_editing(self) -> None:
        card = W.InfoCard("灵敏度")
        card._pal = theme.DARK
        card.set_support_app("G HUB")
        caps = devcaps.DeviceCaps(dpi_min=100, dpi_max=25600, dpi_step=50,
                                  dpi_writable=True)
        card.set_editable(True)
        card.start_edit(600, caps, "dpi")
        # 编辑态不画角标（数值让位给步进器），退出后照旧
        self.assertTrue(card.editing)
        card.stop_edit()
        self.assertEqual(card.support_label(), "由 G HUB 支持")


if __name__ == "__main__":
    unittest.main()
