"""卸载程序（由主程序以 --uninstall 调用）。"""
from __future__ import annotations

import os
import shutil
import sys


def run() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    import autostart
    import setup_core

    quiet = "--quiet" in sys.argv
    app = QApplication.instance() or QApplication(sys.argv)

    if not quiet:
        box = QMessageBox()
        box.setWindowTitle("卸载 三颗豆")
        box.setWindowIcon(QMessageBox.standardIcon(QMessageBox.Question))
        box.setIcon(QMessageBox.Question)
        box.setText("确定要卸载「三颗豆」吗？")
        box.setInformativeText(
            f"程序目录：{setup_core.install_dir()}\n"
            "同时会删除开始菜单 / 桌面快捷方式、开机自启动项和本地配置。"
        )
        ok_button = box.addButton("卸载", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not ok_button:
            return 0

    autostart.clear()
    setup_core.remove_shortcuts()
    setup_core.unregister_uninstall()

    target = setup_core.install_dir()
    running_inside = False
    if getattr(sys, "frozen", False):
        here = os.path.normcase(os.path.abspath(sys.executable))
        running_inside = here.startswith(os.path.normcase(os.path.abspath(target)))
    if os.path.isdir(target):
        if running_inside:
            setup_core.remove_dir_later(target)
        else:
            shutil.rmtree(target, ignore_errors=True)

    shutil.rmtree(setup_core.config_dir(), ignore_errors=True)

    if not quiet:
        box = QMessageBox()
        box.setWindowTitle("卸载完成")
        box.setIcon(QMessageBox.Information)
        box.setText("「三颗豆」已卸载。")
        box.setInformativeText("如果程序目录仍在，会在几秒后自动清理。")
        box.addButton("确定", QMessageBox.AcceptRole)
        box.exec()
    return 0
