"""按品牌决定「驱动」怎么打开。

两种驱动形态差别很大，界面上那句话也得跟着换：

* 西伯利亚 / CROCIRIS（X7、M7 系列）—— 只有**网页驱动**，用系统浏览器打开官网地址；
* 罗技 —— 官方是**客户端软件** G HUB，装了就直接拉起本地程序，
  没装才退回官网下载页。

对外只暴露 :func:`kind_of` / :func:`button_text` / :func:`button_icon` /
:func:`open_driver`，主窗口不用关心细节。
"""
from __future__ import annotations

import os
import subprocess
import sys

#: 罗技 G HUB 官网（本地没装时打开这里）
GHUB_URL = "https://www.logitechg.com.cn/zh-cn/innovation/g-hub.html"

#: 常见安装位置：默认装到 C 盘，但很多人会改到别的盘
_GHUB_DIRS = (
    r"C:\Program Files\LGHUB",
    r"C:\Program Files (x86)\LGHUB",
    r"D:\Program Files\LGHUB",
    r"E:\Program Files\LGHUB",
    r"F:\Program Files\LGHUB",
)

#: 注册表里能翻到安装目录的卸载项（G HUB 会写 DisplayName = "Logi Options+" 之类）
_UNINSTALL_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
)

WEB = "web"
SOFTWARE = "software"
#: 这台鼠标**没有**官方软件（狼途这类只有板载按键的方案），按钮要禁用
NONE = "none"

#: 找过的路径缓存：一次找不到就别每次刷新都去翻注册表
_ghub_cache: str | None = None
_ghub_scanned = False


def kind_of(info) -> str:
    """这台鼠标的驱动是网页还是客户端软件。

    第三种：`NONE`——狼途这类设备没有任何官方软件，DPI / 回报率只在鼠标
    自己的按键上改。这种情况不能退回网页驱动（会把用户送去西伯利亚官网）。
    """
    extra = (info.extra if info is not None else None) or {}
    kind = extra.get("kind")
    if kind == "logitech":
        return SOFTWARE
    if kind == "basic":
        return NONE
    return WEB


def button_text(kind: str) -> str:
    if kind == SOFTWARE:
        return "打开官方软件驱动"
    if kind == NONE:
        # 按钮会被禁用，这句话就是用户唯一能读到的地方——顺手告诉他去哪调
        return "无官方软件 · 按键调节"
    return "打开官方网页驱动"


def button_icon(kind: str) -> str:
    return "app" if kind == SOFTWARE else "link"


def _scan_registry() -> str:
    try:
        import winreg
    except ImportError:  # 非 Windows
        return ""
    roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    for path, hive in _UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(roots[hive], path)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
            for i in range(count):
                try:
                    name = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, name) as sub:
                        display = _reg_str(sub, "DisplayName")
                        if "logi" not in display.lower():
                            continue
                        for value in ("InstallLocation", "DisplayIcon", "UninstallString"):
                            loc = _reg_str(sub, value).strip('"')
                            if not loc:
                                continue
                            folder, exe = _exe_from(loc)
                            if exe:
                                return exe
                            if folder:
                                hit = _join(folder, "lghub.exe")
                                if hit:
                                    return hit
                except OSError:
                    continue
        finally:
            root.Close()
    return ""


def _reg_str(key, name: str) -> str:
    try:
        import winreg

        return str(winreg.QueryValueEx(key, name)[0])
    except (OSError, ImportError, IndexError):
        return ""


def _exe_from(value: str) -> tuple[str, str]:
    """把注册表里那串路径拆成 (目录, 存在的 lghub.exe 或 "")。"""
    if not value:
        return "", ""
    text = value.split(",")[0].strip().strip('"')
    if text.lower().endswith(".exe") and os.path.isfile(text):
        base = os.path.basename(text).lower()
        if base.startswith("lghub"):
            return os.path.dirname(text), text
        # 指向 uninstall.exe 之类：拿它的目录再拼一次
        return os.path.dirname(text), ""
    if os.path.isdir(text):
        return text, ""
    return "", ""


def _join(folder: str, exe: str) -> str:
    candidate = os.path.join(folder, exe)
    return candidate if os.path.isfile(candidate) else ""


def find_ghub() -> str:
    """返回 G HUB 主程序的绝对路径；没装返回空串。"""
    global _ghub_cache, _ghub_scanned
    if _ghub_scanned:
        return _ghub_cache or ""
    _ghub_scanned = True
    for folder in _GHUB_DIRS:
        hit = _join(folder, "lghub.exe")
        if hit:
            _ghub_cache = hit
            return hit
    _ghub_cache = _scan_registry() or ""
    return _ghub_cache


def open_driver(info, web_url: str) -> tuple[bool, str]:
    """真正去打开驱动；返回 (是否打开了本地软件, 目标描述)。

    * 罗技且本地装了 G HUB → 拉起 lghub.exe，返回 (True, 程序路径)；
    * 罗技但没装 → 打开官网下载页；其它品牌 → 打开网页驱动地址。
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    kind = kind_of(info)
    if kind == NONE:
        return False, ""
    if kind == SOFTWARE:
        exe = find_ghub()
        if exe and _launch(exe):
            return True, exe
        QDesktopServices.openUrl(QUrl(GHUB_URL))
        return False, GHUB_URL

    QDesktopServices.openUrl(QUrl(web_url))
    return False, web_url


def _launch(exe: str) -> bool:
    """拉起 G HUB。它自己有单实例保护，重复调用只是把已有窗口切到前台。"""
    try:
        flags = 0
        if sys.platform == "win32":
            # 别跟着我们一起退出：DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            flags = 0x00000008 | 0x00000200
        subprocess.Popen([exe], cwd=os.path.dirname(exe), close_fds=True,
                         creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        return False
