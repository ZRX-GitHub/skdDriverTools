"""Windows 通知：注册 AppUserModelID，并通过托盘气泡弹出系统通知。"""
from __future__ import annotations

import os
import sys

import i18n

APP_ID = "Crociris.MouseGlass.1"
DISPLAY_NAME = "三颗豆"

_tray = None


def bind_tray(tray) -> None:
    global _tray
    _tray = tray


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
    """让通知（以及任务栏）显示中文名称与图标。"""
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


def show(title: str, message: str, level: str = "info", timeout: int = 8000) -> None:
    """弹出一条 Windows 通知；托盘不可用时静默忽略。"""
    if _tray is None:
        return
    from PySide6.QtWidgets import QSystemTrayIcon

    icon = {
        "warning": QSystemTrayIcon.Warning,
        "error": QSystemTrayIcon.Critical,
    }.get(level, QSystemTrayIcon.Information)
    try:
        _tray.showMessage(title, message, icon, timeout)
    except RuntimeError:
        pass


def low_battery(model: str, level: int, threshold: int = 15) -> None:
    """低电量通知。threshold 是用户在设置里定的阈值，写进正文好让人明白为什么弹。

    正文控制在两行内：Windows 的 toast 正文超过两行会被折断显示。
    """
    model = (model or "鼠标").strip()
    if model.endswith("鼠标"):
        model = model[:-2] or "鼠标"
    show(i18n.T("鼠标电量低"),
         f"{model} " + i18n.T("电量 {pct}%，已低于 {thr}%，请及时充电").format(
             pct=level, thr=threshold),
         "warning", 10000)
