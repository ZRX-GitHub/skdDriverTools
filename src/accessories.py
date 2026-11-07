"""非鼠标设备（键盘 / 耳机）的识别：**只列出来，不参与切换**。

设计边界，先说清楚为什么这么切：

* 主程序是鼠标工具，DPI / 回报率 / 电量环这一整套只对鼠标成立。键盘和耳机
  没有这些量，硬塞进 `DeviceHub.sources` 会把「当前设备」这套逻辑搞乱
  （`current_key()`、缓存、卡片的可编辑判定全部按鼠标写的）。所以配件走
  独立一路，只往设备弹窗里补几行，`chosen` 信号永远不为它们发。
* 认不出来的不显示：和狼途那条规矩一致——把杂牌标成某品牌比不显示更糟。
  键盘靠 `hidbrands` 的 VID/PID 表，耳机靠 Windows 音频端点里的名字。

读数一律不伪造：配件全是 USB 取电，菜单右侧写「USB供电」；真遇到读不到
电量的无线配件，写「无法读取」，不编百分比。
"""
from __future__ import annotations

import re
import threading
import time

import hid_win
import hidbrands
import i18n

KIND_MOUSE = "mouse"
KIND_KEYBOARD = "keyboard"
KIND_HEADSET = "headset"

#: 音频端点名字 → 品牌。耳机的 VID 是 C-Media（芯片厂），几十款耳机共用，
#: 从 PID 翻不出品牌；但固件把产品名写在 USB 音频终端描述符里，名字里带品牌。
NAME_BRANDS: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (("血手幽灵", "血手", "幽灵", "bloody"), ("血手幽灵", "Bloody")),
)

#: 音频端点名字里出现这些词时，说明它只是品类名，不是型号
_GENERIC_WORDS = ("耳机", "耳麦", "headset", "headphone", "audio", "usb",
                  "device", "output", "speaker", "扬声器")

_USB_PATH = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", re.I)


# ---------------------------------------------------------------- 行构造
def _row(kind: str, brand: str, model: str, vid: int, pid: int) -> dict:
    """统一的行结构；`switchable=False` 是这里的核心——弹窗点它不响应。"""
    label = f"{i18n.T(brand)} {i18n.T(model)}".strip() if brand else i18n.T(model)
    return {
        "key": f"acc:{kind}:{vid:04X}:{pid:04X}",
        "label": label,
        "brand": i18n.T(brand) if brand else "",
        "model": i18n.T(model),
        "available": True,
        "current": False,          # 永远不会是「当前设备」
        "battery": None,           # 读不到，也不编
        "wired": True,             # USB 取电
        "kind": kind,
        "switchable": False,       # 用户要求：只展示，不能切过去
    }


def brand_from_name(name: str) -> tuple[str, str] | None:
    """音频端点名字 → (中文品牌, 英文品牌)；认不出返回 None。"""
    low = (name or "").lower()
    for keys, brand in NAME_BRANDS:
        if any(k.lower() in low for k in keys):
            return brand
    return None


def row_from_endpoint(name: str, instance: str) -> dict | None:
    """一个音频端点 → 耳机行；不是认识的品牌 / 不是 USB 设备就返回 None。

    `name` 是端点描述（如「幽灵血手耳机」），`instance` 是设备接口路径
    （`USB\\VID_0D8C&PID_0012&MI_00\\...`）。型号取名字里剩下的部分：剥掉
    品牌词和品类词后还有内容才算型号，否则只写「耳机」。
    这个耳机固件报的是「幽灵血手耳机」——品牌+品类，没有型号。
    """
    brand = brand_from_name(name)
    if not brand:
        return None
    m = _USB_PATH.search(instance or "")
    if not m:
        return None
    vid, pid = int(m.group(1), 16), int(m.group(2), 16)

    model = (name or "").strip()
    for keys, _ in NAME_BRANDS:
        for k in keys:
            model = re.sub(re.escape(k), "", model, flags=re.I)
    for w in _GENERIC_WORDS:
        model = re.sub(re.escape(w), "", model, flags=re.I)
    model = model.strip(" -_()（）")
    if not model or len(model) > 24:
        model = "耳机"
    return _row(KIND_HEADSET, brand[0], model, vid, pid)


def row_from_keyboard(ident: dict) -> dict:
    """hidbrands 认出来的键盘 → 键盘行。"""
    return _row(KIND_KEYBOARD, ident["brand"], ident["model"],
                ident["vid"], ident["pid"])


# ---------------------------------------------------------------- 枚举
def keyboards() -> list[dict]:
    """带键盘集合（usage 0x0001/0x0006）且品牌认得出的 HID 设备。

    只看集合类型不看名字：B930 的字符串描述符全是占位值，名字一点用没有。
    """
    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()
    try:
        cols = hid_win.enumerate_hid()
    except Exception:  # noqa: BLE001
        return rows
    for col in cols:
        if col.usage_page != 0x0001 or col.usage != 0x0006:
            continue
        key = (col.vendor_id, col.product_id)
        if key in seen:
            continue                 # 一把键盘常挂两组键盘集合
        ident = hidbrands.identify(col.vendor_id, col.product_id, col.product)
        if not ident or ident.get("kind") != KIND_KEYBOARD:
            continue
        seen.add(key)
        rows.append(row_from_keyboard(ident))
    return rows


def _audio_endpoints() -> list[tuple[str, str]]:
    """Windows 播放端点 → [(端点名, 设备接口路径)]。

    读 MMDevices 注册表而不是 Core Audio：只要名字和路径，不值得为它拉一套
    COM。DeviceState=1 是在用，2/4/8 是禁用/不在位/拔了，一律不收。
    """
    out: list[tuple[str, str]] = []
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return out
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return out
    try:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                guid = winreg.EnumKey(root, i)
                if winreg.QueryValueEx(winreg.OpenKey(root, guid), "DeviceState")[0] != 1:
                    continue
                pk = winreg.OpenKey(root, guid + r"\Properties")
            except OSError:
                continue
            name = inst = ""
            for j in range(winreg.QueryInfoKey(pk)[1]):
                key, data, _ = winreg.EnumValue(pk, j)
                if not isinstance(data, str):
                    continue
                if key == "{a45c254e-df1c-4efd-8020-67d146a850e0},2":
                    name = data           # DEVPKEY_Device_DeviceDesc
                elif key == "{b3f8fa53-0004-438e-9003-51a46e139bfc},2":
                    inst = data           # 设备接口路径，含 VID/PID
            if name and inst:
                out.append((name, inst))
    finally:
        root.Close()
    return out


def headsets() -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for name, inst in _audio_endpoints():
        row = row_from_endpoint(name, inst)
        if not row:
            continue
        key = (int(row["key"].split(":")[2], 16), int(row["key"].split(":")[3], 16))
        if key in seen:
            continue                 # 一台耳机可能有多个播放端点
        seen.add(key)
        rows.append(row)
    return rows


def scan() -> list[dict]:
    """当前在位的配件行（键盘在前、耳机在后）。"""
    return keyboards() + headsets()


# ---------------------------------------------------------------- 后台轮询
class AccessoryMonitor:
    """每隔几秒扫一次配件，`rows()` 立刻返回上一次的结果。

    为什么不在打开弹窗时现扫：`enumerate_hid()` 实测 240ms 上下，点一下标题
    卡住四分之一秒，手感很差。放到后台线程，弹窗只读快照。
    """

    def __init__(self, interval: float = 3.0) -> None:
        self.interval = interval
        self._rows: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="accessory-monitor",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def rows(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._rows]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                found = scan()
                with self._lock:
                    self._rows = found
            except Exception:  # noqa: BLE001
                pass                 # 配件是附加信息，扫崩了也不该影响鼠标功能
            self._stop.wait(self.interval)
