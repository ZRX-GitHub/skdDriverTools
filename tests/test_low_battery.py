"""低电量提醒：状态机 + 通知通道的回归测试（纯逻辑，不弹真通知）。

    python -m unittest discover -s tests -v

重点覆盖用户报的「设了 30% 却没提醒」：阈值必须完全跟随设置页，通知必须
能进 Windows 通知中心；以及旧状态机的三个坑（多设备串台、回升后再跌不报、
改阈值要等一个轮询周期）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import config  # noqa: E402
import i18n  # noqa: E402
import lowbattery  # noqa: E402
import notify  # noqa: E402


def evaluate(tracker, level, *, threshold=30, now=1000.0, key="crociris", **kw):
    """默认是一台在线、无线、非充电、非休眠的鼠标。"""
    params = dict(connected=True, wireless=True, charging=False, sleeping=False,
                  battery=level, threshold=threshold, enabled=True, now=now)
    params.update(kw)
    params.pop("level", None)
    return tracker.evaluate(key=key, **params)


class ThresholdFollowsSettings(unittest.TestCase):
    """阈值取自设置页，不写死任何百分比。"""

    def test_every_preset_triggers_at_equality(self) -> None:
        for low in (10, 15, 20, 30):
            tracker = lowbattery.LowBatteryTracker()
            self.assertFalse(evaluate(tracker, low + 1, threshold=low).notify,
                             f"阈值 {low}%：{low + 1}% 不该报")
            d = evaluate(tracker, low, threshold=low)
            self.assertTrue(d.notify, f"阈值 {low}%：电量正好 {low}% 应该报")
            self.assertEqual(d.reason, "crossed")

    def test_hand_edited_threshold_from_config(self) -> None:
        # config.json 手写的 5–50 任意值也要生效
        for low in (config.THRESHOLD_MIN, 25, 37, config.THRESHOLD_MAX):
            tracker = lowbattery.LowBatteryTracker()
            self.assertTrue(evaluate(tracker, low, threshold=low).notify)
            self.assertFalse(evaluate(tracker, low + 1, threshold=low).notify)

    def test_threshold_change_takes_effect_immediately(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertFalse(evaluate(tracker, 30, threshold=15, now=100).notify)
        # 从 15% 调到 30%，当前正好 30% —— 这一刻就该报，不等定时器
        d = evaluate(tracker, 30, threshold=30, now=101)
        self.assertTrue(d.notify)
        self.assertEqual(d.reason, "crossed")

    def test_raising_threshold_does_not_notify_when_battery_is_higher(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertFalse(evaluate(tracker, 40, threshold=10, now=100).notify)
        self.assertFalse(evaluate(tracker, 40, threshold=30, now=101).notify)

    def test_bad_threshold_is_ignored(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertFalse(evaluate(tracker, 5, threshold="abc").notify)


class SkipConditions(unittest.TestCase):
    def test_wired_usb_power_never_notifies(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertFalse(evaluate(tracker, 0, wireless=False).notify)

    def test_disconnected_sleeping_charging_and_no_reading(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        for kw in (dict(connected=False), dict(sleeping=True), dict(charging=True),
                   dict(battery=None), dict(battery="bad"), dict(battery=101)):
            with self.subTest(**kw):
                self.assertFalse(evaluate(tracker, 5, **kw).notify)

    def test_switch_off_clears_state_and_switch_on_rearms(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, threshold=30, now=100).notify)
        self.assertFalse(evaluate(tracker, 30, threshold=30, now=200,
                                  enabled=False).notify)
        self.assertTrue(evaluate(tracker, 30, threshold=30, now=300).notify)

    def test_low_battery_config_defaults(self) -> None:
        cfg = config.Config()
        self.assertTrue(cfg.low_battery_notify)
        self.assertEqual(cfg.low_battery_threshold, config.THRESHOLD_DEFAULT)


class DebounceAndEscalation(unittest.TestCase):
    def test_no_repeat_while_still_low(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, now=100).notify)
        self.assertFalse(evaluate(tracker, 30, now=200).notify)
        self.assertFalse(evaluate(tracker, 29, now=300).notify)

    def test_recovery_then_drop_notifies_again(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, now=100).notify)
        # 旧版滞回带要求回升 +5 才复位；只回到 31% 就再也报不出来
        self.assertFalse(evaluate(tracker, 31, now=200).notify)
        self.assertTrue(evaluate(tracker, 30, now=1000).notify)

    def test_recovery_bounce_is_throttled(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, now=100).notify)
        self.assertFalse(evaluate(tracker, 31, now=110).notify)
        self.assertFalse(evaluate(tracker, 30, now=120).notify,
                         "临界值抖动 20 秒内不该刷屏")
        self.assertTrue(evaluate(tracker, 30, now=100 + lowbattery.MIN_INTERVAL + 1).notify)

    def test_continued_drop_escalates(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, now=100).notify)
        self.assertTrue(evaluate(tracker, 24, now=100 + lowbattery.MIN_INTERVAL + 1).notify)

    def test_charging_resets(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, now=100).notify)
        self.assertFalse(evaluate(tracker, 60, charging=True, now=200).notify)
        self.assertTrue(evaluate(tracker, 30, now=1000).notify)

    def test_long_low_battery_repeats(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, now=100).notify)
        after = 100 + lowbattery.REPEAT_AFTER + 1
        d = evaluate(tracker, 30, now=after)
        self.assertTrue(d.notify)
        self.assertEqual(d.reason, "reminder")


class MultipleDevices(unittest.TestCase):
    def test_each_device_has_its_own_state(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, key="crociris", now=100).notify)
        # A 报过不能让 B 变成「已提醒」
        self.assertTrue(evaluate(tracker, 20, key="logitech", now=100).notify)
        self.assertFalse(evaluate(tracker, 30, key="crociris", now=200).notify)

    def test_one_wired_one_wireless(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertFalse(evaluate(tracker, 10, key="logitech", wireless=False,
                                  now=100).notify)
        self.assertTrue(evaluate(tracker, 10, key="crociris", now=100).notify)

    def test_reset_single_device(self) -> None:
        tracker = lowbattery.LowBatteryTracker()
        self.assertTrue(evaluate(tracker, 30, key="a", now=100).notify)
        tracker.reset("a")
        self.assertTrue(evaluate(tracker, 30, key="a", now=101).notify)


class FakeTray:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, int]] = []

    def showMessage(self, title, message, icon=None, timeout=0):  # noqa: N802
        self.messages.append((title, message, int(timeout)))


class NotificationChannel(unittest.TestCase):
    def setUp(self) -> None:
        self._old_channel = notify.channel()
        self._old_tray = notify._tray
        self.addCleanup(notify.set_channel, self._old_channel)
        self.addCleanup(notify.bind_tray, self._old_tray)
        self.addCleanup(i18n.set_lang, i18n.lang())
        notify.set_channel("bubble")   # 测试期间别真的弹通知 / 起子进程
        notify.bind_tray(FakeTray())

    def test_low_battery_message_uses_configured_threshold(self) -> None:
        notify.low_battery("X7 Ultra Max", 30, 30)
        title, text, timeout = notify._tray.messages[-1]
        self.assertEqual(title, i18n.T("鼠标电量低"))
        self.assertIn("30%", text)
        self.assertIn("X7 Ultra Max", text)
        self.assertEqual(timeout, 10000)

    def test_message_threshold_is_not_hardcoded(self) -> None:
        for low in (10, 15, 20, 30):
            notify._tray.messages.clear()
            notify.low_battery("X7 Ultra Max", 5, low)
            self.assertIn(f"{low}%", notify._tray.messages[-1][1])

    def test_english_message(self) -> None:
        i18n.set_lang("en")
        notify.low_battery("X7 Ultra Max", 20, 20)
        text = notify._tray.messages[-1][1]
        self.assertIn("20%", text)
        self.assertIn("alert threshold", text)

    def test_toast_xml_escapes_device_names(self) -> None:
        xml = notify.toast_xml("鼠标电量低", 'A&B <X7> "Ultra" 20%')
        self.assertIn("&amp;", xml)
        self.assertIn("&lt;X7&gt;", xml)
        self.assertIn("ToastText02", xml)
        self.assertIn('<text id="1">', xml)
        self.assertIn('<text id="2">', xml)

    def test_toast_xml_strips_control_chars(self) -> None:
        xml = notify.toast_xml("t\x01", "m\x07")
        self.assertNotIn("\x01", xml)
        self.assertNotIn("\x07", xml)

    def test_pump_replays_failed_toasts_via_bubble(self) -> None:
        notify._fallback.append(("title", "body", "warning", 10000))
        self.assertEqual(notify.pump(), 1)
        self.assertEqual(notify._tray.messages[-1][0], "title")
        self.assertEqual(notify.pump(), 0)

    def test_show_returns_channel(self) -> None:
        self.assertEqual(notify.show("t", "m"), "bubble")
        notify.bind_tray(None)
        self.assertEqual(notify.show("t", "m"), "none")

    def test_set_channel_rejects_unknown(self) -> None:
        notify.set_channel("nonsense")
        self.assertEqual(notify.channel(), "bubble")


if __name__ == "__main__":
    unittest.main()
