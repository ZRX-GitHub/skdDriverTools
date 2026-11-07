"""设备可调能力：DPI 范围/步进、回报率档位，以及能不能写回。

UI 的「编辑」只认这个对象，不关心底层是 HID++ 2.0 还是私有协议：
罗技（0x2201 / 0x8060）能读范围也有写命令；CROCIRIS 私有协议目前只逆向出
读命令（0x12 状态 / 0x19 档位表），因此一律判定「不支持编辑」。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import i18n

#: 常见回报率（Hz）。位图换算出来的 Hz 只有落在集合里才采信
KNOWN_RATES = (125, 250, 500, 1000, 2000, 4000, 8000)

#: 各型号的官方回报率挡位（官网参数页口径，2026-11 核对）。
#:
#: 之前是「问设备要一个最大挡位索引，再把 ≤ 它的挡位全列出来」——设备回的
#: 索引只要偏一位（或接收器替鼠标回了自己的上限），界面就会给出错的区间，
#: 所以改成按型号写死。X7 系列官网写明「原生双 8K 回报率」，因此七档齐全。
#: 表里没有的型号（M7 等官网未公开挡位的）仍然回落到设备上报。
RATE_SPECS: dict[str, tuple[int, ...]] = {
    "X7 ULTRA MAX": (125, 250, 500, 1000, 2000, 4000, 8000),
    "X7S ULTRA MAX": (125, 250, 500, 1000, 2000, 4000, 8000),
    "X7 PRO MAX": (125, 250, 500, 1000, 2000, 4000, 8000),
    "X7S": (125, 250, 500, 1000, 2000, 4000, 8000),
    "X7": (125, 250, 500, 1000, 2000, 4000, 8000),
}

#: 私有协议（CROCIRIS / 西伯利亚）按型号给的 DPI 规格：(最小, 最大, 步进)。
#:
#: 这类设备的私有协议只上报「当前最多 8 个档位的值」（0x19），0x12 状态帧
#: 只有当前 DPI、0x13 设备信息只有固件/序列号/最高回报率——**没有任何一条
#: 命令能读到传感器的可调范围**，所以范围只能按型号规格给。规格来自官方
#: 参数页（X7 系列：PAW3950 Ace 原生 30000、X7 Pro Max 的 PAW3955 Ace
#: 原生 40000）。型号表没收录的设备统一按 `DEFAULT_DPI_SPEC` 兜底。
#:
#: 2026-11 官方参数页 + 官方驱动实测：驱动里可调下限 50、步进 1（驱动对
#: 传感器原生值之外做插值），上限按官方标注的「可超频值」收：Max 系列
#: 40000 原生 / 64000 超频，标准版 30000 原生 / 42000 超频。
#: 键必须写成完整展示名——`dpi_spec_for` 是「最长前缀优先」，
#: 「X7S ULTRA MAX」不能被短键「X7S」抢走。
DPI_SPECS: dict[str, tuple[int, int, int]] = {
    "X7 ULTRA MAX": (50, 64000, 1),    # PAW3955 Ace：40000 原生 / 64000 超频
    "X7 PRO MAX": (50, 64000, 1),      # PAW3955 Ace
    "X7S ULTRA MAX": (50, 64000, 1),   # PAW3955 Ace
    "X7S": (50, 42000, 1),             # PAW3950：30000 原生 / 42000 超频
    "X7": (50, 42000, 1),              # PAW3950 Ace：30000 原生 / 42000 超频
    "M7": (50, 42000, 1),              # PAW3950 同档
}
#: 未收录型号的兜底规格（也用于 hidpp 读不到范围时的占位）
DEFAULT_DPI_SPEC = (100, 26000, 50)

#: 罗技（Logitech G）按型号的官方规格：(DPI 最小, DPI 最大, 步进, 最大回报率 Hz)。
#:
#: 罗技走 HID++ 2.0，理论上 0x2201 fn1 能读出传感器全集、0x8060 fn0 能读出
#: 回报率位图，但那两条路都有坑：位图只报「当前链路支持的间隔」（无线机型
#: 经接收器常常只剩 125–1000）、部分固件对 fn1 回空、老机型干脆没有这两个
#: 特征。所以按型号固化一份官方口径，设备读到的值只用来**收窄或补齐**，
#: 型号表负责保证「再怪的链路也不会把 25600 显示成 3000」。
#:
#: 数据来源：logitechg.com 产品页 / support.logi.com 规格页（2026-11 核对）。
#: 步进统一 50——罗技 G 系列全系按 50 DPI 一档，HERO 25K 的 100–25600 就是
#: 511 档；G102/G302 这类低端型号官网只写范围，同样按 50 收。
#:
#: 上限一律按官网「Resolution」字段，不按营销页的传感器型号反推（HERO 2
#: 有 32000 和后续固件升到 44000 两种口径，这里收出货时的 32000）。
LOGITECH_SPECS: dict[str, tuple[int, int, int, int]] = {
    # ---- G1/G3 入门线 ----
    "G102": (200, 8000, 50, 1000),          # G102 / G102 LIGHTSYNC 第二代
    "G302": (200, 4000, 50, 1000),
    "G303": (200, 12000, 50, 1000),
    "G402": (200, 4000, 50, 1000),
    # ---- 无线小鼠 ----
    "G304 X LIGHTSPEED": (100, 25600, 50, 1000),
    "G304": (200, 12000, 50, 1000),
    "G305": (200, 12000, 50, 1000),
    "G309": (100, 25600, 50, 1000),
    # ---- G4/G6/G7/G9 ----
    "G403 HERO": (100, 25600, 50, 1000),
    "G403": (200, 12000, 50, 1000),
    "G600": (200, 8200, 50, 1000),
    "G603": (200, 12000, 50, 1000),         # HI 模式 1000；LO 模式 125
    "G703": (100, 25600, 50, 1000),
    "G900": (200, 12000, 50, 1000),
    "G903": (100, 25600, 50, 1000),
    # ---- G502 家族（长名在前，靠 token 数区分） ----
    "G502 X PLUS": (100, 25600, 50, 1000),
    "G502 X LIGHTSPEED": (100, 25600, 50, 1000),
    "G502 X": (100, 25600, 50, 1000),
    "G502 LIGHTSPEED": (100, 25600, 50, 1000),
    "G502 HERO": (100, 25600, 50, 1000),
    "G502": (200, 12000, 50, 1000),         # Proteus Core / Spectrum 原版
    # ---- PRO 线 ----
    "G PRO X SUPERLIGHT 2": (100, 32000, 50, 2000),
    "G PRO X SUPERLIGHT": (100, 25600, 50, 1000),
    "G PRO 2 LIGHTSPEED": (100, 32000, 50, 1000),   # 8K 需另购 PRO 接收器
    "G PRO WIRELESS": (100, 16000, 50, 1000),
    "G PRO": (200, 12000, 50, 1000),        # 有线版
    "PRO X SUPERLIGHT 2": (100, 32000, 50, 2000),
    "PRO X SUPERLIGHT": (100, 25600, 50, 1000),
    "PRO 2 LIGHTSPEED": (100, 32000, 50, 1000),
    "PRO WIRELESS": (100, 16000, 50, 1000),
    # ---- MX 办公线（走 Bolt/Unifying，仅作识别与范围提示） ----
    "MX MASTER 3S": (200, 8000, 50, 125),
    "MX MASTER 3": (200, 4000, 50, 125),
    "MX ANYWHERE 3S": (200, 8000, 50, 125),
    "MX ANYWHERE 3": (200, 4000, 50, 125),
}


def _norm_tokens(text: str) -> list[str]:
    """型号串 → 小写词元列表，顺手去掉罗技「G PRO」里可省的前缀 G。

    USB 产品串与 HID++ 设备名对同一只鼠标的写法不一样：G502 HERO 两处都是
    「G502 HERO Gaming Mouse」，而 PRO 系列常写成「PRO X SUPERLIGHT 2
    Wireless Gaming Mouse」（没有开头那个 G），还有的写成「G PRO X ...」。
    把孤立的 ``g`` 词元统一抹掉，两种写法就能落到同一个键上。
    """
    s = re.sub(r"[^0-9a-z]+", " ", (text or "").lower()).strip()
    toks = s.split()
    if len(toks) > 1 and toks[0] == "g":
        toks = toks[1:]
    return toks


def logitech_spec_for(model: str) -> tuple[int, int, int, int] | None:
    """按型号取罗技官方规格；认不出返回 None（调用方回落到设备实时读取）。

    匹配方式是「型号键的每个词元都出现在产品串里」，命中多个时取词元最多的
    那个——「G502 X PLUS」有 3 个词元，不会被 2 个词元的「G502 X」抢走；
    「PRO X SUPERLIGHT 2」也不会被「PRO X SUPERLIGHT」抢走。
    """
    toks = _norm_tokens(model)
    if not toks:
        return None
    best_len = -1
    best: tuple[int, int, int, int] | None = None
    for name, spec in LOGITECH_SPECS.items():
        key = _norm_tokens(name)
        if not key or not all(t in toks for t in key):
            continue
        if len(key) > best_len:
            best_len, best = len(key), spec
    return best


def rate_values_up_to(max_hz: int) -> tuple[int, ...]:
    """按最大回报率展开成挡位序列（125/250/500/1000/2000/4000/8000）。"""
    return tuple(r for r in KNOWN_RATES if r <= max_hz)


def dpi_spec_for(model: str) -> tuple[int, int, int]:
    """按型号取传感器 DPI 规格（最小, 最大, 步进）。

    型号来自 `protocol.device_label` 的短名（X7 / X7S / M7 …）。先精确匹配，
    再按前缀匹配（「X7 Pro Max」→「X7 PRO MAX」），都不中就用兜底值。
    """
    key = (model or "").strip().upper()
    if not key:
        return DEFAULT_DPI_SPEC
    if key in DPI_SPECS:
        return DPI_SPECS[key]
    # 长名优先：「X7 PRO MAX」不能被「X7」抢走
    for name in sorted(DPI_SPECS, key=len, reverse=True):
        if key.startswith(name):
            return DPI_SPECS[name]
    return DEFAULT_DPI_SPEC


def rate_values_for(model: str) -> tuple[int, ...]:
    """按型号取官方回报率挡位；返回空 = 官网没公开，调用方回落到设备上报。

    匹配规则与 `dpi_spec_for` 一致（精确 → 最长前缀），保证「X7S ULTRA MAX」
    不会被「X7S」或「X7」抢走。
    """
    key = (model or "").strip().upper()
    if not key:
        return ()
    if key in RATE_SPECS:
        return RATE_SPECS[key]
    for name in sorted(RATE_SPECS, key=len, reverse=True):
        if key.startswith(name):
            return RATE_SPECS[name]
    return ()


def resolve_dpi_range(spec: tuple[int, int, int],
                      table: list[int]) -> tuple[int, int, int]:
    """型号规格 + 设备实际档位表 → 界面最终用的 (最小, 最大, 步进)。

    规格给的是「传感器能力」，档位表给的是「这台设备真在用的档」。取并集的
    外边界：设备里已经存着 32000 那一档，就不能对外宣称上限只有 30000，
    否则编辑框会把一个合法档位判成越界、用户连看都看不到它。
    """
    lo, hi, step = spec
    vals = sorted({int(v) for v in table if int(v) > 0})
    if vals:
        lo = min(lo or vals[0], vals[0])
        hi = max(hi or vals[-1], vals[-1])
    return int(lo or 0), int(hi or 0), int(step or 0)


def parse_rate_bitmap(mask: int) -> list[int]:
    """0x8060 fn0：bit n 表示支持 n+1 ms 的回报间隔。

    G502 HERO 真机返回 0x8B（bit 0/1/3/7）→ 1/2/4/8 ms →
    1000 / 500 / 250 / 125 Hz，整理成升序 [125, 250, 500, 1000]。
    """
    out: list[int] = []
    for bit in range(8):
        if not (mask >> bit) & 1:
            continue
        interval_ms = bit + 1
        if 1000 % interval_ms:
            continue
        hz = 1000 // interval_ms
        if hz in KNOWN_RATES and hz not in out:
            out.append(hz)
    return sorted(out)


def range_from_values(values: list[int]) -> tuple[int, int, int]:
    """把设备上报的 DPI 全集归纳成 (最小, 最大, 步进)。

    设备用「范围标记」上报时已经展开成等差数列（G502 HERO：100,150,…,25600，
    共 511 档）→ 步进 50、判为连续可调；只报几个离散档位（400/800/1600）→
    步进 0，调用方改走 dpi_values 逐档对齐。

    判据是「这个步进能不能把区间铺满」，而不是「是否全等差」：
      * G502 展开后 (25600-100)/50+1 == 511 == 档数 → 连续，步进 50；
      * 400/800/1600 用 400 铺 400–1600 要 4 档、实际只有 3 档 → 离散。
    这样设备只报部分档位（探针里的 100/600/1350）时，不会被误判成
    「步进 1250 的连续范围」而把用户输入乱对齐。
    """
    vals = sorted({int(v) for v in values if 1 <= int(v) <= 65535})
    if not vals:
        return 0, 0, 0
    if len(vals) == 1:
        return vals[0], vals[0], 0
    step = vals[1] - vals[0]
    for a, b in zip(vals[1:], vals[2:]):
        step = math.gcd(step, b - a)
    span = vals[-1] - vals[0]
    # 少于 3 档不判连续：两个值（700/850）的差 150 是「两档间距」，不是步进
    if step and len(vals) >= 3 and span % step == 0 and span // step + 1 == len(vals):
        return vals[0], vals[-1], step
    return vals[0], vals[-1], 0


@dataclass
class DeviceCaps:
    source: str = "none"
    dpi_min: int = 0
    dpi_max: int = 0
    dpi_step: int = 0
    dpi_values: list[int] = field(default_factory=list)
    rate_values: list[int] = field(default_factory=list)
    dpi_writable: bool = False
    rate_writable: bool = False
    #: DPI 档位表（挡位）能否增删——与「改当前 DPI 数值」是两回事
    levels_writable: bool = False
    max_levels: int = 0

    @property
    def dpi_editable(self) -> bool:
        return self.dpi_writable and (self.dpi_max > 0 or bool(self.dpi_values))

    @property
    def levels_editable(self) -> bool:
        return self.levels_writable and self.dpi_editable

    @property
    def rate_editable(self) -> bool:
        return self.rate_writable and bool(self.rate_values)

    def dpi_hint(self) -> str:
        """卡片正上方两行提示：可调范围 + 步进。"""
        if not self.dpi_max:
            return i18n.T("未读取到可调范围")
        first = i18n.T("可调 {lo}–{hi} DPI").format(lo=self.dpi_min, hi=self.dpi_max)
        if self.dpi_step:
            return f"{first}\n" + i18n.T("步进 {step} DPI").format(step=self.dpi_step)
        if self.dpi_values:
            return f"{first}\n" + i18n.T("共 {n} 档可选").format(n=len(self.dpi_values))
        return first

    def rate_hint(self) -> str:
        """卡片正上方提示：可调范围（单行）。

        档位明细（125/250/500/1000…）不在这里重复列——回报率模块改成
        下拉框选挡位后，点「刷新率」弹出的挡位面板已经完整列出每一档，
        提示胶囊再写一遍既挤不下（9px 下四档明细要 198px，占掉整行）又和
        卡片列网格对不齐。这里只给可调范围即可。
        """
        if not self.rate_values:
            return i18n.T("未读取到可调范围")
        lo, hi = min(self.rate_values), max(self.rate_values)
        if lo == hi:
            return i18n.T("仅支持 {lo} Hz").format(lo=lo)
        return i18n.T("可调 {lo}–{hi} Hz").format(lo=lo, hi=hi)

    def input_step(self) -> int:
        """步进器按一下走多少：优先设备上报的步进，离散档位取最小档间距。

        档间距大于 1000 说明那只是「两个孤立档位」（比如 700/26000），拿它当
        步进会让 ± 一按就跨过整段范围，这时回落到常规的 50。
        """
        if self.dpi_step:
            return self.dpi_step
        vals = sorted(self.dpi_values)
        if len(vals) >= 2:
            gaps = [b - a for a, b in zip(vals, vals[1:]) if b > a]
            if gaps and 0 < min(gaps) <= 1000:
                return min(gaps)
        return 50

    def align_dpi(self, value: int) -> int:
        """把输入值对齐到合法档位：连续范围按步进取整，离散档位取最近值。"""
        if not value or value <= 0:
            return self.dpi_min or (self.dpi_values[0] if self.dpi_values else 0)
        if self.dpi_values and not self.dpi_step:
            return min(self.dpi_values, key=lambda v: abs(v - value))
        lo, hi = self.dpi_min, self.dpi_max
        if self.dpi_values:
            lo = lo or min(self.dpi_values)
            hi = hi or max(self.dpi_values)
        if not lo or not hi:
            return value
        value = max(lo, min(hi, value))
        if self.dpi_step:
            value = lo + round((value - lo) / self.dpi_step) * self.dpi_step
            value = max(lo, min(hi, value))
        return int(value)

    def align_rate(self, value: int) -> int:
        if not self.rate_values:
            return value
        return min(self.rate_values, key=lambda v: abs(v - value))

    def copy(self) -> "DeviceCaps":
        return DeviceCaps(
            source=self.source, dpi_min=self.dpi_min, dpi_max=self.dpi_max,
            dpi_step=self.dpi_step, dpi_values=list(self.dpi_values),
            rate_values=list(self.rate_values), dpi_writable=self.dpi_writable,
            rate_writable=self.rate_writable, levels_writable=self.levels_writable,
            max_levels=self.max_levels,
        )
