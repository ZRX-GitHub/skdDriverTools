"""低电量提醒状态机：纯逻辑，不碰 Qt、不碰硬件，方便单测。

旧版把「是否提醒过」压在三个全局标量上（``_low_armed`` / ``_low_level`` /
``_low_notified``），带来三个问题：

* 切换鼠标后状态串台 —— A 设备报过低电量，B 设备再低就不报了；
* 电量从阈值回升几格又跌回来，滞回带不对称，第二次永远不报；
* 阈值改动只清状态、不立刻复查，最多要等一个轮询周期才有反应。

这里改成**每台设备一份状态**，规则集中在一个 ``evaluate()`` 里：

1. 阈值完全由调用方传入（设置页选的 10/15/20/30 或手改的 5–50），
   本模块不写死任何百分比；
2. ``电量 <= 阈值`` 才进入判断，和设置页图例的「≤阈值」文案一致；
3. 回到阈值以上 / 插上充电线 / 换成纯有线设备 → 重新武装；
4. 电量继续大幅下降 → 升级提醒；一直低电量 → 按兜底间隔重复提醒；
5. 两条通知之间有最短间隔，防止临界值抖动把通知中心刷屏。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

#: 电量继续下降多少个百分点算「明显恶化」，值得再提醒一次
ESCALATE_DROP = 5
#: 一直停在低电量时的兜底重复间隔（秒）
REPEAT_AFTER = 1800.0
#: 同一台设备两条通知之间的最短间隔（秒），防止 30%↔31% 抖动刷屏
MIN_INTERVAL = 300.0


@dataclass
class DeviceState:
    """一台设备的提醒状态。"""

    #: True = 下一次跌破阈值可以提醒
    armed: bool = True
    #: 上次真正弹出通知时的电量，用于判断「继续下降」
    last_level: int | None = None
    #: 上次通知的 monotonic 时间；0 表示还没报过
    last_notified: float = 0.0
    #: 上次评估时的阈值，变了就按新设定重新判断
    threshold: int | None = None


@dataclass(frozen=True)
class Decision:
    """一次评估的结果。reason 只用于日志，不参与 UI 文案。"""

    notify: bool
    reason: str = ""


def _armed(state: DeviceState) -> None:
    """重新武装：清掉「报过了」的记号，但保留上次通知时间用于节流。"""
    state.armed = True
    state.last_level = None


class LowBatteryTracker:
    """多设备低电量提醒判定器。

    调用方每次拿到新数据就调一次 :meth:`evaluate`；返回 ``notify=True`` 时
    自己负责把通知发出去。时间用 ``time.monotonic()``，测试可注入 ``now``。
    """

    def __init__(self, *, escalate_drop: int = ESCALATE_DROP,
                 repeat_after: float = REPEAT_AFTER,
                 min_interval: float = MIN_INTERVAL) -> None:
        self.escalate_drop = int(escalate_drop)
        self.repeat_after = float(repeat_after)
        self.min_interval = float(min_interval)
        self._states: dict[str, DeviceState] = {}
        self._enabled = True

    # ---------------------------------------------------------------- 外部控制
    def set_enabled(self, enabled: bool) -> None:
        """开关被拨动时调用。关掉后清空状态，重新打开能立刻再报一次。"""
        on = bool(enabled)
        if on == self._enabled and on:
            return
        self._enabled = on
        self._states.clear()

    def reset(self, key: str | None = None) -> None:
        """手动复位。``key=None`` 清空全部（切换设备 / 阈值大改时使用）。"""
        if key is None:
            self._states.clear()
        else:
            self._states.pop(key, None)

    def state(self, key: str) -> DeviceState:
        """给自检 / 日志看的内部状态（返回引用，别改）。"""
        return self._states.setdefault(key, DeviceState())

    # ---------------------------------------------------------------- 判定
    def evaluate(self, *, key: str, connected: bool, wireless: bool,
                 charging: bool, sleeping: bool, battery: int | None,
                 threshold: int, enabled: bool = True,
                 now: float | None = None) -> Decision:
        """判断这台设备这一刻要不要弹低电量通知。

        ``threshold`` 就是设置页选的值，本方法不夹取、不改写，原样作为分界。
        """
        if now is None:
            now = time.monotonic()
        on = bool(enabled)
        if on != self._enabled:
            self.set_enabled(on)
        if not self._enabled:
            return Decision(False, "disabled")

        try:
            low = int(threshold)
        except (TypeError, ValueError):
            return Decision(False, "bad-threshold")

        st = self._states.setdefault(key, DeviceState())
        if st.threshold != low:
            # 用户改了「提醒阈值」：按新值重新判断，并且允许马上补报一次。
            st.threshold = low
            _armed(st)
            st.last_notified = 0.0

        # 拿不到可信读数时一律不报；充电中的低电量也不算异常。
        if not connected:
            return Decision(False, "disconnected")
        if not wireless:
            # 纯有线 USB 供电：没有电池，也不该被缓存的兜底 100% 误判
            _armed(st)
            return Decision(False, "wired")
        if battery is None:
            return Decision(False, "no-reading")
        if charging:
            _armed(st)
            return Decision(False, "charging")
        if sleeping:
            return Decision(False, "sleeping")

        try:
            level = int(battery)
        except (TypeError, ValueError):
            return Decision(False, "bad-reading")
        if level < 0 or level > 100:
            return Decision(False, "out-of-range")

        if level > low:
            # 回到阈值以上就重新武装；防抖靠 MIN_INTERVAL，不再用不对称滞回带。
            _armed(st)
            return Decision(False, "above-threshold")

        # 到这里 level <= low，确实低电量
        if st.armed:
            reason = "crossed"
        elif (st.last_level is not None
              and level <= st.last_level - self.escalate_drop):
            reason = "dropped"
        elif st.last_notified and now - st.last_notified >= self.repeat_after:
            reason = "reminder"
        else:
            return Decision(False, "already-notified")

        # 首次报过之后，任何一条后续通知都要隔够 MIN_INTERVAL。
        if st.last_notified and now - st.last_notified < self.min_interval:
            return Decision(False, "throttled")

        st.armed = False
        st.last_level = level
        st.last_notified = now
        st.threshold = low
        return Decision(True, reason)
