"""浮窗开着时，主页面的可编辑入口必须完全封锁。

用户报的第 2 个问题：打开设备列表（设备卡片）或设置面板（设置卡片）之后，
鼠标划过主页面仍然会亮悬停、给手型，点下去还会把卡片切进编辑态——而用户点
那一下的本意只是「收起浮窗」。

成因不是控件没接线，而是这些浮窗是**不抢模态的顶层窗口**（Qt.Tool）：主窗口
照旧收得到鼠标事件，卡片照样派发 mousePress。所以封锁只能做在控件层，这里
锁三件事：

* ``InfoCard`` 锁住后：不派发 edit_requested、悬停判据为假、光标是箭头；
* ``GlassButton`` 锁住后：按下被吃掉，抬起也不再补发 clicked（不能刷新 / 开驱动）；
* 主窗口按「有没有浮窗开着」统一下发封锁，且浮窗收起后**再保持 250ms**——
  全局钩子里的关闭动作可能比原生按下事件更晚到，不留宽限期那一下会漏进去。
"""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as M  # noqa: E402
import theme  # noqa: E402
import widgets as W  # noqa: E402


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


class InfoCardLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _card(self) -> W.InfoCard:
        card = W.InfoCard("灵敏度")
        card._pal = theme.DARK
        card.set_editable(True)
        card.resize(113, 78)
        return card

    def test_click_enters_edit_when_unlocked(self) -> None:
        """基线：没锁的时候点一下就是进编辑态，别把正常路径弄坏。"""
        card = self._card()
        hits: list[int] = []
        card.edit_requested.connect(lambda: hits.append(1))
        QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, QPoint(20, 40))
        self.assertEqual(len(hits), 1)

    def test_locked_card_swallows_click(self) -> None:
        card = self._card()
        hits: list[int] = []
        card.edit_requested.connect(lambda: hits.append(1))
        card.set_input_locked(True)
        QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, QPoint(20, 40))
        self.assertEqual(hits, [], "浮窗开着时点卡片不许进编辑态")

    def test_locked_card_has_no_hover_and_arrow_cursor(self) -> None:
        card = self._card()
        card.set_input_locked(True)
        self.assertFalse(card._hover_ok(), "锁住后不能再给悬停反馈")
        self.assertEqual(card.cursor().shape(), Qt.ArrowCursor)

    def test_locked_card_makes_edit_controls_click_through(self) -> None:
        """卡片内的步进器 / 挡位按钮也不能被点到，但外观不变（不是 disable）。"""
        card = self._card()
        card.set_input_locked(True)
        self.assertTrue(card.edit_box.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(card.edit_box.isEnabled())
        card.set_input_locked(False)
        self.assertFalse(card.edit_box.testAttribute(Qt.WA_TransparentForMouseEvents))

    def test_unlock_restores_hand_and_click(self) -> None:
        card = self._card()
        card.set_input_locked(True)
        card.set_input_locked(False)
        self.assertTrue(card._hover_ok())
        self.assertEqual(card.cursor().shape(), Qt.PointingHandCursor)
        hits: list[int] = []
        card.edit_requested.connect(lambda: hits.append(1))
        QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, QPoint(20, 40))
        self.assertEqual(len(hits), 1)


class ButtonLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _button(self) -> W.GlassButton:
        btn = W.GlassButton("刷新", kind="primary", height=46)
        btn.resize(200, 46)
        return btn

    def test_locked_button_does_not_fire_on_press_or_release(self) -> None:
        btn = self._button()
        hits: list[int] = []
        btn.clicked.connect(lambda: hits.append(1))
        btn.set_input_locked(True)
        QTest.mouseClick(btn, Qt.LeftButton, Qt.NoModifier, QPoint(30, 20))
        self.assertEqual(hits, [], "浮窗开着时主按钮不该被点掉")

    def test_unlock_restores_click(self) -> None:
        btn = self._button()
        hits: list[int] = []
        btn.clicked.connect(lambda: hits.append(1))
        btn.set_input_locked(True)
        btn.set_input_locked(False)
        QTest.mouseClick(btn, Qt.LeftButton, Qt.NoModifier, QPoint(30, 20))
        self.assertEqual(len(hits), 1)


class _PanelStub:
    """只回答「开着没」，不必真建浮层。"""

    def __init__(self, is_open: bool) -> None:
        self._is_open = is_open

    def is_open(self) -> bool:
        return self._is_open


class PageLockTests(unittest.TestCase):
    """主窗口那层「有没有浮窗开着 → 锁不锁」的判定。"""

    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _window(self, card: W.InfoCard, btn: W.GlassButton):
        """只借 MouseGlassWindow 的方法，不跑 __init__（那会起线程和托盘）。"""
        win = M.MouseGlassWindow.__new__(M.MouseGlassWindow)
        win._page_locked = False
        win._page_lock_hold_until = 0.0
        win._float_was_open = False
        win.cards = (card,)
        win.btn_primary = btn
        win.btn_refresh = None
        win._device_menu = None
        win._rate_picker = None
        win._level_picker = None
        win.settings = _PanelStub(False)
        return win

    def _parts(self):
        card = W.InfoCard("灵敏度")
        card._pal = theme.DARK
        card.set_editable(True)
        card.resize(113, 78)
        btn = W.GlassButton("刷新", kind="primary", height=46)
        btn.resize(200, 46)
        return card, btn

    def test_panel_open_locks_page(self) -> None:
        card, btn = self._parts()
        win = self._window(card, btn)
        win.settings = _PanelStub(True)
        M.MouseGlassWindow._sync_page_input_lock(win)
        self.assertTrue(card._input_locked)
        self.assertTrue(btn._input_locked)

    def test_all_closed_unlocks_page(self) -> None:
        card, btn = self._parts()
        win = self._window(card, btn)
        win.settings = _PanelStub(True)
        M.MouseGlassWindow._sync_page_input_lock(win)
        win.settings = _PanelStub(False)
        win._page_lock_hold_until = 0.0     # 跳过宽限期，模拟已经等够 250ms
        win._float_was_open = False
        M.MouseGlassWindow._sync_page_input_lock(win)
        self.assertFalse(card._input_locked)
        self.assertFalse(btn._input_locked)

    def test_close_keeps_lock_for_grace_period(self) -> None:
        """浮窗刚收起：那一下点击可能还没到，先继续锁 250ms。"""
        card, btn = self._parts()
        win = self._window(card, btn)
        win.settings = _PanelStub(True)
        M.MouseGlassWindow._sync_page_input_lock(win)
        win.settings = _PanelStub(False)
        M.MouseGlassWindow._sync_page_input_lock(win)
        self.assertTrue(card._input_locked, "收起后要留宽限期挡回放点击")
        self.assertGreater(win._page_lock_hold_until, time.monotonic())
        # 宽限期一到（260ms 的兜底定时器）自动放开
        QTest.qWait(340)
        self.assertFalse(card._input_locked)


if __name__ == "__main__":
    unittest.main()
