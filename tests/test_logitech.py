"""罗技 HID++ 读取层与设备中枢的回归测试（纯逻辑，不碰硬件）。

    python -m unittest discover -s tests -v

硬件相关的部分（真机读写）在 tools/probe_logitech.py 里单独跑；
这里只锁住「解析规则」和「两台设备怎么选」，改代码时能立刻发现回归。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import logitech as L  # noqa: E402
import monitor as M  # noqa: E402


class DpiListTests(unittest.TestCase):
    """0x2201 func1 的档位列表解析（G502 HERO 实测原始返回）。"""

    def test_g502_range_expansion(self) -> None:
        # [sensor=0] [100] [范围标记: 步进 50] [25600] [终止 0]
        raw = bytes.fromhex("000064e0326400000000000000000000")
        lst = L.parse_dpi_list(raw)
        self.assertEqual(len(lst), 511)
        self.assertEqual(lst[0], 100)
        self.assertEqual(lst[-1], 25600)
        self.assertTrue(all(b - a == 50 for a, b in zip(lst, lst[1:])))

    def test_literal_list(self) -> None:
        raw = bytes([0x00, 0x00, 0x64, 0x03, 0xE8, 0x00, 0x00])  # 100, 1000
        self.assertEqual(L.parse_dpi_list(raw), [100, 1000])

    def test_terminator_stops_parsing(self) -> None:
        raw = bytes([0x00, 0x03, 0xE8, 0x00, 0x00, 0x12, 0x34])
        self.assertEqual(L.parse_dpi_list(raw), [1000])

    def test_garbage_and_empty(self) -> None:
        self.assertEqual(L.parse_dpi_list(None), [])
        self.assertEqual(L.parse_dpi_list(b"\x00"), [])
        # 01 02 按协议恰好是合法字面值 258——容错解析只能照字面收
        self.assertEqual(L.parse_dpi_list(b"\x00\x01\x02"), [258])


class OnboardProfileTests(unittest.TestCase):
    """0x8100 板载 profile 的解析（G502 HERO 真机抓的字节）。"""

    # 扇区 0 的 profile 目录：5 条 [扇区号(大端)][标志]，0xFFFF 结束
    DIR_BYTES = (bytes.fromhex("00010100") + bytes.fromhex("00020100")
                 + bytes.fromhex("00030100") + bytes.fromhex("00040100")
                 + bytes.fromhex("00050100") + b"\xff\xff")
    # 当前 profile（扇区 1）头部：1000Hz / 默认档0 / 切换档0 / DPI槽 [600,0,0,0,0]
    PROFILE1 = bytes.fromhex("01000058020000000000000000")

    def test_directory(self) -> None:
        self.assertEqual(L.parse_profile_directory(self.DIR_BYTES), [1, 2, 3, 4, 5])

    def test_directory_terminator_and_garbage(self) -> None:
        raw = bytes.fromhex("00030100") + b"\xff\xff" + b"\xaa" * 8
        self.assertEqual(L.parse_profile_directory(raw), [3])
        self.assertEqual(L.parse_profile_directory(None), [])
        self.assertEqual(L.parse_profile_directory(b"\x00"), [])

    def test_profile_dpi_single_slot(self) -> None:
        # G HUB 只配了一个 600：58 02 小端 = 0x0258
        self.assertEqual(L.parse_profile_dpi(self.PROFILE1), [600])

    def test_profile_dpi_multi_slot(self) -> None:
        # 扇区 3 实测：[1200, 2400, 3200, 6400, 0]，b0 04=1200, a0 06=0x06a0=1696?
        data = bytearray(13)
        data[0] = 1
        for i, v in enumerate((1200, 2400, 3200, 6400)):
            data[3 + 2 * i:5 + 2 * i] = v.to_bytes(2, "little")
        self.assertEqual(L.parse_profile_dpi(bytes(data)), [1200, 2400, 3200, 6400])

    def test_profile_dpi_garbage(self) -> None:
        self.assertEqual(L.parse_profile_dpi(None), [])
        self.assertEqual(L.parse_profile_dpi(b"\x01\x00"), [])
        # 全零槽位 = 一个档都没配
        self.assertEqual(L.parse_profile_dpi(bytes(13)), [])


class MatchLevelTests(unittest.TestCase):
    """_match_level：当前 DPI 在档位列表里定位。"""

    def test_exact_hit(self) -> None:
        self.assertEqual(L.LogitechMouse._match_level([600, 1200, 2400], 1200), (2, 3))

    def test_single_level(self) -> None:
        self.assertEqual(L.LogitechMouse._match_level([600], 600), (1, 1))

    def test_nearest_within_tolerance(self) -> None:
        self.assertEqual(L.LogitechMouse._match_level([600, 1200], 620), (1, 2))

    def test_out_of_range(self) -> None:
        self.assertEqual(L.LogitechMouse._match_level([600, 1200], 8000), (0, 2))

    def test_no_current(self) -> None:
        self.assertEqual(L.LogitechMouse._match_level([600], None), (0, 1))

    def test_empty(self) -> None:
        self.assertEqual(L.LogitechMouse._match_level([], 600), (0, 0))


class ShortModelTests(unittest.TestCase):
    def test_strip_common_suffix(self) -> None:
        self.assertEqual(L.short_model("G502 HERO Gaming Mouse"), "G502 HERO")
        self.assertEqual(L.short_model("G Pro X Superlight 2 Mouse"), "G Pro X Superlight 2")
        self.assertEqual(L.short_model("MX Master 3S 鼠标"), "MX Master 3S")

    def test_keep_plain_name(self) -> None:
        self.assertEqual(L.short_model("G502"), "G502")
        self.assertEqual(L.short_model(""), "")


class WirelessTests(unittest.TestCase):
    def test_battery_feature_means_wireless(self) -> None:
        self.assertTrue(L._looks_wireless("G502 HERO Gaming Mouse", True))

    def test_wired_without_battery(self) -> None:
        self.assertFalse(L._looks_wireless("G502 HERO Gaming Mouse", False))

    def test_receiver_string_means_wireless(self) -> None:
        self.assertTrue(L._looks_wireless("USB Receiver", False))
        self.assertTrue(L._looks_wireless("LIGHTSPEED Receiver", False))


class _FakeSource:
    def __init__(self, info: M.MouseInfo) -> None:
        self.info = info
        self.cache: dict = {}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def refresh(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    def snapshot(self) -> M.MouseInfo:
        return self.info

    def cache_snapshot(self) -> dict:
        return dict(self.cache)


class HubTests(unittest.TestCase):
    def _hub(self, cro_ok: bool, logi_ok: bool, selected: str) -> M.DeviceHub:
        hub = M.DeviceHub.__new__(M.DeviceHub)      # 不启线程，手塞数据源
        hub.selected = selected
        hub.sources = {
            "crociris": _FakeSource(M.MouseInfo(connected=cro_ok, brand="CROCIRIS", model="X7")),
            "logitech": _FakeSource(M.MouseInfo(connected=logi_ok, brand="Logitech", model="G502 HERO")),
        }
        return hub

    def test_selected_device_wins_when_online(self) -> None:
        hub = self._hub(True, True, "logitech")
        self.assertEqual(hub.current_key(), "logitech")
        self.assertEqual(hub.snapshot().model, "G502 HERO")

    def test_falls_back_when_selected_offline(self) -> None:
        hub = self._hub(True, False, "logitech")
        self.assertEqual(hub.current_key(), "crociris")
        self.assertEqual(hub.snapshot().model, "X7")

    def test_selection_survives_unplug(self) -> None:
        hub = self._hub(True, False, "logitech")
        hub.snapshot()
        self.assertEqual(hub.selected, "logitech")   # 回退只是临时让位，不丢选择

    def test_devices_rows_mark_current(self) -> None:
        hub = self._hub(True, True, "logitech")
        rows = hub.devices()
        self.assertEqual([r["key"] for r in rows], ["crociris", "logitech"])
        self.assertEqual([r["current"] for r in rows], [False, True])
        self.assertTrue(all(r["available"] for r in rows))


class CacheIsolationTests(unittest.TestCase):
    """两套采集器不能共用同一个 cache dict。

    真机上撞见过：罗技把自己写进共享 cache 后，西伯利亚那一行也读成
    「Logitech G502 HERO」，设备列表里出现两行一模一样的罗技。
    """

    def test_sources_get_their_own_cache(self) -> None:
        hub = M.DeviceHub(1.0, {"brand": "CROCIRIS", "model": "X7"}, "crociris")
        cro = hub.sources[hub.CROCIRIS].cache
        logi = hub.sources[hub.LOGITECH].cache
        self.assertIsNot(cro, logi)
        self.assertEqual(cro.get("model"), "X7")      # 旧平面格式归西伯利亚
        logi.update(brand="Logitech", model="G502 HERO")
        self.assertEqual(cro.get("model"), "X7")      # 罗技写入不影响另一边

    def test_cache_snapshot_is_per_device(self) -> None:
        hub = M.DeviceHub(1.0, None, "crociris")
        hub.sources[hub.CROCIRIS].cache.update(model="X7")
        hub.sources[hub.LOGITECH].cache.update(model="G502 HERO")
        snap = hub.cache_snapshot()
        self.assertEqual(snap[hub.CROCIRIS]["model"], "X7")
        self.assertEqual(snap[hub.LOGITECH]["model"], "G502 HERO")

    def test_nested_cache_roundtrip(self) -> None:
        saved = {"crociris": {"model": "X7"}, "logitech": {"model": "G502 HERO"}}
        hub = M.DeviceHub(1.0, saved, "logitech")
        self.assertEqual(hub.sources[hub.CROCIRIS].cache.get("model"), "X7")
        self.assertEqual(hub.sources[hub.LOGITECH].cache.get("model"), "G502 HERO")


class _FakeLogiDev:
    """假设备：只提供 _poll 需要的 col.path 与 read()。"""

    class _Col:
        path = r"\\?\hid#vid_046d&pid_c332&mi_01#7&1f2e3d4c&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}"

    def __init__(self, data: dict) -> None:
        self.col = _FakeLogiDev._Col()
        self.data = data
        self.reads = 0

    def read(self) -> dict:
        self.reads += 1
        return dict(self.data)


def _read_dict(**over) -> dict:
    """LogitechMouse.read() 的完整字段集（_apply 需要这些键）。"""
    data = {
        "name": "G502 HERO", "model": "G502 HERO", "dpi": 800,
        "dpi_level": 2, "dpi_levels": 5, "dpi_table": [400, 800],
        "battery": 100, "charging": False, "report_rate": 1000,
        "is_wireless": False, "connection": "USB 有线",
        "vid": 0x046D, "pid": 0xC332,
    }
    data.update(over)
    return data


def _empty_dict() -> dict:
    return _read_dict(name="", model="", dpi=None)


class LogitechUnplugTests(unittest.TestCase):
    """拔掉 USB 后必须马上判掉线。

    真机撞过：G502 拔线后设备列表还挂着它、标题还写「2 台设备」，
    点「立即刷新」也没反应。根因是拔线后 Windows 不给任何异常——
    对失效句柄的 ReadFile/WriteFile 只是静默失败，read() 各项全空，
    而老代码只要没异常就 `connected=True`，于是永远"在线"。
    """

    def _mon(self, data: dict) -> M.LogitechMonitor:
        mon = M.LogitechMonitor(2.0)          # 不起线程，手工塞设备
        mon._dev = _FakeLogiDev(data)
        return mon

    def test_poll_raises_when_path_disappears(self) -> None:
        mon = self._mon(_read_dict())
        with mock.patch.object(M.hid_win, "device_present", return_value=False):
            with self.assertRaises(OSError):
                mon._poll()

    def test_one_empty_round_is_not_fatal(self) -> None:
        # 单轮读空可能只是设备正在唤醒，不该立刻判死
        mon = self._mon(_empty_dict())
        with mock.patch.object(M.hid_win, "device_present", return_value=True):
            mon._poll()
            self.assertTrue(mon.snapshot().stale)
            with self.assertRaises(OSError):      # 连错两轮 → 掉线
                mon._poll()

    def test_empty_round_keeps_last_values(self) -> None:
        mon = self._mon(_read_dict())
        mon._publish(connected=True, dpi=800)
        with mock.patch.object(M.hid_win, "device_present", return_value=True):
            mon._poll()                            # 这一轮读空，别把 800 冲成 None
        self.assertEqual(mon.snapshot().dpi, 800)

    def test_refresh_marks_offline_immediately(self) -> None:
        mon = self._mon(_read_dict())
        mon._publish(connected=True, model="G502 HERO", dpi=800)
        with mock.patch.object(M.hid_win, "device_present", return_value=False):
            mon.refresh()                          # 点「立即刷新」的路径
        info = mon.snapshot()
        self.assertFalse(info.connected)
        self.assertEqual(info.message, "罗技鼠标已断开")

    def test_refresh_keeps_online_device(self) -> None:
        mon = self._mon(_read_dict())
        mon._publish(connected=True, model="G502 HERO")
        with mock.patch.object(M.hid_win, "device_present", return_value=True):
            mon.refresh()
        self.assertTrue(mon.snapshot().connected)

    def test_devices_row_goes_unavailable_after_drop(self) -> None:
        """掉线的那台不再出现在设备列表里（main.py 只渲染 available）。"""
        hub = M.DeviceHub(1.0, None, "crociris")
        logi = hub.sources[hub.LOGITECH]
        logi._publish(connected=True, brand="Logitech", model="G502 HERO", name="G502 HERO")
        self.assertTrue([r for r in hub.devices() if r["key"] == "logitech"][0]["available"])
        logi._drop()
        rows = [r for r in hub.devices() if r["key"] == "logitech"]
        self.assertFalse(rows[0]["available"])
        self.assertEqual(rows[0]["label"], "Logitech G502 HERO")   # 名字留着，别变空行

    def test_device_present_rejects_blank_path(self) -> None:
        self.assertFalse(M.hid_win.device_present(""))


class LogitechModelSpecTests(unittest.TestCase):
    """罗技型号表：按官方参数给范围，设备读数只用来放宽边界 / 兜底。"""

    @staticmethod
    def _caps(model: str, dpi_raw=None, rate_mask=None):
        """造一个只回放固定报文的假设备，跑真正的 capabilities()。"""
        import devcaps

        dev = L.LogitechMouse.__new__(L.LogitechMouse)
        dev._feat_cache = {}
        dev._order = "be"
        dev._onboard_levels = None
        dev._name_cache = model
        dev.col = type("Col", (), {"product": model, "vendor_id": 0x046D,
                                   "product_id": 0xC08B, "path": "fake"})()

        def fake_call(feat_idx, func, params=b"", timeout=0.6):
            if feat_idx == 0x0A and func == 0x01:
                return dpi_raw
            if feat_idx == 0x0B and func == 0x00:
                return None if rate_mask is None else bytes([rate_mask])
            if feat_idx == 0x0B and func == 0x01:
                return b"\x01"                      # 当前 1 ms = 1000 Hz
            return None

        def fake_feature(feature_id):
            return {L.FEAT_DPI: 0x0A, L.FEAT_REPORT_RATE: 0x0B}.get(feature_id, 0)

        with mock.patch.object(dev, "_call", side_effect=fake_call), \
             mock.patch.object(dev, "feature", side_effect=fake_feature), \
             mock.patch.object(dev, "dpi", return_value=1200), \
             mock.patch.object(dev, "_probe_writable", return_value=True):
            return dev.capabilities(), devcaps

    def test_spec_table_matches_long_names_first(self) -> None:
        import devcaps

        # 长名不能被短名抢走
        self.assertEqual(devcaps.logitech_spec_for("G502 X PLUS Wireless RGB Gaming Mouse"),
                         (100, 25600, 50, 1000))
        self.assertEqual(devcaps.logitech_spec_for("G502 X"), (100, 25600, 50, 1000))
        self.assertEqual(devcaps.logitech_spec_for("G502 HERO Gaming Mouse"),
                         (100, 25600, 50, 1000))
        self.assertEqual(devcaps.logitech_spec_for("G502 Proteus Spectrum"),
                         (200, 12000, 50, 1000))
        self.assertEqual(devcaps.logitech_spec_for("PRO X SUPERLIGHT 2 Wireless Mouse"),
                         (100, 32000, 50, 2000))
        self.assertEqual(devcaps.logitech_spec_for("PRO X SUPERLIGHT Wireless Mouse"),
                         (100, 25600, 50, 1000))

    def test_spec_table_tolerates_missing_g_prefix(self) -> None:
        """HID++ 名字有的写 "G PRO X ..."，有的只写 "PRO X ..."。"""
        import devcaps

        self.assertEqual(devcaps.logitech_spec_for("G PRO 2 LIGHTSPEED"),
                         devcaps.logitech_spec_for("PRO 2 LIGHTSPEED Wireless Gaming Mouse"))
        self.assertEqual(devcaps.logitech_spec_for("G Pro Wireless")[1], 16000)

    def test_spec_table_rejects_unknown(self) -> None:
        import devcaps

        for text in ("USB Receiver", "", "Gaming Mouse", "MX Keys"):
            self.assertIsNone(devcaps.logitech_spec_for(text), text)

    def test_device_range_used_when_model_unknown(self) -> None:
        """表里没有的型号完全走设备读数，不能被兜底规格抹掉。"""
        caps, _ = self._caps("Some New Mouse",
                             dpi_raw=bytes.fromhex("000064e0320bb80000000000000000"))
        self.assertEqual((caps.dpi_min, caps.dpi_max, caps.dpi_step), (100, 3000, 50))

    def test_spec_used_when_device_reports_nothing(self) -> None:
        """0x2201 fn1 回空（部分固件如此）时按型号表给范围。"""
        caps, _ = self._caps("G502 HERO")
        self.assertEqual((caps.dpi_min, caps.dpi_max, caps.dpi_step), (100, 25600, 50))
        self.assertEqual(caps.source, "hidpp")      # 界面仍显示「G HUB 管理」

    def test_spec_widens_truncated_device_range(self) -> None:
        """设备只报 100–3000，也不能把 25600 的鼠标说成 3000。"""
        caps, _ = self._caps("G502 HERO",
                             dpi_raw=bytes.fromhex("000064e0320bb80000000000000000"))
        self.assertEqual((caps.dpi_min, caps.dpi_max, caps.dpi_step), (100, 25600, 50))

    def test_device_value_beyond_spec_is_kept(self) -> None:
        """设备档位表里已经存着比官网更高的值，就不能把它判成越界。"""
        caps, _ = self._caps("G102", dpi_raw=bytes.fromhex("000064e0322ee0000000000000000000"))
        self.assertEqual((caps.dpi_min, caps.dpi_max), (100, 12000))

    def test_rate_bitmap_wins_over_spec(self) -> None:
        """无线接收器只支持到 1000 Hz 时，别按表硬列 2000 让用户选。"""
        caps, _ = self._caps("PRO X SUPERLIGHT 2", rate_mask=0x8B)
        self.assertEqual(caps.rate_values, [125, 250, 500, 1000])

    def test_rate_spec_used_when_bitmap_missing(self) -> None:
        caps, _ = self._caps("PRO X SUPERLIGHT 2")
        self.assertEqual(caps.rate_values, [125, 250, 500, 1000, 2000])

    def test_rate_values_up_to(self) -> None:
        import devcaps

        self.assertEqual(devcaps.rate_values_up_to(125), (125,))
        self.assertEqual(devcaps.rate_values_up_to(1000), (125, 250, 500, 1000))
        self.assertEqual(devcaps.rate_values_up_to(8000), devcaps.KNOWN_RATES)


class DeviceIndexTests(unittest.TestCase):
    """接收器机型：鼠标挂在 1–6 号位，0xFF 上只有接收器自己。

    这是「没见过的鼠标也能适配」的前提——DPI 范围、回报率、型号都靠型号名
    查表，而型号名只有在对的设备索引上才问得出来。
    """

    @staticmethod
    def _mouse(present: dict[int, str]):
        """假 HID++：只有 `present` 里列出的索引后面挂着一台会报名字的设备。"""
        m = object.__new__(L.LogitechMouse)
        m._feat_cache = {}
        m._order = "be"
        m._name_cache = None
        m.dev_idx = 0xFF
        m._idx_probed = False

        def call(feat, func, params=b"", timeout=0.6):
            name = present.get(m.dev_idx)
            if name is None:                      # 这个索引后面没人应答
                return None
            if feat == L.FEAT_ROOT and params == bytes([0x00, L.FEAT_DEVICE_NAME]):
                return bytes([7])                 # 有 0x0005 → 特征索引 7
            if feat == 7 and func == 0x00:
                return bytes([len(name)])
            if feat == 7 and func == 0x01 and params and params[0] == 0:
                return name.encode()[:16].ljust(16, b"\x00")
            return None

        m._call = call
        return m

    def test_device_behind_receiver_is_found(self) -> None:
        m = self._mouse({0xFF: "USB Receiver", 2: "G502 HERO"})
        m._ensure_index()
        self.assertEqual(m.dev_idx, 2)
        self.assertEqual(m.name(), "G502 HERO")
        self.assertFalse(m.receiver_only())

    def test_wired_mouse_stays_on_ff(self) -> None:
        m = self._mouse({0xFF: "G502 HERO"})
        m._ensure_index()
        self.assertEqual(m.dev_idx, 0xFF)
        self.assertEqual(m.name(), "G502 HERO")
        self.assertFalse(m.receiver_only())

    def test_receiver_without_mouse_is_not_a_device(self) -> None:
        m = self._mouse({0xFF: "USB Receiver"})
        m._ensure_index()
        self.assertEqual(m.dev_idx, 0xFF)
        self.assertTrue(m.receiver_only())
        self.assertTrue(M.LogitechMonitor._looks_empty({"name": "USB Receiver", "dpi": None}))

    def test_probe_runs_once(self) -> None:
        m = self._mouse({0xFF: "USB Receiver", 4: "PRO X SUPERLIGHT 2"})
        m._ensure_index()
        self.assertEqual(m.dev_idx, 4)
        m.dev_idx = 0xFF                 # 手工拨回去，再调一次不应该重新探
        m._ensure_index()
        self.assertEqual(m.dev_idx, 0xFF)

    def test_outgoing_frame_carries_device_index(self) -> None:
        """发出去的报文第 0 字节必须是当前设备索引，否则接收器收不到。"""
        m = object.__new__(L.LogitechMouse)
        m.dev_idx = 3
        sent: dict[str, bytes] = {}

        class _Dev:
            @staticmethod
            def read(_timeout):
                return None

            @staticmethod
            def write(body, _timeout, report_id=None):
                sent["body"] = bytes(body)
                return True

        m.dev = _Dev()
        m._call(0x07, 0x02, b"\x00", timeout=0.02)
        # body 不含报告 ID（由 write 的 report_id 前置），第 0 字节就是设备索引
        self.assertEqual(sent["body"][0], 3)
        self.assertEqual(sent["body"][1], 0x07)

    def test_receiver_name_counts_as_empty_device(self) -> None:
        self.assertTrue(M.LogitechMonitor._looks_empty({"name": "USB Receiver", "dpi": None}))
        self.assertFalse(M.LogitechMonitor._looks_empty({"name": "G502 HERO", "dpi": None}))


if __name__ == "__main__":
    unittest.main()
