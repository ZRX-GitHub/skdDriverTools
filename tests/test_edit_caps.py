"""编辑模式的能力解析与写命令回归测试（纯逻辑，不碰硬件）。

真机写回验证在 tools/uicheck_edit.py 与 tools/probe_dpi_levels.py 之外单独跑；
这里锁住 0x8060 位图换算、DPI 范围/步进、写命令编码和 monitor 的降级行为。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import devcaps  # noqa: E402
import logitech as L  # noqa: E402
import monitor as M  # noqa: E402
import protocol as P  # noqa: E402

DPI_LIST_RAW = bytes.fromhex("000064e0326400000000000000000000")  # 100, step50, 25600


class CapabilityParseTests(unittest.TestCase):
    def test_rate_bitmap_g502(self) -> None:
        # 0x8B = bit0/1/3/7 → 1/2/4/8 ms → 1000/500/250/125 Hz
        self.assertEqual(devcaps.parse_rate_bitmap(0x8B), [125, 250, 500, 1000])

    def test_rate_bitmap_empty_and_partial(self) -> None:
        self.assertEqual(devcaps.parse_rate_bitmap(0), [])
        self.assertEqual(devcaps.parse_rate_bitmap(0x01), [1000])
        self.assertEqual(devcaps.parse_rate_bitmap(0x80), [125])
        # 3ms/5ms 这种换算不成常见 Hz 的间隔一律丢掉
        self.assertEqual(devcaps.parse_rate_bitmap(0x04), [])

    def test_range_from_values(self) -> None:
        self.assertEqual(devcaps.range_from_values([100, 150, 200, 250]), (100, 250, 50))
        self.assertEqual(devcaps.range_from_values([400, 800, 1600, 3200]), (400, 3200, 0))
        self.assertEqual(devcaps.range_from_values([]), (0, 0, 0))
        self.assertEqual(devcaps.range_from_values([0, 99, 99999]), (99, 99, 0))

    def test_hints(self) -> None:
        caps = devcaps.DeviceCaps(dpi_min=100, dpi_max=25600, dpi_step=50,
                                  rate_values=[125, 250, 500, 1000])
        self.assertEqual(caps.dpi_hint(), "可调 100–25600 DPI\n步进 50 DPI")
        # 回报率提示只给可调范围（单行）；档位明细移到回报率下拉框里了
        self.assertEqual(caps.rate_hint(), "可调 125–1000 Hz")
        self.assertTrue(caps.dpi_editable is False, "没标记可写就不该允许编辑")
        caps.dpi_writable = caps.rate_writable = True
        self.assertTrue(caps.dpi_editable and caps.rate_editable)
        self.assertEqual(devcaps.DeviceCaps().dpi_hint(), "未读取到可调范围")
        self.assertEqual(devcaps.DeviceCaps().rate_hint(), "未读取到可调范围")

    def test_align(self) -> None:
        caps = devcaps.DeviceCaps(dpi_min=100, dpi_max=25600, dpi_step=50,
                                  rate_values=[125, 250, 500, 1000])
        self.assertEqual(caps.align_dpi(1377), 1400)     # 按步进对齐
        self.assertEqual(caps.align_dpi(10), 100)         # 低于下限
        self.assertEqual(caps.align_dpi(99999), 25600)    # 高于上限
        self.assertEqual(caps.align_rate(777), 1000)
        self.assertEqual(caps.align_rate(200), 250)

    def test_discrete_dpi_align(self) -> None:
        caps = devcaps.DeviceCaps(dpi_min=400, dpi_max=3200, dpi_step=0,
                                  dpi_values=[400, 800, 1600, 3200])
        self.assertEqual(caps.align_dpi(900), 800)
        self.assertIn("共 4 档可选", caps.dpi_hint())

    def test_range_needs_full_grid_not_just_even(self) -> None:
        """「等差」不等于「连续」：两档 700/850 的差是档距，不是步进。"""
        g502 = list(range(100, 25601, 50))
        self.assertEqual(devcaps.range_from_values(g502), (100, 25600, 50))
        # 只报三个档位（真机探针常见）：不能当成步进 1250 的连续范围
        self.assertEqual(devcaps.range_from_values([100, 600, 1350]), (100, 1350, 0))
        self.assertEqual(devcaps.range_from_values([700, 850]), (700, 850, 0))
        # 铺不满网格的等差档位仍是离散
        self.assertEqual(devcaps.range_from_values([400, 1200]), (400, 1200, 0))

    def test_input_step(self) -> None:
        self.assertEqual(devcaps.DeviceCaps(dpi_step=50).input_step(), 50)
        # 离散档位：取最小档间距当步进
        self.assertEqual(devcaps.DeviceCaps(dpi_values=[400, 800, 1600]).input_step(), 400)
        # 两档相距太远 → 回落常规 50，别让 ± 一按跨过整段范围
        self.assertEqual(devcaps.DeviceCaps(dpi_values=[700, 26000]).input_step(), 50)

    def test_dpi_spec_by_model(self) -> None:
        # 官网参数页（2026-11）：Max 系列 40000 原生 / 64000 超频，
        # 标准版 30000 原生 / 42000 超频；驱动下限 50、步进 1。
        self.assertEqual(devcaps.dpi_spec_for("X7 Ultra Max"), (50, 64000, 1))
        self.assertEqual(devcaps.dpi_spec_for("X7 Pro Max"), (50, 64000, 1))
        self.assertEqual(devcaps.dpi_spec_for("X7S Ultra Max"), (50, 64000, 1))
        # 标准 X7 / X7S 走 42K 超频值，且不能被「X7S ULTRA MAX」的短前缀抢走
        self.assertEqual(devcaps.dpi_spec_for("X7 Gaming Mouse"), (50, 42000, 1))
        self.assertEqual(devcaps.dpi_spec_for("X7S"), (50, 42000, 1))
        self.assertEqual(devcaps.dpi_spec_for("Unknown Mouse"), devcaps.DEFAULT_DPI_SPEC)

    def test_rate_spec_by_model(self) -> None:
        """X7 系列官网写明「原生双 8K」，七档齐全；未收录型号返回空。"""
        full = (125, 250, 500, 1000, 2000, 4000, 8000)
        for model in ("X7 Ultra Max", "X7 Pro Max", "X7S Ultra Max", "X7", "X7S"):
            self.assertEqual(devcaps.rate_values_for(model), full, model)
        self.assertEqual(devcaps.rate_values_for("M7"), ())
        self.assertEqual(devcaps.rate_values_for(""), ())

    def test_resolve_range_expands_with_device_table(self) -> None:
        spec = (50, 64000, 1)
        # 设备里真存着超规格档位 → 上限必须跟着撑开，否则那档被判越界
        self.assertEqual(devcaps.resolve_dpi_range(spec, [800, 32000]), (50, 64000, 1))
        # 档位比规格小 → 不动规格
        self.assertEqual(devcaps.resolve_dpi_range(spec, [700, 850]), (50, 64000, 1))
        # 空表 → 原样返回规格
        self.assertEqual(devcaps.resolve_dpi_range(spec, []), spec)

    def test_copy_is_independent(self) -> None:
        a = devcaps.DeviceCaps(dpi_values=[600], rate_values=[1000])
        b = a.copy()
        b.dpi_values.append(800)
        self.assertEqual(a.dpi_values, [600])


class LogitechCapabilityTests(unittest.TestCase):
    def _fake(self, responses: dict) -> tuple[L.LogitechMouse, list]:
        mouse = object.__new__(L.LogitechMouse)
        calls: list[tuple[int, int, bytes]] = []

        def call(idx, func, params=b"", timeout=0.6):
            calls.append((idx, func, bytes(params)))
            return responses.get((idx, func))

        mouse._call = call
        mouse.feature = lambda fid: {L.FEAT_DPI: 10, L.FEAT_REPORT_RATE: 11}.get(fid, 0)
        return mouse, calls

    def test_capabilities(self) -> None:
        mouse, calls = self._fake({
            (10, 1): DPI_LIST_RAW,
            (10, 2): bytes([0, 0x02, 0x58, 0x03, 0xE8]),   # 当前 600 / 默认 1000
            (10, 3): bytes(16),                              # 写命令被接受
            (11, 0): bytes([0x8B]) + bytes(15),
            (11, 1): bytes([1]) + bytes(15),                 # 当前 1ms = 1000Hz
            (11, 2): bytes(16),
        })
        caps = mouse.capabilities()
        self.assertEqual((caps.dpi_min, caps.dpi_max, caps.dpi_step), (100, 25600, 50))
        self.assertEqual(len(caps.dpi_values), 511)
        self.assertEqual(caps.rate_values, [125, 250, 500, 1000])
        self.assertTrue(caps.dpi_writable and caps.rate_writable)
        self.assertEqual(caps.source, "hidpp")

    def test_write_rejected_marks_module_read_only(self) -> None:
        """G502 HERO 真机：0x8060 fn2 回 HID++ 错误 → 只有回报率不可编辑。"""
        mouse, _ = self._fake({
            (10, 1): DPI_LIST_RAW,
            (10, 2): bytes([0, 0x02, 0x58, 0x03, 0xE8]),
            (10, 3): bytes(16),
            (11, 0): bytes([0x8B]) + bytes(15),
            (11, 1): bytes([1]) + bytes(15),
            # 故意不给 (11, 2)：错误回包在 _call 里表现为 None
        })
        caps = mouse.capabilities()
        self.assertTrue(caps.dpi_editable)
        self.assertFalse(caps.rate_editable)
        self.assertEqual(caps.rate_values, [125, 250, 500, 1000],
                         "不能写也仍要能给出可调范围")

    def test_capabilities_missing_features(self) -> None:
        mouse, _ = self._fake({})
        caps = mouse.capabilities()
        self.assertFalse(caps.dpi_editable or caps.rate_editable)
        self.assertEqual(caps.dpi_values, [])

    def test_set_dpi_wire_format_and_readback(self) -> None:
        mouse, calls = self._fake({(10, 3): bytes(16)})
        mouse.dpi = lambda: 1350
        with mock.patch.object(L.time, "sleep"):
            self.assertTrue(mouse.set_dpi(1350))
        self.assertIn((10, 3, bytes([0, 0x05, 0x46])), calls)  # 1350 = 0x0546

    def test_set_dpi_fails_on_readback_mismatch(self) -> None:
        mouse, _ = self._fake({(10, 3): bytes(16)})
        mouse.dpi = lambda: 600             # 官方驱动把值覆盖回去了
        with mock.patch.object(L.time, "sleep"):
            self.assertFalse(mouse.set_dpi(1350))

    def test_set_dpi_rejects_out_of_protocol_range(self) -> None:
        """u16 装不下的值在协议层就拒；能不能到 42000 由 caps（设备上报）判断。"""
        mouse, calls = self._fake({})
        self.assertFalse(mouse.set_dpi(0))
        self.assertFalse(mouse.set_dpi(70000))
        self.assertEqual(calls, [])

    def test_set_rate_wire_format_and_readback(self) -> None:
        mouse, calls = self._fake({(11, 2): bytes(16)})
        mouse.report_rate = lambda: 500
        with mock.patch.object(L.time, "sleep"):
            self.assertTrue(mouse.set_report_rate(500))
        self.assertIn((11, 2, bytes([2])), calls)   # 2 ms = 500 Hz

    def test_set_rate_rejects_unsupported(self) -> None:
        mouse, calls = self._fake({})
        self.assertFalse(mouse.set_report_rate(333))
        self.assertEqual(calls, [])

    def test_report_rate_uses_fn1_interval(self) -> None:
        mouse, calls = self._fake({(11, 1): bytes([1]) + bytes(15)})
        self.assertEqual(mouse.report_rate(), 1000)
        self.assertIn((11, 1, b""), calls)


class MonitorWriteTests(unittest.TestCase):
    def test_private_protocol_write_needs_device(self) -> None:
        # 0x1A/0x26 写命令已逆向并真机验证（v1.3.6），没连设备时报「设备未连接」
        src = M.MouseMonitor()
        self.assertEqual(src.set_dpi(800), (False, "设备未连接"))
        self.assertEqual(src.set_report_rate(1000), (False, "设备未连接"))
        self.assertEqual(src.snapshot().caps.source, "private")

    def test_hub_proxies_to_visible_device(self) -> None:
        hub = M.DeviceHub()
        fake = mock.Mock()
        fake.snapshot.return_value = M.MouseInfo(connected=True)
        fake.set_dpi.return_value = (True, "")
        hub.sources[hub.LOGITECH] = fake
        hub.select(hub.LOGITECH)
        self.assertEqual(hub.set_dpi(800), (True, ""))
        fake.set_dpi.assert_called_once_with(800)

    def test_snapshot_caps_is_a_copy(self) -> None:
        src = M.MouseMonitor()
        a = src.snapshot()
        a.caps.dpi_values.append(123)
        self.assertEqual(src.snapshot().caps.dpi_values, [])

    def test_private_caps_range_follows_model(self) -> None:
        """按型号写死：Ultra Max 50–64000 / 步进 1，标准 X7 到 42000。"""
        src = M.MouseMonitor()
        src._build_caps({"mouse_vid": 0x36F9, "mouse_pid": 0xAA25,
                         "dpi_table": [700, 850], "rate_max": 8000})
        caps = src.snapshot().caps
        self.assertEqual((caps.dpi_min, caps.dpi_max, caps.dpi_step), (50, 64000, 1))
        self.assertEqual(caps.dpi_hint(), "可调 50–64000 DPI\n步进 1 DPI")

        src2 = M.MouseMonitor()
        src2._build_caps({"mouse_vid": 0x36F9, "mouse_pid": 0xAA10,
                          "dpi_table": [700, 850], "rate_max": 8000})
        self.assertEqual(src2.snapshot().caps.dpi_max, 42000)

    def test_private_caps_rate_comes_from_official_table(self) -> None:
        """回报率挡位不再看设备回的索引：设备说 1000，X7 仍按官网给到 8000。"""
        src = M.MouseMonitor()
        src._build_caps({"mouse_vid": 0x36F9, "mouse_pid": 0xAA25,
                         "dpi_table": [800, 1600], "rate_max": 1000})
        caps = src.snapshot().caps
        self.assertEqual(caps.rate_values,
                         [125, 250, 500, 1000, 2000, 4000, 8000])
        self.assertEqual(caps.rate_hint(), "可调 125–8000 Hz")

    def test_private_caps_table_pushes_upper_bound(self) -> None:
        """档位表里存着超出规格的档 → 上限以并集为准（该档不能被判越界）。"""
        src = M.MouseMonitor()
        src._build_caps({"mouse_vid": 0x36F9, "mouse_pid": 0xAA10,
                         "dpi_table": [800, 65000], "rate_max": 8000})
        self.assertEqual(src.snapshot().caps.dpi_max, 65000)

    def test_private_set_dpi_rejects_beyond_device_range(self) -> None:
        src = M.MouseMonitor()
        src._build_caps({"mouse_vid": 0x36F9, "mouse_pid": 0xAA25,
                         "dpi_table": [700, 850], "rate_max": 8000})
        # 64000 是驱动上限；协议上限 65535 之上的值判越界
        self.assertEqual(src.set_dpi(64001),
                         (False, "超出设备可调范围（50–64000 DPI）"))
        self.assertEqual(src.set_dpi_levels([700, 64001]),
                         (False, "超出设备可调范围（50–64000 DPI）"))


if __name__ == "__main__":
    unittest.main()


class CROCIRISPayloadTests(unittest.TestCase):
    """0x1A SetDpi 构包器：升序、颜色沿用、当前档越界收敛。"""

    def test_sorted_dedup_and_colors(self) -> None:
        body = P.build_dpi_payload([1600, 700, 850, 700], 1,
                                   {700: (255, 255, 0), 850: (0, 255, 0)})
        self.assertEqual(body[0], 3)
        vals = [body[2 + i * 7] | (body[3 + i * 7] << 8) for i in range(3)]
        self.assertEqual(vals, [700, 850, 1600])          # 自动升序去重
        self.assertEqual(tuple(body[6:9]), (255, 255, 0))  # 700 的颜色沿用
        self.assertEqual(tuple(body[13:16]), (0, 255, 0))  # 850 的颜色沿用
        self.assertEqual(tuple(body[20:23]), (255, 255, 255))  # 新档白色
        self.assertEqual(body[1], 1)                       # 当前档索引


class DpiEditSemanticsTests(unittest.TestCase):
    """编辑框改 DPI 只改当前挡位的数值，**不许新增挡位**。

    挡位数量只能通过挡位面板的「+ 增加挡位」变多（2026-11 修 BUG：
    原来 set_dpi 把新值 union 进档位表，每改一次数值就多出一档）。
    """

    # ---------- 纯函数 apply_dpi_edit ----------

    def test_apply_edit_replaces_current_slot(self) -> None:
        stages, cur0, colors = P.apply_dpi_edit(
            [700, 800, 850], 3, 900, {850: (255, 0, 0)})
        self.assertEqual(stages, [700, 800, 900])     # 挡位数不变：850 被改成 900
        self.assertEqual(cur0, 2)                     # 当前档指向改完的那档
        self.assertEqual(colors.get(900), (255, 0, 0))  # 颜色跟着数值走

    def test_apply_edit_existing_value_only_switches(self) -> None:
        stages, cur0, colors = P.apply_dpi_edit([700, 800, 850], 3, 700)
        self.assertEqual(stages, [700, 800, 850])     # 表不动
        self.assertEqual(cur0, 0)                     # 只把当前档指向它

    def test_apply_edit_empty_table(self) -> None:
        stages, cur0, _ = P.apply_dpi_edit([], 0, 1350)
        self.assertEqual(stages, [1350])
        self.assertEqual(cur0, 0)

    def test_apply_edit_zero_slots_ignored(self) -> None:
        stages, cur0, _ = P.apply_dpi_edit([0, 800, 1600], 2, 900)
        self.assertEqual(stages, [900, 1600])         # 空槽(0)不参与

    # ---------- monitor.set_dpi 整链路（桩掉 HID） ----------

    def _table_payload(self, xs, cur_idx1=3, colors=None) -> bytes:
        body = bytearray([len(xs), cur_idx1 - 1])
        for v in xs:
            rgb = (colors or {}).get(v, (255, 255, 255))
            body += bytes([v & 0xFF, (v >> 8) & 0xFF, v & 0xFF, (v >> 8) & 0xFF,
                           rgb[0], rgb[1], rgb[2]])
        return bytes(body)

    def _monitor(self, table: bytes):
        src = M.MouseMonitor()
        src._caps = devcaps.DeviceCaps(source="private", dpi_min=50, dpi_max=64000,
                                       dpi_step=1, dpi_writable=True, levels_writable=True)
        state = {"table": table}
        set_bodies: list[bytes] = []

        def transact(dev, cmd, payload, timeout):
            if cmd == P.CMD_GET_DPI:
                return (cmd, 0, 0, state["table"])     # parse_frame: payload 在 [3]
            if cmd == P.CMD_SET_DPI:
                set_bodies.append(bytes(payload))
                state["table"] = bytes(payload)   # 设备按写入内容存档位表
                return (cmd, 0, 0, bytes(16))
            return (cmd, 0, 0, bytes(16))

        src._transact = transact
        src._with_dev = lambda fn: fn(object())
        src._refresh_identity = lambda dev, payload: None
        return src, set_bodies

    def test_set_dpi_keeps_level_count(self) -> None:
        src, bodies = self._monitor(
            self._table_payload([700, 800, 850], 3, {850: (9, 9, 9)}))
        with mock.patch.object(M.time, "sleep"):
            ok, msg = src.set_dpi(900)
        self.assertTrue(ok, msg)
        body = bodies[0]
        self.assertEqual(body[0], 3)                      # 挡位数没变多
        slots = [body[2 + i * 7] | (body[3 + i * 7] << 8) for i in range(3)]
        self.assertEqual(slots, [700, 800, 900])          # 当前档 850 → 900
        self.assertEqual(body[1], 2)                      # 当前档指向 900
        self.assertEqual(tuple(body[2 + 2 * 7 + 4:2 + 2 * 7 + 7]),
                         (9, 9, 9))                       # 旧档颜色沿用

    def test_set_dpi_existing_value_only_switches(self) -> None:
        src, bodies = self._monitor(self._table_payload([700, 800, 850], 3))
        with mock.patch.object(M.time, "sleep"):
            ok, msg = src.set_dpi(700)
        self.assertTrue(ok, msg)
        body = bodies[0]
        slots = [body[2 + i * 7] | (body[3 + i * 7] << 8) for i in range(3)]
        self.assertEqual(slots, [700, 800, 850])          # 表原样
        self.assertEqual(body[1], 0)                      # 只切到 700

    def test_current_index_clamped(self) -> None:
        body = P.build_dpi_payload([600], 99)
        self.assertEqual(body[1], 0)
        self.assertEqual(len(body), 2 + 7)

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            P.build_dpi_payload([], 0)


class LogitechLevelsWriteTests(unittest.TestCase):
    """板载扇区写：升序进 5 个 DPI 槽位、CRC 重算、回报率分母不动。"""

    def _sector(self) -> bytes:
        data = bytearray(256)
        data[0] = 1                       # 回报率分母（不能动）
        data[1] = 0                       # 默认档索引
        data[2] = 0
        data[3:13] = ((600).to_bytes(2, "little")
                      + (1350).to_bytes(2, "little")).ljust(10, b"\x00")
        data[254:256] = (0x1622).to_bytes(2, "big")
        return bytes(data)

    def _mouse(self):
        mouse = object.__new__(L.LogitechMouse)
        orig = self._sector()
        written = {}

        def call(idx, func, params=b"", timeout=0.6):
            if func == 0x06:
                written["addr"] = bytes(params)
                return bytes(16)
            if func == 0x07:
                off = written.get("off", 0)
                written[off] = bytes(params)
                written["off"] = off + 16
                return bytes(16)
            if func == 0x08:
                return bytes(16)
            return None

        mouse._call = call
        mouse._current_profile_sector = lambda: (8, 3, orig)
        mouse._read_sector = lambda idx, sec, size=256: b"".join(
            written[i] for i in range(0, 256, 16))
        mouse._onboard_levels = [600, 1350]
        return mouse

    def test_write_sorted_slots_keeps_rate_and_crc(self) -> None:
        mouse = self._mouse()
        with mock.patch.object(L.time, "sleep"):
            self.assertTrue(mouse.set_dpi_levels([1600, 600, 1350]))
        sector = mouse._read_sector(8, 3)
        slots = [sector[3 + 2 * i] | (sector[4 + 2 * i] << 8) for i in range(5)]
        self.assertEqual(slots, [600, 1350, 1600, 0, 0])   # 升序 + 空槽补 0
        self.assertEqual(sector[0], 1)                     # 回报率分母不动
        self.assertEqual(sector[1], 0)                     # 默认档索引仍有效
        self.assertEqual(sector[254:256],
                         L.LogitechMouse._crc_ccitt(sector[:254]).to_bytes(2, "big"))
        self.assertIsNone(mouse._onboard_levels)           # 缓存已失效

    def test_clamps_to_five_levels(self) -> None:
        mouse = self._mouse()
        with mock.patch.object(L.time, "sleep"):
            self.assertTrue(mouse.set_dpi_levels(
                [400, 800, 1600, 2400, 3200, 6400]))
        sector = mouse._read_sector(8, 3)
        slots = [sector[3 + 2 * i] | (sector[4 + 2 * i] << 8) for i in range(5)]
        self.assertEqual(slots, [400, 800, 1600, 2400, 3200])   # 板载只留前 5

    def test_rejects_when_no_onboard(self) -> None:
        mouse = self._mouse()
        mouse._current_profile_sector = lambda: None
        self.assertFalse(mouse.set_dpi_levels([600]))
