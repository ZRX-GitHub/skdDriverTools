"""罗技鼠标（HID++ 2.0）数据读取。

和 CROCIRIS 那套私有协议完全不同：罗技用的是 HID++ 2.0，报文走
**usage page 0xFF00 / usage 0x02 的长报文接口**（报告 ID 0x11，固定 20 字节）：

    下发  [0x11] dev_idx  feat_idx  func<<4|sw_id  params...（补零到 20）
    上报  [0x11] dev_idx  feat_idx  func<<4|sw_id  params...

设备索引（dev_idx）：有线直连固定 0xFF；经 LIGHTSPEED / Bolt / Unifying
接收器的无线鼠标挂在 1–6 号位，0xFF 那一路只会问到接收器本身。所以开局先
探一遍（见 `_ensure_index`），探不到就退回 0xFF。要读某个「特征」（feature）
先得拿它的**索引**：向根特征（索引 0x00）调 GetFeature，参数是 2 字节的特征号。

实测坑（G502 HERO，PID C08B）：这只鼠标的特征号是**大端**的——要发
``[0x22, 0x01]`` 才能拿到 0x2201（Adjustable DPI）的索引，按标准 HID++
的写法发小端 ``[0x01, 0x22]`` 会全部返回 0（表现成「什么都不支持」）。
所以这里两种顺序都试，大端优先，结果缓存起来。

电量：有线鼠标没有电池特征（0x1004 / 0x1000），按产品要求直接显示 100%。
回报率：仅有线机型固定 1000 Hz，0x8060 特征在这只鼠标上返回 0x8B（不是频率），
因此只有当返回值落在已知频率集合里才采信，否则回落到机型默认。
DPI 档位：0x2201 func1 上报的是**传感器能力**（100–25600 每 50 一档，共 511
档），不是 G HUB 里配的那几档；板载配置（0x8100 Onboard Profiles）里只有
出厂档位，G HUB 的「桌面: 默认」这类软件 profile 根本不写进鼠标。所以档位
优先读 G HUB 本地配置（见 ghub.py），其次板载 profile；两者都拿不到就
不给档位数，绝不拿 511 档能力表冒充。
"""
from __future__ import annotations

import struct
import time

import devcaps
import ghub
import hid_win

LOGITECH_VID = 0x046D
VENDOR_USAGE_PAGE = 0xFF00
USAGE_LONG = 0x02
REPORT_LONG = 0x11
SW_ID = 0x08

#: 特征号
FEAT_ROOT = 0x0000
FEAT_DEVICE_INFO = 0x0003
FEAT_DEVICE_NAME = 0x0005
FEAT_BATTERY_LEGACY = 0x1000
FEAT_BATTERY_UNIFIED = 0x1004
FEAT_DPI = 0x2201
FEAT_REPORT_RATE = 0x8060
FEAT_ONBOARD_PROFILES = 0x8100

#: 已知回报率（Hz）。读到别的值就当这特征不是回报率，别硬套
KNOWN_RATES = (125, 250, 500, 1000, 2000, 4000, 8000)

#: 有线机型没有电池特征时给的默认值
WIRED_BATTERY = 100

#: 产品名里带这些词的是无线（接收器 / LIGHTSPEED）
_WIRELESS_HINTS = ("receiver", "lightspeed", "wireless", "蓝牙", "bluetooth")


def _looks_wireless(product: str, has_battery: bool) -> bool:
    if has_battery:
        return True
    low = (product or "").lower()
    return any(h in low for h in _WIRELESS_HINTS)


def short_model(product: str) -> str:
    """把产品串收拾成型号：'G502 HERO Gaming Mouse' → 'G502 HERO'。"""
    text = (product or "").strip()
    for suffix in (" Gaming Mouse", " Gaming", " Mouse", " 鼠标"):
        if text.lower().endswith(suffix.lower()):
            text = text[: -len(suffix)].strip()
    return text


def parse_dpi_list(params: bytes | None) -> list[int]:
    """解析 0x2201 func1（getSensorDpiList）的参数区，得到支持的 DPI 全集。

    线格式（OpenLogi 文档，与 Solaar 交叉核对过）：byte0 是回显的 sensor
    index，后面最多 7 个**大端** u16；0x0000 终止列表。高 3 位为 0b111 的字
    不是 DPI，是**等差范围标记**：低 13 位是步进，前一个字是起点、后一个字
    是终点——G502 HERO 回的就是 ``100, [step=50], 25600``，即 100–25600、
    每 50 一档（共 511 档），展开后排序去重。
    """
    if not params or len(params) < 3:
        return []
    words: list[int] = []
    for i in range(1, min(len(params), 15) - 1, 2):
        w = (params[i] << 8) | params[i + 1]
        if w == 0:                      # 0x0000 终止
            break
        words.append(w)
    vals: list[int] = []
    i = 0
    while i < len(words):
        w = words[i]
        if (w & 0xE000) == 0xE000 and vals and i + 1 < len(words):
            step = w & 0x1FFF
            start, end = vals[-1], words[i + 1]
            if step and start < end <= 65535:
                seg = list(range(start + step, end + 1, step))
                if seg[-1] != end:
                    seg.append(end)     # 终点不落在步进网格上也收进来
                vals.extend(seg)
                i += 2
                continue
        vals.append(w)
        i += 1
    return sorted({v for v in vals if 1 <= v <= 65535})


def parse_profile_directory(data: bytes | None) -> list[int]:
    """解析 0x8100 板载配置的 profile 目录（扇区 0），得到 profile 扇区号。

    每 4 字节一条：``[扇区号(大端 u16)][标志(2 字节)]``，扇区号 0xFFFF 表示
    目录结束。G502 HERO 实测返回 1..5 共 5 条，profile i 存在扇区 i。
    """
    if not data or len(data) < 4:
        return []
    out: list[int] = []
    for i in range(0, len(data) - 1, 4):
        v = (data[i] << 8) | data[i + 1]
        if v == 0xFFFF:
            break
        if v:
            out.append(v)
    return out[:8]


def parse_profile_dpi(data: bytes | None) -> list[int]:
    """解析 256 字节板载 profile 头部，得到配置过的 DPI 档位（升序去零）。

    布局（libratbag 逆向 + G502 真机核对）：``data[0]``=回报率分母
    （1000/ms），``data[1]``=默认档索引，``data[2]``=切换档索引，
    ``data[3..12]`` 是 5 个**小端** u16 的 DPI 槽位——0 表示该槽没配置，
    G HUB 里配了几个档这里就有几个非零槽。G502 当前 profile 实测
    ``[600, 0, 0, 0, 0]``，与 G HUB 滑块上唯一的 600 一致。
    """
    if not data or len(data) < 13:
        return []
    slots = [struct.unpack_from("<H", data, 3 + 2 * i)[0] for i in range(5)]
    return sorted(v for v in slots if 50 <= v <= 40000)


class LogitechMouse:
    """单个罗技 HID++ 设备的读客户端。用多少读多少，不常驻缓存。"""

    def __init__(self, col: hid_win.HidCollection) -> None:
        self.col = col
        self.dev = hid_win.HidDevice(col)
        self._feat_cache: dict[int, int] = {}
        self._order = "be"       # 特征号字节序：先大端，失败再改小端
        # 板载 profile 的 DPI 档位（出厂配置，G HUB 软件 profile 不写进来）。
        # 读一次要翻 6 个扇区近百条报文，必须缓存；配置变更极低频，过期无妨
        self._onboard_levels: list[int] | None = None
        # 设备名（0x0005）读一次就能用到底，别每轮重问
        self._name_cache: str | None = None
        #: 报文该发给哪个设备索引：有线 0xFF，接收器机型 1–6（开局探一次）
        self.dev_idx = 0xFF
        self._idx_probed = False

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> None:
        self.dev.open()
        time.sleep(0.05)
        self._ensure_index()

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 收发
    def _call(self, feat_idx: int, func: int, params: bytes = b"", timeout: float = 0.6):
        """发一条请求，返回参数区（bytes）；失败返回 None。"""
        idx = getattr(self, "dev_idx", 0xFF)
        body = bytes([idx, feat_idx, (func << 4) | SW_ID]) + params
        body = body + bytes(max(0, 20 - len(body)))
        # 丢掉上一次残留的回包，避免串包
        for _ in range(4):
            if not self.dev.read(15):
                break
        try:
            self.dev.write(body, 400, report_id=REPORT_LONG)
        except Exception:  # noqa: BLE001
            return None
        deadline = time.time() + timeout
        want = (func << 4) | SW_ID
        while time.time() < deadline:
            data = self.dev.read(90)
            if not data or len(data) < 7:
                continue
            if data[1] != idx:
                continue
            if data[2] == 0xFF:      # 错误回包
                continue
            if data[2] == feat_idx and data[3] == want:
                return bytes(data[4:])
        return None

    # ------------------------------------------------- 设备索引（有线 / 接收器）
    @staticmethod
    def _is_receiver(name: str) -> bool:
        """设备名是接收器本身，不是它后面那只鼠标。"""
        return "receiver" in (name or "").lower()

    def _reset_caches(self) -> None:
        """换了设备索引，之前问到的特征索引和名字全部作废。"""
        self._feat_cache.clear()
        self._name_cache = None
        self._order = "be"

    def _probe_index(self, idx: int) -> str:
        """在这个索引上试一次：读出设备名就返回它，没人应答返回空串。

        先用一条最便宜的根请求探路（0.25s 超时）——接收器上没有配对的位子
        根本不回包，直接用 name() 会让每个空位都拖上一秒多。
        """
        self.dev_idx = idx
        self._reset_caches()
        if self._call(FEAT_ROOT, 0x00, bytes([0x00, FEAT_DEVICE_NAME]), timeout=0.25) is None:
            return ""
        return self.name()

    def _ensure_index(self) -> None:
        """确认报文该发给哪个设备索引，整个连接周期只做一次。

        有线直连（G502 HERO 这类）在 0xFF，一次都不用探。经 LIGHTSPEED /
        Bolt / Unifying 接收器的无线鼠标挂在 1–6 号位，用 0xFF 问到的只是
        接收器自己（设备名带 Receiver、也没有 0x2201 / 0x8060 特征），以前
        硬编码 0xFF 会让这类机型一律「未检测到罗技鼠标」。

        探不到就退回 0xFF，行为与以前完全一致，不会因为探测而更差。
        """
        if getattr(self, "_idx_probed", False):
            return
        self._idx_probed = True
        host = self._probe_index(getattr(self, "dev_idx", 0xFF))
        # 0xFF 上有名字但那是接收器自己 → 插着的是接收器，鼠标可能没开机 / 没配对
        self._host_is_receiver = self._is_receiver(host)
        if host and not self._host_is_receiver:
            return
        for idx in range(1, 7):
            name = self._probe_index(idx)
            if name and not self._is_receiver(name):
                self._host_is_receiver = False   # 接收器后面找到鼠标了
                return
        self.dev_idx = 0xFF
        self._reset_caches()
        if host:                       # 退回 0xFF 时名字还得留着，供上层判接收器
            self._name_cache = host

    def receiver_only(self) -> bool:
        """插着的是接收器、后面没找到鼠标（关着机 / 没配对 / 休眠）。"""
        return bool(getattr(self, "_host_is_receiver", False))

    # ---------------------------------------------------------------- 特征索引
    def feature(self, feature_id: int) -> int:
        """特征号 → 索引；不支持返回 0。"""
        if feature_id in self._feat_cache:
            return self._feat_cache[feature_id]
        idx = 0
        for order in ((self._order,) + (("le",) if self._order == "be" else ("be",))):
            if order == "be":
                params = bytes([feature_id >> 8, feature_id & 0xFF])
            else:
                params = bytes([feature_id & 0xFF, feature_id >> 8])
            res = self._call(0x00, 0x00, params)
            if res is not None and res[0]:
                self._order = order
                idx = res[0]
                break
        self._feat_cache[feature_id] = idx
        return idx

    # ---------------------------------------------------------------- 各项数据
    def name(self) -> str:
        """设备名。0x0005 的 func1 参数是**字节偏移**，每次最多回 16 字节。

        结果缓存：设备名不会变，而 `model()` 和 `read()` 都要用，不缓存的话
        每轮轮询会多打十几次 HID++ 请求。
        """
        cached = getattr(self, "_name_cache", None)
        if cached is not None:
            return cached
        idx = self.feature(FEAT_DEVICE_NAME)
        if not idx:
            self._name_cache = ""
            return ""
        head = self._call(idx, 0x00, b"")          # getDeviceNameCount
        total = head[0] if head and 0 < head[0] else 32
        chunks: list[bytes] = []
        offset = 0
        while offset < min(total, 64):
            part = self._call(idx, 0x01, bytes([offset, 0, 0]))
            if not part:
                break
            chunk = part.rstrip(b"\x00")
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunk) < 16:                    # 最后一截
                break
            offset += 16
        text = b"".join(chunks)[:total].decode("latin-1", "ignore")
        self._name_cache = text.strip()
        return self._name_cache

    def model(self) -> str:
        """型号短名：优先 HID++ 设备名，读不到再用 USB 产品串。

        顺序不能反——无线机型插着接收器时，USB 产品串报的是「USB Receiver」，
        只有 HID++ 能问出鼠标真名（G502 HERO / PRO X SUPERLIGHT 2 …）。
        """
        col = getattr(self, "col", None)
        product = getattr(col, "product", "") or ""
        return short_model(self.name()) or short_model(product)

    def dpi(self) -> int | None:
        idx = self.feature(FEAT_DPI)
        if not idx:
            return None
        res = self._call(idx, 0x02, bytes([0, 0, 0]))
        if not res or len(res) < 3:
            return None
        # 参数区：[sensor][dpi 高][dpi 低][默认高][默认低]
        value = (res[1] << 8) | res[2]
        return value if 50 <= value <= 32000 else None

    def dpi_levels(self, cur: int | None = None) -> tuple[int, int]:
        """返回 (当前第几档, 共几档)，1 起计数；读不到给 (0, 0)。

        「档位」要跟 G HUB 对齐：优先读 G HUB 软件 profile 的 DPI 档位表
        （ghub.py，例如 [600, 1350] → 1350 是「第 2 档 / 共 2 档」），
        其次才是板载 profile（0x8100）里跟当前 DPI 对得上的出厂档位。

        两者都拿不到时返回 (0, 0) 让界面显示「实时读取」：0x2201 func1 的
        100–25600 / 步进 50 共 511 档只是传感器能力，不是用户配的档位数，
        拿它兜底会显示成「第 25 档 / 共 511」这种明显错误的信息。
        """
        if cur is None:
            cur = self.dpi()
        levels = ghub.dpi_levels_for(cur, fallback_readonly=True)
        if levels:
            return self._match_level(levels, cur)
        if self._onboard_levels is None:
            self._onboard_levels = self._read_onboard_levels(cur)
        if self._onboard_levels:
            return self._match_level(self._onboard_levels, cur)
        return 0, 0

    def levels_table(self, cur: int | None = None) -> list[int]:
        """当前生效的档位表（升序）：G HUB 软件配置优先，其次板载 profile。

        和 dpi_levels 一样带 fallback_readonly：G HUB 运行中 db 与设备实时
        值脱节时，挡位条要用设备实际生效的那张表，否则会和展示态档位数对不上。
        """
        if cur is None:
            cur = self.dpi()
        levels = ghub.dpi_levels_for(cur, fallback_readonly=True)
        if levels:
            return sorted(levels)
        if self._onboard_levels is None:
            self._onboard_levels = self._read_onboard_levels(cur)
        return sorted(self._onboard_levels or [])

    @staticmethod
    def _match_level(levels: list[int], cur: int | None) -> tuple[int, int]:
        """在档位列表里定位当前 DPI；偏差 >60 视为对不上，报 (0, 共几档)。"""
        if not levels:
            return 0, 0
        if cur is None:
            return 0, len(levels)
        if cur in levels:
            return levels.index(cur) + 1, len(levels)
        best = min(range(len(levels)), key=lambda i: abs(levels[i] - cur))
        if abs(levels[best] - cur) > 60:     # 不在配置里，别硬报
            return 0, len(levels)
        return best + 1, len(levels)

    def _read_onboard_levels(self, cur: int | None) -> list[int]:
        """从板载配置里找当前 profile 的 DPI 档位；读不到返回 []。

        扇区 0 是 profile 目录（parse_profile_directory），profile 存在
        各自的扇区里。优先读 0x8100 func4 报上来的当前 profile，对不上
        当前 DPI 再逐个翻，哪个 profile 的档位里有当前 DPI 就用哪个。
        """
        idx = self.feature(FEAT_ONBOARD_PROFILES)
        if not idx:
            return []
        directory = self._read_sector(idx, 0x0000)
        if not directory:
            return []
        addrs = parse_profile_directory(directory)
        if not addrs:
            return []
        order: list[int] = []
        res = self._call(idx, 0x04, b"")     # getCurrentProfile
        if res:
            p = res[1] if len(res) > 1 and res[1] else res[0]
            if 1 <= p <= len(addrs):
                order.append(addrs[p - 1])
        order += [a for a in addrs if a not in order]
        for sec in order:
            data = self._read_sector(idx, sec)
            if not data:
                continue
            slots = parse_profile_dpi(data)
            if cur is not None and cur in slots:
                return slots
        return []

    def _read_sector(self, idx: int, sector: int, size: int = 256) -> bytes | None:
        """0x8100 func5 MEMORY_READ：每次 16 字节，凑满一个扇区。

        固件限制：偏移越过 size-16 会回 ERR_INVALID_ARGUMENT，所以尾部
        不足 16 字节时改从 size-16 读（libratbag 同款处理）。
        """
        out = bytearray()
        off = 0
        while off < size:
            o = size - 16 if size - off < 16 else off
            r = self._call(idx, 0x05, struct.pack(">HH", sector, o))
            if not r:
                return None
            out += r[:16]
            off += 16
        return bytes(out)

    def report_rate(self) -> int | None:
        """0x8060 fn1 给的是**回报间隔（ms）**，换算成 Hz。

        fn0 返回的是「支持哪些间隔」的位图（G502 HERO 是 0x8B：1/2/4/8 ms），
        以前把它当频率读，得到 139 这种不认识的值就回落到机型默认了。
        """
        idx = self.feature(FEAT_REPORT_RATE)
        if not idx:
            return None
        res = self._call(idx, 0x01, b"")
        if not res or not res[0]:
            return None
        ms = res[0]
        if 1000 % ms:
            return None
        hz = 1000 // ms
        return hz if hz in KNOWN_RATES else None

    # ------------------------------------------------------- 可调能力与写回
    def _probe_writable(self, idx: int, func: int, params: bytes) -> bool:
        """发一条「写回当前值」的幂等命令，探固件到底让不让写。

        HID++ 错误回包（data[2] == 0xFF）会被 _call 跳过 → None；接受时回显
        同一 func 的参数区。写的是设备当前值，不会改变任何设置。
        G502 HERO 实测：0x2201 fn3 正常回显，0x8060 fn2 一律回 0x02
        INVALID_ARGUMENT —— 所以回报率模块应判定「不支持编辑」。
        """
        for _ in range(2):
            if self._call(idx, func, params, timeout=0.25) is not None:
                return True
        return False

    def capabilities(self) -> devcaps.DeviceCaps:
        """按型号给能力，设备读数只用来放宽边界 / 兜底。

        两条数据源各管一件事：

        * **型号表**（`devcaps.LOGITECH_SPECS`，官方参数页口径）负责「这只鼠标
          理论上多大范围」。0x2201 fn1 在部分固件上回空、老机型没有这个特征、
          无线机型经接收器时还可能被截断，光靠设备会把 25600 显示成 3000。
        * **设备读数**负责「这台机器上实际是什么」。设备档位表里已经存在的
          值只用来扩大边界，不会把合法档位缩掉。

        型号表认不出来的设备完全走设备读数（跟以前一样），不会因为表里没写
        就被当成「不支持」。

        回报率反过来**以设备为准**：0x8060 fn0 的位图是鼠标自己报「当前链路
        支持哪些间隔」，无线接收器不支持 2000 Hz 时会诚实地只报四档；这种
        情况按型号表硬写 2000 会让用户选到一个写不进去的挡位。只有位图读不
        出来时才用型号表兜底。
        """
        caps = devcaps.DeviceCaps(source="hidpp")
        spec = devcaps.logitech_spec_for(self.model())

        dpi_idx = self.feature(FEAT_DPI)
        vals: list[int] = []
        if dpi_idx:
            vals = parse_dpi_list(self._call(dpi_idx, 0x01, bytes([0, 0, 0])))
        if spec is not None:
            caps.dpi_values = vals
            caps.dpi_min, caps.dpi_max, caps.dpi_step = devcaps.resolve_dpi_range(
                spec[:3], vals)
        elif vals:
            caps.dpi_values = vals
            caps.dpi_min, caps.dpi_max, caps.dpi_step = devcaps.range_from_values(vals)

        # 范围能读不代表能写：写命令是否被接受由 _probe_writable 判定
        if caps.dpi_max and dpi_idx:
            cur = self.dpi()
            if cur:
                caps.dpi_writable = self._probe_writable(
                    dpi_idx, 0x03, bytes([0, (cur >> 8) & 0xFF, cur & 0xFF]))

        rate_idx = self.feature(FEAT_REPORT_RATE)
        rates: list[int] = []
        if rate_idx:
            res = self._call(rate_idx, 0x00, b"")
            rates = devcaps.parse_rate_bitmap(res[0]) if res else []
        if not rates and spec is not None:
            rates = list(devcaps.rate_values_up_to(spec[3]))
        caps.rate_values = rates
        if rates and rate_idx:
            now = self._call(rate_idx, 0x01, b"")
            cur_ms = now[0] if now and now[0] else 1
            caps.rate_writable = self._probe_writable(
                rate_idx, 0x02, bytes([cur_ms]))
        # 挡位（DPI 档位表）走 0x8100 板载扇区写，与回报率无关
        if self.feature(FEAT_ONBOARD_PROFILES):
            caps.levels_writable = True
            caps.max_levels = 5
        return caps

    def set_dpi(self, value: int) -> bool:
        """0x2201 fn3 setSensorDpi：[sensor][DPI 高][DPI 低]，写完读回校验。"""
        idx = self.feature(FEAT_DPI)
        # 范围由上层 DeviceCaps（0x2201 fn1 读回来的真实能力）先卡；这里只守
        # 协议能表达的 u16 边界，别再用一个写死的 40000 把 42000 的传感器挡了
        if not idx or not 1 <= value <= 65535:
            return False
        self._call(idx, 0x03, bytes([0, (value >> 8) & 0xFF, value & 0xFF]))
        time.sleep(0.08)
        return self.dpi() == value

    def set_report_rate(self, hz: int) -> bool:
        """0x8060 fn2 setReportRate：[间隔 ms]，写完读回校验。

        G502 HERO 实测（2026-11-05 深挖）：主机模式下固件把这条路锁死——
        fn2 全部 256 个单字节参数一律回 HID++ 错误 0x02，杀掉 G HUB 再试
        依旧拒绝；该集合写长度固定 21 字节、短报告根本发不出去；把新回报率
        写进板载 profile（0x8100 扇区写+CRC，已验证能落盘）再重新激活当前
        profile，实时回报率也不变。回报率是 G HUB 软件配置在驱动的，第三方
        没有可用的实时写入通道 → 上层据此显示「不支持编辑」。
        """
        idx = self.feature(FEAT_REPORT_RATE)
        if not idx or hz not in KNOWN_RATES or 1000 % hz:
            return False
        self._call(idx, 0x02, bytes([1000 // hz]))
        time.sleep(0.12)
        return self.report_rate() == hz

    # ------------------------------------------------------- 板载档位写回
    @staticmethod
    def _crc_ccitt(buf: bytes) -> int:
        """CRC-CCITT(FALSE)：种子 0xFFFF、多项式 0x1021（libratbag 同款）。"""
        crc = 0xFFFF
        for b in buf:
            t = (crc >> 8) ^ b
            crc = (crc << 8) & 0xFFFF
            q = t ^ (t >> 4)
            crc ^= q
            crc ^= (q << 5) & 0xFFFF
            crc ^= (q << 7) & 0xFFFF
            crc &= 0xFFFF
        return crc

    def _current_profile_sector(self) -> tuple[int, int, bytes] | None:
        """(特征索引, 扇区号, 扇区数据)：当前生效 profile 所在的板载扇区。"""
        idx = self.feature(FEAT_ONBOARD_PROFILES)
        if not idx:
            return None
        res = self._call(idx, 0x04, b"")     # getCurrentProfile
        if not res:
            return None
        pnum = res[1] if len(res) > 1 and res[1] else res[0]
        addrs = parse_profile_directory(self._read_sector(idx, 0))
        if not pnum or not addrs or not 1 <= pnum <= len(addrs):
            return None
        sec = addrs[pnum - 1]
        data = self._read_sector(idx, sec)
        if not data or len(data) != 256:
            return None
        return idx, sec, data

    def set_dpi_levels(self, levels: list[int]) -> bool:
        """把档位表写进板载当前 profile 的 5 个 DPI 槽位（升序、写后读回）。

        G HUB 会把软件配置同步进板载内存（真机核对：profile 1 槽位就是
        [600, 1350]），DPI 按键切换的就是这几档。槽位布局：
        data[1]=默认档索引、data[2]=切换档索引、data[3..12]=5 个小端 u16
        （0=空槽），尾部 2 字节是扇区 CRC-CCITT。G502 板载最多 5 档。
        """
        vals = sorted({int(v) for v in levels if 50 <= int(v) <= 40000})[:5]
        if not vals:
            return False
        info = self._current_profile_sector()
        if info is None:
            return False
        idx, sec, orig = info
        data = bytearray(orig)
        if data[1] >= len(vals):     # 默认/切换档索引越界就指到最后一档
            data[1] = len(vals) - 1
        if data[2] >= len(vals):
            data[2] = len(vals) - 1
        slots = bytearray(10)
        for i, v in enumerate(vals):
            slots[2 * i] = v & 0xFF
            slots[2 * i + 1] = (v >> 8) & 0xFF
        data[3:13] = slots
        data[254:256] = self._crc_ccitt(bytes(data[:254])).to_bytes(2, "big")
        try:
            if self._call(idx, 0x06, struct.pack(">HHH", sec, 0, 256)) is None:
                return False
            for off in range(0, 256, 16):
                if self._call(idx, 0x07, bytes(data[off:off + 16])) is None:
                    return False
            self._call(idx, 0x08, b"")           # WRITE_END（短回包可能吃不到）
        except OSError:
            return False
        time.sleep(0.3)
        back = self._read_sector(idx, sec)
        self._onboard_levels = None              # 缓存失效，下轮重读
        return back is not None and back[3:13] == bytes(slots)

    def battery(self) -> tuple[int | None, bool]:
        """返回 (电量, 是否在充电)；有线机型没有电池特征 → 100 / 不充。"""
        idx = self.feature(FEAT_BATTERY_UNIFIED)
        if idx:
            res = self._call(idx, 0x01, b"")   # getBatteryLevelState
            if res and res[0] <= 100:
                status = res[2] if len(res) > 2 else 0
                return res[0], status in (1, 2)   # 1=charging 2=slow charge
        idx = self.feature(FEAT_BATTERY_LEGACY)
        if idx:
            res = self._call(idx, 0x00, b"")
            if res and res[0] <= 100:
                status = res[2] if len(res) > 2 else 0
                return res[0], status in (1, 2, 3)
        return None, False

    # ---------------------------------------------------------------- 汇总
    def read(self) -> dict:
        """一次性把界面要的字段读齐。"""
        product = self.col.product or ""
        level, charging = self.battery()
        wireless = _looks_wireless(product, level is not None)
        full_name = self.name() or product
        rate = self.report_rate()
        if rate is None and not wireless:
            rate = 1000            # 纯有线机型固定 1000Hz
        cur_dpi = self.dpi()       # 只问一次：DPI 数值和档位用同一帧数据
        dpi_lvl, dpi_lvls = self.dpi_levels(cur_dpi)   # 别叫 level——电量也叫这个
        return {
            "name": full_name,
            "model": short_model(full_name) or short_model(product),
            "dpi": cur_dpi,
            "dpi_level": dpi_lvl,
            "dpi_levels": dpi_lvls,
            "dpi_table": self.levels_table(cur_dpi),
            "battery": level if level is not None else WIRED_BATTERY,
            "charging": charging,
            "report_rate": rate,
            "is_wireless": wireless,
            "connection": "2.4G 无线" if wireless else "USB 有线",
            "vid": self.col.vendor_id,
            "pid": self.col.product_id,
        }


def enumerate_mice() -> list[hid_win.HidCollection]:
    """当前插着的罗技鼠标（只看 HID++ 长报文接口，一个设备只留一个）。"""
    try:
        cols = hid_win.enumerate_hid()
    except Exception:  # noqa: BLE001
        return []
    out: list[hid_win.HidCollection] = []
    seen: set[int] = set()
    for col in cols:
        if col.vendor_id != LOGITECH_VID:
            continue
        if col.usage_page != VENDOR_USAGE_PAGE or col.usage != USAGE_LONG:
            continue
        if col.product_id in seen:      # 同一只鼠标会枚举出多条路径
            continue
        seen.add(col.product_id)
        out.append(col)
    return out
