"""自绘控件：液态玻璃按钮、电量环、信息卡片。"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import i18n
import theme

FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"]


def app_font(size: int, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(size)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def mix_color(a: QColor, b: QColor, t: float) -> QColor:
    """在两色之间线性插值，t=0 取 a、t=1 取 b（保留 a 的透明度）。"""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
        a.alpha(),
    )


def rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def draw_outer_shadow(p: QPainter, rect: QRectF, radius: float, color: QColor,
                      layers: int = 5, strength: float = 0.07, dy: float = 0.6) -> None:
    """只在形状外部绘制柔和阴影，避免半透明填充被自己压暗。"""
    p.save()
    clip = QPainterPath()
    clip.addRect(rect.adjusted(-40, -40, 40, 40))
    clip.addPath(rounded(rect, radius))
    clip.setFillRule(Qt.OddEvenFill)
    p.setClipPath(clip)
    p.setPen(Qt.NoPen)
    for i in range(layers, 0, -1):
        shadow = QColor(color)
        shadow.setAlpha(max(0, int(color.alpha() * strength * (1 - i / (layers + 1)))))
        p.setBrush(shadow)
        grow = i * 0.55
        p.drawRoundedRect(rect.adjusted(-grow, grow * dy, grow, grow * (dy + 0.55)),
                          radius + grow, radius + grow)
    p.restore()


def mix_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def blurred_backdrop(widget: QWidget) -> QPixmap | None:
    """抓取窗口静态背景在本控件位置的那一块并做廉价模糊，当作玻璃的折射层。"""
    win = widget.window()
    pix = getattr(win, "_backdrop", None)
    if pix is None or pix.isNull():
        return None
    try:
        pos = widget.mapTo(win, QPoint(0, 0))
    except RuntimeError:
        return None
    key = (id(pix), pos.x(), pos.y(), widget.width(), widget.height())
    cache = getattr(widget, "_glass_cache", None)
    if cache is not None and cache[0] == key:
        return cache[1]

    dpr = float(pix.devicePixelRatio() or 1.0)
    src = QRectF(pos.x() * dpr, pos.y() * dpr,
                 widget.width() * dpr, widget.height() * dpr).intersected(QRectF(pix.rect()))
    piece = None
    if src.width() > 3 and src.height() > 3:
        small = pix.copy(src.toRect())
        if not small.isNull():
            tiny = small.scaled(max(2, small.width() // 9), max(2, small.height() // 9),
                                Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            piece = tiny.scaled(small.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            piece.setDevicePixelRatio(dpr)
    widget._glass_cache = (key, piece)
    return piece


def paint_glass(p: QPainter, rect: QRectF, radius: float, pal: theme.Palette, *,
                backdrop: QPixmap | None = None, hover: float = 0.0,
                tint: QColor | None = None, tint_strength: float = 0.0,
                glow: QColor | None = None, glow_strength: float = 1.0,
                shadow: bool = True, shadow_strength: float = 1.0,
                body_alpha: float = 1.0, base: QColor | None = None,
                streak: float = 1.0) -> QPainterPath:
    """液态玻璃面板：折射底图 → 半透明本体 → 顶部镜面 → 内缘阴影 → 高光描边。

    base 用于需要遮挡内容的面板（例如设置浮层），先铺一层不透明底色。
    """
    path = rounded(rect, radius)
    dark = pal.name == "dark"
    white = QColor(255, 255, 255)
    clear = QColor(255, 255, 255, 0)

    # 外阴影
    if shadow and shadow_strength > 0:
        draw_outer_shadow(p, rect, radius, pal.surface_overlay, layers=5,
                          strength=0.085 * shadow_strength, dy=0.55)

    # 外发光：用柔和的扩散阴影而不是描边，避免在圆角处堆出硬边缺口
    if glow is not None and glow_strength > 0.01:
        halo = QColor(glow)
        halo.setAlpha(255)
        draw_outer_shadow(p, rect, radius, halo, layers=6,
                          strength=0.15 * glow_strength * (1.0 if dark else 0.9), dy=0.0)

    p.save()
    p.setClipPath(path)

    # 1. 折射底图（对背后的画面做模糊）
    if backdrop is not None and not backdrop.isNull():
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(0.96)
        p.drawPixmap(rect, backdrop, QRectF(backdrop.rect()))
        p.setOpacity(1.0)

    # 2. 底板（需要遮挡背后内容的浮层，叠在模糊层之上）
    if base is not None:
        p.setPen(Qt.NoPen)
        p.setBrush(base)
        p.drawPath(path)

    # 2. 玻璃本体：几乎全透明，只留极淡的乳白
    body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    top_a = (11 if dark else 214) + 8 * hover
    bot_a = (3 if dark else 148) + 6 * hover
    body.setColorAt(0.0, QColor(255, 255, 255, int(top_a * body_alpha)))
    body.setColorAt(1.0, QColor(255, 255, 255, int(bot_a * body_alpha)))
    p.setPen(Qt.NoPen)
    p.setBrush(body)
    p.drawPath(path)

    # 3. 色调（主按钮 / 开关 / 强调）
    if tint is not None and tint_strength > 0:
        tone = QColor(tint)
        tone.setAlpha(int(min(255, tint.alpha()) * min(1.0, tint_strength)))
        p.setBrush(tone)
        p.drawPath(path)

    # 4. 顶缘内侧的折光带（光从玻璃上边缘透进来）
    #    深色模式不做「顶部打光」：深底上这道自上而下的亮带看着就像头顶一盏灯，
    #    整段跳过，让卡面保持平整的哑光。
    if not dark:
        band_h = rect.height() * (0.30 if rect.height() > 34 else 0.46)
        band = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + band_h))
        band.setColorAt(0.0, QColor(255, 255, 255, int(120 + 30 * hover)))
        band.setColorAt(0.45, QColor(255, 255, 255, int(40 + 20 * hover)))
        band.setColorAt(1.0, clear)
        p.setBrush(band)
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), band_h))

    # 5. 上缘一抹高光（湿润的镜面反射；streak=0 时整块关闭，避免局部“打光”）
    #    统一成「上下打光」：高光落在水平正中，只沿 y 方向变化。原来偏在 0.30
    #    宽度处，左半边亮、右半边暗，看着就是从左上方打的一束光。
    if streak > 0.01 and not dark:
        spec_h = max(2.4, rect.height() * 0.10)
        sy = rect.top() + max(2.6, rect.height() * 0.10)
        sx = rect.left() + rect.width() * 0.5
        srx = rect.width() * 0.48
        grad = QRadialGradient(QPointF(sx, sy), srx)
        grad.setColorAt(0.0, QColor(255, 255, 255, int((170 + 40 * hover) * streak)))
        grad.setColorAt(0.55, QColor(255, 255, 255, int((80 + 20 * hover) * streak)))
        grad.setColorAt(1.0, clear)
        p.save()
        p.translate(sx, sy)
        p.scale(1.0, spec_h / max(srx * 0.42, 0.1))
        p.translate(-sx, -sy)
        p.setBrush(grad)
        p.drawEllipse(QPointF(sx, sy), srx, srx * 0.42)
        p.restore()

    # 6. 底部内缘阴影，塑造玻璃厚度
    inner = QLinearGradient(QPointF(rect.left(), rect.bottom() - rect.height() * 0.55),
                            rect.bottomLeft())
    inner.setColorAt(0.0, QColor(0, 0, 0, 0))
    inner.setColorAt(1.0, QColor(0, 0, 0, 46 if dark else 30))
    p.setBrush(inner)
    p.drawRect(rect)

    # 7. 底缘折射亮边
    sheen = QLinearGradient(rect.bottomLeft(),
                            QPointF(rect.left(), rect.bottom() - rect.height() * 0.16))
    sheen.setColorAt(0.0, QColor(255, 255, 255, 62 if dark else 170))
    sheen.setColorAt(1.0, clear)
    p.setBrush(sheen)
    p.drawRect(rect)
    p.restore()

    # 8. 描边：上下打光 —— 顶边最亮，往下衰减，底边再由底缘反光接回一点。
    #    渐变轴必须是**竖**的（top→bottom）：斜着走等于从左上打光，左右两侧一亮一暗。
    border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    if dark:
        border.setColorAt(0.0, QColor(255, 255, 255, 150))
        border.setColorAt(0.45, QColor(255, 255, 255, 92))
        border.setColorAt(1.0, QColor(255, 255, 255, 104))
    else:
        border.setColorAt(0.0, QColor(255, 255, 255, 255))
        border.setColorAt(0.5, QColor(255, 255, 255, 186))
        border.setColorAt(1.0, QColor(255, 255, 255, 132))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(border), 1.2))
    p.drawPath(path)

    # 9. 顶缘 1px 强高光
    rim = QLinearGradient(QPointF(rect.left() + radius, rect.top()),
                          QPointF(rect.right() - radius, rect.top()))
    rim.setColorAt(0.0, clear)
    rim.setColorAt(0.26, QColor(255, 255, 255, 250 if not dark else 120))
    rim.setColorAt(0.74, QColor(255, 255, 255, 210 if not dark else 90))
    rim.setColorAt(1.0, clear)
    p.setPen(QPen(QBrush(rim), 1.1))
    p.drawLine(QPointF(rect.left() + radius, rect.top() + 1.0),
               QPointF(rect.right() - radius, rect.top() + 1.0))

    # 10. 底缘中段的一点反光
    gloss = QLinearGradient(QPointF(rect.left() + rect.width() * 0.28, rect.bottom()),
                            QPointF(rect.right() - rect.width() * 0.28, rect.bottom()))
    gloss.setColorAt(0.0, clear)
    gloss.setColorAt(0.5, QColor(255, 255, 255, 46 if dark else 90))
    gloss.setColorAt(1.0, clear)
    p.setPen(QPen(QBrush(gloss), 1.0))
    p.drawLine(QPointF(rect.left() + rect.width() * 0.30, rect.bottom() - 1.0),
               QPointF(rect.right() - rect.width() * 0.30, rect.bottom() - 1.0))
    return path


def paint_liquid(p: QPainter, rect: QRectF, radius: float, pal: theme.Palette, *,
                 backdrop: QPixmap | None = None, base: QColor | None = None) -> QPainterPath:
    """Liquid Glass：强折射底 + 通透本体 + 内侧厚度环 + 镜面亮边。

    与 paint_glass 的区别是刻意压掉局部打光，靠「整圈亮边 + 底缘折射光」出体积，
    更接近 iOS 26 / macOS 26 的液态玻璃。
    """
    path = rounded(rect, radius)
    dark = pal.name == "dark"
    clear = QColor(255, 255, 255, 0)

    draw_outer_shadow(p, rect, radius, pal.surface_overlay, layers=7, strength=0.10, dy=0.7)

    p.save()
    p.setClipPath(path)

    # 1. 背后的画面（模糊后折射进来）
    if backdrop is not None and not backdrop.isNull():
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(0.95)
        p.drawPixmap(rect, backdrop, QRectF(backdrop.rect()))
        p.setOpacity(1.0)

    # 2. 底色（半透明，保留折射）
    if base is not None:
        p.setPen(Qt.NoPen)
        p.setBrush(base)
        p.drawPath(path)

    # 3. 极薄的乳白本体，上浓下淡
    body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    body.setColorAt(0.0, QColor(255, 255, 255, 30 if dark else 96))
    body.setColorAt(0.45, QColor(255, 255, 255, 10 if dark else 40))
    body.setColorAt(1.0, QColor(255, 255, 255, 3 if dark else 20))
    p.setPen(Qt.NoPen)
    p.setBrush(body)
    p.drawPath(path)

    # 4. 顶部的柔光带（横向均匀，不产生局部亮斑）
    #    深色模式不做「顶部打光」，这道亮带整段跳过。
    if not dark:
        band_h = rect.height() * 0.24
        band = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + band_h))
        band.setColorAt(0.0, QColor(255, 255, 255, 132))
        band.setColorAt(1.0, clear)
        p.setBrush(band)
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), band_h))

    # 5. 内缘厚度环：贴着边缘的一圈微光
    p.setBrush(Qt.NoBrush)
    for width, alpha in ((10.0, 5), (5.5, 9), (2.6, 15)):
        p.setPen(QPen(QColor(255, 255, 255, alpha if dark else int(alpha * 2.3)), width))
        p.drawPath(path)

    # 6. 底缘内侧压暗 + 一道折射亮线（玻璃厚度的来源）
    inner = QLinearGradient(QPointF(rect.left(), rect.bottom() - rect.height() * 0.24),
                            rect.bottomLeft())
    inner.setColorAt(0.0, QColor(0, 0, 0, 0))
    inner.setColorAt(1.0, QColor(0, 0, 0, 48 if dark else 22))
    p.setPen(Qt.NoPen)
    p.setBrush(inner)
    p.drawRect(rect)

    band_b = max(2.0, rect.height() * 0.055)
    sheen = QLinearGradient(QPointF(rect.left(), rect.bottom() - band_b), rect.bottomLeft())
    sheen.setColorAt(0.0, clear)
    sheen.setColorAt(1.0, QColor(255, 255, 255, 120 if dark else 215))
    p.setBrush(sheen)
    p.drawRect(QRectF(rect.left(), rect.bottom() - band_b, rect.width(), band_b))
    p.restore()

    # 7. 镜面亮边：整圈细描边。上下打光 —— 顶边最亮、中段收一点、底缘再亮回来，
    #    左右两侧同亮度（渐变轴竖向，不能斜着走）。
    border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    if dark:
        border.setColorAt(0.0, QColor(255, 255, 255, 150))
        border.setColorAt(0.40, QColor(255, 255, 255, 96))
        border.setColorAt(0.70, QColor(255, 255, 255, 72))
        border.setColorAt(1.0, QColor(255, 255, 255, 110))
    else:
        border.setColorAt(0.0, QColor(255, 255, 255, 255))
        border.setColorAt(0.50, QColor(255, 255, 255, 212))
        border.setColorAt(1.0, QColor(255, 255, 255, 228))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(border), 1.25))
    p.drawPath(path)

    # 8. 顶缘 1px 镜面高光
    rim = QLinearGradient(QPointF(rect.left() + radius, rect.top()),
                          QPointF(rect.right() - radius, rect.top()))
    rim.setColorAt(0.0, clear)
    rim.setColorAt(0.28, QColor(255, 255, 255, 250 if not dark else 120))
    rim.setColorAt(0.72, QColor(255, 255, 255, 198 if not dark else 92))
    rim.setColorAt(1.0, clear)
    p.setPen(QPen(QBrush(rim), 1.2))
    p.drawLine(QPointF(rect.left() + radius, rect.top() + 0.9),
               QPointF(rect.right() - radius, rect.top() + 0.9))
    return path


def paint_group(p: QPainter, rect: QRectF, radius: float, pal: theme.Palette) -> None:
    """Liquid Glass 里的分组容器：比外层更淡的一层玻璃。"""
    dark = pal.name == "dark"
    path = rounded(rect, radius)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 16 if dark else 120))
    p.drawPath(path)

    hl = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.5))
    hl.setColorAt(0.0, QColor(255, 255, 255, 22 if dark else 90))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(hl)
    p.drawPath(path)

    # 上下打光：描边也走竖向，顶亮底暗，左右对称
    border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    border.setColorAt(0.0, QColor(255, 255, 255, 54 if dark else 235))
    border.setColorAt(1.0, QColor(255, 255, 255, 16 if dark else 150))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(border), 1.0))
    p.drawPath(path)


class AnimMixin:
    """轻量的数值补间。"""

    def _anims(self) -> dict:
        if not hasattr(self, "_anim_store"):
            self._anim_store: dict[str, QVariantAnimation] = {}
        return self._anim_store

    def _animate_to(self, attr: str, target: float, duration: int = 190,
                    easing: QEasingCurve.Type = QEasingCurve.OutCubic) -> None:
        store = self._anims()
        old = store.get(attr)
        if old is not None:
            old.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(float(getattr(self, attr)))
        anim.setEndValue(float(target))
        anim.setDuration(duration)
        anim.setEasingCurve(easing)
        anim.valueChanged.connect(lambda v, a=attr: (setattr(self, a, float(v)), self.update()))
        store[attr] = anim
        anim.start()


# --------------------------------------------------------------------------- 图标
def icon_winlogo(p: QPainter, r: QRectF, color: QColor) -> None:
    """Windows 四格徽标：开机自动启动行专用。

    和 Windows 任务管理器「启动」页用同一枚四格徽标，语义最贴「开机启动」。
    26px 瓷砖里只有 14px 可用，四格各约 5.5px，所以用实心小圆角方块而不是描边，
    小尺寸下才不会糊成一团。
    """
    rr = QRectF(r).adjusted(1.0, 1.0, -1.0, -1.0)
    gap = max(1.2, rr.width() * 0.11)
    wq = (rr.width() - gap) / 2
    hq = (rr.height() - gap) / 2
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    for dx in (0, 1):
        for dy in (0, 1):
            p.drawRoundedRect(
                QRectF(rr.left() + dx * (wq + gap), rr.top() + dy * (hq + gap), wq, hq),
                1.1, 1.1)
    p.restore()


def icon_refresh(p: QPainter, r: QRectF, color: QColor, w: float = 1.7) -> None:
    """顺时针刷新箭头：接近完整的环 + 缺口处的实心箭头。"""
    center = r.center()
    radius = min(r.width(), r.height()) / 2 - w * 0.6
    rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
    start_deg, span_deg = 40.0, -300.0
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))

    # 箭头位于环的顺时针末端，指向切线方向
    theta = math.radians((start_deg + span_deg) % 360.0)
    px = center.x() + radius * math.cos(theta)
    py = center.y() - radius * math.sin(theta)
    tx, ty = math.sin(theta), math.cos(theta)     # 顺时针切线（屏幕坐标）
    nx, ny = math.cos(theta), -math.sin(theta)    # 径向
    head = w * 1.95
    tip = QPointF(px + tx * head * 1.05, py + ty * head * 1.05)
    base_x, base_y = px - tx * head * 0.45, py - ty * head * 0.45
    path = QPainterPath()
    path.moveTo(tip)
    path.lineTo(base_x + nx * head * 0.82, base_y + ny * head * 0.82)
    path.lineTo(base_x - nx * head * 0.82, base_y - ny * head * 0.82)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)
    p.restore()


def icon_gear(p: QPainter, r: QRectF, color: QColor, w: float = 1.6) -> None:
    """八齿齿轮（线稿），小尺寸下也清晰。"""
    center = r.center()
    radius = min(r.width(), r.height()) / 2 - w * 0.55
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawEllipse(center, radius * 0.58, radius * 0.58)
    p.setPen(QPen(color, w * 0.95, Qt.SolidLine, Qt.RoundCap))
    for i in range(8):
        angle = math.radians(i * 45)
        p.drawLine(
            QPointF(center.x() + math.cos(angle) * radius * 0.63,
                    center.y() - math.sin(angle) * radius * 0.63),
            QPointF(center.x() + math.cos(angle) * radius * 0.97,
                    center.y() - math.sin(angle) * radius * 0.97),
        )
    p.restore()


def icon_pin(p: QPainter, r: QRectF, color: QColor, active: bool, w: float = 1.7) -> None:
    cx, top = r.center().x(), r.top() + 2.5
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPolyline([QPointF(cx - 5.5, top + 3.5), QPointF(cx, top), QPointF(cx + 5.5, top + 3.5)])
    p.drawLine(QPointF(cx - 5.5, r.bottom() - 2.5), QPointF(cx + 5.5, r.bottom() - 2.5))
    if active:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 70))
        p.drawRoundedRect(QRectF(cx - 4.5, top + 6.0, 9, 4.2), 2.1, 2.1)


def icon_close(p: QPainter, r: QRectF, color: QColor, w: float = 1.7) -> None:
    rr = r.adjusted(4.0, 4.0, -4.0, -4.0)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(rr.topLeft(), rr.bottomRight())
    p.drawLine(rr.topRight(), rr.bottomLeft())


def icon_link(p: QPainter, r: QRectF, color: QColor, w: float = 1.6) -> None:
    rr = r.adjusted(4.0, 4.0, -4.0, -4.0)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(rr.left(), rr.top() + rr.height() * 0.35, rr.width() * 0.62, rr.height() * 0.65),
              90 * 16, 180 * 16)
    p.drawArc(QRectF(rr.left() + rr.width() * 0.38, rr.top(), rr.width() * 0.62, rr.height() * 0.65),
              -90 * 16, 180 * 16)
    p.drawLine(QPointF(rr.left() + rr.width() * 0.30, rr.center().y() + 0.5),
               QPointF(rr.right() - rr.width() * 0.30, rr.center().y() + 0.5))


def icon_app(p: QPainter, r: QRectF, color: QColor, w: float = 1.6) -> None:
    """应用窗口：圆角方框 + 顶部标题栏 + 三个小点。

    用来区分「打开客户端软件」和「打开网页」——两个按钮都画链条图标的话，
    文案换了图标没换，看起来还是同一个东西。
    """
    # 15×15 的尺寸下，三个小点和一条细横线都会糊成一团，所以只留
    # 「边框 + 实心标题栏」这两个最大的特征
    box = r.adjusted(1.8, 1.4, -1.8, -1.4)
    radius = min(3.0, box.width() * 0.26)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(box, radius, radius)
    bar = QRectF(box.left() + 0.9, box.top() + 0.9,
                 box.width() - 1.8, max(2.6, box.height() * 0.30))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(bar, radius * 0.5, radius * 0.5)


def icon_check(p: QPainter, r: QRectF, color: QColor, w: float = 1.8) -> None:
    """对勾：设备列表里标「当前这台」，绿色比系统灰更抓眼。"""
    rr = r.adjusted(r.width() * 0.08, r.height() * 0.10, -r.width() * 0.06, -r.height() * 0.12)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPolyline([
        QPointF(rr.left(), rr.top() + rr.height() * 0.55),
        QPointF(rr.left() + rr.width() * 0.38, rr.bottom()),
        QPointF(rr.right(), rr.top()),
    ])


def icon_bolt(p: QPainter, r: QRectF, color: QColor) -> None:
    path = QPainterPath()
    cx, cy = r.center().x(), r.center().y()
    hw, hh = r.width() * 0.34, r.height() * 0.5
    path.moveTo(cx + hw * 0.35, cy - hh)
    path.lineTo(cx - hw, cy + hh * 0.12)
    path.lineTo(cx - hw * 0.05, cy + hh * 0.12)
    path.lineTo(cx - hw * 0.35, cy + hh)
    path.lineTo(cx + hw, cy - hh * 0.12)
    path.lineTo(cx + hw * 0.05, cy - hh * 0.12)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def icon_kind(p: QPainter, r: QRectF, kind: str, color: QColor, w: float = 1.2) -> None:
    """设备品类图标：鼠标 / 键盘 / 耳机。

    12px 见方，只能画轮廓：鼠标是一个小圆角竖矩形加一条滚轮线，键盘是圆角
    矩形加三颗键位点，耳机是头梁弧加两个耳罩。线条统一 1.2px 圆头，跟旁边
    对勾、闪电同一套笔感——太细在浅色主题发虚，太粗会糊成一团。
    """
    p.save()
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    cx, cy = r.center().x(), r.center().y()

    if kind == "keyboard":
        body = QRectF(r.left() + 1.0, cy - r.height() * 0.30,
                      r.width() - 2.0, r.height() * 0.62)
        p.drawRoundedRect(body, 1.6, 1.6)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        dot = max(1.0, r.width() * 0.085)
        for i in (-1, 0, 1):
            p.drawEllipse(QPointF(body.center().x() + i * dot * 2.6,
                                  body.top() + body.height() * 0.34), dot, dot)
        p.drawRoundedRect(QRectF(body.center().x() - dot * 2.4,
                                 body.bottom() - body.height() * 0.36,
                                 dot * 4.8, dot * 1.15), 0.5, 0.5)
    elif kind == "headset":
        # 头梁：上半圈弧
        span = r.width() * 0.66
        arc = QRectF(cx - span / 2, cy - r.height() * 0.30, span, span)
        p.drawArc(arc, 0, 180 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        cup_w = max(1.4, r.width() * 0.14)
        cup_h = max(2.2, r.height() * 0.30)
        p.drawRoundedRect(QRectF(cx - span / 2 - cup_w * 0.35, cy,
                                 cup_w, cup_h), 0.7, 0.7)
        p.drawRoundedRect(QRectF(cx + span / 2 - cup_w * 0.65, cy,
                                 cup_w, cup_h), 0.7, 0.7)
    else:
        # 鼠标：圆角竖矩形 + 顶部滚轮分隔线
        body = QRectF(cx - r.width() * 0.29, r.top() + 0.6,
                      r.width() * 0.58, r.height() - 1.2)
        p.drawRoundedRect(body, body.width() * 0.48, body.width() * 0.48)
        p.drawLine(QPointF(cx, body.top() + 0.2),
                   QPointF(cx, body.top() + body.height() * 0.38))
    p.restore()


def icon_power(p: QPainter, r: QRectF, color: QColor, w: float = 1.55) -> None:
    center = r.center()
    rad = min(r.width(), r.height()) / 2 - w
    rect = QRectF(center.x() - rad, center.y() - rad, rad * 2, rad * 2)
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(rect, 58 * 16, 244 * 16)
    p.drawLine(QPointF(center.x(), center.y() - rad - w * 0.4),
               QPointF(center.x(), center.y() - rad * 0.05))
    p.restore()


def icon_tray(p: QPainter, r: QRectF, color: QColor, w: float = 1.45) -> None:
    rr = r.adjusted(3.0, 3.0, -3.0, -3.0)
    cx = rr.center().x()
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline([QPointF(rr.left(), rr.center().y() + 1.5),
                    QPointF(rr.left(), rr.bottom()),
                    QPointF(rr.right(), rr.bottom()),
                    QPointF(rr.right(), rr.center().y() + 1.5)])
    p.drawLine(QPointF(cx, rr.top()), QPointF(cx, rr.center().y() + 0.4))
    p.drawPolyline([QPointF(cx - 3.1, rr.center().y() - 2.6),
                    QPointF(cx, rr.center().y() + 0.6),
                    QPointF(cx + 3.1, rr.center().y() - 2.6)])
    p.restore()


def icon_theme(p: QPainter, r: QRectF, color: QColor, w: float = 1.45) -> None:
    rr = QRectF(r).adjusted(3.0, 3.0, -3.0, -3.0)
    full = QPainterPath()
    full.addEllipse(rr)
    half = QPainterPath()
    half.addRect(QRectF(rr.center().x(), rr.top() - 1, rr.width() / 2 + 1, rr.height() + 2))
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(full.intersected(half))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine))
    p.drawEllipse(rr)
    p.restore()


def icon_battery(p: QPainter, r: QRectF, color: QColor, level: float = 0.62,
                 w: float = 1.3) -> None:
    """横向电池：圆角外壳 + 右侧电极 + 内部电量条。"""
    body = QRectF(r.left(), r.top() + r.height() * 0.2, r.width() * 0.8, r.height() * 0.6)
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawRoundedRect(body, 2.2, 2.2)

    nub = QRectF(body.right() + w * 0.9, body.center().y() - body.height() * 0.17,
                 max(1.2, r.width() * 0.08), body.height() * 0.34)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(nub, 0.9, 0.9)

    fill = body.adjusted(w + 0.9, w + 0.9, -w - 0.9, -w - 0.9)
    fill.setWidth(fill.width() * max(0.0, min(1.0, level)))
    p.drawRoundedRect(fill, 1.1, 1.1)
    p.restore()


def icon_percent(p: QPainter, r: QRectF, color: QColor, w: float = 1.25) -> None:
    """百分号：两个小圆圈 + 一道斜杠，用于「提醒阈值」这类百分比设置。"""
    rr = QRectF(r).adjusted(w * 0.5, w * 0.5, -w * 0.5, -w * 0.5)
    rad = max(1.0, rr.width() * 0.15)
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawEllipse(QPointF(rr.left() + rad, rr.top() + rad), rad, rad)
    p.drawEllipse(QPointF(rr.right() - rad, rr.bottom() - rad), rad, rad)
    p.drawLine(QPointF(rr.left() + rad * 0.5, rr.bottom()),
               QPointF(rr.right() - rad * 0.5, rr.top()))
    p.restore()


def icon_globe(p: QPainter, r: QRectF, color: QColor, w: float = 1.25) -> None:
    """地球：外圆 + 赤道 + 一条经线椭圆，用于「语言」行。

    26px 的瓷砖里可用只有 14px，所以只画最有辨识度的三笔：外圆、横向赤道、
    竖向椭圆经线。再加两条斜的纬线在小尺寸下会糊成一团，省掉。
    """
    rr = QRectF(r).adjusted(w * 0.5, w * 0.5, -w * 0.5, -w * 0.5)
    cx, cy = rr.center().x(), rr.center().y()
    rad = min(rr.width(), rr.height()) / 2
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawEllipse(QPointF(cx, cy), rad, rad)
    p.drawLine(QPointF(cx - rad, cy), QPointF(cx + rad, cy))
    p.drawEllipse(QPointF(cx, cy), rad * 0.46, rad)
    p.restore()


def icon_folder(p: QPainter, r: QRectF, color: QColor, w: float = 1.35) -> None:
    """文件夹：左上角带斜标签页的轮廓。"""
    rr = QRectF(r).adjusted(w / 2, w / 2, -w / 2, -w / 2)
    top = rr.top()
    body_top = rr.top() + rr.height() * 0.30
    tab_w = rr.width() * 0.42
    path = QPainterPath()
    path.moveTo(rr.left(), body_top)
    path.lineTo(rr.left(), top + rr.height() * 0.08)
    path.lineTo(rr.left() + tab_w * 0.55, top + rr.height() * 0.08)
    path.lineTo(rr.left() + tab_w, body_top)
    path.lineTo(rr.right(), body_top)
    path.lineTo(rr.right(), rr.bottom())
    path.lineTo(rr.left(), rr.bottom())
    path.closeSubpath()
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)
    p.restore()


# --------------------------------------------------------------------------- 按钮
class GlassButton(QWidget, AnimMixin):
    """液态玻璃质感按钮。"""

    clicked = Signal()

    def __init__(self, text: str = "", kind: str = "glass", icon: str = "", parent=None,
                 height: int = 46, radius: float | None = None,
                 hover_tint: str = "accent") -> None:
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self._icon = icon
        self._hover = 0.0
        self._press = 0.0
        self._radius = radius
        self._icon_active = False
        self._pal = theme.DARK
        self._enabled = True
        # 悬停时的高亮色调："accent" 走主题蓝，"danger" 走红色（关闭按钮）
        self._hover_tint = hover_tint
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        if kind == "icon":
            self.setFixedWidth(height)

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        self.update()

    def set_icon_active(self, active: bool) -> None:
        if active != self._icon_active:
            self._icon_active = active
            self.update()

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def set_icon(self, name: str) -> None:
        """换左侧图标：网页驱动用链条，客户端软件用应用窗口。"""
        if name != self._icon:
            self._icon = name
            self.update()

    def set_enabled_state(self, enabled: bool) -> None:
        self._enabled = enabled
        self.setEnabled(enabled)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_to("_hover", 1.0, 170)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_to("_hover", 0.0, 220)
        self._animate_to("_press", 0.0, 160)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._animate_to("_press", 1.0, 90)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self._animate_to("_press", 0.0, 260, QEasingCurve.OutBack)
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        r = QRectF(self.rect())
        scale = 1.0 - 0.04 * self._press + 0.010 * self._hover
        # 缩放只作用于玻璃底/描边/图标，**不走 QPainter 变换**：
        # 整体 scale 会把文字画到半像素位置上，动画期间字形取整方向逐帧变化，
        # CJK 方块字（如「取消」「安装」的第二个字）会肉眼可见地左右横跳。
        cx, cy = r.center().x(), r.center().y()
        rect = QRectF(cx - r.width() * scale / 2, cy - r.height() * scale / 2,
                      r.width() * scale, r.height() * scale)
        rect = rect.adjusted(3.0, 3.0, -3.0, -3.0) if self._kind != "icon" else rect.adjusted(2.0, 2.0, -2.0, -2.0)
        rad = self._radius if self._radius is not None else rect.height() / 2

        tint, strength = None, 0.0
        if self._kind == "primary":
            tint = QColor(pal.accent)
            strength = (0.22 + 0.18 * self._hover) if pal.name == "dark" \
                else (0.17 + 0.14 * self._hover)
        elif self._icon_active:
            tint, strength = QColor(pal.accent), 0.22 + 0.08 * self._hover
        elif self._hover > 0.002:
            # 悬停即「选中」：铺一层淡淡的主题色，让按钮一眼看出被指到了
            tint = self._hover_color(pal)
            strength = (0.20 if pal.name == "dark" else 0.13) * self._hover

        alpha = 1.0 if self._enabled else 0.55
        paint_glass(
            p, rect, rad, pal,
            backdrop=blurred_backdrop(self),
            hover=self._hover,
            tint=tint, tint_strength=strength,
            # 主按钮不再要那圈蓝色外发光光晕（浅色下会叠出「蓝—白—蓝」三层，
            # 用户觉得最外面那圈蓝多余）
            glow=None,
            glow_strength=(0.45 + 0.55 * self._hover) * (1.0 if self._enabled else 0.3),
            shadow_strength=alpha, body_alpha=alpha,
            # 主按钮不要左上角的局部打光，否则圆头处会出现明显的色块分界
            streak=0.0 if self._kind == "primary" else 1.0,
        )

        # 悬停描边：在玻璃边框外面再套一圈主题色细环，作为「已选中」的第二重提示
        if self._hover > 0.02 and self._enabled:
            ring = QColor(pal.accent) if self._kind == "primary" else self._hover_color(pal)
            ring.setAlpha(int((70 if self._kind == "primary" else 92) * self._hover))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.3))
            p.drawPath(rounded(rect, rad))

        if self._kind == "primary":
            content = QColor("#FFFFFF") if pal.name == "dark" else QColor(pal.accent)
        elif self._icon_active:
            content = QColor(pal.accent)
        else:
            content = QColor(pal.text_primary)
            # 图标/文字一起往主题色过渡，比单纯变背景更明显
            if self._hover > 0.002:
                content = mix_color(content, self._hover_color(pal), 0.9 * self._hover)
        content.setAlpha(255 if self._enabled else 120)
        if self._kind == "icon":
            self._paint_icon(p, rect, content)
        else:
            self._paint_content(p, rect, content)

    def _hover_color(self, pal: theme.Palette) -> QColor:
        return QColor(pal.danger) if self._hover_tint == "danger" else QColor(pal.accent)

    def _paint_icon(self, p: QPainter, rect: QRectF, color: QColor) -> None:
        size = min(rect.width(), rect.height()) * 0.56
        box = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size)
        if self._icon == "refresh":
            icon_refresh(p, box, color)
        elif self._icon == "pin":
            icon_pin(p, box, color, self._icon_active)
        elif self._icon == "close":
            icon_close(p, box, color)
        elif self._icon == "link":
            icon_link(p, box, color)
        elif self._icon == "app":
            icon_app(p, box, color)
        elif self._icon == "gear":
            icon_gear(p, box, color)

    def _paint_content(self, p: QPainter, rect: QRectF, color: QColor) -> None:
        font = app_font(13, QFont.Weight.DemiBold, 0.2)
        p.setFont(font)
        fm = QFontMetricsF(font)
        text_w = fm.horizontalAdvance(self._text)
        icon_w = 15.0 if self._icon else 0.0
        gap = 7.0 if self._icon else 0.0
        total = text_w + icon_w + gap
        # 文字排版基准用**未缩放**的控件矩形：背景随悬停/按压缩放时文字纹丝不动
        base = QRectF(self.rect())
        start = base.center().x() - total / 2
        if self._icon:
            box = QRectF(start, base.center().y() - 7.5, 15, 15)
            if self._icon == "link":
                icon_link(p, box, color, 1.5)
            elif self._icon == "app":
                icon_app(p, box, color, 1.5)
            elif self._icon == "refresh":
                icon_refresh(p, box, color, 1.5)
        p.setPen(QPen(color))
        p.drawText(
            QRectF(start + icon_w + gap, base.top(), text_w + 2, base.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._text,
        )


# --------------------------------------------------------------------------- 电量环
CHARGE_FLOW_PERIOD = 2.9      # 光带跑完一整圈的时间（秒）
CHARGE_FLOW_SEG_DEG = 4.2     # 单段光带占的角度
CHARGE_FLOW_TAIL = 20         # 一段彗尾由多少个小段拼成
CHARGE_FLOW_STEP = 0.0115     # 相邻小段之间的间隔（占整条弧的比例）


def charge_flow_segments(t: float, span_deg: float) -> list[tuple[float, float, int, float]]:
    """算充电光带的几何：每段返回 (起点比例, 终点比例, 不透明度, 线宽系数)。

    比例以「电量弧」为 1.0。**每一段都必须裁到 [0, 1] 内**：光带原本从
    `head` 往后画固定 4.2°、且用 RoundCap 收尾，圆头会越过弧的两端——
    起点前冒出一小块白、终点外溢出一截白边（用户截图里的两处异常）。

    t 用秒；span_deg 是电量弧的角度（负值，顺时针）。
    """
    arc_len = abs(span_deg)
    if arc_len < 1e-6:
        return []
    seg = CHARGE_FLOW_SEG_DEG / arc_len
    out: list[tuple[float, float, int, float]] = []
    for k in range(2):
        raw = ((t / CHARGE_FLOW_PERIOD) + k * 0.5) % 1.0
        head = raw * raw * (3 - 2 * raw)  # smoothstep，推进有呼吸感
        for i in range(CHARGE_FLOW_TAIL):
            lo = head - i * CHARGE_FLOW_STEP
            hi = lo + seg
            lo_c, hi_c = max(0.0, lo), min(1.0, hi)
            if hi_c - lo_c <= 1e-9:
                break
            fade = (1 - i / CHARGE_FLOW_TAIL) ** 1.7
            alpha = int(205 * fade)
            if alpha <= 3:
                break
            out.append((lo_c, hi_c, alpha, 0.30 + 0.34 * fade))
    return out


class BatteryRing(QWidget, AnimMixin):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pal = theme.DARK
        self._value = 0.0
        self._target = 0.0
        self._charging = False
        self._connected = False
        self._sleeping = False
        self._has_value = False
        self._wired = False
        self._low = theme.LOW_BATTERY
        self._phase = 0.0
        self._pulse = 0.0
        self._t = 0.0
        self.setMinimumSize(200, 200)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        self.update()

    def set_low_threshold(self, low: int) -> None:
        """低电量阈值跟着设置页走：红/黄档的分界点一起挪。"""
        low = int(low)
        if low != self._low:
            self._low = low
            self.update()

    def set_state(self, level: float | None, charging: bool, connected: bool,
                  sleeping: bool = False, wired: bool = False) -> None:
        self._charging = charging
        self._connected = connected
        self._sleeping = sleeping
        self._wired = bool(wired)
        self._has_value = level is not None
        target = float(level) if (connected and level is not None) else 0.0
        if abs(target - self._target) > 0.01:
            self._target = target
            self._animate_to("_value", target, 850, QEasingCurve.OutCubic)

    def usb_power(self) -> bool:
        """纯有线鼠标（插着线、没有电池、也没在充电）：环画满 100%、中间写「USB供电」。

        充电中的无线鼠标不算——那种情况有真实电量在涨，照常显示百分比。
        """
        return (self._connected and self._wired
                and not self._charging and not self._sleeping)

    def _tick(self) -> None:
        if not (self._charging or not self._connected):
            if self._pulse > 0:
                self._pulse = 0.0
                self.update()
            return
        self._phase = (self._phase + 0.06) % (math.pi * 2)
        self._t += 0.04
        self._pulse = (math.sin(self._phase) + 1) / 2
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        dark = pal.name == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        side = min(self.width(), self.height())
        stroke = 12.0
        rect = QRectF(
            (self.width() - side) / 2 + stroke,
            (self.height() - side) / 2 + stroke,
            side - stroke * 2,
            side - stroke * 2,
        )
        charging = self._charging and self._connected
        # 纯有线鼠标：没有电池可读，但也不能空着环。用户要求「环画满 100%，
        # 中间写 USB供电」——环满 = 供电正常，文字说明是 USB 供电，而不是
        # 一块 100% 满电的电池（后者是读到的电量，这里根本没有电量）。
        usb_power = self.usb_power()
        level = 100.0 if usb_power else self._value

        # 轨道
        track = QColor(pal.text_primary)
        track.setAlpha(16 if dark else 34)
        pen = QPen(track, stroke, Qt.SolidLine, Qt.RoundCap)
        if not self._connected:
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern([1.6, 3.2])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawArc(rect, 0, 360 * 16)

        color = theme.battery_color(pal, int(round(level)) if self._connected else None,
                                    self._low)
        if charging:
            color = QColor(self._pal.success)
        if self._sleeping:
            dim = QColor(color)
            dim.setAlpha(140)
            color = dim

        if self._connected and level > 0.4:
            span_deg = -level * 3.6
            span = int(span_deg * 16)

            # 充电时环内淡淡的绿色氛围光
            if charging:
                halo = QRadialGradient(rect.center(), rect.width() * 0.62)
                halo.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(),
                                           int(16 + 10 * self._pulse)))
                halo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(rect.adjusted(-stroke, -stroke, stroke, stroke))

            # 光晕
            for extra, alpha in ((16.0, 13), (8.0, 25)):
                glow = QColor(color)
                glow.setAlpha(int(alpha * (0.65 + 0.35 * self._pulse) * (1.45 if charging else 1.0)))
                p.setPen(QPen(glow, stroke + extra, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(rect, 90 * 16, span)

            # 主弧
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, QColor(color).lighter(120))
            grad.setColorAt(1.0, color)
            p.setPen(QPen(QBrush(grad), stroke, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 90 * 16, span)

            # 弧末端不再点小白点：圆头收尾已经干净，额外那点白色看着像渲染残留
            if charging:
                self._paint_charge_flow(p, rect, stroke, color, span_deg)

        # 中央文字
        if usb_power:
            self._paint_usb_power(p, rect, side)
        elif self._connected and self._has_value:
            num_text = str(int(round(self._value)))
            num_font = app_font(int(side * 0.235), QFont.Weight.Light, -1.0)
            unit_font = app_font(int(side * 0.072), QFont.Weight.Medium, 0.0)
            cap_font = app_font(int(side * 0.055), QFont.Weight.Medium, 0.6)
            nfm = QFontMetricsF(num_font)
            ufm = QFontMetricsF(unit_font)
            num_w = nfm.horizontalAdvance(num_text)
            unit_w = ufm.horizontalAdvance("%")
            total = num_w + unit_w + 3
            center = rect.center()
            # 纯有线鼠标：电量恒为 100%，环下面那句「已充满」是纯噪音，直接不画；
            # 不画时数字要往下挪一点，否则整块数字会贴在圆心上半部。
            caption = ("休眠中" if self._sleeping else
                       ("正在充电" if charging else ("已充满" if self._value >= 99.5 else "剩余电量")))
            caption = i18n.T(caption) if caption else ""
            if self._wired and not charging and not self._sleeping:
                caption = ""
            base_y = center.y() + nfm.capHeight() / 2 - 4 + (9.0 if not caption else 0.0)

            # 充电时数字带一点暖光
            if charging:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color.red(), color.green(), color.blue(),
                                  int(14 + 10 * self._pulse)))
                p.drawEllipse(center, rect.width() * 0.34, rect.height() * 0.30)

            p.setFont(num_font)
            p.setPen(QPen(pal.text_primary))
            p.drawText(QRectF(center.x() - total / 2, base_y - nfm.ascent(), num_w + 2, nfm.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, num_text)
            p.setFont(unit_font)
            p.setPen(QPen(QColor(pal.text_secondary)))
            p.drawText(QRectF(center.x() - total / 2 + num_w + 3, base_y - ufm.ascent() + 2,
                              unit_w + 2, ufm.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, "%")

            if caption:
                p.setFont(cap_font)
                cap_rect = QRectF(rect.left(), center.y() + nfm.capHeight() / 2 + 10,
                                  rect.width(), 18)
                p.setPen(QPen(QColor(color)) if charging else QPen(QColor(pal.text_tertiary)))
                p.drawText(cap_rect, Qt.AlignHCenter | Qt.AlignTop, caption)

            if charging:
                bolt_color = QColor(color)
                bolt_color.setAlpha(int(200 + 55 * self._pulse))
                icon_bolt(p, QRectF(center.x() + total / 2 + 6, center.y() - 15, 13, 17), bolt_color)
        else:
            cap_font = app_font(int(side * 0.075), QFont.Weight.Light, 0.5)
            p.setFont(cap_font)
            p.setPen(QPen(pal.text_tertiary))
            # 有线且本就读不到电量（狼途这类没有电池的设备）：写「休眠中」
            # 是错的——它一直插着，永远不会「醒来」；写「USB供电」才如实
            if self._connected and self._wired:
                tip = i18n.T("USB供电")
            elif self._connected:
                tip = i18n.T("休眠中")
            else:
                tip = i18n.T("未连接")
            p.drawText(rect, Qt.AlignCenter, tip)

    def _paint_usb_power(self, p: QPainter, rect: QRectF, side: float) -> None:
        """纯有线鼠标的环心文字：写「USB供电」，不写百分比。

        字号按圆环内可用的宽度自适应：「USB供电」比「100%」宽一截，直接用
        数字那套 0.235 倍字号会顶到环上。绿色与设备弹窗里那一行同色，
        同一个事实（这根线在供电）在两处看起来才是同一件事。
        """
        text = i18n.T("USB供电")
        # 留足内边距：文字贴到环上的话，整个环看起来像被撑满了
        avail = rect.width() * 0.55
        size = max(11, int(side * 0.145))
        while size > 10:
            font = app_font(size, QFont.Weight.Medium, 0.2)
            if QFontMetricsF(font).horizontalAdvance(text) <= avail:
                break
            size -= 1
        p.setFont(app_font(size, QFont.Weight.Medium, 0.2))
        p.setPen(QPen(QColor(self._pal.success)))
        p.drawText(rect, Qt.AlignCenter, text)

    def _paint_charge_flow(self, p: QPainter, rect: QRectF, stroke: float,
                           color: QColor, span_deg: float) -> None:
        """充电状态的能量流动：两束彗尾光带沿电量弧推进 + 轨道上环绕的微光点。"""
        p.save()
        p.setBrush(Qt.NoBrush)

        arc_len = abs(span_deg) or 1.0
        for lo, hi, alpha, width in charge_flow_segments(self._t, span_deg):
            seg = QColor(255, 255, 255)
            seg.setAlpha(alpha)
            p.setPen(QPen(seg, stroke * width, Qt.SolidLine, Qt.RoundCap))
            ang = 90 + span_deg * lo
            p.drawArc(rect, int(round(ang * 16)), int(round(-(hi - lo) * arc_len * 16)))

        # 环绕轨道的微光点
        p.setPen(Qt.NoPen)
        for k in range(3):
            ph = (self._t / 3.6 + k / 3.0) % 1.0
            ang = math.radians(90 - 360 * ph)
            cx = rect.center().x() + rect.width() / 2 * math.cos(ang)
            cy = rect.center().y() - rect.height() / 2 * math.sin(ang)
            dot = QColor(255, 255, 255)
            dot.setAlpha(int(40 + 70 * ((math.sin(ph * math.pi * 2) + 1) / 2)))
            p.setBrush(dot)
            p.drawEllipse(QPointF(cx, cy), stroke * 0.16, stroke * 0.16)
        p.restore()


# --------------------------------------------------------------------------- 信息卡
def edit_controls_qss(pal: theme.Palette) -> str:
    """编辑控件（步进器/输入框/应用取消/挡位章）共用的一套 QSS。

    浅色主题下控件必须「看得见」：早先按钮底只有 rgba(0,0,0,16)、边框用
    淡到几乎透明的 card_border，结果整行步进器在白卡片上看不见，卡片里只剩
    一片空白（用户报：底部模块看不清）。现在按钮统一白底 + 深边框 + 深色
    符号，输入框白底深字，「应用」实心主色、「取消」描边次要色。
    """
    dark = pal.name == "dark"
    txt = pal.text_primary.name()
    sub = pal.text_secondary.name()
    accent = pal.accent.name()
    accent_text = pal.accent_text.name()      # 文字用主色（浅色已达 AA）
    # 「应用」实心按钮的悬停/按下色：之前 hover 直接写成 accent 本身，
    # 鼠标移上去一点变化都没有（用户报「没有颜色标识」）。深色底提亮、
    # 浅色底压深，两个主题都能看出「现在指着它」。
    accent_q = QColor(accent)
    accent_hover = (accent_q.lighter(118) if dark else accent_q.darker(112)).name()
    accent_press = (accent_q.darker(112) if dark else accent_q.darker(122)).name()
    # 英文按钮字（Apply / Stages / Cancel）比中文两字宽一截：卡片三等分后
    # 每个只有 ~31px，11px 下会被压成「A…y」。英文降到 9.5px 才能完整放下，
    # 中文仍用 11px（两字 22px，绰绰有余）
    btn_fs = "9px" if i18n.is_en() else "11px"
    if dark:
        btn_bg = "rgba(255,255,255,34)"
        btn_hover = "rgba(255,255,255,64)"
        edit_bg = "rgba(0,0,0,110)"
        border = "rgba(255,255,255,86)"
        chip_bg = "rgba(255,255,255,26)"
        chip_border = "rgba(255,255,255,58)"
        on_accent = "#08121C"          # 深色主色底上用深字，比白字更清楚
    else:
        btn_bg = "rgba(255,255,255,232)"
        btn_hover = "#ffffff"
        edit_bg = "rgba(255,255,255,245)"
        border = "rgba(16,20,28,78)"    # 比 card_border 深一档，浅底上看得清
        chip_bg = "rgba(255,255,255,214)"
        chip_border = "rgba(16,20,28,52)"
        on_accent = "#ffffff"
    return _add_focus_ring({
        "line": (f"QLineEdit {{ background-color: {edit_bg}; color: {txt};"
                 f" border: 1px solid {border}; border-radius: 7px;"
                 f" font-size: 13px; font-weight: 600; padding: 0 4px; }}"
                 f"QLineEdit:focus {{ border: 2px solid {accent_text}; }}"),
        "step": (f"QToolButton {{ background-color: {btn_bg}; color: {txt};"
                 f" border: 1px solid {border}; border-radius: 7px;"
                 f" font-size: 14px; font-weight: 600; padding: 0; }}"
                 f"QToolButton:hover {{ background-color: {btn_hover};"
                 f" border-color: {accent_text}; color: {accent_text}; }}"
                 f"QToolButton:pressed {{ background-color: {btn_hover}; }}"),
        "apply": (f"QToolButton {{ background-color: {accent}; color: {on_accent};"
                  f" border: 1px solid {accent}; border-radius: 7px;"
                  f" font-size: {btn_fs}; font-weight: 600; padding: 0; }}"
                  f"QToolButton:hover {{ background-color: {accent_hover};"
                  f" border-color: {accent_hover}; }}"
                  f"QToolButton:pressed {{ background-color: {accent_press};"
                  f" border-color: {accent_press}; }}"
                  f"QToolButton:disabled {{ background-color: transparent;"
                  f" color: {pal.text_tertiary.name()}; border-color: {border}; }}"),
        "cancel": (f"QToolButton {{ background-color: transparent; color: {sub};"
                   f" border: 1px solid {border}; border-radius: 7px;"
                   f" font-size: {btn_fs}; font-weight: 600; padding: 0; }}"
                   f"QToolButton:hover {{ background-color: {btn_hover};"
                   f" color: {accent_text}; border-color: {accent_text}; }}"
                   f"QToolButton:pressed {{ background-color: {btn_hover}; }}"),
        "chip": (f"QToolButton {{ background-color: {chip_bg}; color: {txt};"
                 f" border: 1px solid {chip_border}; border-radius: 9px;"
                 f" font-size: 10px; font-weight: 500; padding: 0 7px; }}"
                 f"QToolButton:hover {{ background-color: {btn_hover}; }}"),
        "add": (f"QToolButton {{ background-color: {chip_bg}; color: {accent_text};"
                f" border: 1px dashed {chip_border}; border-radius: 9px;"
                f" font-size: 10px; font-weight: 600; padding: 0 7px; }}"
                f"QToolButton:hover {{ background-color: {btn_hover}; }}"
                f"QToolButton:disabled {{ color: {pal.text_tertiary.name()};"
                f" background-color: transparent; }}"),
        # 挡位选择按钮（回报率用）：不能手打数字，点一下弹挡位面板
        "pick": (f"QToolButton {{ background-color: {btn_bg}; color: {txt};"
                 f" border: 1px solid {border}; border-radius: 8px;"
                 f" font-size: 12px; font-weight: 600; padding: 0 6px; }}"
                 f"QToolButton:hover {{ background-color: {btn_hover};"
                 f" border-color: {accent}; }}"),
        # 挡位管理入口（灵敏度用）：描边 + 主色字，和「取消」并排但更醒目。
        # 文字色用 accent_text：浅色主题下 #007AFF 对白底只有 ≈4.0:1，小字不达 AA
        "levels": (f"QToolButton {{ background-color: transparent; color: {accent_text};"
                   f" border: 1px solid {border}; border-radius: 7px;"
                   f" font-size: {btn_fs}; font-weight: 600; padding: 0; }}"
                   f"QToolButton:hover {{ background-color: {btn_bg}; }}"),
        "tag": f"QLabel {{ color: {sub}; font-size: 9px; font-weight: 600; }}",
    }, pal)


def _add_focus_ring(rules: dict, pal: theme.Palette) -> dict:
    """给编辑控件统一挂焦点环（键盘 Tab 走查时「当前停在哪个按钮」必须看得见）。

    Qt 默认焦点框在自绘卡片里会被 QSS 边框覆盖掉，所以显式补一条
    `:focus` 规则：2px 主题色描边（focus_ring 令牌，带透明度，深浅主题通用）。
    """
    ring = pal.focus_ring
    focus = (f"rgba({ring.red()}, {ring.green()}, {ring.blue()}, "
             f"{round(ring.alpha() / 255, 2)})")
    for key in ("step", "apply", "cancel", "levels", "chip", "add", "pick"):
        if key in rules:
            rules[key] += f"QToolButton:focus {{ border: 2px solid {focus}; }}"
    return rules


class InfoCard(QWidget, AnimMixin):
    """信息卡：默认纯展示；设备可写时点一下进入编辑态。

    展示态**不做任何悬停反馈**——鼠标移上去卡片颜色、阴影一律不变、光标保持
    箭头。之前这里跟着 GlassButton 一起做了 hover 高亮，看着像能点，用户会去点。
    只有 ``set_editable(True)`` 之后才给手型光标和编辑控件；不支持编辑的模块
    右上角常驻一行「由 XX 软件支持」（``set_support_app``），同时这张卡**不可点**，
    点下去毫无反应（用户明确要求：不支持就别给点击动线）。

    「挡位选择」模式（``set_picker(True)``）：回报率用它——不能手打数字，
    进编辑态后卡片里是一个整行下拉按钮「2000 Hz ▾」，点它才弹挡位面板，
    列出鼠标内置的各挡位，当前挡位自动选中。
    """

    edit_requested = Signal()
    value_committed = Signal(int)
    edit_cancelled = Signal()
    picker_requested = Signal()
    levels_requested = Signal()

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._value = "—"
        self._unit = ""
        self._sub = ""
        self._pal = theme.DARK
        self._editable = False
        self._editing = False
        #: 不支持编辑时右上角那行「由 XX 软件支持」里的 XX；空串 = 本软件能改，不挂角标
        self._support_app = ""
        self._kind = ""
        self._caps = None
        self._picker = False
        self._levels_btn = False
        #: 可编辑卡片的悬停进度（0~1）：只有它能点时才动，不可编辑卡保持零反馈
        self._hover = 0.0
        self._pick_values: list[int] = []
        self.setMinimumHeight(78)
        self._build_edit_controls()

    # ------------------------------------------------------------ 编辑控件
    def _build_edit_controls(self) -> None:
        self.edit_box = QWidget(self)
        # 网格布局：两行各占满卡片宽度（嵌套 HBox 会被父布局按 sizeHint 收窄，
        # 按钮就挤在左边），行高按控件实际高，整体在卡片内容区垂直居中
        lay = QGridLayout(self.edit_box)
        # 左右只留 3px：英文按钮字（Apply / Stages / Cancel）比中文两字宽，三等分后
        # 每个按钮只有 31px，边距多给一点就会被压成省略号
        lay.setContentsMargins(3, 2, 3, 6)
        lay.setHorizontalSpacing(4)
        lay.setVerticalSpacing(5)
        # 整组控件在卡片内容区水平+垂直居中：窄卡里两侧留白，视觉不挤
        lay.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(3)
        self.btn_minus = QToolButton()
        self.btn_minus.setText("−")
        self.btn_minus.setObjectName("step")
        self.btn_minus.setFixedSize(22, 22)
        self.edit_line = QLineEdit()
        self.edit_line.setAlignment(Qt.AlignCenter)
        self.edit_line.setFixedSize(50, 22)
        self.btn_plus = QToolButton()
        self.btn_plus.setText("+")
        self.btn_plus.setObjectName("step")
        self.btn_plus.setFixedSize(22, 22)
        row1.addWidget(self.btn_minus)
        row1.addWidget(self.edit_line)
        row1.addWidget(self.btn_plus)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(4)
        self.btn_apply = QToolButton()
        self.btn_apply.setText(i18n.T("应用"))
        self.btn_apply.setObjectName("apply")
        self.btn_apply.setFixedHeight(21)
        self.btn_cancel = QToolButton()
        self.btn_cancel.setText(i18n.T("取消"))
        self.btn_cancel.setObjectName("cancel")
        self.btn_cancel.setFixedHeight(21)
        # 「挡位」入口：仅灵敏度卡（DPI 可编辑且挡位可增删）显示，点它弹出
        # 竖向挡位管理面板（LevelsPicker），不占数字步进器的位置。
        self.btn_levels = QToolButton()
        self.btn_levels.setText(i18n.T("挡位"))
        self.btn_levels.setObjectName("levels")
        self.btn_levels.setFixedHeight(21)
        self.btn_levels.setCursor(Qt.PointingHandCursor)
        self.btn_levels.setToolTip(i18n.T("管理 DPI 挡位（切换 / 增加 / 删除）"))
        self.btn_levels.clicked.connect(self.levels_requested.emit)
        self.btn_levels.hide()
        row2.addWidget(self.btn_apply, 1)
        row2.addWidget(self.btn_levels, 1)
        row2.addWidget(self.btn_cancel, 1)

        lay.addLayout(row1, 0, 0)
        lay.addLayout(row2, 1, 0)
        self.edit_box.hide()

        # ---- 挡位选择模式（回报率用）----
        # 不给数字输入框：一个整行按钮「2000 Hz ▾」，点一下由主窗口弹出
        # 鼠标内置的挡位面板来选。面板弹出后自动选中当前已设置的挡位。
        # 第二行给「取消」：回报率是选完即写、没有「应用」，缺了它就只能靠
        # 选一档才能退出编辑态（DPI 也不可写时整窗会卡在编辑态）。
        self.pick_box = QWidget(self)
        pl = QGridLayout(self.pick_box)
        pl.setContentsMargins(6, 2, 6, 6)
        pl.setHorizontalSpacing(4)
        pl.setVerticalSpacing(5)
        pl.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.btn_pick = QToolButton()
        self.btn_pick.setText("—")
        self.btn_pick.setObjectName("pick")
        self.btn_pick.setFixedHeight(24)
        self.btn_pick.setCursor(Qt.PointingHandCursor)
        self.btn_pick.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_pick.setToolTip(i18n.T("选择回报率挡位"))
        self.btn_pick.clicked.connect(self.picker_requested.emit)
        self.btn_rate_cancel = QToolButton()
        self.btn_rate_cancel.setText(i18n.T("取消"))
        self.btn_rate_cancel.setObjectName("cancel")
        self.btn_rate_cancel.setFixedHeight(21)
        self.btn_rate_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_rate_cancel.clicked.connect(self.edit_cancelled.emit)
        pl.addWidget(self.btn_pick, 0, 0)
        # 取消居中：跟下拉框同宽会像第二个主操作，贴左又显得没对齐
        pl.addWidget(self.btn_rate_cancel, 1, 0, Qt.AlignHCenter)
        self.pick_box.hide()

        self.btn_minus.clicked.connect(lambda: self._step(-1))
        self.btn_plus.clicked.connect(lambda: self._step(1))
        self.btn_apply.clicked.connect(self._commit)
        self.btn_cancel.clicked.connect(self.edit_cancelled.emit)
        self.edit_line.returnPressed.connect(self._commit)
        self._apply_edit_style()

    def _apply_edit_style(self) -> None:
        """逐控件下发样式。

        样式表挂在父容器上**传不到子控件**（InfoCard 自绘背景，Qt 的
        样式表传播在这里失效）——控件会退回系统深色调色板渲染，浅色主题下
        输入框和 ± 按钮几乎看不见（用户报「底部模块看不清」）。所以每个
        控件单独 setStyleSheet。
        """
        qss = edit_controls_qss(self._pal)
        self.edit_line.setStyleSheet(qss["line"])
        self.btn_minus.setStyleSheet(qss["step"])
        self.btn_plus.setStyleSheet(qss["step"])
        self.btn_apply.setStyleSheet(qss["apply"])
        self.btn_cancel.setStyleSheet(qss["cancel"])
        self.btn_levels.setStyleSheet(qss["levels"])
        self.btn_pick.setStyleSheet(qss["pick"])
        self.btn_rate_cancel.setStyleSheet(qss["cancel"])
        # 键盘走查：Tab 依次停在 −/输入框/+/应用/挡位/取消/选挡位，焦点环由
        # _add_focus_ring 统一给；这里必须显式 StrongFocus，否则部分平台
        # QToolButton 默认不进 Tab 链，焦点环永远不出现。
        for w in (self.btn_minus, self.btn_plus, self.btn_apply,
                  self.btn_cancel, self.btn_levels, self.btn_pick,
                  self.btn_rate_cancel):
            w.setFocusPolicy(Qt.StrongFocus)
        # 「应用 / 取消 / ± 」以前没设手型光标，指上去还是箭头（用户报）。
        # QSS 不支持 cursor 属性，只能在这里逐控件设。
        for w in (self.btn_minus, self.btn_plus, self.btn_apply, self.btn_cancel):
            w.setCursor(Qt.PointingHandCursor)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.edit_box.setGeometry(0, 24, self.width(), max(0, self.height() - 25))
        self.pick_box.setGeometry(0, 24, self.width(), max(0, self.height() - 25))

    def _hover_ok(self) -> bool:
        """只有「这一下点下去真能进编辑」的卡片才做悬停反馈。"""
        return self._editable and not self._editing

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._hover_ok():
            self._animate_to("_hover", 1.0, 150)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover > 0.001:
            self._animate_to("_hover", 0.0, 200)
        super().leaveEvent(event)

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        self._apply_edit_style()
        self.update()

    def set_editable(self, editable: bool) -> None:
        """设备是否允许改这个模块的数值。"""
        if editable != self._editable:
            self._editable = editable
            if not editable:
                self._hover = 0.0        # 变成不可点：悬停高亮必须立刻撤掉
            if not editable and self._editing:
                self.stop_edit()
            self._update_cursor()
            self.update()

    def set_picker(self, on: bool) -> None:
        """回报率用：不能手打数字，编辑态给一个下拉按钮，点它弹挡位面板。"""
        self._picker = bool(on)
        self._update_cursor()

    def set_levels_button(self, on: bool) -> None:
        """灵敏度用：编辑态多一个「挡位」按钮，点它弹竖向挡位管理面板。"""
        self._levels_btn = bool(on)

    def set_pick_values(self, values) -> None:
        """鼠标内置的挡位（回报率档位）；空列表 = 没有可选项。"""
        self._pick_values = [int(v) for v in (values or [])]
        self._update_cursor()

    def _update_cursor(self) -> None:
        """能点就给手型光标。

        只看 ``_editable``：挡位模式下「读得到挡位但写不回去」也算不可点
        （用户要求：不支持编辑就不给点击动线，连碰都不该变手型）。
        """
        self.setCursor(Qt.PointingHandCursor if self._editable else Qt.ArrowCursor)

    def set_support_app(self, app: str) -> None:
        """不支持编辑时，右上角常驻「由 XX 软件支持」；空串 = 本软件能改，不挂角标。

        只存软件名（G HUB / 官方软件），整句模板和翻译都在 paintEvent 里拼，
        切语言时不用重新 set 一次。
        """
        app = app or ""
        if app != self._support_app:
            self._support_app = app
            self.update()

    @property
    def support_app(self) -> str:
        return self._support_app

    @property
    def editing(self) -> bool:
        return self._editing

    def start_edit(self, value: int, caps, kind: str) -> None:
        """kind: "dpi" / "rate"；caps 是 devcaps.DeviceCaps。"""
        if not self._editable:
            return
        self._editing = True
        self._caps = caps
        self._kind = kind
        if self._picker:
            # 挡位选择模式：卡片内不出现数字输入框，只有一个「选挡位」按钮
            self.edit_box.hide()
            self.btn_pick.setText(f"{int(value)} Hz  ▾")
            self.pick_box.show()
        else:
            self.edit_line.setText(str(value))
            self.edit_box.show()
            self.pick_box.hide()
            # 「挡位」入口按钮只在非 picker 且开了 levels_button 时出现
            self.btn_levels.setVisible(self._levels_btn)
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def current_value(self) -> int:
        """编辑框里的当前数值（未对齐）。"""
        return self._current()

    def stop_edit(self) -> None:
        self._editing = False
        self.edit_box.hide()
        self.pick_box.hide()
        self.btn_levels.hide()
        self._update_cursor()
        self.update()

    def _current(self) -> int:
        try:
            return int(self.edit_line.text().strip())
        except ValueError:
            return 0

    def _step(self, direction: int) -> None:
        cur = self._current()
        caps = self._caps
        if self._kind == "rate" and caps is not None and caps.rate_values:
            vals = sorted(caps.rate_values)
            idx = min(range(len(vals)), key=lambda i: abs(vals[i] - cur))
            value = vals[max(0, min(len(vals) - 1, idx + direction))]
        else:
            # 步进跟着设备走：G502 是 50，某些型号是 100/400，别写死
            step = caps.input_step() if caps is not None else 50
            value = cur + direction * step
            if caps is not None:
                value = caps.align_dpi(value)
        self.edit_line.setText(str(value))

    def _commit(self) -> None:
        value = self._current()
        caps = self._caps
        if caps is not None:
            value = (caps.align_dpi(value) if self._kind == "dpi"
                     else caps.align_rate(value))
        self.value_committed.emit(int(value))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 点卡片 = 进编辑态（灵敏度给步进器、回报率给「2000 Hz ▾」下拉）。
        # 回报率以前是「点一下直接弹浮层、卡片本身毫无变化」，看着像没反应；
        # 现在统一先进卡片内的编辑模块，再点那个下拉按钮才弹挡位面板。
        # 不可编辑的模块一个分支都不走 —— 点了不该有任何动静。
        if not self._editing and self._editable:
            self.edit_requested.emit()
        super().mousePressEvent(event)

    def set_content(self, value: str, unit: str = "", sub: str = "") -> None:
        if (value, unit, sub) != (self._value, self._unit, self._sub):
            self._value, self._unit, self._sub = value, unit, sub
            self.update()

    def set_title(self, title: str) -> None:
        """换卡片标题（切语言时用）；内容不变，只重排一次。"""
        if title != self._title:
            self._title = title
            self.update()

    def retranslate(self) -> None:
        """切语言后重设卡片里的控件文案。

        这些是真正的 QToolButton，文字不是画出来的，光 `update()` 不会变；
        其余文字（标题 / 副标题 / 角标）都在 paintEvent 里即时翻译。
        """
        self.btn_apply.setText(i18n.T("应用"))
        self.btn_cancel.setText(i18n.T("取消"))
        self.btn_rate_cancel.setText(i18n.T("取消"))
        self.btn_levels.setText(i18n.T("挡位"))
        self.btn_levels.setToolTip(i18n.T("管理 DPI 挡位（切换 / 增加 / 删除）"))
        self.btn_pick.setToolTip(i18n.T("选择回报率挡位"))
        # 英文按钮字更宽，QSS 里的字号也跟着语言走，得重挂一次样式
        self._apply_edit_style()
        self.update()

    #: 有角标时标题缩到 9px：英文标题（Sensitivity）在 10px 下几乎占满卡片，
    #: 不留出这几像素，角标只能被截成「via G…」
    BADGE_TITLE_PX = 9

    #: 少数「不支持」不值得写成「由 XX 支持」的情形，直接换整句
    #: （键是 `set_support_app` 传进来的值；没有占位符，format 不会报错）
    SUPPORT_LABELS = {"按键": "按键调节"}

    def support_label(self) -> str:
        """角标整句：「由 G HUB 支持」/「via G HUB」；没挂角标时是空串。

        设备本身没有厂商通道时（狼途这类，`support_app == "按键"`）走另一句
        「按键调节」——它根本没有官方软件可指向，写成「由 按键 支持」不通。
        """
        if not self._support_app:
            return ""
        tpl = self.SUPPORT_LABELS.get(self._support_app, "由 {app} 支持")
        return i18n.T(tpl).format(app=i18n.T(self._support_app))

    def _paint_support_badge(self, p: QPainter, rect: QRectF, pal) -> None:
        """右上角常驻一行「由 XX 软件支持」——只在不支持编辑的模块上出现。

        卡片只有 113px 宽，标题又占掉左边，留给角标的横向空间只有 35~57px：
        先按 8px 排，放不下降一档 7px，再放不下才用省略号收尾，绝不去压标题。
        只有「不支持」才挂它（可编辑时 ``_support_app`` 是空串），所以这行字
        一直看得见、不会闪，也不会被当成按钮（卡片本身不给手型 / 悬停）。
        """
        label = self.support_label()
        title_w = QFontMetricsF(
            app_font(self.BADGE_TITLE_PX, QFont.Weight.Medium, 0.8)
        ).horizontalAdvance(i18n.T(self._title))
        avail = max(20.0, rect.width() - 13 - title_w - 6 - 6)
        size = 8.0
        font = app_font(size, QFont.Weight.Normal, 0.2)
        fm = QFontMetricsF(font)
        # 留 5px 间距再降字号：不留的话角标会贴着标题，读起来像标题的续写
        # （「回报率由 G HUB 支持」一长串）
        while size > 7.0 and fm.horizontalAdvance(label) > avail - 5:
            size -= 1.0
            font = app_font(size, QFont.Weight.Normal, 0.2)
            fm = QFontMetricsF(font)
        text = label
        if fm.horizontalAdvance(label) > avail:
            text = fm.elidedText(label, Qt.ElideRight, int(avail))
        p.setFont(font)
        # 比标题再淡一档：标题 9px、角标 8px 又挨着，同色会被读成「灵敏度由 G HUB 支持」
        # 一长串；压暗之后才像一行的注解而不是标题的续写
        color = QColor(pal.text_secondary)
        color.setAlpha(120)
        p.setPen(QPen(color))
        p.drawText(QRectF(rect.right() - 8 - avail, rect.top() + 10, avail, 14),
                   Qt.AlignRight | Qt.AlignVCenter, text)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        hover = self._hover if self._hover_ok() else 0.0
        paint_glass(p, rect, 17, pal, backdrop=blurred_backdrop(self),
                    hover=hover, shadow_strength=0.75, glow=None)
        # 悬停预告「这里能点」：一圈 1.2px 主题色描边（不可编辑卡永不出现在此）
        if hover > 0.02:
            ring = QColor(pal.accent)
            ring.setAlpha(int(120 * hover))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.2))
            p.drawPath(rounded(rect, 17))

        left = rect.left() + 13
        show_badge = bool(self._support_app) and not self._editing
        p.setFont(app_font(self.BADGE_TITLE_PX if show_badge else 10,
                           QFont.Weight.Medium, 0.8))
        # 标题用次级色而非三级色：浅色主题下三级色太淡，「灵敏度」几乎看不见
        p.setPen(QPen(pal.text_secondary))
        p.drawText(QRectF(left, rect.top() + 10, rect.width() - 20, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, i18n.T(self._title))
        if show_badge:
            self._paint_support_badge(p, rect, pal)

        if self._editing:
            return          # 数值 / 副标题让位给卡片内的步进控件

        value_font = app_font(17, QFont.Weight.DemiBold, -0.2)
        vfm = QFontMetricsF(value_font)
        unit_font = app_font(10, QFont.Weight.Medium, 0.2)
        ufm = QFontMetricsF(unit_font)
        avail = rect.width() - 26
        unit_w = (ufm.horizontalAdvance(self._unit) + 6) if self._unit else 0
        size = 17
        while size > 11 and vfm.horizontalAdvance(self._value) + unit_w > avail:
            size -= 1
            value_font = app_font(size, QFont.Weight.DemiBold, -0.2)
            vfm = QFontMetricsF(value_font)
        p.setFont(value_font)
        p.setPen(QPen(pal.text_primary))
        p.drawText(QRectF(left, rect.top() + 27, rect.width() - 24, 22),
                   Qt.AlignLeft | Qt.AlignVCenter, self._value)
        if self._unit:
            val_w = vfm.horizontalAdvance(self._value)
            p.setFont(unit_font)
            p.setPen(QPen(pal.text_secondary))
            p.drawText(QRectF(left + val_w + 6, rect.top() + 31, 40, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, self._unit)

        if self._sub:
            p.setFont(app_font(9, QFont.Weight.Normal, 0.3))
            p.setPen(QPen(pal.text_secondary))
            p.drawText(QRectF(left, rect.bottom() - 21, rect.width() - 20, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, i18n.T(self._sub))


# --------------------------------------------------------------------------- 开关
class Switch(QWidget, AnimMixin):
    """iOS 风格开关。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if checked else 0.0
        self._hover = 0.0
        self._pal = theme.DARK
        self._enabled = True
        self.setFixedSize(42, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool, animate: bool = True) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        target = 1.0 if checked else 0.0
        if animate:
            self._animate_to("_pos", target, 210, QEasingCurve.OutCubic)
        else:
            self._pos = target
            self.update()

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        self.update()

    def set_enabled_state(self, on: bool) -> None:
        self._enabled = bool(on)
        self.setEnabled(self._enabled)
        # 禁用的开关必须回到箭头：显式的 PointingHandCursor 在禁用状态下
        # 也不会自动失效，留着就是「看着能点、点了没反应」
        if self._enabled:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()
        if not self._enabled:
            self._hover = 0.0
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            event.accept()
            self.set_checked(not self._checked)
            self.toggled.emit(self._checked)
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        dark = pal.name == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        t = max(0.0, min(1.0, self._pos))
        if not self._enabled:
            # 关掉的开关整块压暗：只减 track 的 alpha 时，开着的绿色依旧扎眼，
            # 看上去像「还能点」
            p.setOpacity(0.45)

        # 扁平实心轨道：不做折射底图、镜面高光、内缘阴影和外发光
        # 深色下卡片本身是浅一点的玻璃灰，轨道要提亮到 #48484A 才看得见
        off = QColor(72, 72, 74) if dark else QColor(222, 222, 228)
        on = QColor(pal.success)
        track = mix_color(off, on, t)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(rect, radius, radius)

        # 白色圆钮：固定大小，不随悬停缩放（用户不喜欢那个大小切换动画）
        knob_r = radius - 2.6
        travel = rect.width() - knob_r * 2 - 5.2
        cx = rect.left() + 2.6 + knob_r + travel * t
        cy = rect.center().y()

        # 白色圆钮：只留一层柔和投影，去掉径向渐变与顶部镜面反光
        for grow, alpha in ((1.6, 24), (0.8, 48)):
            p.setBrush(QColor(0, 0, 0, int(alpha * (0.6 if dark else 1.0))))
            p.drawEllipse(QPointF(cx, cy + 1.0), knob_r + grow * 0.4, knob_r + grow * 0.4)

        p.setBrush(QColor(255, 255, 255, 255))
        p.drawEllipse(QPointF(cx, cy), knob_r, knob_r)


class Segment(QWidget, AnimMixin):
    """iOS 风格分段控件：扁平实心凹槽 + 滑动的纯白胶囊 + 文字淡入淡出。"""

    changed = Signal(int)

    PAD = 2.0

    def __init__(self, items: list[str], index: int = 0, width: int = 138, parent=None) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._index = max(0, min(len(self._items) - 1, int(index)))
        self._pos = float(self._index)
        self._hover = -1
        self._pal = theme.DARK
        self._enabled = True
        self.setFixedSize(width, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)

    # ---------------------------------------------------------------- 状态
    def index(self) -> int:
        return self._index

    def set_index(self, index: int, animate: bool = True) -> None:
        index = max(0, min(len(self._items) - 1, int(index)))
        if index == self._index:
            return
        self._index = index
        if animate:
            self._animate_to("_pos", float(index), 240, QEasingCurve.OutCubic)
        else:
            self._pos = float(index)
            self.update()

    def set_labels(self, items: list[str], keep_index: bool = True) -> None:
        """换一套文字（切语言时用）：段数不变，选中项默认保持不变。"""
        items = list(items)
        if items == self._items:
            return
        self._items = items
        if not keep_index:
            self._index = min(self._index, len(items) - 1)
        self._pos = float(self._index)
        self.update()

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        self.update()

    def set_enabled_state(self, on: bool) -> None:
        self._enabled = bool(on)
        self.setEnabled(self._enabled)
        if self._enabled:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()
            self._hover = -1
        self.update()

    # ---------------------------------------------------------------- 交互
    def _seg_width(self) -> float:
        return (self.width() - self.PAD * 2) / max(1, len(self._items))

    def _index_at(self, x: float) -> int:
        rel = (x - self.PAD) / max(1.0, self._seg_width())
        return max(0, min(len(self._items) - 1, int(rel)))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._enabled:
            idx = self._index_at(event.position().x())
            if idx != self._index:
                self.set_index(idx)
                self.changed.emit(idx)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        idx = self._index_at(event.position().x())
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        dark = pal.name == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        path = rounded(rect, radius)
        if not self._enabled:
            p.setOpacity(0.45)

        # 扁平凹槽：单色实心，不做内凹渐变、底缘折射亮线和白色描边
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26) if dark else QColor(120, 120, 128, 36))
        p.drawPath(path)

        # 选中胶囊（带滑动的中间态）
        seg_w = self._seg_width()
        pos = max(0.0, min(float(len(self._items) - 1), self._pos))
        pill = QRectF(rect.left() + self.PAD + seg_w * pos,
                      rect.top() + self.PAD,
                      seg_w, rect.height() - self.PAD * 2)
        pill_radius = pill.height() / 2

        draw_outer_shadow(p, pill, pill_radius,
                          QColor(0, 0, 0, 80 if dark else 34),
                          layers=4, strength=0.15, dy=1.0)
        pill_path = rounded(pill, pill_radius)
        p.setPen(Qt.NoPen)
        # 纯白 / 半透明白：去掉原来的竖向渐变和顶部高光，与开关的圆钮同一套底色
        p.setBrush(QColor(255, 255, 255, 112) if dark else QColor(255, 255, 255))
        p.drawPath(pill_path)

        # 文字
        font_on = app_font(10, QFont.Weight.DemiBold, 0.2)
        font_off = app_font(10, QFont.Weight.Medium, 0.2)
        for i, text in enumerate(self._items):
            cell = QRectF(rect.left() + self.PAD + seg_w * i,
                          rect.top() + self.PAD, seg_w, rect.height() - self.PAD * 2)
            near = max(0.0, 1.0 - abs(pos - i))
            if i == self._index:
                color = QColor(pal.text_primary)
                font = font_on
            else:
                color = QColor(pal.text_secondary)
                color.setAlpha(150 + int(70 * near) + (18 if i == self._hover else 0))
                font = font_off
            p.setFont(font)
            p.setPen(QPen(color))
            p.drawText(cell, Qt.AlignCenter, text)

class DeviceMenu(QWidget):
    """设备切换弹窗：独立的无边框浮层，四角干净、当前设备用绿色对勾。

    为什么既不是 QMenu / Qt.Popup，也不是主窗口里的子控件：

    * `QMenu.exec()` / `Qt.Popup` 会抢鼠标 grab，点标题想「再点一次收起」时，
      按下的那一下就被弹出层吃掉了，做不到同一个按钮开关。
    * 做成主窗口的子控件确实没有 grab，但它和标题、电量环、说明文字这些兄弟
      控件**互相重叠**。Qt 对重叠兄弟控件的绘制没有保证：标题区每 110ms 呼吸
      一次，重绘时会把浮层糊掉——v1.2.52 现场看到的就是被横向切掉半行、
      底下还叠着一层残影。

    所以用一个不接受键盘焦点、不抢 grab 的顶层工具窗口：它有自己的绘制表面，
    不会跟主窗口的控件抢图层；点外面收起交给主窗口的全局鼠标钩子。
    """

    chosen = Signal(str)
    closed = Signal()

    ROW_H = 32.0
    PAD = 7.0
    RADIUS = 12.0
    ICON = 14.0          # 当前设备的对勾
    CHECK_X = 7.0
    KIND = 12.0          # 品类图标（鼠标 / 键盘 / 耳机）
    KIND_X = 24.0        # 对勾右侧，图标竖着排成一条线，每行都对齐
    TEXT_X = 41.0        # 勾位 7 + 勾 14 + 品类图标 12 + 间距 8
    PAD_R = 24.0         # 右侧留白：屏幕排版比度量值能宽出几个像素，留足余量
    SHADOW = 10.0        # 窗口四周预留的阴影边距：内容区从 (SHADOW, SHADOW) 开始
    #: 电量标识：固定宽度 + 统一贴右缘，不随型号长短漂移
    BATT_GAP = 14.0      # 型号文字与电量之间的最小空隙
    BATT_R_INSET = 11.0  # 电量离单元格右边缘的距离

    def __init__(self, rows: list[dict], pal: theme.Palette, parent=None,
                 low: int | None = None) -> None:
        super().__init__(
            parent,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._pal = pal
        self._rows = [dict(r) for r in rows]
        self._hover = -1
        # 低电量阈值跟设置里的可调阈值走，红/黄分档才和电量环、通知一致
        self._low = theme.LOW_BATTERY if low is None else int(low)

        font = app_font(12, QFont.Weight.Medium, 0.1)
        fm = QFontMetricsF(font)
        # 名字的实际占位要按**绘制时的排法**量：品牌用 11px DemiBold、型号用
        # 12px，两段拼起来的宽度会比「整串用 12px 量」宽一点。只按整串量的话，
        # 电量列一变宽，型号尾巴就被裁掉（狼途那行 "…Mouse" 会被切掉 e）。
        bfm = QFontMetricsF(self._brand_font())
        widest = 0.0
        for row in self._rows:
            brand, model = self._split(row)
            if brand:
                width = bfm.horizontalAdvance(brand) + 4.0 + fm.horizontalAdvance(model)
            else:
                width = fm.horizontalAdvance(self._label(row))
            widest = max(widest, width)
        # 电量列定宽，每行都占同一块地方，右边缘才会齐。列宽取两种内容的较宽
        # 者：读得到电池的写 "100%"，纯有线鼠标写「USB供电」——后者比三个数字
        # 宽一截，按 "100%" 量的话弹窗会被那行字顶宽/或挤到型号上。
        self._batt_font = app_font(11, QFont.Weight.Medium, 0.2)
        _bfm = QFontMetricsF(self._batt_font)
        self._batt_w = max(QFontMetricsF(font).horizontalAdvance("100%"),
                           _bfm.horizontalAdvance(i18n.T("USB供电")),
                           _bfm.horizontalAdvance(i18n.T("无法读取")))
        # 宽度按最长的那条「品牌 + 型号」算，再加上电量列，别让名字被截断
        self._width = max(200.0, widest + self.TEXT_X + self.BATT_GAP
                          + self._batt_w + self.PAD_R)
        self._height = self.PAD * 2 + self.ROW_H * len(self._rows)
        # 顶层窗口不会在 show 时自动套用 _width/_height，必须显式定尺寸
        # 尺寸 = 内容 + 四周阴影边距，阴影画在窗口内不越界
        self.setFixedSize(int(round(self._width + self.SHADOW * 2)),
                          int(round(self._height + self.SHADOW * 2)))

    # ---------------------------------------------------------------- 数据
    @staticmethod
    def _label(row: dict) -> str:
        text = str(row.get("label", ""))
        return text if row.get("available", True) else text + i18n.T("（未连接）")

    @staticmethod
    def _split(row: dict) -> tuple[str, str]:
        """(品牌, 型号)；缺一个就返回 ("", "")，退回整串单色绘制。

        品牌主色、型号次级灰——和标题栏同一套读法，同一台设备在两处看起来
        才是同一个东西。
        """
        brand = str(row.get("brand", "") or "").strip()
        model = str(row.get("model", "") or "").strip()
        if brand and model and not row.get("available", True):
            return "", ""          # 未连接时整串压淡，不再分两档
        if brand and model:
            return brand, model
        return "", ""

    @staticmethod
    def _switchable(row: dict) -> bool:
        """这一行能不能点着切过去。

        键盘、耳机这类配件是**只展示**的：主程序的 DPI / 回报率 / 电量环
        都对它们不成立，切过去只会把界面切成一堆「无法读取」。默认 True，
        配件行由数据层显式写 `switchable=False`。
        """
        return bool(row.get("available", True)) and bool(row.get("switchable", True))

    @staticmethod
    def _brand_font() -> QFont:
        return app_font(11, QFont.Weight.DemiBold, 0.4)

    def frame_rect(self) -> QRectF:
        """内容区：从阴影边距处开始，阴影画在它外面一圈。"""
        return QRectF(self.SHADOW, self.SHADOW, self._width, self._height)

    def show_below(self, anchor: QPoint, max_right: float) -> None:
        """摆在锚点（标题区底边，屏幕坐标）下方：右边缘不越过 max_right。"""
        x = max(4.0, min(float(anchor.x()), max_right - self._width))
        # 内容区在窗口内偏移了 SHADOW，锚点对准内容区左上角 → 窗口要左移 SHADOW
        self.move(int(round(x - self.SHADOW)), int(anchor.y() - self.SHADOW))
        self.show()
        self.raise_()

    def hide_menu(self) -> None:
        if self.isVisible():
            self.hide()

    # ---------------------------------------------------------------- 交互
    def _index_at(self, pos) -> int:
        if pos.x() < 0 or pos.x() > self.width():
            return -1
        # 内容区从 (SHADOW, SHADOW) 开始，行判断要在内容坐标系里做
        idx = int((pos.y() - self.SHADOW - self.PAD) // self.ROW_H)
        return idx if 0 <= idx < len(self._rows) else -1

    def wants_hand(self, global_pos: QPoint) -> bool:
        """这个屏幕坐标算不算可点的行。

        主窗口的浮窗轮询每 30ms 强推一次 SetCursor，菜单自己在 mouseMoveEvent
        里设的手型会被它按回箭头，所以判定要暴露出来给轮询共用一份。
        """
        if not self.isVisible():
            return False
        idx = self._index_at(QPointF(self.mapFromGlobal(global_pos)))
        return idx >= 0 and self._switchable(self._rows[idx])

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        idx = self._index_at(event.position())
        if idx != self._hover:
            self._hover = idx
            usable = idx >= 0 and self._switchable(self._rows[idx])
            if usable:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            idx = self._index_at(event.position())
            if idx >= 0 and self._switchable(self._rows[idx]):
                key = str(self._rows[idx].get("key", ""))
                self.hide()
                self.chosen.emit(key)
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self.closed.emit()
        super().hideEvent(event)

    # ---------------------------------------------------------------- 绘制
    def _check_color(self) -> QColor:
        return QColor(self._pal.success)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        dark = pal.name == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        fr = self.frame_rect()
        # 外圈柔和投影：让弹窗从背景浮起来（用户要求弹窗加阴影）
        draw_outer_shadow(p, fr, self.RADIUS, QColor(0, 0, 0, 90 if dark else 55),
                          layers=7, strength=0.3, dy=0.9)
        # 主体：一块干净的圆角实心
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.surface_glass_solid))
        p.drawPath(rounded(fr, self.RADIUS))

        label_font = app_font(12, QFont.Weight.Medium, 0.1)
        batt_font = app_font(12, QFont.Weight.DemiBold, 0.1)
        for i, row in enumerate(self._rows):
            usable = bool(row.get("available", True))
            switchable = self._switchable(row)
            cell = QRectF(fr.left() + self.PAD, fr.top() + self.PAD + i * self.ROW_H,
                          self._width - self.PAD * 2, self.ROW_H)
            if i == self._hover and switchable:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 12))
                p.drawRoundedRect(cell, 8, 8)
            if row.get("current"):
                icon_check(p, QRectF(cell.left() + self.CHECK_X,
                                     cell.center().y() - self.ICON / 2,
                                     self.ICON, self.ICON), self._check_color())
            color = QColor(pal.text_primary if usable else pal.text_tertiary)
            # 品类图标压得比文字淡一档：它是标签，不是内容
            icon_color = QColor(pal.text_secondary if usable else pal.text_tertiary)
            if not usable:
                color.setAlpha(120)
                icon_color.setAlpha(120)
            elif not switchable:
                icon_color.setAlpha(170)
            icon_kind(p, QRectF(cell.left() + self.KIND_X,
                                cell.center().y() - self.KIND / 2,
                                self.KIND, self.KIND),
                      str(row.get("kind") or "mouse"), icon_color)
            # 文字区右边界扣掉电量列：名字再长也压不到百分比上
            text_rect = QRectF(cell.left() + self.TEXT_X, cell.top(),
                               cell.width() - self.TEXT_X - self.BATT_GAP - self._batt_w,
                               cell.height())
            brand, model = self._split(row)
            if brand:
                fb = self._brand_font()
                bw = QFontMetricsF(fb).horizontalAdvance(brand) + 4.0
                # 品牌主色、型号次级灰：和标题栏同一套层次（身份压住、型号退后）
                mcolor = QColor(pal.text_secondary if usable else pal.text_tertiary)
                if not usable:
                    mcolor.setAlpha(120)
                p.setFont(fb)
                p.setPen(QPen(color))
                p.drawText(QRectF(text_rect.left(), text_rect.top(), bw, text_rect.height()),
                           Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, brand)
                p.setFont(label_font)
                p.setPen(QPen(mcolor))
                p.drawText(QRectF(text_rect.left() + bw, text_rect.top(),
                                  max(0.0, text_rect.width() - bw), text_rect.height()),
                           Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, model)
            else:
                p.setFont(label_font)
                p.setPen(QPen(color))
                p.drawText(text_rect,
                           Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, self._label(row))
            self._paint_battery(p, cell, row, batt_font, usable)

    def _battery_box(self, cell: QRectF) -> QRectF:
        """电量标识的定宽矩形：右缘固定贴 cell 右侧内缩处，每行完全一致。"""
        return QRectF(cell.right() - self.BATT_R_INSET - self._batt_w, cell.top(),
                      self._batt_w, cell.height())

    def battery_text(self, row: dict) -> str:
        """行尾写什么：有线写「USB供电」、有读数写「NN%」、没有就空着。

        **纯有线鼠标不写百分比**：有线机型读到的「100%」不是电池电量
        （罗技那边是 `WIRED_BATTERY` 兜底值），写出来会让人以为那是一块满电
        的电池；改成「USB供电」，跟电量环同一个说法。

        配件（键盘 / 耳机）走同一套规则，区别在最后一行：鼠标读不到电量就
        留白（免得天天挂一句废话），配件是无线却没读数时写「无法读取」——
        用户明确要求无线要显示电量，读不到就得如实说，不能空着让人猜。
        """
        if row.get("wired"):
            return i18n.T("USB供电")
        level = row.get("battery")
        if isinstance(level, int) and level >= 0:
            return f"{level}%"
        if str(row.get("kind") or "mouse") != "mouse":
            return i18n.T("无法读取")
        return ""

    def _paint_battery(self, p: QPainter, cell: QRectF, row: dict,
                       font, usable: bool) -> None:
        """行尾电量：定宽矩形右对齐，每行右边缘严格对齐，不随型号长短漂。

        颜色按 `theme.battery_color()` 分档（≤阈值红 / 阈值+15 内琥珀 / 更高绿），
        跟电量环、低电量通知共用一套规则，避免三处颜色打架。有线鼠标的
        「USB供电」用成功绿：它不是警告，只是说明这根线在供电。
        """
        text = self.battery_text(row)
        if not text:
            return              # 未连接：没有电量就不占视觉噪音
        if row.get("wired"):
            p.setFont(self._batt_font)
            p.setPen(QPen(QColor(self._pal.text_tertiary if not usable
                                 else self._pal.success)))
            p.drawText(self._battery_box(cell),
                       Qt.AlignRight | Qt.AlignVCenter | Qt.TextSingleLine, text)
            return
        level = row.get("battery")
        if not isinstance(level, int) or level < 0:
            # 无线但读不到（配件）：灰字如实说，不上红绿分档
            p.setFont(self._batt_font)
            p.setPen(QPen(QColor(self._pal.text_tertiary)))
            p.drawText(self._battery_box(cell),
                       Qt.AlignRight | Qt.AlignVCenter | Qt.TextSingleLine, text)
            return
        tint = theme.battery_color(self._pal, int(level), self._low)
        if not usable:
            tint = QColor(self._pal.text_tertiary)
        p.setFont(font)
        p.setPen(QPen(tint))
        p.drawText(self._battery_box(cell), Qt.AlignRight | Qt.AlignVCenter | Qt.TextSingleLine,
                   text)


class RatePicker(QWidget):
    """回报率挡位弹窗：列出鼠标内置的刷新率挡位，当前挡位自动打勾。

    跟 DeviceMenu 同一套架构——顶层无边框工具窗口：
    不抢鼠标 grab（点外面能自然收起）、不接受键盘焦点、不跟主窗口的兄弟
    控件抢图层（弹出层画在自己的表面上）。

    回报率**不允许手动输入数字**：这里列出的每一档都是设备自己上报的
    可选值，弹出来自动给当前已设置的那档打勾，选哪档就写哪档。
    """

    chosen = Signal(int)
    closed = Signal()

    ROW_H = 30.0
    HEAD_H = 20.0
    PAD = 7.0
    RADIUS = 12.0
    ICON = 14.0
    TEXT_X = 28.0
    PAD_R = 20.0
    SHADOW = 10.0        # 窗口四周预留的阴影边距：内容区从 (SHADOW, SHADOW) 开始

    def __init__(self, values: list[int], current: int, pal: theme.Palette,
                 writable: bool = True, parent=None) -> None:
        super().__init__(
            parent,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._pal = pal
        # 挡位从高到低（降序）显示：大值在前，与鼠标驱动惯例一致（用户要求）
        self._values = sorted({int(v) for v in values if int(v) > 0}, reverse=True)
        self._current = int(current or 0)
        self._writable = bool(writable)
        self._hover = -1

        font = app_font(12, QFont.Weight.Medium, 0.1)
        fm = QFontMetricsF(font)
        widest = max((fm.horizontalAdvance(self._label(v)) for v in self._values),
                      default=0.0)
        self._width = max(126.0, widest + self.TEXT_X + self.PAD_R)
        self._height = self.PAD * 2 + self.HEAD_H + self.ROW_H * len(self._values)
        self.setFixedSize(int(round(self._width + self.SHADOW * 2)),
                          int(round(self._height + self.SHADOW * 2)))

    # ---------------------------------------------------------------- 数据
    @staticmethod
    def _label(value: int) -> str:
        return f"{value} Hz"

    def values(self) -> list[int]:
        return list(self._values)

    def frame_rect(self) -> QRectF:
        return QRectF(self.SHADOW, self.SHADOW, self._width, self._height)

    def show_near(self, anchor: QPoint, prefer_above: bool = True) -> None:
        """摆在锚点（屏幕坐标）附近：优先放上方，放不下就翻到下方。

        回报率卡片在窗口下半屏，HUD 又常常贴着屏幕底部——所以默认往上摆。
        """
        screen = QApplication.screenAt(anchor)
        area = screen.availableGeometry() if screen is not None else None
        h = self._height                 # 内容高度（不含阴影）
        x = anchor.x() - self.width() // 2
        if area is not None:
            x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
            # 内容区底部距锚点 8px + 阴影边距：让阴影也完整落在锚点上方
            y = anchor.y() - h - 8 - self.SHADOW * 2
            if prefer_above and y < area.top() + 4:
                y = anchor.y() + 8 - self.SHADOW
            if y + self.height() > area.bottom() - 4:
                y = max(area.top() + 4, area.bottom() - self.height() - 4)
        else:
            y = anchor.y() - h - 8 - self.SHADOW * 2
        self.move(int(x), int(y))
        self.show()
        self.raise_()

    def hide_picker(self) -> None:
        if self.isVisible():
            self.hide()

    # ---------------------------------------------------------------- 交互
    def _index_at(self, pos) -> int:
        if pos.x() < 0 or pos.x() > self.width():
            return -1
        # 内容区从 (SHADOW, SHADOW) 开始
        top = self.SHADOW + self.PAD + self.HEAD_H
        idx = int((pos.y() - top) // self.ROW_H)
        return idx if 0 <= idx < len(self._values) else -1

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        idx = self._index_at(event.position())
        if idx != self._hover:
            self._hover = idx
            self.setCursor(Qt.PointingHandCursor if idx >= 0 else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            idx = self._index_at(event.position())
            if idx >= 0:
                value = self._values[idx]
                self.chosen.emit(value)
                # 稍后再收起：全局鼠标钩子会在按下后 ~30ms 把这次点击送回主
                # 窗口做「是不是点了外面」的判断，面板这时还在，才不会把 HUD
                # 顺带收走
                QTimer.singleShot(250, self.hide)
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self.closed.emit()
        super().hideEvent(event)

    # ---------------------------------------------------------------- 绘制
    def _check_color(self) -> QColor:
        return QColor(self._pal.success)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        dark = pal.name == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        fr = self.frame_rect()
        # 外圈柔和投影：让弹窗从背景浮起来（用户要求弹窗加阴影）
        draw_outer_shadow(p, fr, self.RADIUS, QColor(0, 0, 0, 90 if dark else 55),
                          layers=7, strength=0.3, dy=0.9)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.surface_glass_solid))
        p.drawPath(rounded(fr, self.RADIUS))

        # 标题行：写清楚这是什么，别让人猜
        p.setFont(app_font(9, QFont.Weight.DemiBold, 0.4))
        p.setPen(QPen(pal.text_secondary))
        p.drawText(QRectF(fr.left() + self.PAD, fr.top() + self.PAD,
                          self._width - self.PAD * 2, self.HEAD_H),
                   Qt.AlignLeft | Qt.AlignVCenter, i18n.T("回报率挡位"))
        p.setPen(QPen(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 20)))
        y_line = fr.top() + self.PAD + self.HEAD_H
        p.drawLine(QPointF(fr.left() + self.PAD, y_line),
                   QPointF(fr.left() + self._width - self.PAD, y_line))

        top = y_line
        p.setFont(app_font(12, QFont.Weight.Medium, 0.1))
        for i, value in enumerate(self._values):
            cell = QRectF(fr.left() + self.PAD, top + i * self.ROW_H,
                          self._width - self.PAD * 2, self.ROW_H)
            if i == self._hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 12))
                p.drawRoundedRect(cell, 8, 8)
            if value == self._current:
                # 当前已设置的挡位：绿勾 + 清晰选中底纹，弹出来就高亮它。
                # 浅色主题底纹之前是 (0,0,0,8)，几乎看不见（用户报选中态
                # 与设计稿不一致）——提到 28，跟 hover 一样醒目，靠绿勾区分
                # 「当前值」和「鼠标悬停」两种高亮。
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 28) if dark else QColor(0, 0, 0, 28))
                p.drawRoundedRect(cell, 8, 8)
                icon_check(p, QRectF(cell.left() + 7, cell.center().y() - self.ICON / 2,
                                     self.ICON, self.ICON), self._check_color())
            p.setPen(QPen(pal.text_primary))
            p.drawText(QRectF(cell.left() + self.TEXT_X, cell.top(),
                              cell.width() - self.TEXT_X, cell.height()),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                       self._label(value))


class LevelsPicker(QWidget):
    """DPI 挡位管理弹窗：竖向列表，和回报率挡位面板（RatePicker）同一套架构。

    列出设备当前的 DPI 挡位（降序），当前激活的那档打绿勾；点某一行把当前档
    切到它、点行尾「×」删除（先弹确认框）、底部「+ 增加挡位」输入新值入表。
    增删切都即时写回设备。挡位多（> MAX_ROWS）时列表区竖向滚动，右侧画一条
    细滚动条指示位置。

    与 RatePicker 的差别：这里不是「选完就走」，而是「持续管理」——切档、增档、
    删档都保持面板打开、即时刷新，方便连着调好几个档；只有点外部 / Esc 才收起。
    """

    chosen = Signal(int)   # 切换到该挡位（设为当前激活档）
    removed = Signal(int)  # 删除该挡位（已确认）
    added = Signal(int)    # 新增该挡位
    closed = Signal()

    ROW_H = 30.0
    HEAD_H = 20.0
    PAD = 7.0
    RADIUS = 12.0
    ICON = 14.0
    TEXT_X = 28.0
    CLOSE_W = 30.0        # 行尾「×」命中区宽
    PAD_R = 12.0
    SHADOW = 10.0
    ADD_H = 30.0          # 底部「+ 增加挡位」行高
    MAX_ROWS = 6          # 超过就竖向滚动
    SCROLL_W = 4.0

    def __init__(self, levels: list[int], current: int, caps, pal: theme.Palette,
                 parent=None, table_writable: bool = True) -> None:
        # 注意：这里**不能**加 Qt.WindowDoesNotAcceptFocus——否则底部「增加挡位」
        # 展开出的 QLineEdit 永远拿不到键盘焦点，数字打不进去（2026-11 修 BUG）。
        # WA_ShowWithoutActivating 保证弹出瞬间不抢主窗口的焦点。
        super().__init__(
            parent,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._pal = pal
        self._caps = caps
        self._table_writable = bool(table_writable)
        # 表只读（G502 板载扇区不开放）：面板降级为「仅切换」——行尾「×」和
        # 底部「+ 增加挡位」都不出现，点某档只切当前 DPI（0x2201 fn3）。
        self._max_levels = int(getattr(caps, "max_levels", 0) or 8)
        self._levels = sorted({int(v) for v in levels if int(v) > 0}, reverse=True)
        self._current = int(current or 0)
        self._hover = -1          # hover 的数据行索引（-1 无）
        self._hover_close = False  # hover 在某行的 × 上
        self._hover_add = False   # hover 在底部「+ 增加挡位」上
        self._adding = False      # 底部展开为输入框
        self._scroll = 0          # 滚动偏移（数据行数）
        self._msg = ""            # 底部反馈文字（成功/失败）
        self._msg_err = False

        font = app_font(12, QFont.Weight.Medium, 0.1)
        fm = QFontMetricsF(font)
        widest = max((fm.horizontalAdvance(self._label(v)) for v in self._levels),
                     default=0.0)
        self._width = max(150.0, widest + self.TEXT_X + self.CLOSE_W + self.PAD_R)
        self._list_h = self.ROW_H * self._visible_rows()
        add_h = self.ADD_H if self._table_writable else 0.0
        self._height = self.PAD * 2 + self.HEAD_H + self._list_h + add_h
        self.setFixedSize(int(round(self._width + self.SHADOW * 2)),
                          int(round(self._height + self.SHADOW * 2)))

        # 底部「+ 增加挡位」展开出的输入控件（浮窗子控件，绝对定位）
        if self._table_writable:
            self._edit_line = QLineEdit(self)
            self._edit_line.setAlignment(Qt.AlignCenter)
            self._edit_line.setPlaceholderText("DPI 数值")
            self._edit_line.returnPressed.connect(self._commit_add)
            self._edit_ok = QToolButton(self)
            self._edit_ok.setText("✓")
            self._edit_ok.setCursor(Qt.PointingHandCursor)
            self._edit_ok.clicked.connect(self._commit_add)
            qss = edit_controls_qss(pal)
            self._edit_line.setStyleSheet(qss["line"])
            self._edit_ok.setStyleSheet(qss["apply"])
            self._edit_line.hide()
            self._edit_ok.hide()

        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(self._clear_msg)
        # Esc 先收起输入框、再收面板；顺便拦掉输入框里的 Esc 让 keyPressEvent 能收到
        self._edit_line.installEventFilter(self) if self._table_writable else None

    def eventFilter(self, obj, event):  # noqa: N802
        if (self._table_writable and obj is self._edit_line
                and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key_Escape):
            self._hide_add_editor()
            self.update()
            return True
        return super().eventFilter(obj, event)

    # ---------------------------------------------------------------- 数据
    @staticmethod
    def _label(value: int) -> str:
        return f"{value} DPI"

    def levels(self) -> list[int]:
        """当前面板里的挡位表（降序，未含删除确认前的最新变化已即时反映）。"""
        return list(self._levels)

    def _visible_rows(self) -> int:
        return max(1, min(len(self._levels), self.MAX_ROWS))

    def frame_rect(self) -> QRectF:
        return QRectF(self.SHADOW, self.SHADOW, self._width, self._height)

    # ---------------------------------------------------------------- 几何
    def _row_rect(self, disp: int) -> QRectF:
        """显示行 disp（0..visible_rows-1）的矩形。"""
        top = self.SHADOW + self.PAD + self.HEAD_H + disp * self.ROW_H
        return QRectF(self.SHADOW + self.PAD, top,
                      self._width - self.PAD * 2, self.ROW_H)

    def _close_rect(self, disp: int) -> QRectF:
        r = self._row_rect(disp)
        return QRectF(r.right() - self.CLOSE_W, r.top(), self.CLOSE_W, r.height())

    def _add_rect(self) -> QRectF:
        top = self.SHADOW + self.PAD + self.HEAD_H + self._list_h
        return QRectF(self.SHADOW + self.PAD, top,
                      self._width - self.PAD * 2, self.ADD_H)

    def show_near(self, anchor: QPoint, prefer_above: bool = True) -> None:
        screen = QApplication.screenAt(anchor)
        area = screen.availableGeometry() if screen is not None else None
        x = anchor.x() - self.width() // 2
        if area is not None:
            x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
            y = anchor.y() - self._height - 8 - self.SHADOW * 2
            if prefer_above and y < area.top() + 4:
                y = anchor.y() + 8 - self.SHADOW
            if y + self.height() > area.bottom() - 4:
                y = max(area.top() + 4, area.bottom() - self.height() - 4)
        else:
            y = anchor.y() - self._height - 8 - self.SHADOW * 2
        self.move(int(x), int(y))
        self.show()
        self.raise_()

    def hide_picker(self) -> None:
        if self.isVisible():
            self.hide()

    def _relayout(self) -> None:
        """增删挡位后列表行数可能变：重算高度并保持窗口底边不挪（向上增长/收缩）。"""
        old_bottom = self.geometry().bottom() if self.isVisible() else None
        self._list_h = self.ROW_H * self._visible_rows()
        add_h = self.ADD_H if self._table_writable else 0.0
        self._height = self.PAD * 2 + self.HEAD_H + self._list_h + add_h
        self.setFixedSize(int(round(self._width + self.SHADOW * 2)),
                          int(round(self._height + self.SHADOW * 2)))
        if old_bottom is not None and self.isVisible():
            self.move(self.x(), old_bottom - self.height())
        self._scroll = max(0, min(self._scroll, len(self._levels) - self._visible_rows()))
        self._position_add_controls()
        self.update()

    def _position_add_controls(self) -> None:
        if not self._table_writable:
            return
        r = self._add_rect()
        ok_w = 24
        line_w = max(44, int(r.width() - ok_w - 4))
        self._edit_line.setGeometry(int(r.left()), int(r.center().y() - 10), line_w, 20)
        self._edit_ok.setGeometry(int(r.right() - ok_w), int(r.center().y() - 10), ok_w, 20)

    # ---------------------------------------------------------------- 交互
    def _index_at(self, pos) -> int:
        if pos.x() < self.SHADOW or pos.x() > self.SHADOW + self._width:
            return -1
        top = self.SHADOW + self.PAD + self.HEAD_H
        if pos.y() < top:
            return -1
        disp = int((pos.y() - top) // self.ROW_H)
        if disp < 0 or disp >= self._visible_rows():
            return -1
        data = disp + self._scroll
        return data if 0 <= data < len(self._levels) else -1

    def _close_at(self, pos) -> int:
        if not self._table_writable:
            return -1
        i = self._index_at(pos)
        if i < 0:
            return -1
        disp = i - self._scroll
        return i if self._close_rect(disp).contains(pos) else -1

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        i = self._index_at(pos)
        ci = self._close_at(pos)
        on_add = (self._table_writable and self._add_rect().contains(pos)
                  and not self._adding)
        hand = i >= 0 or on_add
        if (i, ci >= 0, on_add) != (self._hover, self._hover_close, self._hover_add):
            self._hover, self._hover_close, self._hover_add = i, ci >= 0, on_add
            self.update()
        self.setCursor(Qt.PointingHandCursor if hand else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover != -1 or self._hover_close or self._hover_add:
            self._hover, self._hover_close, self._hover_add = -1, False, False
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            pos = event.position()
            ci = self._close_at(pos)
            if ci >= 0:
                self._confirm_remove(self._levels[ci])
                return
            if self._table_writable and self._add_rect().contains(pos):
                self._open_add_editor()
                return
            i = self._index_at(pos)
            if i >= 0 and self._levels[i] != self._current:
                self._current = self._levels[i]
                self.update()
                self.chosen.emit(self._current)
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        total = len(self._levels)
        if total > self._visible_rows():
            delta = -1 if event.angleDelta().y() > 0 else 1
            lo, hi = 0, total - self._visible_rows()
            if self._scroll + delta < lo or self._scroll + delta > hi:
                return
            self._scroll += delta
            self._hover = -1
            self.update()
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self._hover_close = False
        self._hover_add = False
        self._adding = False
        if self._table_writable:
            self._edit_line.hide()
            self._edit_ok.hide()
        self.closed.emit()
        super().hideEvent(event)

    def set_current(self, value: int) -> None:
        """写回失败时由主窗口回调：把勾移回设备实际的当前档。"""
        if int(value) != self._current:
            self._current = int(value)
            self.update()

    # ---------------------------------------------------------------- 增 / 删 / 反馈
    def _confirm_remove(self, value: int) -> None:
        """点「×」→ 弹确认框，确认了才删（至少留一档）。"""
        if not self._table_writable:
            return
        if len(self._levels) <= 1:
            self._flash(i18n.T("至少留一档"), error=True)
            return
        from PySide6.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, i18n.T("删除挡位"),
            i18n.delete_stage_confirm(value),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self._levels = [v for v in self._levels if v != value]
        self._relayout()
        self.removed.emit(int(value))
        self._flash(f"{i18n.T('已删 ')}{value}")

    def _open_add_editor(self) -> None:
        if not self._table_writable:
            return
        if len(self._levels) >= self._max_levels:
            self._flash(f"{i18n.T('最多 ')}{self._max_levels}{i18n.T(' 档')}", error=True)
            return
        cur = self._current if self._current else 0
        self._edit_line.setText(str(cur) if cur else "")
        self._adding = True
        self._hover_add = False
        self._position_add_controls()
        self._edit_line.show()
        self._edit_ok.show()
        self._edit_line.setFocus(Qt.OtherFocusReason)
        self._edit_line.selectAll()
        self.update()

    def _hide_add_editor(self) -> None:
        self._adding = False
        self._edit_line.hide()
        self._edit_ok.hide()

    def _commit_add(self) -> None:
        raw = self._edit_line.text().strip()
        if not raw:
            self._flash(i18n.T("请填数值"), error=True)
            return
        try:
            value = int(round(float(raw)))
        except ValueError:
            self._flash(i18n.T("请填数值"), error=True)
            return
        caps = self._caps
        if caps is not None:
            lo, hi = caps.dpi_min, caps.dpi_max
            if (lo and value < lo) or (hi and value > hi):
                self._flash(i18n.T("超出范围"), error=True)
                return
            if value not in self._levels:
                value = caps.align_dpi(value)
        if value in self._levels:
            self._flash(i18n.T("已存在"), error=True)
            return
        if len(self._levels) >= self._max_levels:
            self._flash(f"{i18n.T('最多 ')}{self._max_levels}{i18n.T(' 档')}", error=True)
            return
        self._levels.append(int(value))
        self._levels.sort(reverse=True)
        self._edit_line.clear()
        self._hide_add_editor()
        self._relayout()
        self.added.emit(int(value))
        self._flash(f"{i18n.T('已加 ')}{int(value)}")

    def _flash(self, text: str, error: bool = False) -> None:
        pal = self._pal
        self._msg = text
        self._msg_err = error
        self._msg_timer.start(1800)
        if self._adding:
            # 输入框开着时，反馈文字会被 QLineEdit 盖住 → 改用 ToolTip 提示
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(self._edit_line.mapToGlobal(self._edit_line.rect().center()),
                              text, self._edit_line)
        self.update()

    def _clear_msg(self) -> None:
        self._msg = ""
        self._msg_err = False
        self.update()

    def _check_color(self) -> QColor:
        return QColor(self._pal.success)

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        dark = pal.name == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        fr = self.frame_rect()
        draw_outer_shadow(p, fr, self.RADIUS, QColor(0, 0, 0, 90 if dark else 55),
                          layers=7, strength=0.3, dy=0.9)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.surface_glass_solid))
        p.drawPath(rounded(fr, self.RADIUS))

        # 标题行（表只读时右侧补一句来源说明）
        p.setFont(app_font(9, QFont.Weight.DemiBold, 0.4))
        p.setPen(QPen(pal.text_secondary))
        p.drawText(QRectF(fr.left() + self.PAD, fr.top() + self.PAD,
                          self._width - self.PAD * 2, self.HEAD_H),
                   Qt.AlignLeft | Qt.AlignVCenter, i18n.T("DPI 挡位"))
        if not self._table_writable:
            p.setFont(app_font(8, QFont.Weight.Normal, 0.2))
            p.setPen(QPen(pal.text_tertiary))
            p.drawText(QRectF(fr.left() + self.PAD, fr.top() + self.PAD,
                              self._width - self.PAD * 2, self.HEAD_H),
                       Qt.AlignRight | Qt.AlignVCenter, i18n.T("挡位由 G HUB 管理"))
        p.setPen(QPen(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 20)))
        y_line = fr.top() + self.PAD + self.HEAD_H
        p.drawLine(QPointF(fr.left() + self.PAD, y_line),
                   QPointF(fr.left() + self._width - self.PAD, y_line))

        # 挡位列表（可见窗口）
        p.setFont(app_font(12, QFont.Weight.Medium, 0.1))
        for disp in range(self._visible_rows()):
            i = disp + self._scroll
            if i >= len(self._levels):
                break
            value = self._levels[i]
            cell = self._row_rect(disp)
            if i == self._hover and not self._hover_close:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 12))
                p.drawRoundedRect(cell, 8, 8)
            if value == self._current:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 28) if dark else QColor(0, 0, 0, 28))
                p.drawRoundedRect(cell, 8, 8)
                icon_check(p, QRectF(cell.left() + 7, cell.center().y() - self.ICON / 2,
                                     self.ICON, self.ICON), self._check_color())
            p.setPen(QPen(pal.text_primary))
            p.drawText(QRectF(cell.left() + self.TEXT_X, cell.top(),
                              cell.width() - self.TEXT_X - self.CLOSE_W, cell.height()),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                       self._label(value))
            if not self._table_writable:
                continue
            # 行尾「×」：hover 时高亮，不可删（只剩一档）时淡灰
            cr = self._close_rect(disp)
            cx, cy = cr.center().x(), cr.center().y()
            r = 5.5
            close_hover = self._hover_close and self._hover == i
            can_del = len(self._levels) > 1
            if close_hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 16))
                p.drawEllipse(QPointF(cx, cy), r + 2, r + 2)
            cc = QColor(255, 255, 255, 200 if can_del else 80) if dark \
                else QColor(0, 0, 0, 180 if can_del else 80)
            if close_hover:
                cc = QColor(pal.danger)
            p.setPen(QPen(cc, 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 3, cy - 3), QPointF(cx + 3, cy + 3))
            p.drawLine(QPointF(cx - 3, cy + 3), QPointF(cx + 3, cy - 3))

        # 底部「+ 增加挡位」行（或输入框 / 反馈文字）；表只读时整段不画
        if not self._table_writable:
            self._draw_scrollbar(p, fr)
            return
        ar = self._add_rect()
        if self._adding:
            # 展开输入框后只画行背景，不再画「+ 增加挡位」/反馈文字——
            # 画了也会被悬浮的 QLineEdit 盖住或穿底重叠（2026-11 修 BUG）。
            p.setPen(QPen(QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 16)))
            p.drawLine(QPointF(ar.left(), ar.top()), QPointF(ar.right(), ar.top()))
        elif self._msg:
            col = QColor(pal.danger) if self._msg_err else QColor(pal.success)
            p.setFont(app_font(10, QFont.Weight.DemiBold, 0.2))
            p.setPen(QPen(col))
            p.drawText(ar, Qt.AlignCenter | Qt.AlignVCenter, self._msg)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 16)))
            y_add = ar.top()
            p.drawLine(QPointF(ar.left(), y_add), QPointF(ar.right(), y_add))
            if self._hover_add:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 10))
                p.drawRoundedRect(ar.adjusted(0, 2, 0, -2), 7, 7)
            p.setFont(app_font(11, QFont.Weight.DemiBold, 0.2))
            p.setPen(QPen(pal.accent_text))     # 文字用 AA 变体
            p.drawText(ar, Qt.AlignCenter | Qt.AlignVCenter, i18n.T("+ 增加挡位"))

        self._draw_scrollbar(p, fr)

    def _draw_scrollbar(self, p: QPainter, fr: QRectF) -> None:
        # 竖向滚动条指示（挡位多时）
        total = len(self._levels)
        vis = self._visible_rows()
        if total > vis:
            track = QRectF(fr.right() - self.PAD_R + 1, fr.top() + self.PAD + self.HEAD_H,
                           self.SCROLL_W, self._list_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 20) if dark else QColor(0, 0, 0, 16))
            p.drawRoundedRect(track, 2, 2)
            thumb_h = max(14.0, track.height() * vis / total)
            ratio = self._scroll / (total - vis)
            thumb_y = track.top() + (track.height() - thumb_h) * ratio
            p.setBrush(QColor(255, 255, 255, 70) if dark else QColor(0, 0, 0, 60))
            p.drawRoundedRect(QRectF(track.left(), thumb_y, self.SCROLL_W, thumb_h), 2, 2)
