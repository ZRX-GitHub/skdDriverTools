"""Windows 原生窗口效果：亚克力/云母背景、圆角、深色标题栏。"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2

DWMSBT_AUTO = 0
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2  # Mica
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
DWMSBT_TABBEDWINDOW = 4

ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

WCA_ACCENT_POLICY = 19


class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _dwm():
    return ctypes.WinDLL("dwmapi.dll")


def _set_attr(hwnd: int, attr: int, value: int, size: int = 4) -> bool:
    if sys.platform != "win32":
        return False
    try:
        dwm = _dwm()
        buf = ctypes.create_string_buffer(size)
        ctypes.memmove(buf, ctypes.byref(ctypes.c_int(value)), 4)
        res = dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), ctypes.c_uint(attr), buf, ctypes.c_uint(size))
        return res == 0
    except OSError:
        return False


def set_dark_mode(hwnd: int, dark: bool) -> None:
    _set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)


def set_rounded(hwnd: int, rounded: bool = True) -> None:
    _set_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)


def set_border_none(hwnd: int) -> None:
    # DWMWA_COLOR_NONE
    _set_attr(hwnd, DWMWA_BORDER_COLOR, 0xFFFFFFFE)


def set_system_backdrop(hwnd: int, kind: int = DWMSBT_TRANSIENTWINDOW) -> bool:
    return _set_attr(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, kind)


SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
GWL_HWNDPARENT = -8
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2


def set_topmost(hwnd: int, on: bool) -> bool:
    """置顶开关：直接调 SetWindowPos，不碰 Qt 的 windowFlags。

    Qt 的 setWindowFlags 会销毁并重建原生窗口，挂在主窗口上的设置浮层
    会被一起收掉（点一下「窗口置顶」面板就消失），所以这里走 Win32。
    注意：HWND_NOTOPMOST 对已经在普通层的窗口是 no-op（不会在层内重排），
    降层后想让窗口回到同层最上面，需要再调一次 raise_window()。
    """
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32.dll")
        res = user32.SetWindowPos(
            wintypes.HWND(hwnd),
            ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        return bool(res)
    except OSError:
        return False


WM_SETCURSOR = 0x0020
HTCLIENT = 1


def refresh_cursor(hwnd: int) -> bool:
    """让窗口按「光标下的控件」重新算一次光标形状。

    Qt 只在窗口收到 WM_SETCURSOR 时才下发 SetCursor。设置浮层是 Qt.Popup，
    弹出的那段时间鼠标被它接管，主窗口收不到这个时机；面板收起后 Windows
    也不会补发，于是主窗口的光标就停在最后一次的形状上 —— 典型表现是手型
    黏在整块 HUD 上（鼠标从别的窗口移进来也还是手指）。

    这里主动补发一次 WM_SETCURSOR（等价于鼠标重新移入窗口），Qt 会按当前
    光标位置重新取控件的光标，手型立刻消失。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = ctypes.WinDLL("user32.dll")
        return bool(user32.PostMessageW(
            wintypes.HWND(hwnd), WM_SETCURSOR,
            wintypes.WPARAM(hwnd), wintypes.LPARAM(HTCLIENT),
        ))
    except OSError:
        return False


def raise_window(hwnd: int) -> bool:
    """在同层内把窗口抬到最上面（HWND_TOP）。

    降层用的 HWND_NOTOPMOST 对已在该层的窗口不重排；切换置顶后要保证
    设置浮层仍压在主窗口上面，最后必须显式抬一次。
    """
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32.dll")
        res = user32.SetWindowPos(
            wintypes.HWND(hwnd),
            ctypes.c_void_p(HWND_TOP),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        return bool(res)
    except OSError:
        return False


def set_window_owner(hwnd: int, owner_hwnd: int) -> bool:
    """把窗口设成 owner_hwnd 的 owned window（Win32 owner 关系）。

    Windows 的硬规则：**owned window 永远显示在 owner 之上**，且 owner 的
    置顶状态变化时 owned window 会自动跟随。

    这正是设置浮层需要的：主窗口切「窗口置顶」时会被 SetWindowPos 送到
    置顶层顶部，如果浮层不是 owned window，主窗口就会把它盖住（浮层
    「消失」）；设为 owned window 后，Windows 自己保证浮层始终压在主窗口
    之上，切换置顶不再有任何中间态 —— 实测 60 次连续切换 0 帧闪动。

    注意：owner 关系只在原生窗口创建/重设时生效，Qt 在 show()/setParent()
    时可能把它重置，所以浮层每次显示前后都要重新设一次。
    """
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32.dll")
        # 64 位下 HWND 是指针宽，必须用 SetWindowLongPtrW
        try:
            setter = user32.SetWindowLongPtrW
        except AttributeError:
            setter = user32.SetWindowLongW
        setter(wintypes.HWND(hwnd), GWL_HWNDPARENT, wintypes.HWND(owner_hwnd))
        return True
    except (OSError, AttributeError, ValueError):
        return False


def set_accent(hwnd: int, state: int, tint_abgr: int = 0x00000000, flags: int = 0) -> bool:
    try:
        user32 = ctypes.WinDLL("user32.dll")
        policy = ACCENTPOLICY(state, flags, tint_abgr, 0)
        data = WINCOMPATTRDATA(WCA_ACCENT_POLICY, ctypes.cast(ctypes.byref(policy), ctypes.c_void_p),
                               ctypes.sizeof(policy))
        res = user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))
        return bool(res)
    except (OSError, AttributeError):
        return False


def apply_window_chrome(hwnd: int, dark: bool) -> None:
    """无边框窗口的窗口级装饰。

    注意：这里刻意不启用 DWM 的系统背景（亚克力/云母）。系统背景会填满整个
    窗口矩形，导致圆角卡片外面的透明区域被染成灰色方块。
    """
    set_dark_mode(hwnd, dark)
    set_rounded(hwnd, False)
    set_border_none(hwnd)


def apply_glass(hwnd: int, dark: bool, acrylic: bool = True) -> None:
    """给无边框窗口加上系统级毛玻璃背景（保留，默认不再使用）。"""
    set_dark_mode(hwnd, dark)
    set_rounded(hwnd, True)
    set_border_none(hwnd)
    # Win11 原生云母/亚克力；失败时退回 Win10 的 accent 模糊
    if not set_system_backdrop(hwnd, DWMSBT_TRANSIENTWINDOW if acrylic else DWMSBT_NONE):
        tint = 0xCC1A1A1E if dark else 0xCCF2F2F6
        set_accent(hwnd, ACCENT_ENABLE_ACRYLICBLURBEHIND, tint)
# --------------------------------------------------------------------------- 全局鼠标钩子
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207

_BUTTON_DOWN = (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


_hook_handle = None
_hook_proc = None


def install_mouse_hook(callback) -> bool:
    """装全局低级鼠标钩子：任何窗口上的鼠标按下都会回调 callback(x, y)。

    为什么不用 GetAsyncKeyState 轮询：异步按键状态只反映「调用线程自己取走过
    的输入」，鼠标点在别的程序上时那个线程根本没取过消息，状态永远不推进
    （实测：点自己的窗口能看到按下，点桌面一路读下来全是 0）。低级钩子由系统
    直接回调，不管输入落在哪个窗口。

    回调只做一件事：把按下坐标交给调用方，别在里面碰 UI。
    """
    global _hook_handle, _hook_proc
    if _hook_handle is not None:
        return True
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32.dll")
        # 不显式声明参数类型，ctypes 会把 64 位句柄按 32 位整数传，
        # SetWindowsHookExW 直接报 126（模块未找到）
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                             wintypes.HMODULE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_long
        hookproc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM)

        def _proc(code, wparam, lparam):
            if code == 0 and wparam in _BUTTON_DOWN:
                try:
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    callback(int(info.pt.x), int(info.pt.y))
                except Exception:  # noqa: BLE001
                    pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        proc = hookproc(_proc)
        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        module = kernel32.GetModuleHandleW(None)
        handle = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, module, 0)
        if not handle:
            return False
        _hook_proc = proc          # 必须留住引用，否则回调被 GC 掉会崩
        _hook_handle = handle
        return True
    except (OSError, ValueError):
        return False


def remove_mouse_hook() -> None:
    global _hook_handle, _hook_proc
    if _hook_handle is None:
        return
    try:
        user32 = ctypes.WinDLL("user32.dll")
        user32.UnhookWindowsHookEx(_hook_handle)
    except (OSError, ValueError):
        pass
    _hook_handle = None
    _hook_proc = None


# --------------------------------------------------------------------- 托盘弹窗
#: 系统托盘「点开那个小窗」的窗口类名。
#: Win11 是 XAML Island（标题「系统托盘溢出窗口。」），Win10 是 Win32 的
#: NotifyIconOverflowWindow。Win11 上不能用 Windows.UI.Core.CoreWindow，
#: 那个类名太泛（输入法、桌面都会用），会误判。
TRAY_FLYOUT_CLASSES = (
    "TopLevelWindowForOverflowXamlIsland",
    "NotifyIconOverflowWindow",
)

DWMWA_EXTENDED_FRAME_BOUNDS = 9


def primary_monitor_rect() -> tuple[int, int, int, int] | None:
    """物理像素下「主显示器」的矩形（含任务栏区域）。"""
    if sys.platform != "win32":
        return None

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32.dll")
    found: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def _cb(hmon, _hdc, _rect, _lp):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)) and (info.dwFlags & 1):
            m = info.rcMonitor
            found.append((m.left, m.top, m.right, m.bottom))
        return True

    try:
        user32.EnumDisplayMonitors(None, None, _cb, 0)
    except (OSError, ValueError):
        return None
    return found[0] if found else None


def _visible_frame(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口的可见外框（优先用 DWM 的扩展边框，排除阴影）。"""
    user32 = ctypes.WinDLL("user32.dll")
    rect = wintypes.RECT()
    if sys.platform == "win32":
        try:
            buf = wintypes.RECT()
            res = _dwm().DwmGetWindowAttribute(wintypes.HWND(hwnd),
                                               ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
                                               ctypes.byref(buf), ctypes.sizeof(buf))
            if res == 0 and buf.right > buf.left and buf.bottom > buf.top:
                return (buf.left, buf.top, buf.right, buf.bottom)
        except (OSError, ValueError):
            pass
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


#: 托盘小窗是「无边框 + 透明阴影」窗口：GetWindowRect / DWM 扩展边框都比肉眼
#: 可见的面板大一圈 —— 实测 Win11 顶部多出 13px 物理像素（截图逐行扫出来的），
#: 左右下也各有一点。贴边时必须拿**可见顶边**当基准，否则 HUD 底部会平白多
#: 留出这 13px，看上去比「距任务栏 12px」明显更远。
FLYOUT_TOP_INSET = 13


def visible_flyout_frame(frame: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """把托盘小窗的窗口矩形收成「肉眼可见面板」的矩形（只收顶边）。

    顶边是贴边定位的唯一基准（下边框要距它 12px）。左右下保持宽松值：那几处
    只用于「点击是否落在小窗内」的判断，宽一点更不容易误判成外部点击。
    """
    left, top, right, bottom = frame
    return (left, min(top + FLYOUT_TOP_INSET, bottom - 1), right, bottom)


def tray_flyout_rect() -> tuple[int, int, int, int] | None:
    """主屏上「弹出的托盘小窗」外框（物理像素）。没弹出时返回 None。

    只认主屏：副屏任务栏被点开时那个弹窗也在副屏，不该把主屏上的窗口拽过去。
    """
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32.dll")
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    primary = primary_monitor_rect()
    if primary is None:
        return None

    def _ok(frame):
        """够大、且确实落在主屏上。"""
        if frame is None:
            return False
        if frame[2] - frame[0] < 80 or frame[3] - frame[1] < 40:
            return False                 # 太小：动画首帧或别的控件
        return (frame[0] < primary[2] and frame[2] > primary[0]
                and frame[1] < primary[3] and frame[3] > primary[1])

    # 用 FindWindowEx 按「类名」遍历，比 EnumWindows 翻全表便宜得多（100ms 一次）。
    # 注意不能只用 FindWindowW：它只返回第一个同类名窗口，而系统里那个真弹窗
    # 平时是隐藏的，很可能就排在前面 —— 会一直拿到隐藏的那个，永远看不见
    # 别的进程新建的同类名窗口。
    for cls in TRAY_FLYOUT_CLASSES:
        after = None
        while True:
            hwnd = user32.FindWindowExW(None, after, cls, None)
            if not hwnd:
                break
            after = hwnd
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                continue
            frame = _visible_frame(hwnd)
            if _ok(frame):
                return visible_flyout_frame(frame)
    return None


def taskbar_tray_rect() -> tuple[int, int, int, int] | None:
    """主屏任务栏上「托盘图标区」的矩形（物理像素）。

    点击这里（时钟、音量、显示桌面、那个小箭头）都算在跟系统托盘打交道，
    不该把 HUD 收起来 —— 尤其是小箭头，点它正是为了弹出托盘窗口。
    Shell_TrayWnd 只对应主屏任务栏，副屏的是 Shell_SecondaryTrayWnd。
    """
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32.dll")
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    try:
        bar = user32.FindWindowW("Shell_TrayWnd", None)
        if not bar:
            return None
        tray = user32.FindWindowExW(bar, None, "TrayNotifyWnd", None)
        if not tray or not user32.IsWindowVisible(tray):
            return None
        frame = _visible_frame(tray)
        if frame is None or frame[2] - frame[0] < 20:
            return None
        return frame
    except (OSError, ValueError):
        return None
