"""运行日志与全局异常兜底。

windowed 打包的程序没有控制台，任何未捕获异常都会静默消失。这里统一写
滚动日志到 ``%APPDATA%\\MouseGlass\\logs\\app.log``，并接管：
  * ``sys.excepthook``      —— 主线程未捕获异常
  * ``threading.excepthook``—— 后台线程（HID 轮询）异常
  * Qt 的 critical/fatal 消息
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

import config

LOGGER_NAME = "mouseglass"
_ready = False


def log_dir() -> str:
    return os.path.join(config.APP_DIR, "logs")


def log_path() -> str:
    return os.path.join(log_dir(), "app.log")


def setup(level: int = logging.INFO) -> logging.Logger:
    """初始化日志（幂等）。返回根 logger。"""
    global _ready
    logger = logging.getLogger(LOGGER_NAME)
    if _ready:
        return logger
    _ready = True
    logger.setLevel(level)
    logger.propagate = False

    try:
        os.makedirs(log_dir(), exist_ok=True)
        handler = RotatingFileHandler(
            log_path(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    except OSError:
        pass

    _install_excepthooks(logger)
    _install_qt_handler(logger)
    return logger


def get(name: str = "") -> logging.Logger:
    return logging.getLogger(LOGGER_NAME + ("." + name if name else ""))


# ---------------------------------------------------------------- 钩子
def _install_excepthooks(logger: logging.Logger) -> None:
    def excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.error("未捕获异常", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook

    def thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        logger.error(
            "线程 %s 异常退出",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = thread_hook


def _install_qt_handler(logger: logging.Logger) -> None:
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:  # noqa: BLE001
        return

    levels = {
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, context, message):
        lvl = levels.get(mode)
        if lvl is not None:
            logger.log(lvl, "Qt: %s", message)

    qInstallMessageHandler(handler)
