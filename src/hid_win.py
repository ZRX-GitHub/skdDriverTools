"""Windows HID 访问层（ctypes 直连 hid.dll / setupapi.dll）。

只依赖标准库，便于 PyInstaller 打包成单文件 exe。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes as wt
from dataclasses import dataclass, field

if sys.platform != "win32":  # pragma: no cover
    raise RuntimeError("仅支持 Windows")

hid = ctypes.WinDLL("hid.dll")
setupapi = ctypes.WinDLL("setupapi.dll")
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102

# 常用 HID 用途页 / 用途
UP_GENERIC_DESKTOP = 0x01
UP_SIMULATION = 0x02
UP_GENERIC_DEVICE = 0x06
UP_VENDOR = 0xFF00


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wt.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wt.ULONG),
        ("VendorID", wt.USHORT),
        ("ProductID", wt.USHORT),
        ("VersionNumber", wt.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT),
        ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.ULONG),
        ("NumberInputButtonCaps", wt.ULONG),
        ("NumberInputValueCaps", wt.ULONG),
        ("NumberInputDataIndices", wt.ULONG),
        ("NumberOutputButtonCaps", wt.ULONG),
        ("NumberOutputValueCaps", wt.ULONG),
        ("NumberOutputDataIndices", wt.ULONG),
        ("NumberFeatureButtonCaps", wt.ULONG),
        ("NumberFeatureValueCaps", wt.ULONG),
        ("NumberFeatureDataIndices", wt.ULONG),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wt.DWORD),
        ("OffsetHigh", wt.DWORD),
        ("hEvent", wt.HANDLE),
    ]


setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(GUID),
    wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wt.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]

hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetProductString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetManufacturerString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetSerialNumberString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]

kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    wt.DWORD,
    wt.HANDLE,
]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ResetEvent.argtypes = [wt.HANDLE]


def _wide(buf: ctypes.Array) -> str:
    return ctypes.wstring_at(ctypes.addressof(buf)).split("\x00")[0].strip()


@dataclass
class HidCollection:
    """一条 HID 设备接口（一个 top-level collection）。"""

    path: str
    vendor_id: int = 0
    product_id: int = 0
    version: int = 0
    usage_page: int = 0
    usage: int = 0
    input_len: int = 0
    output_len: int = 0
    feature_len: int = 0
    product: str = ""
    manufacturer: str = ""
    serial: str = ""
    instance: str = ""

    @property
    def vid_pid(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}"

    @property
    def key(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}:{self.usage_page:04X}:{self.usage:04X}:{self.serial}"

    @property
    def group_key(self) -> str:
        """同一只鼠标的多个 collection 归到一组。"""
        base = self.instance.split("#")[2] if self.instance.count("#") >= 2 else self.path
        return base.split("&col")[0]

    def __str__(self) -> str:
        return (
            f"{self.vid_pid} page={self.usage_page:04X} usage={self.usage:04X} "
            f"in={self.input_len} out={self.output_len} | {self.product or self.manufacturer}"
        )


def enumerate_hid() -> list[HidCollection]:
    """枚举当前所有 HID 接口。"""
    result: list[HidCollection] = []
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if hdev == INVALID_HANDLE_VALUE:
        return result
    try:
        index = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), index, ctypes.byref(did)):
                break
            index += 1
            required = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(required), None
            )
            if required.value <= 0:
                continue
            buf = ctypes.create_string_buffer(required.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), buf, required.value, ctypes.byref(required), None
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            col = _describe(path)
            if col:
                result.append(col)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return result


def _describe(path: str) -> HidCollection | None:
    handle = kernel32.CreateFileW(
        path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        handle = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if handle == INVALID_HANDLE_VALUE:
            return None
    try:
        col = HidCollection(path=path, instance=path)
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
            col.vendor_id = attrs.VendorID
            col.product_id = attrs.ProductID
            col.version = attrs.VersionNumber
        pp = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(handle, ctypes.byref(pp)):
            try:
                caps = HIDP_CAPS()
                if hid.HidP_GetCaps(pp, ctypes.byref(caps)) == 0x00110000:  # HIDP_STATUS_SUCCESS
                    col.usage_page = caps.UsagePage
                    col.usage = caps.Usage
                    col.input_len = caps.InputReportByteLength
                    col.output_len = caps.OutputReportByteLength
                    col.feature_len = caps.FeatureReportByteLength
            finally:
                hid.HidD_FreePreparsedData(pp)
        buf = ctypes.create_unicode_buffer(260)
        if hid.HidD_GetProductString(handle, buf, ctypes.sizeof(buf)):
            col.product = _wide(buf)
        buf2 = ctypes.create_unicode_buffer(260)
        if hid.HidD_GetManufacturerString(handle, buf2, ctypes.sizeof(buf2)):
            col.manufacturer = _wide(buf2)
        buf3 = ctypes.create_unicode_buffer(260)
        if hid.HidD_GetSerialNumberString(handle, buf3, ctypes.sizeof(buf3)):
            col.serial = _wide(buf3)
        return col
    finally:
        kernel32.CloseHandle(handle)


def device_present(path: str) -> bool:
    """这个设备路径现在还挂在系统上吗（拔插检测）。

    拔掉设备后，手里那个句柄并不会立刻失效：后续 ReadFile/WriteFile 只是
    静默失败（返回 None / 抛 OSError），上层拿不到任何"设备已走"的信号。
    唯一可靠的判据是重新 CreateFile 一次——路径没了就打不开。
    只开不读，开完立刻关，不占设备。
    """
    if not path:
        return False
    for access in (GENERIC_READ | GENERIC_WRITE, 0):
        handle = kernel32.CreateFileW(
            path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        if handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(handle)
            return True
    return False


class HidDevice:
    """对单个 collection 的读写封装（重叠 I/O）。"""

    def __init__(self, col: HidCollection):
        self.col = col
        self._handle = None
        self._event = None
        self._read_len = max(col.input_len, 64)
        self._write_len = max(col.output_len, 64)

    def open(self) -> None:
        handle = kernel32.CreateFileW(
            self.col.path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            raise OSError(f"无法打开设备 (WinError {ctypes.get_last_error()})")
        self._handle = handle
        self._event = kernel32.CreateEventW(None, True, False, None)

    def close(self) -> None:
        if self._handle:
            kernel32.CancelIoEx(self._handle, None)
            kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._event:
            kernel32.CloseHandle(self._event)
            self._event = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def write(self, data: bytes, timeout_ms: int = 500, report_id: int | None = 0) -> int:
        """写入报告。

        ``report_id=None`` 表示缓冲区不带报告 ID 前缀；默认 0 表示首字节放报告 ID，
        这是 Windows 对声明了报告 ID（含 0 号）的设备的强制要求。
        """
        assert self._handle, "device not open"
        if report_id is None:
            payload = bytearray(self._write_len)
            payload[: len(data)] = data[: self._write_len]
        else:
            body = bytes([report_id & 0xFF]) + data
            payload = bytearray(self._write_len)
            payload[: min(len(body), self._write_len)] = body[: self._write_len]
        if len(payload) != self._write_len:
            raise ValueError("报告长度与设备声明不符")
        buf = (ctypes.c_ubyte * len(payload)).from_buffer(payload)
        ov = OVERLAPPED()
        ov.hEvent = self._event
        kernel32.ResetEvent(self._event)
        written = wt.DWORD(0)
        ok = kernel32.WriteFile(self._handle, buf, len(payload), ctypes.byref(written), ctypes.byref(ov))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                raise OSError(f"HID 写入失败 (WinError {err})")
            if kernel32.WaitForSingleObject(self._event, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self._handle, ctypes.byref(ov))
                raise TimeoutError("HID 写入超时")
            if not kernel32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(written), False):
                raise OSError(f"HID 写入失败 (WinError {ctypes.get_last_error()})")
        return written.value

    def read(self, timeout_ms: int = 600) -> bytes | None:
        assert self._handle, "device not open"
        buf = (ctypes.c_ubyte * self._read_len)()
        ov = OVERLAPPED()
        ov.hEvent = self._event
        kernel32.ResetEvent(self._event)
        got = wt.DWORD(0)
        ok = kernel32.ReadFile(self._handle, buf, self._read_len, ctypes.byref(got), ctypes.byref(ov))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                return None
            if kernel32.WaitForSingleObject(self._event, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self._handle, ctypes.byref(ov))
                kernel32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(got), True)
                return None
            if not kernel32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(got), False):
                return None
        return bytes(buf[: got.value])
