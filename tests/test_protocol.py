"""协议解析的回归测试（纯函数，不碰硬件）。

    python -m unittest discover -s tests -v

这些断言把逆向得到的帧格式固定下来：以后换了别的手法改代码，
或者遇到新固件导出的数据异常，这里会先报错。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import protocol as P  # noqa: E402


class FrameTests(unittest.TestCase):
    def test_build_frame_layout(self) -> None:
        frame = P.build_frame(P.CMD_DEVICE_STATUS)
        self.assertEqual(len(frame), P.FRAME_LEN)
        self.assertEqual(frame[0], P.HEAD_OUT)
        self.assertEqual(frame[1], P.CMD_DEVICE_STATUS)
        self.assertEqual(frame[2], 0)  # payload 长度
        self.assertEqual(frame[3], 0)

    def test_build_frame_payload(self) -> None:
        frame = P.build_frame(P.CMD_DEVICE_INFO, b"\x00\x04")
        self.assertEqual(frame[2], 2)
        self.assertEqual(frame[4:6], b"\x00\x04")

    def test_build_frame_rejects_oversize(self) -> None:
        with self.assertRaises(ValueError):
            P.build_frame(P.CMD_DEVICE_STATUS, b"x" * 61)

    def test_parse_frame_roundtrip(self) -> None:
        raw = bytes([P.HEAD_IN, P.CMD_DEVICE_STATUS, 3, 0, 7, 8, 9]) + b"\x00" * 50
        parsed = P.parse_frame(raw)
        self.assertIsNotNone(parsed)
        cmd, status, length, payload = parsed
        self.assertEqual(cmd, P.CMD_DEVICE_STATUS)
        self.assertEqual(length, 3)
        self.assertEqual(payload, bytes([7, 8, 9]))

    def test_parse_frame_tolerates_report_id_prefix(self) -> None:
        raw = b"\x00" + bytes([P.HEAD_IN, P.CMD_GET_DPI, 2, 0, 4, 5]) + b"\x00" * 50
        cmd, _status, _length, payload = P.parse_frame(raw)
        self.assertEqual(cmd, P.CMD_GET_DPI)
        self.assertEqual(payload, bytes([4, 5]))

    def test_parse_frame_rejects_garbage(self) -> None:
        self.assertIsNone(P.parse_frame(b""))
        self.assertIsNone(P.parse_frame(b"\x11" * 60))

    def test_parse_frame_truncated_length_is_clamped(self) -> None:
        # 设备声明长度超过实际得到的字节数时不能抛索引错误
        raw = bytes([P.HEAD_IN, P.CMD_DEVICE_STATUS, 200, 0, 1, 2])
        cmd, _status, length, payload = P.parse_frame(raw)
        self.assertEqual(length, len(payload))
        self.assertEqual(payload, bytes([1, 2]))


class StatusTests(unittest.TestCase):
    def _payload(self, **over) -> bytes:
        base = [1, 50, 0, 0x11, 1, 0, 0, 0, 5, 1, 0x8A, 0x02, 0x8A, 0x02]
        for i, v in over.items():
            base[int(i[1:])] = v
        return bytes(base)

    def test_fields(self) -> None:
        st = P.parse_status(self._payload())
        self.assertEqual(st.battery, 50)
        self.assertTrue(st.charging)
        self.assertFalse(st.full)
        self.assertEqual(st.connection_mode, 1)
        self.assertTrue(st.wireless_connected)
        self.assertEqual(st.report_rate, 4000)
        self.assertEqual(st.dpi_stage, 1)
        self.assertEqual(st.dpi_x, 650)
        self.assertEqual(st.dpi_y, 650)

    def test_battery_is_clamped(self) -> None:
        self.assertEqual(P.parse_status(self._payload(b1=200)).battery, 100)
        self.assertEqual(P.parse_status(self._payload(b1=0)).battery, 0)

    def test_unknown_report_rate_is_none(self) -> None:
        self.assertIsNone(P.parse_status(self._payload(b8=9)).report_rate)

    def test_short_payload_does_not_raise(self) -> None:
        st = P.parse_status(b"\x01")
        self.assertEqual(st.battery, 0)
        self.assertEqual(st.dpi_x, 0)

    def test_sleep_frame_is_detected(self) -> None:
        # 鼠标休眠时接收器回全 0：battery=0 且 dpi=0
        st = P.parse_status(bytes(20))
        self.assertEqual(st.battery, 0)
        self.assertEqual(st.dpi_x, 0)


class LevelTests(unittest.TestCase):
    def test_parse_levels(self) -> None:
        payload = bytes([2, 1]) + bytes([0x8A, 0x02, 0x8A, 0x02, 0, 0, 0]) * 2
        current, count, cur_x, cur_y, xs = P.parse_levels(payload)
        self.assertEqual(count, 2)
        self.assertEqual(current, 2)
        self.assertEqual(cur_x, 650)
        self.assertEqual(cur_y, 650)
        self.assertEqual(xs, [650, 650])

    def test_parse_levels_empty(self) -> None:
        self.assertEqual(P.parse_levels(b""), (1, 1, 0, 0, []))

    def test_parse_levels_clamps_index(self) -> None:
        # 当前档位超出档位数量时要收敛，不能越界
        payload = bytes([1, 9]) + bytes([0xE8, 0x03, 0xE8, 0x03, 0, 0, 0])
        current, count, cur_x, _y, _xs = P.parse_levels(payload)
        self.assertEqual(current, 1)
        self.assertEqual(count, 1)
        self.assertEqual(cur_x, 1000)


class TextTests(unittest.TestCase):
    def test_decode_serial(self) -> None:
        payload = b"\x00\x05" + b"092608004306" + b"\x00" * 20
        self.assertEqual(P.decode_string(payload), "092608004306")

    def test_decode_strips_non_ascii(self) -> None:
        payload = b"\x00\x05" + b"AB\xff\xfeCD" + b"\x00" * 10
        self.assertEqual(P.decode_string(payload), "ABCD")


class LabelTests(unittest.TestCase):
    def test_known_device(self) -> None:
        self.assertEqual(P.device_label(0x36F9, 0xAA25), ("X7", "X7 Ultra Max", "usb"))
        self.assertEqual(P.device_label(0x36F9, 0xAA20)[2], "receiver")

    def test_unknown_device_falls_back_to_product(self) -> None:
        model, name, kind = P.device_label(0x1111, 0x2222, "Foo Bar Mouse")
        self.assertEqual(name, "Foo Bar Mouse")
        self.assertEqual(model, "Foo")
        self.assertEqual(kind, "unknown")

    def test_unknown_device_without_product(self) -> None:
        _model, name, _kind = P.device_label(0x1111, 0x2222)
        self.assertIn("1111:2222", name)


class RateLatencyTests(unittest.TestCase):
    """回报率 → 延迟挡位：界面第二张卡片底下那行小字。"""

    def test_high_latency_below_500hz(self) -> None:
        # 125Hz = 8ms、250Hz = 4ms，快速甩枪能看到指针追不上
        self.assertEqual(P.rate_latency(125), "高延迟")
        self.assertEqual(P.rate_latency(250), "高延迟")

    def test_medium_latency_500_to_1000hz(self) -> None:
        self.assertEqual(P.rate_latency(500), "中延迟")
        self.assertEqual(P.rate_latency(1000), "中延迟")

    def test_low_latency_from_2000hz(self) -> None:
        self.assertEqual(P.rate_latency(2000), "低延迟")
        self.assertEqual(P.rate_latency(4000), "低延迟")
        self.assertEqual(P.rate_latency(8000), "低延迟")

    def test_every_supported_rate_has_a_tier(self) -> None:
        labels = {P.rate_latency(hz) for hz in P.REPORT_RATES}
        self.assertEqual(labels, {"高延迟", "中延迟", "低延迟"})

    def test_unknown_rate_is_blank(self) -> None:
        for value in (None, 0, -1):
            self.assertEqual(P.rate_latency(value), "")

    def test_frame_ms(self) -> None:
        self.assertAlmostEqual(P.rate_frame_ms(1000) or 0, 1.0)
        self.assertAlmostEqual(P.rate_frame_ms(8000) or 0, 0.125)
        self.assertIsNone(P.rate_frame_ms(0))


if __name__ == "__main__":
    unittest.main()
