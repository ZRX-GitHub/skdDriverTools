"""贴边边距的换算测试（纯函数，不碰硬件）。

    python -m unittest discover -s tests -v

要点：12 / 160 是**物理像素**。Qt 的 move() 只认逻辑像素，100% 缩放下两者
相等，125% / 150% / 200% 下不等 —— 直接把 160 当逻辑像素用，2K 屏上量出来
就是 200 / 240 物理像素，和 1080p 屏对不上。这里把换算钉死。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import main as M  # noqa: E402


class LogicalGapTests(unittest.TestCase):
    def test_unscaled_screen_is_identity(self) -> None:
        self.assertEqual(M.logical_gap(M.DOCK_RIGHT_GAP, 1.0), 160)
        self.assertEqual(M.logical_gap(M.DOCK_BOTTOM_GAP, 1.0), 12)

    def test_common_scales(self) -> None:
        # 100% 不缩、125%（2K 常见）、150%、200%（4K 常见）
        self.assertEqual(M.logical_gap(160, 1.25), 128)   # 128 × 1.25 = 160
        self.assertEqual(M.logical_gap(160, 1.5), 107)    # 107 × 1.5  = 160.5
        self.assertEqual(M.logical_gap(160, 2.0), 80)     # 80  × 2.0  = 160
        self.assertEqual(M.logical_gap(12, 1.25), 10)     # 10  × 1.25 = 12.5
        self.assertEqual(M.logical_gap(12, 1.5), 8)       # 8   × 1.5  = 12
        self.assertEqual(M.logical_gap(12, 2.0), 6)       # 6   × 2.0  = 12

    def test_physical_distance_survives_rounding(self) -> None:
        """任何缩放档下，折回物理像素后误差不超过半个逻辑像素。"""
        for scale in (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0):
            for physical in (M.DOCK_BOTTOM_GAP, M.DOCK_RIGHT_GAP):
                logical = M.logical_gap(physical, scale)
                self.assertGreaterEqual(logical, 1, f"scale={scale} physical={physical}")
                err = abs(logical * scale - physical)
                self.assertLessEqual(err, scale / 2 + 0.5,
                                     f"scale={scale} physical={physical} "
                                     f"logical={logical} 物理距离={logical * scale}")

    def test_never_collapses_to_zero(self) -> None:
        """超大缩放比下也不能算成 0（否则直接贴到边缘）。"""
        self.assertEqual(M.logical_gap(12, 100.0), 1)
        self.assertEqual(M.logical_gap(0, 1.5), 1)

    def test_bad_scale_falls_back(self) -> None:
        self.assertEqual(M.logical_gap(160, 0), 160)
        self.assertEqual(M.logical_gap(160, -1.5), 160)


class ScreenMathTests(unittest.TestCase):
    """按真实屏幕参数走一遍定位公式，确认落盘位置换算回物理像素后仍是 160 / 12。"""

    def _check(self, phys_w: int, phys_h: int, scale: float, win_w: int, win_h: int,
               taskbar_logical: int) -> tuple[float, float]:
        logical_w = round(phys_w / scale)
        logical_h = round(phys_h / scale)
        avail_bottom = logical_h - taskbar_logical      # 任务栏顶部（逻辑坐标）
        gap_x = M.logical_gap(M.DOCK_RIGHT_GAP, scale)
        gap_y = M.logical_gap(M.DOCK_BOTTOM_GAP, scale)
        x = logical_w - gap_x - win_w
        y = avail_bottom - gap_y - win_h
        right_gap_phys = phys_w - (x + win_w) * scale
        bottom_gap_phys = (avail_bottom - y - win_h) * scale
        return right_gap_phys, bottom_gap_phys

    def test_1080p_and_2k_keep_same_margins(self) -> None:
        cases = [
            (1920, 1080, 1.0, 40),     # 1080p 100%
            (2560, 1440, 1.0, 40),     # 2K 100%
            (2560, 1440, 1.25, 48),    # 2K 125%
            (2560, 1440, 1.5, 48),     # 2K 150%
            (3840, 2160, 1.5, 48),     # 4K 150%
            (3840, 2160, 2.0, 72),     # 4K 200%
        ]
        for phys_w, phys_h, scale, taskbar in cases:
            right, bottom = self._check(phys_w, phys_h, scale, 396, 470, taskbar)
            self.assertLessEqual(abs(right - 160), 1.0,
                                 f"{phys_w}x{phys_h} @{scale:.0%} 右边距={right}")
            self.assertLessEqual(abs(bottom - 12), 1.0,
                                 f"{phys_w}x{phys_h} @{scale:.0%} 底部距={bottom}")


if __name__ == "__main__":
    unittest.main()
