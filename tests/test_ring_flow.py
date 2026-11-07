"""充电光带的几何回归测试（纯计算，不建窗口）。

用户报过两个现象：
  1. 充电时，开头那截白色滚动条没有从起点出发，而是冒到起点前面一点点；
  2. 末尾的白色滚动条越出了绿色圆环。
根因是每段光带都从 head 往后画固定 4.2°、还用 RoundCap 收尾，圆头会越过弧的
两端。现在每段都裁到 [0, 1]（0 = 弧起点，1 = 弧终点），任何一端都不可能出头。

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import widgets  # noqa: E402

LEVELS = (1, 3, 17, 42, 77, 99, 100)
FRAMES = 240


def frames(level: float) -> list[list[tuple[float, float, int, float]]]:
    span = -level * 3.6
    return [
        widgets.charge_flow_segments(widgets.CHARGE_FLOW_PERIOD * i / FRAMES, span)
        for i in range(FRAMES)
    ]


class ChargeFlowBoundsTests(unittest.TestCase):
    def test_every_segment_stays_inside_the_arc(self):
        for level in LEVELS:
            span = -level * 3.6
            for i in range(FRAMES):
                t = widgets.CHARGE_FLOW_PERIOD * i / FRAMES
                for lo, hi, alpha, width in widgets.charge_flow_segments(t, span):
                    with self.subTest(level=level, t=round(t, 3), lo=lo, hi=hi):
                        self.assertGreaterEqual(lo, 0.0, "光带跑到弧起点之前")
                        self.assertLessEqual(hi, 1.0, "光带越过弧终点")
                        self.assertLess(lo, hi, "空段")
                        self.assertGreater(alpha, 3)
                        self.assertLessEqual(alpha, 205)
                        self.assertGreater(width, 0.0)

    def test_flow_really_covers_the_start_and_the_end(self):
        """裁剪不能把光带整段裁没：起点、终点附近都得真的出现过白条。"""
        for level in (15, 77):
            segs = frames(level)
            at_start = [hi for fr in segs for lo, hi, *_ in fr if lo == 0.0]
            self.assertTrue(at_start, "从未有光带贴着弧起点出现")
            self.assertGreater(max(at_start), 0.01, "起点的光带太短，等于没画")
            self.assertTrue(any(hi >= 0.999 for fr in segs for lo, hi, *_ in fr),
                            "从未有光带跑到弧终点")

    def test_head_leads_the_tail(self):
        """每一束光带内部，尾部必须比头部靠后，否则就是光带倒着跑。"""
        for fr in frames(77):
            comet: list[tuple] = []
            for seg in fr:
                if comet and seg[1] > comet[-1][1]:
                    self.assertGreaterEqual(comet[0][1], comet[-1][1])
                    comet = []
                comet.append(seg)
            if comet:
                self.assertGreaterEqual(comet[0][1], comet[-1][1])

    def test_zero_span_is_empty(self):
        self.assertEqual(widgets.charge_flow_segments(1.0, 0.0), [])
        self.assertEqual(widgets.charge_flow_segments(1.0, -0.0), [])

    def test_tiny_arc_still_clipped(self):
        """1% 时弧只有 3.6°，单段光带比弧还长，不能整段漏出去。"""
        for lo, hi, *_ in widgets.charge_flow_segments(0.4, -3.6):
            self.assertGreaterEqual(lo, 0.0)
            self.assertLessEqual(hi, 1.0)


if __name__ == "__main__":
    unittest.main()
