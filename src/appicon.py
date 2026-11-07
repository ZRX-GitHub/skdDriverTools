"""应用图标（运行时绘制，避免外部资源文件）。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
                          QPixmap)

#: 打进 ico 的全部尺寸（16 是任务栏/列表视图用，256 是资源管理器大图标用）
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _base_plate(p: QPainter, size: int) -> QRectF:
    """画深色圆角底板（两个图标共用，保证桌面上一看就是同一家人）。"""
    rect = QRectF(0.5, 0.5, size - 1, size - 1)
    # 上下打光：图标底也是顶亮底暗，不用斜向
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, QColor("#3A3A3F"))
    grad.setColorAt(1.0, QColor("#101013"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(rect, size * 0.24, size * 0.24)
    return rect


def make_icon() -> QIcon:
    """主程序图标：深色底 + 绿色圆环（电量环）。"""
    icon = QIcon()
    for size in ICON_SIZES:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        rect = _base_plate(p, size)
        p.setBrush(QColor("#32D74B"))
        r = size * 0.30
        p.drawEllipse(rect.center() + QPointF(0, size * 0.06), r, r)
        p.setBrush(QColor("#101013"))
        p.drawEllipse(rect.center() + QPointF(0, size * 0.06), r * 0.42, r * 0.42)
        p.end()
        icon.addPixmap(pm)
    return icon


def make_uninstall_icon() -> QIcon:
    """卸载程序图标：同样的深色底，中间一个浅灰垃圾桶。

    为什么要单独一套：安装目录里两个 exe 挨着放，图标一样的话根本分不清
    哪个是主程序、哪个是卸载。垃圾桶是 Windows 用户对「删除」最直觉的形状。
    """
    icon = QIcon()
    for size in ICON_SIZES:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        _base_plate(p, size)
        s = float(size)
        body = QColor("#F1F1F4")

        # 提手：只描边，上半截被盖子压住，露出来的正好是个环
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(body, max(1.0, s * 0.058)))
        p.drawRoundedRect(QRectF(s * 0.385, s * 0.238, s * 0.23, s * 0.13),
                          s * 0.030, s * 0.030)

        # 盖子：一条略宽的圆角横杠，比桶身稍宽，是「垃圾桶」的识别关键
        p.setPen(Qt.NoPen)
        p.setBrush(body)
        p.drawRoundedRect(QRectF(s * 0.225, s * 0.352, s * 0.55, s * 0.078),
                          s * 0.038, s * 0.038)

        # 桶身：上宽下窄，底边两角收圆
        path = QPainterPath()
        path.moveTo(s * 0.262, s * 0.462)
        path.lineTo(s * 0.738, s * 0.462)
        path.lineTo(s * 0.683, s * 0.806)
        path.quadTo(s * 0.674, s * 0.852, s * 0.628, s * 0.852)
        path.lineTo(s * 0.372, s * 0.852)
        path.quadTo(s * 0.326, s * 0.852, s * 0.317, s * 0.806)
        path.closeSubpath()
        p.setBrush(body)
        p.drawPath(path)

        # 桶身竖纹：16/24 像素下画了只会糊成一片，小尺寸直接省略
        if size >= 32:
            p.setPen(QPen(QColor("#232329"), max(1.0, s * 0.036),
                          Qt.SolidLine, Qt.RoundCap))
            for fx in (0.402, 0.5, 0.598):
                p.drawLine(QPointF(s * fx, s * 0.560), QPointF(s * fx, s * 0.752))
            p.setPen(Qt.NoPen)
        p.end()
        icon.addPixmap(pm)
    return icon
