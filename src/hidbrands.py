"""按 VID/PID 认出「没有私有协议」的品牌设备（狼途鼠标、血手键盘这类）。

为什么要单独一张表：西伯利亚和罗技都能靠 HID 里的**厂商用途页**（0xFF00 /
0xFF0A / 0xFF52）认出来，而狼途这种入门有线鼠用的是通用鼠标控制器
（无锡英斯特 Wuxi Instant，VID 0x30FA）——HID 描述符里只有一条标准鼠标
集合，没有厂商用途页、没有 output / feature 报表。等于固件没有对外开任何
一条命令通道：DPI 和回报率只能按鼠标上的按键调，软件既读不到也写不了。

所以这里只做「认人」：把 VID 翻成品牌，让设备列表能列出「狼途」，而不是
干脆看不见它。认不出的 VID 返回 None——把一台杂牌鼠标标成狼途比不显示更糟。

血手 B930 更进一步：连品牌串都是占位值，型号只能靠 PID 查公开的 usb.ids。
它还会带一个标准鼠标集合，所以 `KINDS` 得把它从「鼠标」里排除出去——
一张表同时管「叫什么」和「是什么」。
"""
from __future__ import annotations

#: VID → (中文品牌, 英文品牌)。英文名给 i18n 表用，见 `i18n.TABLE`。
VENDORS: dict[int, tuple[str, str]] = {
    # 狼途（深圳市跬步电子）：通用鼠标控制器，无厂商通道
    0x30FA: ("狼途", "LANGTU"),
    # 双飞燕 / A4Tech：血手幽灵（Bloody）的母公司。键盘、鼠标、耳机都用这个 VID，
    # 具体是什么品类只能看 PID（见 `KINDS`）
    0x09DA: ("血手幽灵", "Bloody"),
}

#: 个别型号的展示名（USB 产品串本身没信息量时用）；空串 = 直接用产品串。
MODELS: dict[tuple[int, int], str] = {
    # 0x30FA/0x0400 的产品串就叫 "USB Optical Mouse"，没有更细的型号信息，
    # 与其编一个「M8」之类的名字，不如原样显示设备自己报的串
    #
    # 血手 B930：固件把字符串描述符全填成了占位值（product="USB Device"、
    # manufacturer 字面就是 "COMPANY"、无序列号），设备自己一个字都问不出来。
    # 型号来自 usb.ids（0x09DA 0xFA44 → B930 Light Strike RGB Mechanical
    # Gaming Keyboard）与 OpenRGB 的设备登记单，两处独立来源一致。
    (0x09DA, 0xFA44): "B930",
}

#: 例外品类：默认都当鼠标，这里点名哪些 PID 其实不是鼠标。
#:
#: 为什么必须写：B930 除了两组键盘集合，**还带一个标准鼠标集合**
#: （usage 0x0001/0x0002，input 7 字节）。只往 VENDORS 里加 VID 的话，
#: `BrandHidMonitor` 会把这个鼠标集合捡起来，界面上就多出一台
#: 「血手幽灵 B930」鼠标，后面还挂着 DPI 和刷新率卡片——那是假的。
KINDS: dict[tuple[int, int], str] = {
    (0x09DA, 0xFA44): "keyboard",
}


def vendor_of(vid: int) -> tuple[str, str] | None:
    """VID → (中文品牌, 英文品牌)；不在表里返回 None。"""
    return VENDORS.get(int(vid or 0))


def identify(vid: int, pid: int, product: str = "") -> dict | None:
    """认得出就返回 {brand, model, vid, pid, product, kind}，认不出返回 None。

    brand 给的是**中文名**（界面按当前语言用 i18n.T 翻），model 优先取
    型号表，其次取设备自报的产品串，两者都没有才退回 ``VID:PID``。
    kind 是品类（"mouse" / "keyboard"），默认鼠标——只有 `KINDS` 里点名的
    才不是，调用方必须先看这个字段再决定要不要当成鼠标。
    """
    info = vendor_of(vid)
    if not info:
        return None
    vid, pid = int(vid), int(pid)
    product = (product or "").strip()
    model = MODELS.get((vid, pid)) or product or f"{vid:04X}:{pid:04X}"
    return {"brand": info[0], "model": model, "vid": vid, "pid": pid,
            "product": product, "kind": KINDS.get((vid, pid), "mouse")}
