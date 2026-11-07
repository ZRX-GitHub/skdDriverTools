"""CROCIRIS HUB 兼容鼠标的私有 HID 协议（由网页驱动协议逆向得到）。

帧格式（单帧，64 字节，报告 ID 0）::

    下发  0xAA | cmd | len | 0x00 | payload...
    上报  0x55 | cmd | len | status | payload...

电量/状态命令 0x12（GetDeviceStatus）返回::

    [0] 充电中        [1] 电量百分比   [2] 充满
    [3] 连接模式(低4位) / 无线信道(高4位)
    [4] 无线已连接     [8] 回报率档位   [9] DPI 档位
    [10:12] 当前 DPI X（小端）  [12:14] 当前 DPI Y
"""
from __future__ import annotations

from dataclasses import dataclass

HEAD_OUT = 0xAA
HEAD_IN = 0x55
FRAME_LEN = 64

CMD_DEVICE_STATUS = 0x12
CMD_DEVICE_INFO = 0x13
CMD_GET_DPI = 0x19
#: 写命令来自官方网页驱动 JS bundle（hub.crociris.com/static/js/index-*.js，
#: 命令表 oe；2026-11-05 复抓核对，真机幂等写/真实写均验证通过）：
#:   setDpi        payload = [档位数(1..8), 当前档0基, 每档7字节(xLo,xHi,yLo,yHi,r,g,b)]
#:   setReportRate payload = [Io 索引, 0]，Io=[125,250,500,1000,2000,4000,8000]
CMD_SET_DPI = 0x1A
CMD_GET_REPORT_RATE = 0x25
CMD_SET_REPORT_RATE = 0x26

#: DPI 档位数上限（网页驱动 W0 构包器：stages.length 必须 1..8）
MAX_DPI_STAGES = 8
#: 协议层面的 DPI 硬边界。payload 里每档是 u16，设备自己会按传感器能力再卡
#: 一道；这里只防「装不下/负数」这种构包级错误，**不能拿来当界面可调范围**
#: （X7 的 PAW3950 Ace 能到 30000，老代码写死 26000 会把它判越界）。
DPI_PROTOCOL_MIN = 1
DPI_PROTOCOL_MAX = 65535
#: 未收录型号的兜底可调范围（具体型号的规格见 devcaps.DPI_SPECS）
DPI_VALUE_MIN = 100
DPI_VALUE_MAX = 26000

INFO_FIRMWARE = 1
INFO_SERIAL = 5

REPORT_RATES = [125, 250, 500, 1000, 2000, 4000, 8000]

#: 回报率 → 延迟挡位。
#:
#: 一帧的采样间隔就是 1000/Hz 毫秒：125Hz = 8ms、250Hz = 4ms、500Hz = 2ms、
#: 1000Hz = 1ms、2000Hz = 0.5ms、4000Hz = 0.25ms、8000Hz = 0.125ms。
#: 按「人手能感知的差别」分三档：
#:   < 500Hz      一帧 4ms 以上，快速甩枪能看出指针拖尾 → 高延迟
#:   500–1000Hz   1–2ms，主流游戏鼠标的甜点 → 中延迟
#:   >= 2000Hz    半个毫秒以内，基本摸到「无感」 → 低延迟
RATE_LATENCY_TIERS: tuple[tuple[int, str], ...] = (
    (500, "高延迟"),
    (2000, "中延迟"),
    (1 << 30, "低延迟"),
)


def rate_latency(hz: int | None) -> str:
    """回报率（Hz）→ 延迟挡位文案；未知回报率返回空串。"""
    if not hz or hz <= 0:
        return ""
    for limit, label in RATE_LATENCY_TIERS:
        if hz < limit:
            return label
    return RATE_LATENCY_TIERS[-1][1]


def rate_frame_ms(hz: int | None) -> float | None:
    """一帧的间隔（毫秒）。顺手给日志/测试用，不参与界面。"""
    if not hz or hz <= 0:
        return None
    return 1000.0 / hz

#: 网页驱动内置的型号表： (VID, PID) -> (型号, 展示名, 连接方式)
KNOWN_DEVICES: dict[tuple[int, int], tuple[str, str, str]] = {
    (0x36F9, 0xAA10): ("X7", "X7 Gaming Mouse", "usb"),
    (0x36F9, 0xAA20): ("X7", "X7 Receiver 8K", "receiver"),
    (0x36F9, 0xAA23): ("X7", "X7 Pro Max", "usb"),
    (0x36F9, 0xAA25): ("X7", "X7 Ultra Max", "usb"),
    (0x36F9, 0xAA21): ("X7S", "X7S Ultra Max", "usb"),
    (0x36F9, 0xAA22): ("X7S", "X7S Ultra Max Receiver", "receiver"),
    (0x3554, 0x1111): ("M7", "M7 Gaming Mouse", "usb"),
    (0x3554, 0x1112): ("M7", "M7 Receiver", "receiver"),
}

#: 旧版固件（EX02 系）使用的报告 ID
LEGACY_REPORT_ID = 160
LEGACY_CMD_BATTERY = 2
LEGACY_BATTERY_PAYLOAD = [37]

VENDOR_USAGE_PAGE = 0xFF0A
VENDOR_USAGE = 1


@dataclass
class MouseStatus:
    battery: int | None = None
    charging: bool = False
    full: bool = False
    connection_mode: int = 0
    wireless_connected: bool = False
    report_rate: int | None = None
    dpi_stage: int = 0
    dpi_x: int = 0
    dpi_y: int = 0
    raw: bytes = b""

    @property
    def connection_label(self) -> str:
        if self.connection_mode == 1:
            return "无线已连接" if self.wireless_connected else "无线未连接"
        return "USB 有线"


def build_frame(cmd: int, payload: bytes = b"", length: int = FRAME_LEN) -> bytes:
    body = bytes([HEAD_OUT, cmd & 0xFF, len(payload) & 0xFF, 0x00]) + bytes(payload)
    if len(body) > length:
        raise ValueError("payload 过长")
    return body.ljust(length, b"\x00")


def parse_frame(data: bytes) -> tuple[int, int, int, bytes] | None:
    """返回 (cmd, status, length, payload)，自动兼容带/不带报告 ID 的读取结果。"""
    if not data:
        return None
    for offset in (0, 1, 2):
        if len(data) > offset + 4 and data[offset] == HEAD_IN:
            length = data[offset + 2]
            if length > len(data) - offset - 4:
                length = max(0, len(data) - offset - 4)
            return data[offset + 1], data[offset + 3], length, data[offset + 4 : offset + 4 + length]
    return None


def parse_status(payload: bytes) -> MouseStatus:
    def b(i: int) -> int:
        return payload[i] if i < len(payload) else 0

    mode = b(3)
    rate_index = b(8)
    st = MouseStatus(
        battery=min(100, max(0, b(1))),
        charging=b(0) == 1,
        full=b(2) == 1,
        connection_mode=mode & 0x0F,
        wireless_connected=(mode & 0x0F) != 1 or b(4) == 1,
        report_rate=REPORT_RATES[rate_index] if 0 <= rate_index < len(REPORT_RATES) else None,
        dpi_stage=b(9),
        dpi_x=b(10) | (b(11) << 8),
        dpi_y=b(12) | (b(13) << 8),
        raw=bytes(payload),
    )
    return st


def parse_levels(payload: bytes) -> tuple[int, int, int, int, list[int]]:
    """解析 0x19 GetDPI 返回： (档位序号, 档位数量, 当前 X, 当前 Y, 所有档位 X 值)。"""
    count = payload[0] if payload else 0
    count = max(1, min(8, count))
    current = (payload[1] if len(payload) > 1 else 0) + 1
    current = max(1, min(count, current))
    xs: list[int] = []
    ys: list[int] = []
    for i in range(count):
        base = 2 + i * 7
        if base + 1 >= len(payload):
            break
        x = payload[base] | (payload[base + 1] << 8)
        y = payload[base + 2] | (payload[base + 3] << 8) if base + 3 < len(payload) else x
        xs.append(x)
        ys.append(y)
    idx = current - 1
    cur_x = xs[idx] if idx < len(xs) else (xs[0] if xs else 0)
    cur_y = ys[idx] if idx < len(ys) else cur_x
    return current, count, cur_x, cur_y, xs


def decode_string(payload: bytes) -> str:
    """设备信息命令返回的 ASCII 串（开头 2 字节是类型/目标）。"""
    chunk = bytes(payload[2:34]) if len(payload) > 2 else bytes(payload)
    chunk = chunk.rstrip(b"\x00\xff")
    text = bytes(c for c in chunk if 32 <= c <= 126).decode("ascii", "ignore").strip()
    return text


def apply_dpi_edit(xs: list[int], cur_idx1: int, value: int,
                   color_of: dict[int, tuple[int, int, int]] | None = None,
                   ) -> tuple[list[int], int, dict[int, tuple[int, int, int]]]:
    """编辑当前 DPI 数值 → 新挡位表：**只改当前激活那一档的数值**，不新增挡位。

    规矩：挡位数量只能通过挡位面板的「+ 增加挡位」变多。在编辑框里改 DPI
    数值时——
      * 目标值已经在表里 → 表不动，只把当前档指向它（等同切挡）；
      * 不在表里 → 把当前激活档的数值改成目标值（颜色沿用该档旧色），
        其余档保持原样；挡位数量不变。
    返回 (升序去重后的新表, 当前档 0 基索引, 沿用后的颜色表)。
    """
    colors = dict(color_of or {})
    raw = [int(v) for v in xs]
    slots = [v for v in raw if 0 < v <= DPI_PROTOCOL_MAX]
    if not slots:
        return [value], 0, colors
    if value in slots:
        slots = sorted(set(slots))
        return slots, slots.index(value), colors
    # 当前档的定位要在**原表**上做（原表可能含 0 填充槽，过滤后索引会错位）
    cur_val = raw[max(0, min(len(raw) - 1, int(cur_idx1) - 1))] if raw else value
    try:
        i = slots.index(cur_val)
    except ValueError:
        i = len(slots) - 1
    old = slots[i]
    if old in colors:
        colors[value] = colors[old]        # 被改的那档颜色跟着数值走
    slots[i] = value
    slots = sorted(set(slots))
    return slots, slots.index(value), colors


def build_dpi_payload(stages: list[int], current_idx0: int,
                      color_of: dict[int, tuple[int, int, int]] | None = None) -> bytes:
    """组 0x1A SetDpi 帧 payload（档位自动升序，颜色按 DPI 值沿用/缺省白）。

    只按协议的 u16 边界过滤；具体型号能到什么范围由调用方（monitor）用
    `DeviceCaps` 先卡好，这里不再重复用一个写死的上限。
    """
    vals = sorted({int(v) for v in stages
                   if DPI_PROTOCOL_MIN <= int(v) <= DPI_PROTOCOL_MAX})
    if not vals:
        raise ValueError("至少要有一个合法 DPI 档位")
    vals = vals[:MAX_DPI_STAGES]
    body = bytearray([len(vals), 0])
    for v in vals:
        rgb = (color_of or {}).get(v, (255, 255, 255))
        body += bytes([v & 0xFF, (v >> 8) & 0xFF, v & 0xFF, (v >> 8) & 0xFF,
                       rgb[0] & 0xFF, rgb[1] & 0xFF, rgb[2] & 0xFF])
    body[1] = max(0, min(len(vals) - 1, int(current_idx0)))
    return bytes(body)


def device_label(vid: int, pid: int, product: str = "") -> tuple[str, str, str]:
    """返回 (型号, 展示名, 连接方式)；优先使用已知型号表。"""
    info = KNOWN_DEVICES.get((vid, pid))
    if info:
        return info
    name = product or f"未知设备 {vid:04X}:{pid:04X}"
    return name.split()[0], name, "unknown"
