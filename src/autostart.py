"""开机自启动：HKCU\\...\\Run 注册表项。"""
from __future__ import annotations

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MouseGlass"

# 自启动时只驻留托盘、不弹主窗口（像正常的后台工具那样）。
# main() 里判断 "--tray" 决定是否 show_window()。
TRAY_ARG = "--tray"


def current_command() -> str:
    """当前程序的可执行文件路径（带引号）+ 静默驻留托盘参数。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {TRAY_ARG}'
    exe = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "skdDriver.exe"
    )
    if os.path.exists(exe):
        return f'"{exe}" {TRAY_ARG}'
    return ""


def registered_command() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return str(value or "")
    except OSError:
        return ""


def is_enabled() -> bool:
    return bool(registered_command())


def set_command(command: str) -> bool:
    """写入指定的启动命令行（安装程序用）。"""
    if not command:
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False


def clear() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
        return True
    except OSError:
        return True


def set_enabled(on: bool) -> bool:
    if not on:
        return clear()
    return set_command(current_command())


def sync_command() -> None:
    """程序被移动到别处时，修正自启动项里的路径。"""
    if not is_enabled():
        return
    command = current_command()
    if command and command != registered_command():
        set_enabled(True)
