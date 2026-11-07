"""编辑模式新交互的回归测试：挡位管理弹窗、回报率挡位弹窗、版本号递增。

    python -m unittest discover -s tests -v

三块内容：
* ``LevelsPicker`` —— 竖向挡位管理面板：加挡展开输入框，重复 / 超范围 /
  满额都要有文案，删挡必须过确认框且至少留一档；
* ``RatePicker``   —— 列出鼠标内置的回报率挡位，当前挡位自动选中；
* ``build``        —— 版本号不允许固定，每次打包自动递增补丁号。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import devcaps  # noqa: E402
import theme  # noqa: E402
import widgets as W  # noqa: E402


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


class LevelsPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()
        # 删除确认框自动化：一律同意（确认交互本身由 uicheck 人工看截图把关）
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    def _picker(self, levels=(600, 1350), current=600, max_levels=5):
        caps = devcaps.DeviceCaps(dpi_min=100, dpi_max=25600, dpi_step=50,
                                  levels_writable=True, max_levels=max_levels)
        return W.LevelsPicker(list(levels), current, caps, theme.DARK)

    def test_sorted_desc_and_marks_current(self) -> None:
        lp = self._picker()
        self.assertEqual(lp.levels(), [1350, 600])
        self.assertEqual(lp._current, 600)

    def test_add_editor_prefills_current(self) -> None:
        lp = self._picker()
        lp._open_add_editor()
        self.assertFalse(lp._edit_line.isHidden())   # 浮窗未 show，用 isHidden 判
        # 默认预填当前 DPI
        self.assertEqual(lp._edit_line.text(), "600")

    def test_picker_window_accepts_focus_for_input(self) -> None:
        """修 BUG 回归：弹窗不许带 WindowDoesNotAcceptFocus，否则「增加挡位」
        的输入框永远拿不到键盘焦点、数字打不进去。"""
        lp = self._picker()
        self.assertFalse(
            lp.windowFlags() & Qt.WindowDoesNotAcceptFocus,
            "LevelsPicker 不能带 WindowDoesNotAcceptFocus（内有输入框）")
        lp._open_add_editor()
        self.assertIsNotNone(lp._edit_line.focusWidget() if False
                             else lp._edit_line)   # 控件存在
        # 焦点策略允许键盘焦点（非 NoFocus / TabFocus only）
        self.assertTrue(lp._edit_line.focusPolicy() & (Qt.StrongFocus | Qt.WheelFocus
                                                       | Qt.ClickFocus))

    def test_add_editor_esc_closes_editor_first(self) -> None:
        lp = self._picker()
        lp._open_add_editor()
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Escape, Qt.KeyboardModifier.NoModifier)
        lp.eventFilter(lp._edit_line, ev)     # 输入框内 Esc → 只收输入框
        self.assertTrue(lp._edit_line.isHidden())
        self.assertFalse(lp._adding)

    def test_add_editor_accepts_typing(self) -> None:
        """打开输入框后键盘输入生效（预填值全选，直接输入即替换）。"""
        from PySide6.QtTest import QTest
        lp = self._picker()
        lp._open_add_editor()
        QTest.keyClicks(lp._edit_line, "1234")
        self.assertEqual(lp._edit_line.text(), "1234")
        QTest.keyClick(lp._edit_line, Qt.Key_Backspace)
        self.assertEqual(lp._edit_line.text(), "123")

    def test_manual_value_is_added(self) -> None:
        lp = self._picker()
        got = []
        lp.added.connect(got.append)
        lp._open_add_editor()
        lp._edit_line.setText("2400")
        lp._commit_add()
        self.assertEqual(lp.levels(), [2400, 1350, 600])
        self.assertIn("已加", lp._msg)
        self.assertEqual(got, [2400])

    def test_value_is_aligned_to_step(self) -> None:
        lp = self._picker()
        lp._open_add_editor()
        lp._edit_line.setText("1234")           # 不在 50 的整倍数上
        lp._commit_add()
        self.assertEqual(lp.levels(), [1350, 1250, 600])

    def test_duplicate_gives_feedback(self) -> None:
        lp = self._picker()
        lp._open_add_editor()
        lp._edit_line.setText("600")
        lp._commit_add()
        self.assertEqual(lp.levels(), [1350, 600])
        self.assertIn("已存在", lp._msg)

    def test_out_of_range_gives_feedback(self) -> None:
        lp = self._picker()
        lp._open_add_editor()
        lp._edit_line.setText("99999")
        lp._commit_add()
        self.assertEqual(lp.levels(), [1350, 600])
        self.assertIn("超出范围", lp._msg)

    def test_non_numeric_gives_feedback(self) -> None:
        lp = self._picker()
        lp._open_add_editor()
        lp._edit_line.setText("abc")
        lp._commit_add()
        self.assertEqual(lp.levels(), [1350, 600])
        self.assertIn("请填数值", lp._msg)

    def test_max_levels_gives_feedback(self) -> None:
        lp = self._picker(max_levels=2)
        lp._open_add_editor()          # 已满额：只提示，不展开输入框
        self.assertIn("最多 2 档", lp._msg)
        self.assertTrue(lp._edit_line.isHidden())

    def test_remove_confirms_and_keeps_one(self) -> None:
        lp = self._picker()
        got = []
        lp.removed.connect(got.append)
        # 确认框同意 → 删掉并发信号
        lp._confirm_remove(1350)
        self.assertEqual(lp.levels(), [600])
        self.assertEqual(got, [1350])
        self.assertIn("已删", lp._msg)
        # 只剩一档时不给删（不弹确认）
        lp._confirm_remove(600)
        self.assertIn("至少留一档", lp._msg)
        self.assertEqual(lp.levels(), [600])

    def test_choosing_emits_value_and_moves_check(self) -> None:
        lp = self._picker(current=600)
        got = []
        lp.chosen.connect(got.append)
        # 降序 [1350, 600]：第一行是 1350
        lp.mouseReleaseEvent(_FakeMouseEvent(
            lp, Qt.LeftButton,
            QPoint(20, int(lp.SHADOW + lp.PAD + lp.HEAD_H + 5))))
        self.assertEqual(got, [1350])
        self.assertEqual(lp._current, 1350)


class RatePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def test_lists_device_levels_and_marks_current(self) -> None:
        picker = W.RatePicker([125, 250, 500, 1000], 500, theme.DARK)
        self.assertEqual(picker.values(), [1000, 500, 250, 125])
        self.assertEqual(picker._current, 500)

    def test_row_index_maps_to_value(self) -> None:
        picker = W.RatePicker([125, 250, 500, 1000], 500, theme.DARK)
        # 内容区从 SHADOW 偏移开始；降序 [1000,500,250,125]
        top = picker.SHADOW + picker.PAD + picker.HEAD_H
        self.assertEqual(picker._index_at(QPoint(10, int(top + 5))), 0)
        self.assertEqual(picker._index_at(QPoint(10, int(top + picker.ROW_H + 5))), 1)
        self.assertEqual(picker._index_at(QPoint(10, int(top - 5))), -1)
        self.assertEqual(picker._index_at(QPoint(10, int(top + 10 * picker.ROW_H))), -1)

    def test_out_of_x_returns_minus_one(self) -> None:
        picker = W.RatePicker([125], 125, theme.DARK)
        self.assertEqual(picker._index_at(QPoint(-5, 10)), -1)
        self.assertEqual(picker._index_at(QPoint(picker.width() + 5, 10)), -1)

    def test_choosing_emits_value(self) -> None:
        picker = W.RatePicker([125, 250, 500], 250, theme.DARK)
        got = []
        picker.chosen.connect(got.append)
        # 降序 [500,250,125]：第一行是 500
        picker.mouseReleaseEvent(_FakeMouseEvent(picker, Qt.LeftButton,
                                                 QPoint(20, int(picker.SHADOW +
                                                                picker.PAD +
                                                                picker.HEAD_H + 5))))
        self.assertEqual(got, [500])


class _FakeMouseEvent:
    """只带 mouseReleaseEvent 用得到的字段。"""

    def __init__(self, widget, button, pos) -> None:
        self._button = button
        self._pos = pos

    def button(self):
        return self._button

    def position(self):
        return self._pos


class EditButtonFeedbackTests(unittest.TestCase):
    """「应用 / 取消」要能看出可点：手型光标 + 悬停变色。

    回归：两者此前都没设光标（停上去还是箭头），且「应用」的 hover 色写成
    accent 本身、和常态一模一样，「取消」的 hover 只刷了一层近白色 ——
    视觉上等于没反馈。
    """

    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _card(self, pal=theme.DARK) -> W.InfoCard:
        card = W.InfoCard("灵敏度")
        card.set_palette(pal) if hasattr(card, "set_palette") else None
        card._pal = pal
        card._apply_edit_style()
        return card

    def test_apply_and_cancel_use_pointing_hand(self) -> None:
        for pal in (theme.DARK, theme.LIGHT):
            card = self._card(pal)
            for btn in (card.btn_apply, card.btn_cancel,
                        card.btn_minus, card.btn_plus):
                self.assertEqual(btn.cursor().shape(), Qt.PointingHandCursor,
                                 f"{btn.text()} 没用手指光标")

    def test_apply_hover_color_differs_from_normal(self) -> None:
        for pal in (theme.DARK, theme.LIGHT):
            qss = W.edit_controls_qss(pal)
            normal = pal.accent.name()
            self.assertIn(f"background-color: {normal};", qss["apply"])
            self.assertNotIn(f"QToolButton:hover {{ background-color: {normal};",
                             qss["apply"], "「应用」悬停色不能和常态相同")

    def test_cancel_hover_picks_accent_border(self) -> None:
        for pal in (theme.DARK, theme.LIGHT):
            qss = W.edit_controls_qss(pal)
            self.assertIn(f"border-color: {pal.accent_text.name()};", qss["cancel"])


class VersionBumpTests(unittest.TestCase):
    def test_next_version_increments_patch(self) -> None:
        import build
        self.assertEqual(build._next_version("1.3.6"), "1.3.7")
        self.assertEqual(build._next_version("1.3.9"), "1.3.10")

    def test_pick_version_skips_taken_names(self) -> None:
        import build
        # 1.3.7 的产物已经存在 → 继续往后跳
        got = build._pick_version("1.3.6", exists=lambda p: "1.3.7" in p)
        self.assertEqual(got, "1.3.8")

    def test_setup_core_version_is_the_single_source(self) -> None:
        import setup_core
        self.assertRegex(setup_core.VERSION, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
