"""独立卸载程序（安装目录里的 `uninstall.exe`）。

刻意**不导入 PySide6/Qt**：主程序的 `_internal` 被删坏、或者主程序本身起不来时，
这个 exe 自带 Python 运行时，照样能把安装目录清干净（包括它自己）。确认框用
Win32 的 MessageBoxW，不依赖 Qt 平台插件。

用法：
    uninstall.exe              弹确认框
    uninstall.exe /quiet       静默卸载（也接受 --quiet / --silent / /S）
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
import traceback

# MessageBox 常量（不引第三方库，纯 Win32）
MB_OK = 0x00000000
MB_OKCANCEL = 0x00000001
MB_ICONQUESTION = 0x00000020
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000
IDOK = 1
IDCANCEL = 2
IDYES = 6
IDNO = 7

_QUIET_FLAGS = ("/quiet", "--quiet", "/silent", "--silent", "/s", "/verysilent")


def _dialog(text: str, title: str, flags: int) -> int:
    """Win32 消息框；拿不到 user32 时按“确定”处理，别把卸载卡死。"""
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags))
    except Exception:  # noqa: BLE001
        return IDOK


def confirm(target: str) -> bool:
    text = (
        "确定要卸载「三颗豆」吗？\n\n"
        f"程序目录：{target}\n"
        "同时会删除开始菜单 / 桌面快捷方式、开机自启动项和本地配置。"
    )
    answer = _dialog(text, "卸载 三颗豆",
                     MB_OKCANCEL | MB_ICONQUESTION | MB_SETFOREGROUND)
    return answer in (IDOK, IDYES)


def uninstall(quiet: bool) -> int:
    import setup_core

    target = setup_core.install_dir()
    if not quiet and not confirm(target):
        return 0

    setup_core.kill_running()
    setup_core.wait_process_gone()

    try:
        import autostart

        autostart.clear()
    except Exception:  # noqa: BLE001
        pass

    setup_core.remove_shortcuts()
    setup_core.remove_legacy_shortcuts()
    setup_core.unregister_uninstall()
    shutil.rmtree(setup_core.config_dir(), ignore_errors=True)

    if not quiet:
        _dialog("「三颗豆」已卸载。\n\n如果程序目录仍在，会在几秒后自动清理。",
                "卸载完成", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND)

    # 本 exe 就躺在安装目录里，运行中删不掉自己 —— 必须**最后**再挂后台删除命令：
    # 上面的「卸载完成」框会一直占着进程，提前挂的话 2 秒后 rmdir 会撞上还锁着的
    # uninstall.exe，目录就删不掉了。
    if os.path.isdir(target):
        setup_core.remove_dir_later(target)
    return 0


def parse_quiet(args: list[str]) -> bool:
    """识别静默开关：/quiet --quiet /silent --silent /s /verysilent（不分大小写）。"""
    return any(str(a).lower() in _QUIET_FLAGS for a in args)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    quiet = parse_quiet(args)
    try:
        return uninstall(quiet)
    except Exception:  # noqa: BLE001
        # 卸载失败不能静默吞掉：留一份日志，用户能在 %TEMP% 找到
        try:
            log = os.path.join(os.environ.get("TEMP", "."), "skd-uninstall.log")
            with open(log, "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except OSError:
            pass
        if not quiet:
            _dialog("卸载失败，详情见 %TEMP%\\skd-uninstall.log",
                    "卸载失败", MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND)
        return 1


if __name__ == "__main__":
    sys.exit(main())
