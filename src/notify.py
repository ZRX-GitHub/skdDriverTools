"""Windows 通知：注册 AppUserModelID，并把提醒投递到 **Windows 通知中心**。

旧版只调 ``QSystemTrayIcon.showMessage()``。那是 Win32 托盘气泡（NIF_INFO），
在 Windows 10/11 上经常不弹、也不进通知中心，专心模式下一律被吞掉 —— 这正是
「低电量提醒没生效」的根因。

现在主通道走 WinRT ``ToastNotificationManager``：用注册表里已注册的 AUMID
创建 notifier，投递标准 toast，通知会留在通知中心里、受系统通知设置管理。
WinRT 只在 PowerShell 5.1 里做了类型投影，Python 侧没有 winrt/winsdk 依赖，
所以用一个隐藏窗口的 PowerShell 子进程完成投递；进程失败或被用户禁用通知时
自动回退到托盘气泡，保证至少有一条可见提醒。
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import threading
import xml.sax.saxutils as saxutils

import applog
import i18n

APP_ID = "Crociris.MouseGlass.1"
DISPLAY_NAME = "三颗豆"

_log = applog.get("notify")

_tray = None
#: "auto"（toast 优先，失败回退）/ "toast"（只走通知中心）/ "bubble"（只走托盘）
_channel = "auto"
#: toast 投递失败后待补发的托盘气泡，由 GUI 线程的 pump() 消费
_fallback: list[tuple[str, str, str, int]] = []
_fallback_lock = threading.Lock()

#: Toast 的 Tag/Group：同组同 Tag 会替换通知中心里的旧条目，避免堆积重复提醒
TOAST_GROUP = "battery"
TOAST_TAG = "low-battery"

# PowerShell 5.1 需要先显式加载 WinRT 类型投影；脚本本身全 ASCII，文案通过
# 环境变量传进来，避免中文 / 引号 / 换行拼进命令行造成转义问题。
_TOAST_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.NotificationSetting, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:SKD_TOAST_AUMID)
    if ($notifier.Setting -ne [Windows.UI.Notifications.NotificationSetting]::Enabled) {
        Write-Error ('notifier disabled: ' + $notifier.Setting)
        exit 3
    }
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($env:SKD_TOAST_XML)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    $toast.Tag = $env:SKD_TOAST_TAG
    $toast.Group = $env:SKD_TOAST_GROUP
    $notifier.Show($toast)
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 2
}
'''

_XML_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def bind_tray(tray) -> None:
    global _tray
    _tray = tray


def set_channel(channel: str) -> None:
    """切换通知通道。自检脚本 / 诊断工具用，正常运行时保持 ``auto``。"""
    global _channel
    if channel in ("auto", "toast", "bubble"):
        _channel = channel


def channel() -> str:
    return _channel


def icon_path() -> str:
    """AUMID 图标：安装目录 `_internal/assets/app.ico`，开发态回退到 assets/app.ico。

    v1.4.8 起图标不再放安装根目录（`install_dir()/app.ico` 只作老版本兼容）。
    """
    candidates = []
    try:
        import setup_core

        base_dir = setup_core.install_dir()
        candidates.append(os.path.join(base_dir, "_internal", "assets", "app.ico"))
        candidates.append(os.path.join(base_dir, "assets", "app.ico"))
        candidates.append(os.path.join(base_dir, "app.ico"))
    except Exception:  # noqa: BLE001
        pass
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, "assets", "app.ico"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "assets", "app.ico"))
    for path in candidates:
        if os.path.exists(path):
            return os.path.abspath(path)
    return ""


def register_app_identity() -> None:
    """让通知（以及任务栏）显示中文名称与图标。

    ``ShowInSettings`` 让「三颗豆」出现在 系统 → 通知 的应用列表里，用户可以在
    那里单独开关本应用的通知；toast 的 notifier 也依赖这个 AUMID。
    """
    if sys.platform != "win32":
        return
    try:
        import winreg

        path = "Software\\Classes\\AppUserModelId\\" + APP_ID
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)
            icon = icon_path()
            if icon:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon)
            winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


# ---------------------------------------------------------------- Toast 通道
def powershell_exe() -> str:
    """系统自带 PowerShell 5.1 的绝对路径，避免依赖 PATH / 被同名程序劫持。"""
    root = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def toast_xml(title: str, message: str) -> str:
    """构造 ToastText02 XML；文案做 XML 转义，设备型号里带 & < > 也不会炸。"""
    def clean(text: object) -> str:
        return saxutils.escape(_XML_CTRL.sub("", str(text or "")))

    return (
        '<toast duration="long" launch="skd://low-battery">'
        '<visual><binding template="ToastText02">'
        f'<text id="1">{clean(title)}</text>'
        f'<text id="2">{clean(message)}</text>'
        '</binding></visual></toast>'
    )


def _toast_env(title: str, message: str, tag: str, group: str) -> dict:
    env = dict(os.environ)
    env.update({
        "SKD_TOAST_XML": toast_xml(title, message),
        "SKD_TOAST_AUMID": APP_ID,
        "SKD_TOAST_TAG": tag or TOAST_TAG,
        "SKD_TOAST_GROUP": group or TOAST_GROUP,
    })
    return env


def _run_toast(payload: tuple[str, str, str, int], tag: str, group: str) -> None:
    """后台线程：投递 toast；失败把 payload 放进补发队列，交给 GUI 线程气泡兜底。"""
    title, message, _level, _timeout = payload
    encoded = base64.b64encode(_TOAST_SCRIPT.encode("utf-16-le")).decode("ascii")
    cmd = [powershell_exe(), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
           "-EncodedCommand", encoded]
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd, env=_toast_env(title, message, tag, group),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=flags,
        )
        _out, err = proc.communicate(timeout=8)
        if proc.returncode == 0:
            _log.info("已投递通知中心 toast: %s", title)
            return
        detail = (err or b"").decode("utf-8", "replace").strip().replace("\n", " ")[:240]
        _log.warning("toast 投递失败(code=%s): %s", proc.returncode, detail)
    except subprocess.TimeoutExpired:
        _log.warning("toast 投递超时")
        try:
            proc.kill()
            proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        _log.warning("toast 通道不可用: %s", exc)
    if _channel == "auto":
        with _fallback_lock:
            _fallback.append(payload)


def _post_toast(title: str, message: str, level: str, timeout: int,
                *, tag: str = TOAST_TAG, group: str = TOAST_GROUP) -> bool:
    """异步提交一次 toast。返回 False 表示连子进程都起不来，调用方立刻回退。"""
    if sys.platform != "win32" or not os.path.exists(powershell_exe()):
        return False
    payload = (str(title), str(message), str(level), int(timeout))
    try:
        threading.Thread(target=_run_toast, args=(payload, tag, group),
                         name="skd-toast", daemon=True).start()
    except Exception as exc:  # noqa: BLE001
        _log.warning("无法启动 toast 线程: %s", exc)
        return False
    return True


def _bubble(title: str, message: str, level: str = "info", timeout: int = 8000) -> bool:
    """托盘气泡兜底。托盘不可用时静默忽略。"""
    if _tray is None:
        return False
    from PySide6.QtWidgets import QSystemTrayIcon

    icon = {
        "warning": QSystemTrayIcon.Warning,
        "error": QSystemTrayIcon.Critical,
    }.get(level, QSystemTrayIcon.Information)
    try:
        _tray.showMessage(title, message, icon, timeout)
        return True
    except (RuntimeError, AttributeError):
        return False


def pump() -> int:
    """GUI 线程周期性调用：把 toast 失败的通知用托盘气泡补发，避免漏报。

    投递在后台线程，Qt 的托盘调用必须回到主线程，所以拆成「入队 + 泵」两步。
    返回本次补发的条数。
    """
    with _fallback_lock:
        items = _fallback[:]
        _fallback.clear()
    for title, message, level, timeout in items:
        _bubble(title, message, level, timeout)
    if items:
        _log.info("toast 失败，已用托盘气泡补发 %d 条", len(items))
    return len(items)


def diagnose() -> dict:
    """同步探测通知能力，给自检脚本 / 日志用（不会真的弹通知）。"""
    result: dict = {
        "platform": sys.platform,
        "channel": _channel,
        "aumid": APP_ID,
        "powershell": powershell_exe(),
        "powershell_exists": os.path.exists(powershell_exe()),
        "tray_bound": _tray is not None,
    }
    if sys.platform != "win32" or not result["powershell_exists"]:
        result["setting"] = "unavailable"
        return result
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.UI.Notifications.NotificationSetting, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null; "
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Setting"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = [str(result["powershell"]), "-NoProfile", "-NonInteractive",
           "-WindowStyle", "Hidden", "-EncodedCommand", encoded]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result["setting"] = out.stdout.decode("utf-8", "replace").strip() or "unknown"
        result["error"] = out.stderr.decode("utf-8", "replace").strip()[:200]
    except Exception as exc:  # noqa: BLE001
        result["setting"] = "error"
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------- 对外 API
def show(title: str, message: str, level: str = "info", timeout: int = 8000,
         *, tag: str = "", group: str = "") -> str:
    """弹出一条系统通知，返回实际使用的通道：``toast`` / ``bubble`` / ``none``。

    ``auto`` 模式下先投递通知中心 toast；PowerShell 缺失 / 线程起不来时立即
    用托盘气泡，运行期失败由 :func:`pump` 补发。
    """
    if _channel == "bubble" or sys.platform != "win32":
        return "bubble" if _bubble(title, message, level, timeout) else "none"
    if _post_toast(title, message, level, timeout, tag=tag, group=group):
        return "toast"
    if _channel == "auto":
        return "bubble" if _bubble(title, message, level, timeout) else "none"
    return "none"


def low_battery(model: str, level: int, threshold: int = 15) -> str:
    """低电量通知。threshold 就是设置页选的提醒阈值，写进正文说明为什么弹。

    正文控制在两行内：Windows 的 toast 正文超过两行会被折断显示。
    """
    model = (model or "鼠标").strip()
    if model.endswith("鼠标"):
        model = model[:-2] or "鼠标"
    title = i18n.T("鼠标电量低")
    message = (f"{model} " + i18n.T("电量 {pct}%，已达设定的 {thr}% 提醒阈值，请及时充电")
               .format(pct=level, thr=threshold))
    # 每台设备一条：同 Tag 的新通知替换通知中心里的旧条目，不堆一叠重复提醒
    tag = f"{TOAST_TAG}:{model}"[:64]
    return show(title, message, "warning", 10000, tag=tag, group=TOAST_GROUP)
