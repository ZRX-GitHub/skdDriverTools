"""标题栏版面：第一行固定是应用名，品牌挪到型号前面。

v1.4.12 改的版面：原来第一行是「CROCIRIS · 2台设备 · 已连接」，读起来像
「这个软件叫 CROCIRIS」；品牌应该在型号前面读成「CROCIRIS X7 Ultra Max」，
第一行留给「三颗豆驱动管理 · 2台设备 · 已连接」。

这里锁三件事：第一段文案不跟着品牌变、品牌宽度计入型号行、悬停底衬
跟着变宽（底衬窄了会包不住行首的品牌）。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as M  # noqa: E402
import widgets as W  # noqa: E402


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


class HeaderLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def _header(self, brand: str = "CROCIRIS", model: str = "X7 Ultra Max") -> M.Header:
        h = M.Header()
        # 用真实卡片宽度：标题区可用宽度 = CARD_W - 112，量出来的取舍才是线上行为
        h.resize(M.CARD_W, M.CARD_H)
        h.set_devices([{"key": "c", "label": f"{brand} {model}".strip(),
                        "available": True, "current": True}])
        h.set_state(brand, model, "已连接", True)
        return h

    def _fm(self) -> QFontMetricsF:
        return QFontMetricsF(W.app_font(16, QFont.Weight.DemiBold, -0.2))

    def test_first_line_is_app_title(self) -> None:
        h = self._header()
        self.assertEqual(h._status_segments()[0][0], "三颗豆驱动管理")

    def test_first_line_does_not_follow_brand(self) -> None:
        """换成罗技也不能把「Logitech」写回第一行。"""
        h = self._header(brand="Logitech", model="G502 HERO")
        texts = [t for t, _ in h._status_segments()]
        self.assertEqual(texts[0], "三颗豆驱动管理")
        self.assertNotIn("Logitech", " ".join(texts))

    def test_first_line_keeps_count_and_status(self) -> None:
        h = self._header()
        texts = [t for t, _ in h._status_segments()]
        self.assertIn("1台设备", texts)
        self.assertIn("已连接", texts)

    def test_brand_width_is_counted(self) -> None:
        # 用短型号：离屏平台的字体度量比真实渲染偏宽，长名会触发品牌让位
        h = self._header(model="X7")
        self.assertGreater(h._brand_width(self._fm()), 0)

    def test_brand_width_uses_smaller_font(self) -> None:
        """品牌宽度按 13px 自己量，不能借用型号的 16px 度量（会虚宽把品牌挤掉）。"""
        h = self._header(model="X7")
        brand = h._brand or ""
        w = h._brand_width(self._fm())
        wide = QFontMetricsF(W.app_font(16, QFont.Weight.DemiBold, -0.2)).horizontalAdvance(brand)
        small = QFontMetricsF(h._brand_font()).horizontalAdvance(brand)
        self.assertAlmostEqual(w, small + h.BRAND_GAP, delta=1.0)
        self.assertLess(w, wide)

    def test_three_identities_have_distinct_colors(self) -> None:
        """软件名 / 品牌 / 型号必须是三种颜色，不能糊成一片。"""
        h = self._header()
        app_color = h._status_segments()[0][1]
        self.assertEqual(app_color, QColor(h._pal.accent_text))
        brand_color = h._brand_color()
        model_color = h._model_color()
        self.assertNotEqual(brand_color, model_color)
        self.assertNotEqual(app_color, brand_color)
        self.assertNotEqual(app_color, model_color)

    def test_brand_is_heavier_than_model(self) -> None:
        """品牌是身份、型号是补充：品牌取主色，型号必须比它淡。"""
        h = self._header()
        self.assertEqual(h._brand_color(), QColor(h._pal.text_primary))
        self.assertEqual(h._model_color(), QColor(h._pal.text_secondary))
        self.assertGreater(h._brand_color().alpha(), h._model_color().alpha())

    def test_status_line_starts_at_dot(self) -> None:
        """第一行左缘贴齐绿色状态点（x=3），不能再缩进到 16。"""
        h = self._header()
        rect = h._status_rect()
        self.assertAlmostEqual(rect.left(), h.STATUS_X, delta=0.5)
        self.assertLess(rect.left(), 15.0)      # 必须比型号行（15）更靠左
        # 右边界不动：只挪起点，不改变能排到哪
        self.assertAlmostEqual(rect.right(), h.width() - 96.0, delta=1.0)

    def test_app_title_is_bolder_than_status(self) -> None:
        """第一行的软件名要带 DemiBold，和后面的计数/状态分开。"""
        h = self._header()
        self.assertEqual(h._title_weight(), QFont.Weight.DemiBold)

    def test_brand_width_zero_without_brand(self) -> None:
        h = self._header()
        h.set_state("", "X7 Ultra Max", "已连接", True)
        self.assertEqual(h._brand_width(self._fm()), 0.0)

    def test_hover_rect_covers_title_text(self) -> None:
        """品牌挪进来以后，悬停底衬要包得住「品牌 + 型号」整段。

        底衬宽度取的是型号行和状态行里更宽的那个，所以不能简单断言「有品牌
        就更宽」——状态行本来就更长时两者一样宽。直接验「装得下」。
        """
        h = self._header(model="X7")
        fm = self._fm()
        need = h._brand_width(fm) + fm.horizontalAdvance("X7")
        self.assertGreaterEqual(h._hover_rect().width(), need)

    def test_long_model_drops_brand(self) -> None:
        """标题区只有 284px：型号长约 300px 时，品牌先让位给型号。"""
        h = self._header(brand="Logitech", model="PRO X SUPERLIGHT 2 Wireless DEX Edition")
        brand, width = h._brand_parts(self._fm(), h.width() - 112)
        self.assertEqual(brand, "")
        self.assertEqual(width, 0.0)

    def test_short_model_keeps_brand(self) -> None:
        h = self._header(model="X7")
        brand, width = h._brand_parts(self._fm(), h.width() - 112)
        self.assertEqual(brand, "CROCIRIS")
        self.assertGreater(width, 0)

    def test_elided_model_never_overflows(self) -> None:
        """型号再长也只能占满剩余宽度，不能压到右侧按钮下面。"""
        h = self._header(brand="Logitech", model="PRO X SUPERLIGHT 2 Wireless DEX Edition")
        fm = self._fm()
        avail = h.width() - 112
        _brand, brand_w = h._brand_parts(fm, avail)
        rest = avail - brand_w
        text = fm.elidedText(h._model, Qt.TextElideMode.ElideRight, rest)
        self.assertTrue(text.endswith("\u2026"))
        self.assertLessEqual(fm.horizontalAdvance(text), rest)


if __name__ == "__main__":
    unittest.main()
