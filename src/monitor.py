"""鼠标数据采集：后台线程轮询 HID 私有协议。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import applog
import devcaps
import ghub
import hid_win
import hidbrands
import i18n
import logitech
import protocol as P


@dataclass
class MouseInfo:
    connected: bool = False
    brand: str = "CROCIRIS"
    model: str = ""
    name: str = ""
    battery: int | None = None
    charging: bool = False
    full: bool = False
    dpi: int | None = None
    dpi_level: int = 0
    dpi_levels: int = 0
    report_rate: int | None = None
    connection: str = ""
    is_wireless: bool = True
    firmware: str = ""
    receiver_firmware: str = ""
    serial: str = ""
    vid: int = 0
    pid: int = 0
    updated_at: float = 0.0
    stale: bool = False
    sleeping: bool = False
    message: str = ""
    extra: dict = field(default_factory=dict)
    caps: devcaps.DeviceCaps = field(default_factory=devcaps.DeviceCaps)
    #: 当前生效的 DPI 档位表（升序）；挡位编辑的初始值
    dpi_table: list = field(default_factory=list)

    @property
    def display_model(self) -> str:
        return self.name or self.model or "未识别鼠标"

    @property
    def subtitle(self) -> str:
        bits = [self.name or self.model]
        if self.connection:
            bits.append(self.connection)
        return " · ".join(b for b in bits if b)


def _fw(a: int, b: int) -> str:
    if a == 0 and b == 0:
        return ""
    return f"{b & 0xFF}.{a:02X}"


class MouseMonitor:
    """持续轮询，线程安全地暴露最新状态快照。"""

    def __init__(self, interval: float = 2.0, cache: dict | None = None) -> None:
        self.interval = interval
        self.cache: dict = dict(cache or {})
        self._cache_lock = threading.Lock()
        self._delay = interval
        self._info = MouseInfo(message="正在扫描设备…",
                               caps=devcaps.DeviceCaps(source="private"))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._dev: hid_win.HidDevice | None = None
        self._col: hid_win.HidCollection | None = None
        self._identity: dict = {}
        self._last_ok = 0.0
        self._tick = 0
        # 档位换算：状态帧的 [9] 与 0x19 的 1 基档位之间可能差 1（也可能滞后一帧），
        # 用同一时刻的一对读数标定出固定偏移，之后每帧都能本地换算
        self._stage_offset = 0
        self._last_dpi = 0
        # 编辑写回（UI 线程）与轮询（监控线程）共用一条 HID 管道，必须串行
        self._io_lock = threading.RLock()
        self._caps = devcaps.DeviceCaps(source="private")

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mouse-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def refresh(self) -> None:
        self._wake.set()

    def snapshot(self) -> MouseInfo:
        with self._lock:
            info = MouseInfo(**{**self._info.__dict__})
            info.extra = dict(self._info.extra)
            info.caps = self._info.caps.copy()
            return info

    def cache_snapshot(self) -> dict:
        """缓存副本：主线程保存配置时不能直接迭代监控线程正在写的 dict。"""
        with self._cache_lock:
            return dict(self.cache)

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------- 编辑写回（UI 线程调用，私有协议 0x1A / 0x26） ----------
    def _with_dev(self, fn) -> tuple[bool, str] | None:
        with self._io_lock:
            if self._dev is None:
                return None
            return fn(self._dev)

    def set_dpi(self, value: int) -> tuple[bool, str]:
        """写当前 DPI 数值：**只改当前激活那一档的数值**并指向它（0x1A）。

        挡位数量不变——新增挡位只能走挡位面板的「+ 增加挡位」按钮
        （set_dpi_levels）。目标值已在表里时表不动、只切过去。
        """
        value = int(value)
        lo, hi = self._dpi_range()
        if not lo <= value <= hi:
            return False, f"超出设备可调范围（{lo}–{hi} DPI）"

        def do(dev) -> tuple[bool, str]:
            levels = self._transact(dev, P.CMD_GET_DPI, b"", 1.2)
            if not levels:
                return False, "读档位表失败"
            payload = levels[3]
            cur0_dev, cnt, x0, _, xs = P.parse_levels(payload)
            color_of: dict[int, tuple[int, int, int]] = {}
            for i in range(cnt):
                base = 2 + i * 7
                if base + 6 < len(payload):
                    x = payload[base] | (payload[base + 1] << 8)
                    color_of[x] = (payload[base + 4], payload[base + 5], payload[base + 6])
            stages, cur_idx, color_of = P.apply_dpi_edit(xs, cur0_dev, value, color_of)
            body = P.build_dpi_payload(stages, cur_idx, color_of)
            ack = self._transact(dev, P.CMD_SET_DPI, body, 1.2)
            if not ack or ack[1] != 0:
                return False, "设备拒绝了写入"
            time.sleep(0.15)
            back = self._transact(dev, P.CMD_GET_DPI, b"", 1.2)
            if not back:
                return False, "写后校验失败"
            cur2, cnt2, x2, _, xs2 = P.parse_levels(back[3])
            ok = (abs(x2 - value) <= max(2, round(value * 0.02))
                  and len(xs2) == len(stages))   # 挡位数不许变多
            if ok:
                self._refresh_identity(dev, back[3])   # 写完立刻刷新本地档位缓存
            return ok, "" if ok else f"写入未生效（读到 {x2}）"

        res = self._with_dev(do)
        if res is None:
            return False, "设备未连接"
        if res[0]:
            self._wake.set()
        return res

    def set_dpi_levels(self, levels: list[int], cur_dpi: int | None = None) -> tuple[bool, str]:
        """增删档位：整表升序写回（0x1A），当前档指向最接近 cur_dpi 的一档。"""
        # 范围先卡（跟设备型号/档位表对齐），别等拿到设备才说「越界」
        lo, hi = self._dpi_range()
        out_of_range = [int(v) for v in levels if not lo <= int(v) <= hi]
        if out_of_range:
            return False, f"超出设备可调范围（{lo}–{hi} DPI）"

        def do(dev) -> tuple[bool, str]:
            levels_now = self._transact(dev, P.CMD_GET_DPI, b"", 1.2)
            color_of: dict[int, tuple[int, int, int]] = {}
            if levels_now:
                payload = levels_now[3]
                cnt = max(1, min(8, payload[0] if payload else 0))
                for i in range(cnt):
                    base = 2 + i * 7
                    if base + 6 < len(payload):
                        x = payload[base] | (payload[base + 1] << 8)
                        color_of[x] = (payload[base + 4], payload[base + 5], payload[base + 6])
            vals = sorted({int(v) for v in levels
                           if P.DPI_PROTOCOL_MIN <= int(v) <= P.DPI_PROTOCOL_MAX})
            if not vals:
                return False, "档位表为空"
            vals = vals[:P.MAX_DPI_STAGES]
            cur = cur_dpi or (self.cache.get("dpi") or 0)
            cur0 = min(range(len(vals)), key=lambda i: abs(vals[i] - cur)) if cur else 0
            body = P.build_dpi_payload(vals, cur0, color_of)
            ack = self._transact(dev, P.CMD_SET_DPI, body, 1.2)
            if not ack or ack[1] != 0:
                return False, "设备拒绝了写入"
            time.sleep(0.15)
            back = self._transact(dev, P.CMD_GET_DPI, b"", 1.2)
            if not back:
                return False, "写后校验失败"
            ok = P.parse_levels(back[3])[4] == vals
            if ok:
                self._refresh_identity(dev, back[3])   # 写完立刻刷新本地档位缓存
            return ok, "" if ok else "档位表写入未生效"

        res = self._with_dev(do)
        if res is None:
            return False, "设备未连接"
        if res[0]:
            self._wake.set()
        return res

    def set_report_rate(self, hz: int) -> tuple[bool, str]:
        """0x26 SetReportRate：[Io 索引, 0]，写后读 0x25 校验。"""
        if hz not in P.REPORT_RATES:
            return False, "不支持的回报率"
        max_rate = self._caps.rate_values[-1] if self._caps.rate_values else P.REPORT_RATES[-1]
        if hz > max_rate:
            return False, f"超出设备上限 {max_rate} Hz"

        def do(dev) -> tuple[bool, str]:
            ack = self._transact(dev, P.CMD_SET_REPORT_RATE,
                                 bytes([P.REPORT_RATES.index(hz), 0]), 1.2)
            if not ack or ack[1] != 0:
                return False, "设备拒绝了写入"
            time.sleep(0.2)
            back = self._transact(dev, P.CMD_GET_REPORT_RATE, b"", 1.2)
            if not back:
                return False, "写后校验失败"
            ok = P.REPORT_RATES[back[3][0]] == hz if back[3] else False
            return ok, "" if ok else "写入未生效"

        res = self._with_dev(do)
        if res is None:
            return False, "设备未连接"
        if res[0]:
            self._wake.set()
        return res

    def _publish(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._info, k, v)

    # ---------- 主循环 ----------
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if self._dev is None:
                        self._connect()
                    if self._dev is not None:
                        self._poll()
                    else:
                        time.sleep(1.0)
                except Exception as exc:  # noqa: BLE001
                    self._drop(str(exc))
                self._wake.wait(self._delay if self._dev else 2.5)
                self._wake.clear()
        except Exception:  # noqa: BLE001
            log = applog.get("monitor")
            log.exception("轮询线程异常退出")
        finally:
            self._close()

    def _close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001
                pass
        self._dev = None
        self._col = None

    def _drop(self, reason: str) -> None:
        self._close()
        recent = time.time() - self._last_ok < 30
        self._publish(
            connected=False,
            sleeping=False,
            stale=recent and self._info.battery is not None,
            message=reason or "设备已断开",
        )
        time.sleep(0.8)

    # ---------- 连接与识别 ----------
    def _connect(self) -> None:
        try:
            cols = [c for c in hid_win.enumerate_hid() if c.usage_page == P.VENDOR_USAGE_PAGE]
        except Exception as exc:  # noqa: BLE001
            self._publish(connected=False, message=f"枚举设备失败：{exc}")
            return
        if not cols:
            self._publish(connected=False, stale=False, message="未检测到兼容鼠标")
            return

        # 无线已连接的接收器优先
        cols.sort(key=lambda c: (0 if "receiver" in (c.product or "").lower() else 1, c.product_id))
        for col in cols:
            dev = hid_win.HidDevice(col)
            try:
                dev.open()
                time.sleep(0.15)  # 接收器唤醒需要一点时间
                parsed = self._transact(dev, P.CMD_DEVICE_STATUS, b"", 1.2)
                if not parsed:
                    dev.close()
                    continue
                self._dev, self._col = dev, col
                st = P.parse_status(parsed[3])
                self._identity = self._read_identity(dev) if self._is_awake(st) else {}
                if self._identity:
                    self._calibrate_stage(st)   # 拿到 0x19 的档位就先标定一次
                self._last_dpi = st.dpi_x
                self._apply_status(parsed[3], col)
                self._last_ok = time.time()
                return
            except Exception:  # noqa: BLE001
                try:
                    dev.close()
                except Exception:  # noqa: BLE001
                    pass
        self._publish_runtime_sleep("鼠标休眠中，移动即可唤醒")

    def _publish_runtime_sleep(self, message: str) -> None:
        """开机后鼠标还没被唤醒：用缓存数据展示「休眠中」，避免面板一片空白。"""
        if self.cache.get("battery") is None:
            self._publish(connected=False, stale=False, message="鼠标无响应，请轻移鼠标唤醒")
            return
        self._publish(
            connected=True,
            sleeping=True,
            stale=False,
            brand=self._cached("brand", "CROCIRIS"),
            model=self._cached("model", ""),
            name=self._cached("name", ""),
            battery=self._cached("battery"),
            dpi=self._cached("dpi"),
            report_rate=self._cached("report_rate"),
            dpi_level=self._cached("dpi_level", 0),
            dpi_levels=self._cached("dpi_levels", 0),
            firmware=self._cached("firmware", ""),
            serial=self._cached("serial", ""),
            connection=self._cached("connection", "2.4G 无线"),
            is_wireless=True,
            vid=self._cached("vid", 0),
            pid=self._cached("pid", 0),
            charging=False,
            message=message,
        )

    def _read_identity(self, dev: hid_win.HidDevice) -> dict:
        ident: dict = {}
        parsed = self._transact(dev, P.CMD_DEVICE_INFO, b"\x00\x04", 1.2)
        if parsed and len(parsed[3]) >= 6:
            payload = parsed[3]
            mouse_vid = payload[2] | (payload[3] << 8)
            mouse_pid = payload[4] | (payload[5] << 8)
            if mouse_vid and mouse_pid:
                ident["mouse_vid"] = mouse_vid
                ident["mouse_pid"] = mouse_pid
        time.sleep(0.06)
        parsed = self._transact(dev, P.CMD_DEVICE_INFO, b"\x00\x05", 1.2)
        if parsed:
            ident["serial"] = P.decode_string(parsed[3])
        time.sleep(0.06)
        parsed = self._transact(dev, P.CMD_DEVICE_INFO, b"\x00\x01", 1.2)
        if parsed and len(parsed[3]) >= 4:
            ident["firmware"] = _fw(parsed[3][2], parsed[3][3])
        time.sleep(0.06)
        parsed = self._transact(dev, P.CMD_DEVICE_INFO, b"\x01\x01", 1.2)
        if parsed and len(parsed[3]) >= 4:
            ident["receiver_firmware"] = _fw(parsed[3][2], parsed[3][3])
        time.sleep(0.06)
        levels = self._transact(dev, P.CMD_GET_DPI, b"", 1.2)
        if levels:
            current, count, x, y, xs = P.parse_levels(levels[3])
            ident["dpi_level"] = current
            ident["dpi_levels"] = count
            ident["dpi_table"] = xs
        # 最大回报率（0x13 payload [target=0, type=2]；回包前 2 字节是
        # [type, target] 回显，真值在 payload[2]，是 REPORT_RATES 的索引）
        parsed = self._transact(dev, P.CMD_DEVICE_INFO, b"\x00\x02", 1.2)
        if parsed and len(parsed[3]) > 2:
            idx = parsed[3][2]
            if 0 <= idx < len(P.REPORT_RATES):
                ident["rate_max"] = P.REPORT_RATES[idx]
        self._build_caps(ident)
        return ident

    def _build_caps(self, ident: dict) -> None:
        """私有协议写命令（0x1A / 0x26）已按官方 JS 逆向并真机验证 → 可编辑。

        DPI 范围/步进按**型号规格**给（设备没有任何一条命令能读传感器可调
        范围），再用这台设备当前的档位表把边界撑开——规格说 50–64000，
        设备里真存着超出规格的档位，就按档位表算，绝不把合法档位判越界。
        """
        model = self._model_from_ident(ident)
        # 回报率挡位优先用官网写死的型号表：设备回的只是一个「最大挡位索引」，
        # 接收器替鼠标作答时给出的是接收器的上限，换算出来的区间会和实际不符。
        official = devcaps.rate_values_for(model)
        if official:
            rates = list(official)
        else:
            rate_max = ident.get("rate_max") or P.REPORT_RATES[-1]
            rates = [r for r in P.REPORT_RATES if r <= rate_max]
        table = [int(v) for v in (ident.get("dpi_table") or []) if int(v) > 0]
        spec = devcaps.dpi_spec_for(model)
        lo, hi, step = devcaps.resolve_dpi_range(spec, table)
        self._caps = devcaps.DeviceCaps(
            source="private",
            dpi_min=lo,
            dpi_max=hi,
            dpi_step=step,
            dpi_values=sorted(table),
            rate_values=rates,
            dpi_writable=True,
            rate_writable=bool(rates),
            levels_writable=True,
            max_levels=P.MAX_DPI_STAGES,
        )
        # 立刻同步给界面：写回后（_refresh_identity）重建 caps 时不用等下一轮
        self._publish(caps=self._caps.copy())

    @staticmethod
    def _model_from_ident(ident: dict) -> str:
        """从 0x13 type=4 拿到的鼠标 VID/PID 反查型号名。

        用**展示名**而不是短名：短名只有「X7」，X7 Pro Max（PAW3955 Ace，
        原生 40000）和 X7（PAW3950 Ace，原生 30000）会撞在一起。
        """
        vid = int(ident.get("mouse_vid") or 0)
        pid = int(ident.get("mouse_pid") or 0)
        if vid and pid:
            model, name, _kind = P.device_label(vid, pid)
            return name or model
        return ""

    def _dpi_range(self) -> tuple[int, int]:
        """当前设备的 DPI 可调边界；caps 还没建好时退回协议硬边界。"""
        caps = self._caps
        if caps is not None and caps.dpi_min and caps.dpi_max:
            return caps.dpi_min, caps.dpi_max
        return P.DPI_PROTOCOL_MIN, P.DPI_PROTOCOL_MAX

    def _refresh_identity(self, dev, payload: bytes) -> None:
        """写回成功后用刚读回的 0x19 帧同步本地档位缓存（省一次查询）。

        档位表一变，可调边界也可能跟着变（比如刚加进一档 32000，规格表给的
        30000 就撑不住了），所以顺手把 caps 重建一次——纯内存计算，不发包。
        """
        current, count, x, y, xs = P.parse_levels(payload)
        self._identity["dpi_level"] = current
        self._identity["dpi_levels"] = count
        if xs:
            self._identity["dpi_table"] = xs
            self._build_caps(self._identity)

    def _transact(self, dev: hid_win.HidDevice, cmd: int, payload: bytes, timeout: float):
        dev.write(P.build_frame(cmd, payload), 400, report_id=0)
        time.sleep(0.02)
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = dev.read(200)
            if not data:
                continue
            parsed = P.parse_frame(data)
            if parsed and parsed[0] == cmd:
                return parsed
        return None

    # ---------- 档位同步 ----------
    @staticmethod
    def _dpi_match(ref: int, value: int) -> bool:
        if ref <= 0 or value <= 0:
            return False
        return abs(ref - value) <= max(2, round(ref * 0.02))

    def _calibrate_stage(self, st: P.MouseStatus) -> None:
        """标定「状态帧档位字段」到 0x19 的 1 基档位的偏移。

        只在 DPI 没变化的那一轮标定：换档瞬间状态帧的档位字段可能比 DPI 数值
        慢一拍，拿它去标定会把偏移带歪。
        """
        level = self._identity.get("dpi_level") or 0
        if not level:
            return
        self._stage_offset = level - (st.dpi_stage + 1)
        if abs(self._stage_offset) > 3:   # 该字段根本不是档位：不要用它换算
            self._stage_offset = 0

    def _level_for(self, st: P.MouseStatus) -> int:
        """当前 DPI 属于第几档 —— 纯本地换算，和 DPI 数字同一帧出来。

        原来档位取自每 5 轮才查一次的 0x19（2s 一轮 → 最多晚 10s），所以数字
        先到、档位后到。这里优先用状态帧的档位字段（经偏移标定），再用 0x19
        拿到的档位表按 DPI 值反查兜底，两路都不依赖额外的一次查询。
        """
        level = st.dpi_stage + 1 + self._stage_offset
        table = self._identity.get("dpi_table") or []
        if not table:
            return max(1, level)
        count = len(table)
        if 1 <= level <= count and self._dpi_match(table[level - 1], st.dpi_x):
            return level
        # 固件没填满的档位会是 0，别让这些空档参与反查
        candidates = [i for i in range(count) if table[i] > 0]
        if not candidates:
            return max(1, min(count, level))
        best = min(candidates, key=lambda i: abs(table[i] - st.dpi_x))
        if self._dpi_match(table[best], st.dpi_x):
            return best + 1
        return max(1, min(count, level))

    def _refresh_levels(self, st: P.MouseStatus | None = None, calibrate: bool = False) -> int:
        """查一次 0x19：刷新档位总数 / 每档 DPI 表，需要时顺便标定偏移。"""
        if self._dev is None:
            return 0
        levels = self._transact(self._dev, P.CMD_GET_DPI, b"", 1.2)
        if not levels:
            return 0
        current, count, _, _, xs = P.parse_levels(levels[3])
        self._identity["dpi_level"] = current
        self._identity["dpi_levels"] = count
        if xs:
            self._identity["dpi_table"] = xs
        if calibrate and st is not None:
            self._calibrate_stage(st)
        return current

    # ---------- 轮询 ----------
    def _poll(self) -> None:
        with self._io_lock:
            assert self._dev is not None
            parsed = self._transact(self._dev, P.CMD_DEVICE_STATUS, b"", 1.2)
        if not parsed:
            if time.time() - self._last_ok > 6:
                self._drop("设备休眠或已断开")
            else:
                self._publish(stale=True)
            return
        self._last_ok = time.time()
        self._tick += 1
        st = P.parse_status(parsed[3])
        changed = bool(self._last_dpi) and st.dpi_x != self._last_dpi
        level = 0
        if self._is_awake(st):
            with self._io_lock:
                if not self._identity or self._tick % 20 == 0:
                    self._identity = self._read_identity(self._dev)
                    if not changed:
                        self._calibrate_stage(st)
                if changed or self._tick % 5 == 0:
                    # 换档当轮就把档位问清楚：0x19 只用 ~30ms，赶得及和数字一起画出来
                    current = self._refresh_levels(st, calibrate=not changed)
                    if changed:
                        level = current
        self._last_dpi = st.dpi_x
        self._apply_status(parsed[3], self._col, level=level or None)

    @staticmethod
    def _is_awake(st: P.MouseStatus) -> bool:
        """接收器在鼠标休眠时会回一帧全 0 的状态。"""
        return st.battery > 0 or st.dpi_x > 0

    def _cached(self, key: str, default=None):
        return self.cache.get(key, default)

    def _apply_status(self, payload: bytes, col: hid_win.HidCollection | None,
                      level: int | None = None) -> None:
        st = P.parse_status(payload)
        if not self._is_awake(st):
            self._delay = max(5.0, self.interval * 2.5)
            self._publish(
                connected=True,
                sleeping=True,
                stale=False,
                brand=self._cached("brand", (col.manufacturer if col else "") or "CROCIRIS"),
                model=self._cached("model", ""),
                name=self._cached("name", "") or (col.product if col else ""),
                battery=self._cached("battery"),
                dpi=self._cached("dpi"),
                report_rate=self._cached("report_rate"),
                dpi_level=self._cached("dpi_level", 0),
                dpi_levels=self._cached("dpi_levels", 0),
                firmware=self._cached("firmware", ""),
                serial=self._cached("serial", ""),
                connection=self._cached("connection", "2.4G 无线"),
                is_wireless=True,
                vid=self._cached("vid", col.vendor_id if col else 0),
                pid=self._cached("pid", col.product_id if col else 0),
                charging=False,
                message="鼠标休眠中，移动即可唤醒",
                extra={"receiver": col.product if col else ""},
            )
            return
        self._delay = self.interval
        mouse_vid = self._identity.get("mouse_vid") or (col.vendor_id if col else 0)
        mouse_pid = self._identity.get("mouse_pid") or (col.product_id if col else 0)
        model, name, kind = P.device_label(mouse_vid, mouse_pid, col.product if col else "")

        is_wireless = st.connection_mode == 1
        if is_wireless:
            connection = "2.4G 无线"
        elif col and "receiver" in (col.product or "").lower():
            connection = "接收器 · 有线"
        else:
            connection = "USB 有线"

        receiver = ""
        if col and "receiver" in (col.product or "").lower():
            receiver = col.product

        # 档位和 DPI 数值出自同一个状态帧：同一次刷新一起变，不再等 0x19
        lvl = level or self._level_for(st)
        lvls = self._identity.get("dpi_levels", 0)
        table = sorted(self._identity.get("dpi_table") or [])

        with self._cache_lock:
            self.cache.update(
                brand=(col.manufacturer if col and col.manufacturer else "CROCIRIS"),
                model=model,
                name=name,
                battery=st.battery,
                dpi=st.dpi_x or None,
                report_rate=st.report_rate,
                dpi_level=lvl,
                dpi_levels=lvls,
                firmware=self._identity.get("firmware", ""),
                serial=self._identity.get("serial", ""),
                connection=connection,
                vid=mouse_vid,
                pid=mouse_pid,
            )
        self._publish(
            connected=True,
            sleeping=False,
            stale=False,
            brand=(col.manufacturer if col and col.manufacturer else "CROCIRIS"),
            model=model,
            name=name,
            battery=st.battery,
            charging=st.charging,
            full=st.full,
            dpi=st.dpi_x or None,
            dpi_level=lvl,
            dpi_levels=lvls,
            report_rate=st.report_rate,
            connection=connection,
            is_wireless=is_wireless,
            firmware=self._identity.get("firmware", ""),
            receiver_firmware=self._identity.get("receiver_firmware", ""),
            serial=self._identity.get("serial", ""),
            vid=mouse_vid,
            pid=mouse_pid,
            updated_at=time.time(),
            message="",
            caps=self._caps.copy(),
            dpi_table=table,
            extra={"receiver": receiver, "kind": kind},
        )


class LogitechMonitor:
    """罗技鼠标（HID++ 2.0）的采集线程。

    接口与 MouseMonitor 完全一致，主窗口只认 snapshot()/start()/stop()，
    这样才能用同一个 DeviceHub 把两套协议拼起来。
    """

    def __init__(self, interval: float = 2.0, cache: dict | None = None) -> None:
        self.interval = interval
        self.cache: dict = dict(cache or {})
        self._info = MouseInfo(brand="Logitech", message="正在扫描设备…")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._dev = None
        self._key = ""
        # HID 读写必须串行：后台线程每 2 秒 poll 一次，编辑写回从 UI 线程来，
        # 两条流交错会把对方等的那帧回包读走（或清空），所以共用一把锁
        self._io_lock = threading.RLock()
        self._caps = devcaps.DeviceCaps(source="hidpp")
        # 连续几轮"一个字段都读不到"。拔线后罗技的读写全是静默失败，不给异常，
        # 只能靠这个计数判定掉线（单轮失败可能只是设备正在唤醒，不急着判死）。
        self._empty_polls = 0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="logi-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def refresh(self) -> None:
        # 手动点刷新（UI 线程）时先做一次即时的在位检查：拔掉的罗技必须马上
        # 从设备列表里消失，不能干等下一轮 2 秒轮询（用户报过「点了刷新没用」）。
        # 这里只改对外状态，不关句柄——关句柄要抢 IO 锁，交给轮询线程自己收。
        if self._dev is not None and not self._present():
            self._publish(connected=False, sleeping=False, stale=False,
                          message="罗技鼠标已断开")
        self._wake.set()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> MouseInfo:
        with self._lock:
            info = MouseInfo(**{**self._info.__dict__})
            info.extra = dict(self._info.extra)
            info.caps = self._info.caps.copy()
            return info

    def cache_snapshot(self) -> dict:
        return dict(self.cache)

    def _publish(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._info, k, v)

    # ---------- 主循环 ----------
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if self._dev is None:
                        self._connect()
                    if self._dev is not None:
                        self._poll()
                except Exception:  # noqa: BLE001
                    self._drop()
                self._wake.wait(self.interval)
                self._wake.clear()
        finally:
            self._close()

    def _close(self) -> None:
        with self._io_lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                except Exception:  # noqa: BLE001
                    pass
            self._dev = None
            self._key = ""
            self._empty_polls = 0

    def _drop(self) -> None:
        self._close()
        self._publish(connected=False, sleeping=False, stale=False,
                      message="罗技鼠标已断开")

    def _connect(self) -> None:
        try:
            cols = logitech.enumerate_mice()
        except Exception:  # noqa: BLE001
            cols = []
        if not cols:
            self._publish(connected=False, stale=False, message="未检测到罗技鼠标")
            return
        receiver_only = False
        for col in cols:
            dev = logitech.LogitechMouse(col)
            try:
                with self._io_lock:
                    dev.open()
                    if dev.receiver_only():
                        # 接收器在线但后面没有鼠标：别把接收器当鼠标读成一台设备
                        receiver_only = True
                        raise OSError("接收器后面没有鼠标")
                    data = dev.read()
                    if self._looks_empty(data):
                        # 刚上电 / 刚从休眠回来的设备可能第一轮还没准备好
                        time.sleep(0.12)
                        data = dev.read()
                    if self._looks_empty(data):
                        raise OSError("设备无响应")
                    caps = dev.capabilities()   # 只在连接时读一次，范围不会变
            except Exception:  # noqa: BLE001
                dev.close()
                continue
            self._dev = dev
            self._key = f"{col.vendor_id:04X}:{col.product_id:04X}"
            self._caps = caps
            self._apply(data)
            return
        if receiver_only:
            self._publish(connected=False, stale=False,
                          message="接收器已连接，未找到鼠标")
            return
        self._publish(connected=False, stale=False, message="罗技鼠标无响应")

    @staticmethod
    def _looks_empty(data: dict) -> bool:
        """整轮一个字段都没读到：设备名空 + DPI 读不到，基本就是通讯断了。

        设备名是「接收器」也算空——那台东西没有 DPI、没有电池，把它当成一台
        鼠标发布出去，界面上就会出现一台型号叫 "USB Receiver" 的幽灵设备。
        """
        name = str(data.get("name") or "")
        if logitech.LogitechMouse._is_receiver(name):
            return True
        return not name and data.get("dpi") is None

    def _present(self) -> bool:
        """手上的设备路径还挂着吗；查不出来就当在位，别误判掉线。"""
        dev = self._dev
        if dev is None:
            return False
        try:
            return hid_win.device_present(dev.col.path)
        except Exception:  # noqa: BLE001
            return True

    def _poll(self) -> None:
        if self._dev is None:
            return
        # 判据一：设备路径还在不在。拔线后 Windows 不给我们任何异常，
        # 对失效句柄的读写只是静默失败，所以每轮先问一次系统。
        if not self._present():
            raise OSError("罗技鼠标已拔出")
        with self._io_lock:
            data = self._dev.read()
        # 判据二：整轮一个字段都没读到。路径检查偶尔会被共享阻塞骗过，
        # 数据全空是更硬的证据；连错两轮才判死，单轮留给唤醒中的设备。
        if self._looks_empty(data):
            self._empty_polls += 1
            if self._empty_polls >= 2:
                raise OSError("罗技鼠标无响应")
            self._publish(stale=True)
            return
        self._empty_polls = 0
        self._apply(data)

    # ---------- 编辑写回（UI 线程调用） ----------
    def set_dpi(self, value: int) -> tuple[bool, str]:
        caps = self._caps
        if caps is not None and caps.dpi_min and caps.dpi_max:
            if not caps.dpi_min <= value <= caps.dpi_max:
                return False, f"超出设备可调范围（{caps.dpi_min}–{caps.dpi_max} DPI）"
        with self._io_lock:
            if self._dev is None:
                return False, "设备未连接"
            ok = self._dev.set_dpi(value)
        if ok:
            self._wake.set()      # 立刻刷新一次，别等下一轮 2 秒
        return ok, "" if ok else "写入未生效，可能被官方驱动覆盖"

    def set_report_rate(self, hz: int) -> tuple[bool, str]:
        with self._io_lock:
            if self._dev is None:
                return False, "设备未连接"
            ok = self._dev.set_report_rate(hz)
        if ok:
            self._wake.set()
        return ok, "" if ok else "写入未生效，可能被官方驱动覆盖"

    def set_dpi_levels(self, levels: list[int],
                       cur_dpi: int | None = None) -> tuple[bool, str]:
        """挡位增删：板载 profile 扇区写 + G HUB 配置库同步（尽力而为）。"""
        with self._io_lock:
            if self._dev is None:
                return False, "设备未连接"
            ok = self._dev.set_dpi_levels(levels)
        if not ok:
            return False, "板载档位写入未生效"
        notes = []
        db_ok, db_msg = ghub.write_levels(levels, active_dpi=cur_dpi, cur_dpi=cur_dpi)
        if not db_ok:
            notes.append(f"G HUB 未同步（{db_msg}）")
        elif self._ghub_running():
            notes.append("重启 G HUB 后它才会显示新档位")
        self._wake.set()
        return True, "；".join(notes)

    @staticmethod
    def _ghub_running() -> bool:
        import subprocess
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq lghub_agent.exe", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=5,
                encoding="mbcs", errors="replace").stdout
            return "lghub_agent" in out
        except Exception:  # noqa: BLE001
            return False

    def _apply(self, data: dict) -> None:
        self.cache.update(
            brand="Logitech",
            model=data["model"],
            name=data["model"],
            battery=data["battery"],
            dpi=data["dpi"],
            report_rate=data["report_rate"],
            dpi_level=data.get("dpi_level", 0),
            dpi_levels=data.get("dpi_levels", 0),
            connection=data["connection"],
            vid=data["vid"],
            pid=data["pid"],
        )
        self._publish(
            connected=True,
            sleeping=False,
            stale=False,
            brand="Logitech",
            model=data["model"],
            name=data["model"],
            battery=data["battery"],
            charging=data["charging"],
            full=False,
            dpi=data["dpi"],
            dpi_level=data.get("dpi_level", 0),
            dpi_levels=data.get("dpi_levels", 0),
            report_rate=data["report_rate"],
            connection=data["connection"],
            is_wireless=data["is_wireless"],
            vid=data["vid"],
            pid=data["pid"],
            updated_at=time.time(),
            message="",
            caps=self._caps,
            dpi_table=sorted(data.get("dpi_table") or []),
            extra={"receiver": "", "kind": "logitech"},
        )


class BrandHidMonitor:
    """只能「认出来」的品牌鼠标（狼途这类没有厂商命令通道的设备）。

    接口与 MouseMonitor / LogitechMonitor 完全一致，`DeviceHub` 才能把三种
    设备拼成一个对外接口。

    这类鼠标在 HID 里只有一条标准鼠标集合：没有厂商用途页 → 没有一个能下发
    命令的报表；连读都被系统挡住了（mouhid 独占，`CreateFile` 要读权限直接
    ERROR_ACCESS_DENIED，只能以 0 权限打开来问型号）。所以这里**不猜、不伪造
    数据**：DPI / 回报率一律留空，caps 全 False（界面据此挂「按键调节」），
    值只能靠用户在鼠标上按按键调。
    """

    def __init__(self, interval: float = 2.0, cache: dict | None = None) -> None:
        self.interval = interval
        self.cache: dict = dict(cache or {})
        self._info = MouseInfo(brand="", is_wireless=False,
                               message="正在扫描设备…",
                               caps=devcaps.DeviceCaps(source="hw"))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._col: hid_win.HidCollection | None = None
        self._last_ok = 0.0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="brand-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def refresh(self) -> None:
        self._wake.set()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> MouseInfo:
        with self._lock:
            info = MouseInfo(**{**self._info.__dict__})
            info.extra = dict(self._info.extra)
            info.caps = self._info.caps.copy()
            return info

    def cache_snapshot(self) -> dict:
        return dict(self.cache)

    def _publish(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._info, k, v)

    # ---------- 主循环 ----------
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if self._col is None:
                        self._connect()
                    elif not self._present():
                        self._drop()
                    else:
                        self._touch()
                except Exception:  # noqa: BLE001
                    self._drop()
                self._wake.wait(self.interval)
                self._wake.clear()
        except Exception:  # noqa: BLE001
            applog.get("monitor").exception("品牌鼠标轮询线程异常退出")

    def _drop(self) -> None:
        self._col = None
        self._publish(connected=False, sleeping=False, stale=False,
                      message="设备已断开", dpi=None, report_rate=None,
                      battery=None, dpi_level=0, dpi_levels=0)

    def _present(self) -> bool:
        col = self._col
        if col is None:
            return False
        try:
            return hid_win.device_present(col.path)
        except Exception:  # noqa: BLE001
            return True          # 查不出来就当在位，别误判掉线

    # ---------- 识别 ----------
    def _connect(self) -> None:
        try:
            cols = [c for c in hid_win.enumerate_hid()
                    if c.usage_page == 0x0001 and c.usage == 0x0002]
        except Exception as exc:  # noqa: BLE001
            self._publish(connected=False, message=f"枚举设备失败：{exc}")
            return
        for col in cols:
            ident = hidbrands.identify(col.vendor_id, col.product_id, col.product)
            if not ident:
                continue
            # 键盘/耳机也会挂标准鼠标集合（血手 B930 就带一个），只认鼠标品类，
            # 否则列表里会多出一台假鼠标，后面还挂着 DPI 卡片
            if ident.get("kind", "mouse") != "mouse":
                continue
            self._col = col
            self._last_ok = time.time()
            self._apply(col, ident)
            return
        self._publish(connected=False, stale=False, sleeping=False,
                      message="未检测到兼容鼠标", dpi=None, report_rate=None,
                      battery=None)

    def _touch(self) -> None:
        """在位轮询：只更新时间戳，值不会变（这套设备本来就读不到数值）。"""
        self._last_ok = time.time()
        self._publish(updated_at=self._last_ok)

    def _apply(self, col: hid_win.HidCollection, ident: dict) -> None:
        self.cache.update(
            brand=ident["brand"], model=ident["model"], name=ident["model"],
            connection="USB 有线", vid=ident["vid"], pid=ident["pid"],
            battery=None, dpi=None, report_rate=None,
            dpi_level=0, dpi_levels=0,
        )
        self._publish(
            connected=True,
            sleeping=False,
            stale=False,
            brand=ident["brand"],
            model=ident["model"],
            name=ident["model"],
            battery=None,
            charging=False,
            full=False,
            dpi=None,
            dpi_level=0,
            dpi_levels=0,
            report_rate=None,
            connection="USB 有线",
            is_wireless=False,
            firmware="",
            receiver_firmware="",
            serial=(col.serial or ""),
            vid=ident["vid"],
            pid=ident["pid"],
            updated_at=time.time(),
            message="",
            caps=devcaps.DeviceCaps(source="hw"),
            dpi_table=[],
            extra={"receiver": "", "kind": "basic", "product": ident["product"]},
        )

    # ---------- 写回（这套设备没有通道，一律拒绝） ----------
    @staticmethod
    def _no_channel() -> tuple[bool, str]:
        return False, "这台鼠标没有软件调节通道"

    def set_dpi(self, value: int) -> tuple[bool, str]:
        return self._no_channel()

    def set_report_rate(self, hz: int) -> tuple[bool, str]:
        return self._no_channel()

    def set_dpi_levels(self, levels: list[int],
                       cur_dpi: int | None = None) -> tuple[bool, str]:
        return self._no_channel()


class DeviceHub:
    """把「西伯利亚（CROCIRIS）」「罗技」「只能认人的品牌鼠标」拼成一个对外接口。

    主窗口只跟这个对象打交道：``snapshot()`` 永远返回**当前选中**的那台；
    选中的那台拔掉了就临时让位给另一台（选择本身不丢，插回来还能回到原来那台）。
    """

    CROCIRIS = "crociris"
    LOGITECH = "logitech"
    #: 没有私有协议、只能认出来的品牌鼠标（狼途这类），见 `BrandHidMonitor`
    BASIC = "basic"

    def __init__(self, interval: float = 2.0, cache: dict | None = None,
                 selected: str = CROCIRIS) -> None:
        # 每台设备的缓存必须各存一份。以前两边共用同一个 dict，罗技先把自己
        # 写进去之后，西伯利亚那条也会读到 brand="Logitech"、model="G502 HERO"，
        # 设备列表里于是出现两行一模一样的罗技（真机截图撞见过）。
        raw = dict(cache or {})
        cro = raw.get(self.CROCIRIS)
        logi = raw.get(self.LOGITECH)
        basic = raw.get(self.BASIC)
        if not isinstance(cro, dict):
            # 旧格式：整个 cache 就是一个平面 dict，里面只有西伯利亚那一套
            cro = {k: v for k, v in raw.items()
                   if k not in (self.CROCIRIS, self.LOGITECH, self.BASIC)}
        if not isinstance(logi, dict):
            logi = {}
        if not isinstance(basic, dict):
            basic = {}
        self.sources: dict[str, object] = {
            self.CROCIRIS: MouseMonitor(interval, dict(cro)),
            self.LOGITECH: LogitechMonitor(interval, dict(logi)),
            self.BASIC: BrandHidMonitor(interval, dict(basic)),
        }
        self.selected = selected if selected in self.sources else self.CROCIRIS

    # ---------- 代理给主窗口的接口 ----------
    @property
    def cache(self) -> dict:
        return self.sources[self.selected].cache

    def start(self) -> None:
        for src in self.sources.values():
            src.start()

    def stop(self) -> None:
        for src in self.sources.values():
            src.stop()

    def refresh(self) -> None:
        for src in self.sources.values():
            src.refresh()

    def set_dpi(self, value: int) -> tuple[bool, str]:
        """把新 DPI 写到当前显示的那台鼠标；不支持写的设备返回说明文案。"""
        return self.sources[self.current_key()].set_dpi(value)

    def set_report_rate(self, hz: int) -> tuple[bool, str]:
        return self.sources[self.current_key()].set_report_rate(hz)

    def set_dpi_levels(self, levels: list[int],
                       cur_dpi: int | None = None) -> tuple[bool, str]:
        return self.sources[self.current_key()].set_dpi_levels(levels, cur_dpi)

    def is_alive(self) -> bool:
        return any(src.is_alive() for src in self.sources.values())

    def cache_snapshot(self) -> dict:
        """按设备分开存：{crociris: {...}, logitech: {...}, basic: {...}}。"""
        return {
            self.CROCIRIS: self.sources[self.CROCIRIS].cache_snapshot(),
            self.LOGITECH: self.sources[self.LOGITECH].cache_snapshot(),
            self.BASIC: self.sources[self.BASIC].cache_snapshot(),
        }

    # ---------- 选择 ----------
    def select(self, key: str) -> None:
        if key in self.sources:
            self.selected = key

    def current_key(self) -> str:
        """当前实际显示的那台：选中的在就用选中的，否则用另一台。"""
        if self.sources[self.selected].snapshot().connected:
            return self.selected
        for key, src in self.sources.items():
            if key != self.selected and src.snapshot().connected:
                return key
        return self.selected

    def snapshot(self) -> MouseInfo:
        info = self.sources[self.current_key()].snapshot()
        info.extra = dict(info.extra)
        info.extra["device_key"] = self.current_key()
        info.extra["selected_key"] = self.selected
        return info

    def devices(self) -> list[dict]:
        """给标题菜单用：有哪些设备、哪台在线、哪台正在显示。

        名单里用 `name or model`（和标题行同一个 `display_model`）：
        CROCIRIS 的 model 是 "X7"、完整名是 "X7 Ultra Max"，只拼 model 会
        把尾巴丢掉——用户报过「型号没有显示完整」。
        """
        rows = []
        for key, src in self.sources.items():
            info = src.snapshot()
            shown = (info.name or info.model or "").strip()
            brand = i18n.T(info.brand) if info.brand else ""
            if key == self.CROCIRIS:
                label = f"{brand} {shown}".strip() if shown else "CROCIRIS 接收器"
            elif key == self.LOGITECH:
                label = f"{brand} {shown}".strip() if shown else "罗技鼠标"
            else:
                label = f"{brand} {shown}".strip() if shown else "品牌鼠标"
            rows.append({
                "key": key,
                "label": label,
                # 拆开给菜单排版用：品牌和型号在列表里也分两档颜色，
                # 和标题栏同一套读法（品牌是定语、型号是主角）
                "brand": brand,
                "model": shown,
                "available": bool(info.connected),
                "current": key == self.current_key(),
                # 菜单右侧的百分比标识：有线机型 / 未连接时读不到电量，给 None
                "battery": info.battery if info.connected else None,
                # 纯有线（USB 供电）：菜单右侧写「USB供电」而不是百分比——
                # 罗技有线机型这里会报一个 100，那是兜底值，不是真实电量
                "wired": bool(info.connected and not info.is_wireless),
            })
        return rows
