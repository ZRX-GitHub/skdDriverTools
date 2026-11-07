"""三颗豆 —— 多品牌鼠标驱动管理主窗口。"""
from __future__ import annotations

import ctypes
import math
import os
import sys
import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextCharFormat,
    QTextLayout,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSpacerItem,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

import accessories
import applog
import autostart
import config
import driver
import i18n
import monitor as mon
import notify
import protocol as P
import setup_core
import theme
import widgets as W
import wineffects
from appicon import make_icon

CARD_RADIUS = 26.0
CARD_W, CARD_H = 396, 470
#: 编辑模式卡片高度（挡位增删行 + 步进器 + 应用/取消）
#: 88 而不是 108/96：窗口高度恒定 470，编辑态要塞下「提示胶囊 + 挡位条
#: + 卡片 + 主按钮」，且**卡片行 / 主按钮的垂直位置要保持和展示态一致**
#: （用户报「编辑时下方模块被上方连带位移」）。卡片内编辑控件实测只需
#: ~56px（标题区 24 + 步进器行 22 + 应用/取消行 21 扣掉重叠），88 够放。
EDIT_CARD_H = 88
#: 编辑模式电量环直径（窗口高度固定不变，靠缩小环 + 收紧间距腾地方）
#: 194：挡位从横向条（26px）改成竖向弹窗后，那 26px 让给环——提示条正好
#: 贴到卡片上方（bottom=291 vs 卡片 y=292），下方模块 y 坐标与展示态一致。
EDIT_RING_D = 194
PAD = 20

#: 贴边边距（**物理像素**）：下边框距任务栏顶部外边缘、右边框距屏幕右边缘
DOCK_BOTTOM_GAP = 12
DOCK_RIGHT_GAP = 160


def logical_gap(physical: int, scale: float) -> int:
    """物理像素边距 → Qt 逻辑像素（move() 只认逻辑像素）。

    1080p（100%）上 160 物理像素就是 160 逻辑像素；2K 屏常见 125%/150%，
    同一个数值会被 Qt 放大成 200/240 物理像素，于是两块屏量出来的距离对不上。
    这里统一折回逻辑像素，保证最终落到屏幕上的物理距离始终是 physical。
    """
    if not scale or scale <= 0:
        return physical
    return max(1, int(round(physical / scale)))


def device_menu_rows(mouse_rows: list[dict], accessory_rows: list[dict]) -> list[dict]:
    """设备列表的**唯一数据源**：在线鼠标在前，键盘 / 耳机配件在后。

    头部那句「N台设备」和点标题展开的弹窗都必须用这一份数据。以前头部只
    喂鼠标行、弹窗另外追加配件行，结果弹窗列 5 行而标题写「3台设备」——
    数字对不上就是这么来的。这里只做「过滤未连接 + 配件排在后面」，别的
    都不改（配件行自带 `switchable=False`，弹窗自己会处理不可点）。
    """
    rows = [r for r in mouse_rows if r.get("available")]
    rows += [r for r in accessory_rows if r.get("available")]
    return rows


class Header(QWidget):
    """应用名 + 型号 + 状态点，右侧操作按钮。

    灰色底衬那一块（状态行 + 型号行，按文字宽度收窄）同时是
    **设备切换入口**：识别到任意一台鼠标就能点开设备列表，再点一次收起；
    一台都没识别到时不可点、也不显示箭标。

    两行的分工：第一行固定是「三颗豆驱动管理 · N台设备 · 已连接」，第二行
    把品牌和型号连起来写（「CROCIRIS X7 Ultra Max」）——品牌原来占着第一行
    开头，读起来像「CROCIRIS 这个软件的界面」，挪到型号前面才读得成
    「这台是 CROCIRIS 的 X7 Ultra Max」。

    N 数的是**弹窗里列得出来的全部在位设备**，包括只展示、不能切过去的
    键盘 / 耳机配件；不然弹窗 5 行、标题写「3台设备」，一眼就是错的。
    """

    device_menu_requested = Signal()

    #: 第一行左侧的固定文案（原来这里放的是品牌名）。中文「三颗豆驱动管理」，
    #: 英文按品牌名 SKD 译成「SKD Driver Manager」——翻译在绘制时做，切语言
    #: 只需重绘，不用重建 Header
    APP_TITLE = "三颗豆驱动管理"

    #: 型号行里品牌前缀的视觉：字号比型号小一号 + 一点字距，读起来像「标签」
    #: 而不是名字的一部分；再配次级灰，和旁边主色的型号拉开层次。
    #: 三段文字三种身份——软件名（品牌蓝 9px）、品牌（次级灰 13px）、
    #: 型号（主色 16px），颜色和字号都不同，一眼分得清谁是谁。
    BRAND_PX = 13
    BRAND_TRACK = 0.35
    BRAND_GAP = 5.0
    #: 型号行字号：和品牌一致（13px），不再单独大一号
    MODEL_PX = 13

    #: 第一行（软件名 · 设备数 · 状态）的左起点：和型号行前面那颗绿色状态点的
    #: 左缘对齐。两行原来各起各的（16 / 15），第一行看着比绿点缩进去一截。
    STATUS_X = 3.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pal = theme.DARK
        self._brand = "CROCIRIS"
        self._model = "正在扫描…"
        self._status = "连接中"
        self._connected = False
        self._pulse = 0.0
        self._devices: list[dict] = []
        self._hover_title = False
        self.setFixedHeight(48)
        self.setMouseTracking(True)

        self._close_to_tray = True

        self.btn_settings = W.GlassButton(kind="icon", icon="gear", height=30, parent=self)
        self.btn_close = W.GlassButton(kind="icon", icon="close", height=30, parent=self,
                                       hover_tint="danger")
        self.btn_settings.setToolTip("设置")
        self.set_close_to_tray(True)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)
        for btn in (self.btn_settings, self.btn_close):
            row.addWidget(btn)
            btn.set_palette_theme(self._pal)
            btn.installEventFilter(self)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(110)

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        for btn in (self.btn_settings, self.btn_close):
            btn.set_palette_theme(pal)
        self.update()

    def set_devices(self, rows: list[dict]) -> None:
        """行 = {key, label, available, current, ...}；只在真的变了时重绘。

        传进来的是 `device_menu_rows()` 的结果：鼠标 + 配件都在里面，头部
        的「N台设备」才跟弹窗行数一致。配件行没有 `current`、带
        `switchable=False`，这里一律不关心，只数 `available`。
        """
        sig = [(r.get("key"), r.get("label"), r.get("available"), r.get("current")) for r in rows]
        old = [(r.get("key"), r.get("label"), r.get("available"), r.get("current"))
               for r in self._devices]
        self._devices = list(rows)
        if sig == old:
            return
        # 手型只在鼠标真正移到可点的标题区时才给（见 mouseMoveEvent）；
        # 整个头部设成手型会让空白处也变手型——用户之前就报过这个 bug。
        if not self._title_clickable():
            self._hover_title = False
            self.unsetCursor()
        elif self._hover_title:
            self.setCursor(Qt.PointingHandCursor)
        self.update()

    def _title_rect(self) -> QRectF:
        """标题块的候选范围：状态行 + 型号行，右侧留 100px 给齿轮和 ×。

        v1.2.57 起它只是宽度上限——实际可点/悬停范围是 _hover_rect()
        （灰色底衬那块，按文字宽度收窄），底衬右侧的空白不再能点开菜单。
        """
        return QRectF(2, 5, max(140.0, self.width() - 100.0), 38)

    def _available(self) -> int:
        """当前在线的**设备总数**：鼠标 + 键盘 / 耳机配件，等于弹窗行数。"""
        return sum(1 for r in self._devices if r.get("available"))

    def _title_clickable(self) -> bool:
        # 只要有设备能列出来就允许点开（哪怕全是不可切换的配件）——
        # 弹窗是「看有哪些设备」的地方，不是只有切换时才需要打开。
        return self._available() >= 1

    def _in_title(self, pos) -> bool:
        # v1.2.57：点击也只认灰色底衬那一块。命中范围跟「高亮亮到哪」完全一致，
        # 底衬右侧那段空白（红色区域）不再能点开设备列表——用户圈过照
        return self._hover_rect().contains(pos)

    @staticmethod
    def _brand_font() -> QFont:
        """品牌前缀的字体：比型号小一号，带一点字距（大写品牌名读作标签）。"""
        return W.app_font(Header.BRAND_PX, QFont.Weight.DemiBold, Header.BRAND_TRACK)

    def _brand_parts(self, fm, avail: float) -> tuple[str, float]:
        """型号行里品牌那一段：(文字, 占宽含间距)；让位时返回 ("", 0)。

        品牌是型号的定语，不是主角——**型号放得下完整名字**时才在它前面挂
        品牌，放不下就让位，把整行宽度留给型号（型号自己超宽时再收省略号）。
        罗技 PRO 系列的名字（PRO X SUPERLIGHT 2 Wireless…）单独就接近标题区
        宽度，硬塞一个品牌只会把型号截成「PRO X SUPERL…」，用户反而认不出
        是哪只鼠标；设备菜单里还有完整名字可查。

        宽度按**品牌自己的字体**量（比型号小一号），用型号的 16px 度量算会
        虚宽十几个像素，白白把品牌挤掉。
        """
        brand = (self._brand or "").strip()
        if not brand:
            return "", 0.0
        w = QFontMetricsF(self._brand_font()).horizontalAdvance(brand) + self.BRAND_GAP
        if w + fm.horizontalAdvance(self._model) > avail:
            return "", 0.0
        return brand, w

    def _brand_width(self, fm, avail: float | None = None) -> float:
        """品牌那一段占的宽度；没有品牌或已让位都是 0。

        底衬宽度、悬停命中范围、箭标起点都按「品牌 + 型号」整段的宽度算，
        否则品牌挪到型号前面以后底衬会短一截、包不住开头那半句。
        """
        if avail is None:
            avail = self.width() - 112
        return self._brand_parts(fm, avail)[1]

    def _status_rect(self) -> QRectF:
        """第一行的排版矩形：左缘贴齐绿点，右缘保持原位（不压到右侧按钮）。

        状态行的右边界原来固定在 width-96，左移之后宽度补回来，右边不动——
        只挪起点，不改变「最多能排到哪」。
        """
        left = self.STATUS_X
        return QRectF(left, 8, max(60.0, self.width() - 96.0 - left), 12)

    def _hover_rect(self) -> QRectF:
        """灰色悬停底衬实际画的那块矩形——悬停命中范围必须和它一模一样。

        v1.2.55 只修了「挪到齿轮上高亮不灭」；真正的坑是命中范围（整个标题块、
        一直到按钮左边）比可见的灰色底衬宽一截：光标已经离开灰色区域，高亮
        却还挂着，要完全移出标题块才熄灭（用户拍照报过两次）。现在悬停只认
        底衬那一块；点击范围不变，仍是整个标题块（见 mousePressEvent）。
        """
        mf = W.app_font(self.MODEL_PX, QFont.Weight.DemiBold, -0.2)
        fm = QFontMetricsF(mf)
        tw = fm.horizontalAdvance(self._model) + self._brand_width(fm)
        _lay, _line, status_right, _cx = self._status_layout(self._status_rect())
        hw = min(self._title_rect().width() + 4, max(tw + 48, status_right + 16, 140))
        return QRectF(-2, 4, hw, 40)

    def title_hit(self, global_pos) -> bool:
        """屏幕坐标是否落在可点的标题区（和 _in_title 同一份几何）。"""
        point = QPoint(int(global_pos[0]), int(global_pos[1]))
        return self._hover_rect().contains(self.mapFromGlobal(point))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._title_clickable() and self._in_title(event.position()):
            self.device_menu_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # 悬停命中 = 底衬本身：底衬画到哪、高亮亮到哪，光标一出底衬立刻熄灭
        hover = self._title_clickable() and self._hover_rect().contains(event.position())
        # 光标样式**无条件**刷新：之前只在状态翻转时才设，可 leaveEvent 只清
        # 状态、不清样式——光标绕去设备菜单再回到标题栏空白处时状态早已是
        # False，`hover != self._hover_title` 不成立，手型就挂在不可点的地方
        # （用户拍过照）
        self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
        if hover != self._hover_title:
            self._hover_title = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        # 离开标题栏（含绕去设备菜单 / 子控件）时把光标样式一并复位，
        # 只清悬停状态的话手型会残留到下一次 mouseMove 才被刷掉
        self.setCursor(Qt.ArrowCursor)
        if self._hover_title:
            self._hover_title = False
            self.update()
        super().leaveEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 鼠标挪到齿轮 / × 上时立刻熄掉标题的悬停态：子控件会拦走 mouseMove，
        # header 的 leaveEvent 不会触发，底衬和手型就会一直挂着——用户拍过照，
        # 光标都到齿轮旁边了底衬还亮着
        if event.type() == QEvent.Enter and obj in (self.btn_settings, self.btn_close):
            if self._hover_title:
                self._hover_title = False
                self.setCursor(Qt.ArrowCursor)
                self.update()
        return super().eventFilter(obj, event)

    def set_close_to_tray(self, enabled: bool) -> None:
        """右上角 × 的提示跟着「关闭时最小化到托盘」开关走。

        开关打开时点 × 只是收回托盘，提示「最小化到托盘」；关掉后点 × 直接退出，
        提示「关闭」——不然用户以为只是收起来，结果进程没了。
        """
        self._close_to_tray = bool(enabled)
        self.btn_close.setToolTip(i18n.T("最小化到托盘" if self._close_to_tray else "关闭"))

    def set_state(self, brand: str, model: str, status: str, connected: bool) -> None:
        changed = (brand, model, status, connected) != (
            self._brand, self._model, self._status, self._connected
        )
        self._brand, self._model, self._status, self._connected = brand, model, status, connected
        if changed:
            self.update()

    def _tick(self) -> None:
        self._pulse = (self._pulse + 0.07) % 6.283
        self.update()

    def set_animating(self, on: bool) -> None:
        """静止时把呼吸灯降到低频，降低常驻 CPU。"""
        self._timer.setInterval(110 if on else 800)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # 状态点：放在型号那一行的前面（用户要求从品牌名前面挪过来）。
        # 它是「已连接」的指示灯，跟着鼠标本体（型号）走比跟着品牌走更直观。
        dot_color = self._ok_color(pal) if self._connected else QColor(pal.text_tertiary)
        if not self._connected:
            dot_color.setAlpha(120)
        glow = QColor(dot_color)
        glow.setAlpha(int(40 + 30 * ((math.sin(self._pulse) + 1) / 2)) if self._connected else 20)
        # 型号行矩形是 (15, 20, w, 22)，垂直中心 31；点就贴着文字左侧
        dot_cy = 31.0
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QRectF(1, dot_cy - 4.5, 9, 9))
        p.setBrush(dot_color)
        p.drawEllipse(QRectF(3, dot_cy - 2.5, 5, 5))

        # 状态行排版只算一次：底衬要多宽、箭标落在哪，都从这份结果里取，
        # 免得两处各算一遍、改了一边另一边对不上
        status_rect = self._status_rect()
        # 布局对象要活到画完（QTextLine 只是它内部的一行）
        layout, status_line, status_right, lian_cx = self._status_layout(status_rect)
        clickable = self._title_clickable()

        # 悬停底衬：整块标题区可点时，鼠标移上去铺一层极淡的圆角底，
        # 比只把箭标调亮更能说明「这一整块能点」。
        if clickable and self._hover_title:
            # 底衬要完整包住两行文字：第一行通常比型号更长，
            # 只按型号宽度算会把「已连接」截掉半句（用户报过）。
            # 矩形和命中范围共用 _hover_rect()，改一处两边同步
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 22) if pal.name == "dark"
                       else QColor(24, 26, 34, 15))
            p.drawRoundedRect(self._hover_rect(), 12, 12)

        self._paint_status(p, status_line, status_rect)

        p.setFont(W.app_font(self.MODEL_PX, QFont.Weight.DemiBold, -0.2))
        fm = QFontMetricsF(p.font())
        model_rect = QRectF(15, 20, self.width() - 112, 22)
        brand, brand_w = self._brand_parts(fm, model_rect.width())
        x = model_rect.left()
        if brand:
            # 品牌和型号同字号、靠基线对齐：品牌主色带字距是「身份标签」，
            # 型号次级灰紧跟其后读成「这台设备的名字」
            bfont = self._brand_font()
            bfm = QFontMetricsF(bfont)
            base_y = model_rect.top() + (model_rect.height()
                                         + fm.ascent() - fm.descent()) / 2.0
            by = base_y - (model_rect.height() + bfm.ascent() - bfm.descent()) / 2.0
            p.setFont(bfont)
            p.setPen(QPen(self._brand_color()))
            p.drawText(QRectF(x, by, brand_w, model_rect.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, brand)
            p.setFont(W.app_font(self.MODEL_PX, QFont.Weight.DemiBold, -0.2))
            x += brand_w
        rest = max(0.0, model_rect.right() - x)
        p.setPen(QPen(self._model_color()))
        # 型号超宽时收省略号：设备菜单里还有完整名字，这里断在半截更难看
        p.drawText(QRectF(x, model_rect.top(), rest, model_rect.height()),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self._model, Qt.ElideRight, rest))
        if clickable:
            # 小箭标落在「已连接」的「连」字正下方（悬停时加深）：
            # 竖着对上去，视觉上是「这行字往下拉出列表」而不是型号的尾巴
            tw = (x - model_rect.left()
                  + min(fm.horizontalAdvance(self._model), rest))
            cx = max(lian_cx - 4.5, model_rect.left() + tw + 8)
            cx = min(cx, model_rect.right() - 10)
            arrow = QColor(pal.accent if self._hover_title else pal.text_tertiary)
            arrow.setAlpha(240 if self._hover_title else 140)
            p.setPen(QPen(arrow, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline([QPointF(cx, 30), QPointF(cx + 4.5, 34.5), QPointF(cx + 9, 30)])

    @staticmethod
    def _ok_color(pal) -> QColor:
        """「已连接」的绿：浅色底上 #32D74B 太浅，要压深一档才读得清。"""
        return QColor(pal.success)

    def _brand_color(self) -> QColor:
        """型号行里品牌的颜色：主色（最重的一档）。

        用户定的层次：品牌是身份、型号是补充信息——品牌用主色压住，
        型号退到次级灰。三段仍然是三种颜色（软件名品牌蓝 / 品牌主色 /
        型号次级灰），一眼分得清谁是谁。
        """
        return QColor(self._pal.text_primary)

    def _model_color(self) -> QColor:
        return QColor(self._pal.text_secondary)

    @staticmethod
    def _title_weight() -> QFont.Weight:
        """软件名那一段的字重：比后半句（设备数 / 状态）实一档。"""
        return QFont.Weight.DemiBold

    def _status_segments(self) -> list[tuple[str, QColor]]:
        """第一行分段：应用名 · N台设备 · 状态。

        第一段固定是「三颗豆驱动管理」，不再放品牌——品牌已经挪到第二行
        型号前面（和型号连成一个整体读作「CROCIRIS X7 Ultra Max」），
        第一行留给「这是谁的界面 + 有几台设备 + 连没连上」。

        「几台设备」紧挨在「已连接」前面，用户一眼能看出还有设备可以点开
        看清单；数字必须与弹窗行数一致（含只展示、不可切换的配件）。
        状态本身用连接色，和左侧那颗呼吸点呼应。
        """
        pal = self._pal
        n = self._available()
        # 软件名走品牌色：第一行是「这是谁的界面」，用品牌蓝把「三颗豆驱动
        # 管理」和后面的设备计数（灰）、连接状态（绿）分开——原来三段全是
        # 灰/绿，软件名糊在句子里，看不出它才是软件的名字。
        segs: list[tuple[str, QColor]] = [(i18n.T(self.APP_TITLE),
                                           QColor(pal.accent_text))]
        if n >= 1:
            count = QColor(pal.text_secondary)
            count.setAlpha(235)
            segs.append((i18n.devices(n), count))
        if self._connected:
            ok = self._ok_color(pal)
            ok.setAlpha(240)
            segs.append((i18n.T(self._status), ok))
        else:
            segs.append((i18n.T(self._status), QColor(pal.text_tertiary)))
        return segs

    def _status_layout(self, rect: QRectF):
        """把第一行排成一条 QTextLayout：返回 (布局, 行, 右边界, 「连」字中心 x)。

        用 QTextLayout 而不是 QFontMetrics 硬算：屏幕上按像素网格排版，逐段
        drawText 的真实推进值和字体度量能差出半个字，箭标就会偏到隔壁字下面。
        这里「怎么排」和「怎么画」用同一个布局对象，位置天然对得上。
        """
        pal = self._pal
        segs = self._status_segments()
        sep_color = QColor(pal.text_tertiary)
        sep_color.setAlpha(130)

        parts: list[str] = []
        ranges = []
        lian_idx = -1
        pos = 0
        for i, (text, color) in enumerate(segs):
            if i:
                dot = "\u2009\u00b7\u2009"
                parts.append(dot)
                rng = QTextLayout.FormatRange()
                rng.start, rng.length = pos, len(dot)
                fmt = QTextCharFormat()
                fmt.setForeground(sep_color)
                rng.format = fmt
                ranges.append(rng)
                pos += len(dot)
            parts.append(text)
            if lian_idx < 0:
                j = text.find("\u8fde")
                if j >= 0:
                    lian_idx = pos + j
            rng = QTextLayout.FormatRange()
            rng.start, rng.length = pos, len(text)
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            if i == 0:
                # 软件名比后面两段「设备数 / 连接状态」实一档：9px 的细字
                # 在深色底上本来就偏虚，仅靠颜色还不够跳出来
                fmt.setFontWeight(self._title_weight())
            rng.format = fmt
            ranges.append(rng)
            pos += len(text)

        lay = QTextLayout("".join(parts), W.app_font(9, QFont.Weight.Medium, 1.1))
        lay.setFormats(ranges)
        lay.beginLayout()
        line = lay.createLine()
        line.setLineWidth(4096.0)
        lay.endLayout()

        right = rect.left() + line.naturalTextWidth()
        if lian_idx < 0:
            cx = rect.left()
        else:
            x0 = line.cursorToX(lian_idx)[0]
            x1 = line.cursorToX(lian_idx + 1)[0]
            cx = rect.left() + (x0 + x1) / 2
        return lay, line, right, cx

    def _paint_status(self, p: QPainter, line, rect: QRectF) -> None:
        """把排好的第一行画出来（颜色在 QTextLayout 的格式里，这里只给位置）。"""
        p.save()
        p.setClipRect(rect)
        y = rect.top() + (rect.height() - line.height()) / 2
        line.draw(p, QPointF(rect.left(), y))
        p.restore()


class Caption(QWidget):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._pal = theme.DARK
        self.setFixedHeight(14)

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        self.update()

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(W.app_font(9, QFont.Weight.Normal, 0.4))
        p.setPen(QPen(QColor(self._pal.text_tertiary)))
        p.drawText(self.rect().adjusted(1, 0, -1, 0), Qt.AlignLeft | Qt.AlignVCenter, self._text)


# --------------------------------------------------------------------------- 设置浮层
class SettingsPanel(QWidget):
    """Liquid Glass 设置浮层：每行一块漂浮玻璃胶囊 + 镜面亮边 + 抓屏折射。"""

    changed = Signal(str, bool)
    theme_changed = Signal(int)
    threshold_changed = Signal(int)
    language_changed = Signal(str)
    closed = Signal()

    SHADOW = 12
    SLIDE = 6            # 出现 / 隐藏时上下滑动的距离（px）
    ROW_H = 40
    GAP = 5
    # 宽度必须让卡片左缘落到主窗口电量环（最左像素 x≈96）之外，
    # 否则环的左弧会从浮层左边缘露出 2px，像一条竖直的杂边。
    W, H = 312, 386
    SEG_W = 138

    # 固定布局（局部坐标）
    CARD = QRectF(12, 12, 288, 362)
    ROW_X = 28.0
    ROW_W = 256.0
    ROWS_TOP = 50.0
    TITLE_Y = 20.0

    #: 与电量强相关的行：纯有线鼠标时整行压暗且不可点
    BATTERY_KEYS = ("lowbat", "lowbat_level")

    ROWS = [
        ("autostart", "开机自动启动"),
        ("pin", "窗口置顶"),
        ("language", "语言"),
        ("theme", "主题"),
        ("lowbat", "低电量提醒"),
        ("lowbat_level", "提醒阈值"),
    ]

    #: 提醒阈值可选项（百分比）。四档够用，也不会把分段控件挤成小方块
    THRESHOLDS = (10, 15, 20, 30)

    #: 换成下拉/分段时都认这个键，避免各处再散落字符串
    THRESHOLD_KEY = "lowbat_level"

    def __init__(self, cfg: config.Config, parent=None) -> None:
        # 为什么是 Qt.Tool 而不是 Qt.Popup（v1.3.3）：
        # Popup 展开期间会**抢全局鼠标 grab**——主窗口头部按钮（齿轮/关闭）
        # 收不到任何悬停和点击，手型光标也失效，正是用户报的
        # 「设置页面外原本应该是鼠标手的区域功能丢失」。
        # 改成不抢 grab 的顶层工具窗口（同 DeviceMenu 的架构），
        # 点外部收起交给主窗口已有的全局鼠标钩子。
        super().__init__(
            parent,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint,
        )
        self._pal = theme.DARK
        self._cfg = cfg
        self._blur: QPixmap | None = None
        self._hover_row = -1
        self._cursor_hand: bool | None = None
        self._pill_paths: dict[int, object] = {}
        # 出现 / 隐藏动画的状态机：hidden → showing → shown → hiding → hidden
        self._state = "hidden"
        self._anchor = QPoint()
        self._closed_at = 0.0
        self._anim: QAbstractAnimation | None = None
        self._win_hooked = None
        # 齿轮主动收起的标记（托盘菜单等路径共用），保留语义
        self.gear_hidden = False
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setFixedSize(self.W, self.H)
        # 兜底校准高亮：Popup 会漏发 mouseMove / leave，靠它保证不会「残留高亮」
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(60)
        self._hover_timer.timeout.connect(self._sync_hover_from_cursor)

        self._rows = self.ROWS
        self._battery_ok = True      # 无线鼠标才有低电量提醒；有线时整块置灰
        self.switches: dict[str, W.Switch] = {}
        for i, (key, _title) in enumerate(self._rows):
            # 语言 / 主题 / 阈值那三行右侧是分段控件，不能再叠一个开关上去
            if key in ("language", "theme", self.THRESHOLD_KEY):
                continue
            sw = W.Switch(parent=self)
            sw.move(int(self.ROW_X + self.ROW_W - 12 - sw.width()),
                    int(self.rows_top(i) + (self.ROW_H - sw.height()) / 2))
            sw.toggled.connect(lambda on, k=key: self.changed.emit(k, on))
            self.switches[key] = sw

        self.theme_seg = W.Segment(self.theme_labels(), 0, self.SEG_W, parent=self)
        theme_index = self.row_index("theme")
        self.theme_seg.move(int(self.ROW_X + self.ROW_W - 12 - self.SEG_W),
                            int(self.rows_top(theme_index) + (self.ROW_H - self.theme_seg.height()) / 2))
        self.theme_seg.changed.connect(self._on_theme_segment)

        # 语言：中文 / English。放的就是原来「关闭时最小化到托盘」那一行——
        # 关闭行为已固定为最小化到托盘，不再占一个开关位。
        self.lang_seg = W.Segment(["中文", "English"], 0, self.SEG_W, parent=self)
        lang_index = self.row_index("language")
        self.lang_seg.move(int(self.ROW_X + self.ROW_W - 12 - self.SEG_W),
                           int(self.rows_top(lang_index)
                               + (self.ROW_H - self.lang_seg.height()) / 2))
        self.lang_seg.changed.connect(self._on_language_segment)

        # 低电量提醒阈值：四档百分比，放在「提醒阈值」那一行
        self.threshold_seg = W.Segment([f"{v}%" for v in self.THRESHOLDS],
                                       1, self.SEG_W, parent=self)
        level_index = self.row_index(self.THRESHOLD_KEY)
        self.threshold_seg.move(int(self.ROW_X + self.ROW_W - 12 - self.SEG_W),
                                int(self.rows_top(level_index)
                                    + (self.ROW_H - self.threshold_seg.height()) / 2))
        self.threshold_seg.changed.connect(self._on_threshold_segment)

        # 鼠标可能直接落在开关或分段控件上（不经过行内空白），靠 Enter 事件补一次
        for ctrl in (*self.switches.values(), self.theme_seg, self.threshold_seg,
                     self.lang_seg):
            ctrl.installEventFilter(self)
        self.sync()

    @staticmethod
    def theme_labels() -> list[str]:
        return [i18n.T("系统"), i18n.T("浅色"), i18n.T("深色")]

    def _on_language_segment(self, index: int) -> None:
        self.language_changed.emit(str(i18n.LANGS[int(index)]))

    def _on_theme_segment(self, index: int) -> None:
        self.theme_changed.emit(int(index))

    def _on_threshold_segment(self, index: int) -> None:
        self.threshold_changed.emit(self.THRESHOLDS[int(index)])

    def rows_top(self, index: int) -> float:
        return self.ROWS_TOP + index * (self.ROW_H + self.GAP)

    def row_index(self, key: str) -> int:
        """行号按 key 查，别在代码里写死下标（加行就会错位）。"""
        for i, (k, _title) in enumerate(self._rows):
            if k == key:
                return i
        return -1

    def pill_rect(self, index: int) -> QRectF:
        """某一行的可交互范围 = 画出来的那块胶囊，命中判定和绘制共用同一份。"""
        return QRectF(self.ROW_X, self.rows_top(index), self.ROW_W, self.ROW_H)

    def _pill_path(self, index: int):
        """圆角路径，用来把四个角里「胶囊之外」的那一点也算成不命中。"""
        path = self._pill_paths.get(index)
        if path is None:
            path = W.rounded(self.pill_rect(index), 15)
            self._pill_paths[index] = path
        return path

    def _row_at(self, pos: QPointF) -> int:
        """必须二维命中：只看纵坐标会让卡片左右两侧的空白也点亮整行。"""
        for i in range(len(self._rows)):
            if self._pill_path(i).contains(pos):
                return i
        return -1

    # ---------------------------------------------------------------- 整行交互
    def _row_disabled(self, row: int) -> bool:
        """电量相关的行在纯有线鼠标下是「不可点」的灰行。"""
        if not 0 <= row < len(self._rows):
            return False
        return (not self._battery_ok) and self._rows[row][0] in self.BATTERY_KEYS

    def _row_clickable(self, row: int) -> bool:
        """整行点击能切换开关的才给手型；主题 / 阈值那两行由子控件自己响应。"""
        if not 0 <= row < len(self._rows) or self._row_disabled(row):
            return False
        return self._rows[row][0] in self.switches

    def _segment_for(self, key: str):
        """某一行右侧的分段控件（语言 / 主题 / 提醒阈值），没有就 None。"""
        if key == "language":
            return self.lang_seg
        if key == "theme":
            return self.theme_seg
        if key == self.THRESHOLD_KEY:
            return self.threshold_seg
        return None

    def wants_hand(self, global_pos: QPoint) -> bool:
        """这个屏幕坐标算不算「可点」——手型光标的唯一判据。

        浮窗打开期间，主窗口的轮询（`MainWindow._poll_float_hover`）每 30ms
        会强制 SetCursor 一次；设置浮层是非激活的 Tool 窗口，自己的
        WM_SETCURSOR 抢不过它，所以「哪里该是手型」必须两边共用这一份判定，
        不然轮询会把这里设好的手型立刻按回箭头（就是用户看到的「设置页里
        怎么点都是箭头」）。
        """
        if not self.isVisible():
            return False
        local = QPointF(self.mapFromGlobal(global_pos))
        if not self.rect().contains(local.toPoint()):
            return False
        row = self._row_at(local)
        if row < 0 or self._row_disabled(row):
            return False
        key = self._rows[row][0]
        if key in self.switches:
            return True                    # 开关行整行都可点，开关本身也在行内
        seg = self._segment_for(key)
        return bool(seg is not None and not seg.isHidden()
                    and seg.geometry().contains(local.toPoint()))

    def _apply_cursor(self) -> None:
        """按当前光标位置刷新形状；判定没变就不重复 setCursor。"""
        hand = self.wants_hand(QCursor.pos())
        if hand == self._cursor_hand:
            return
        self._cursor_hand = hand
        self.setCursor(Qt.PointingHandCursor if hand else Qt.ArrowCursor)

    def _sync_hover(self, row: int) -> None:
        # 光标形状单独判：同一行里「行空白」和「右侧分段控件」的手型不一样，
        # 只看行号有没有变化，主题行就会一直卡在箭头上
        self._apply_cursor()
        if row == self._hover_row:
            return
        self._hover_row = row
        self.update()

    def _sync_hover_from_cursor(self) -> None:
        """按真实光标位置校准高亮。

        Windows 上 Popup 窗口偶尔漏发/延迟 mouseMove 与 leave，光靠事件会让
        上一次的高亮「粘」在面板上（鼠标已经移开，某一行还亮着）。这里用定时
        器兜底：只要光标不在面板里，就一定把高亮清掉。
        """
        if not self.isVisible():
            return
        local = self.mapFromGlobal(QCursor.pos())
        row = self._row_at(QPointF(local)) if self.rect().contains(local) else -1
        self._sync_hover(row)

    def _restore_window_cursor(self) -> None:
        """面板收起时把主窗口光标还原。

        面板开着的那段时间主窗口可能正停在齿轮手型上；收起时主动清一次，
        并补发 WM_SETCURSOR 让 Windows 重算光标，避免手型黏在整个 HUD 上。
        """
        win = self.parentWidget()
        if win is None:
            return
        self._cursor_hand = None
        win.unsetCursor()
        win.refresh_cursor()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 这个过滤器同时装在子控件和窗口上，只有子控件才参与行高亮
        if event.type() == QEvent.Enter and isinstance(obj, QWidget):
            pt = obj.mapTo(self, QPoint(0, 0))
            self._sync_hover(self._row_at(QPointF(pt.x() + obj.width() / 2,
                                                 pt.y() + obj.height() / 2)))
        return super().eventFilter(obj, event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._sync_hover(self._row_at(event.position()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        # 移到子控件（开关 / 分段控件）上也会触发 leaveEvent，用光标位置区分
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._sync_hover(-1)
        super().leaveEvent(event)

    def _hit_control(self, pos: QPointF) -> bool:
        """pos 是否落在某个子控件（开关 / 分段控件）上。"""
        pt = pos.toPoint()
        return any(ctrl.geometry().contains(pt)
                   for ctrl in (*self.switches.values(), self.theme_seg,
                                self.threshold_seg, self.lang_seg))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # 落在开关上的点击已经由控件自己处理，它的 release 会冒泡到这里
        # （QWidget 默认 ignore）。不拦下来，整行点击会把刚切换的开关又拨回去，
        # 用户看到的就是「开关点不动」。
        if self._hit_control(event.position()):
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.LeftButton:
            row = self._row_at(event.position())
            if 0 <= row < len(self._rows) and not self._row_disabled(row):
                key = self._rows[row][0]
                sw = self.switches.get(key)
                if sw is not None:
                    sw.set_checked(not sw.is_checked())
                    self.changed.emit(key, sw.is_checked())
                    event.accept()
                    return
        super().mouseReleaseEvent(event)

    # ---------------------------------------------------------------- 出现 / 隐藏动画
    def is_open(self) -> bool:
        """正在出现、已出现、正在消失都算「开着」，避免动画中途被再次触发。"""
        return self._state != "hidden"

    def closed_within(self, seconds: float) -> bool:
        """刚刚是否因为点击外部而收起（Qt 会把那次点击回放到齿轮上）。"""
        return self._closed_at > 0.0 and (time.monotonic() - self._closed_at) <= seconds

    def _stop_anim(self) -> None:
        anim = self._anim
        self._anim = None
        if anim is not None:
            anim.stop()

    def show_animated(self) -> None:
        """淡入 + 从上方 6px 滑入，避免「啪」地一下出现。"""
        if self._state in ("showing", "shown"):
            return
        self._stop_anim()
        target = self.pos()
        self._anchor = QPoint(target)
        self._state = "showing"
        self._closed_at = 0.0

        self.setWindowOpacity(0.0)
        self.move(target.x(), target.y() - self.SLIDE)
        # 先挂上 owner 关系再显示；Qt 的原生 show 是异步的，真正的挂载
        # 放在 showEvent 里，这里只是提前铺好，减少一帧的空窗。
        self._attach_owner()
        self.show()
        self.raise_()

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(150)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(170)
        slide.setStartValue(QPoint(target.x(), target.y() - self.SLIDE))
        slide.setEndValue(target)
        slide.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.finished.connect(self._finish_show)
        group.start()
        self._anim = group

    def _finish_show(self) -> None:
        if self._state == "showing":
            self._state = "shown"
            self.setWindowOpacity(1.0)
            if not self._anchor.isNull():
                self.move(self._anchor)

    def _attach_owner(self) -> None:
        """把设置浮层设成主窗口的 owned window。

        Windows 的硬规则：**owned window 永远显示在 owner 之上**，并且 owner
        的置顶状态变化时 owned window 会自动跟随。

        这是「切换窗口置顶时设置页面消失 / 闪一下再出现」的根治办法：
        浮层不是 owned window 时，主窗口 SetWindowPos(HWND_TOPMOST) 会被
        送到置顶层顶部，直接把浮层盖住（实测每次切换必闪）；改成 owned
        window 后，Windows 自己保证浮层始终压在主窗口之上，切换过程没有
        任何中间态 —— 实测 60 次连续切换 0 帧闪动。
        """
        win = self.parentWidget()
        if win is None:
            return
        try:
            wineffects.set_window_owner(int(self.winId()), int(win.winId()))
        except (RuntimeError, ValueError):
            pass

    def hide_animated(self) -> None:
        """淡出 + 向上 6px 收回，和出现动画对称。"""
        if self._state in ("hidden", "hiding"):
            return
        self._stop_anim()
        start = self.pos()
        anchor = self._anchor if not self._anchor.isNull() else start
        end = QPoint(anchor.x(), anchor.y() - self.SLIDE)
        self._state = "hiding"
        # 记下收起时刻：紧随其后的那次回放点击不能再把面板叫回来
        self._closed_at = time.monotonic()

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(130)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InCubic)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(150)
        slide.setStartValue(start)
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.InCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.finished.connect(self._finish_hide)
        group.start()
        self._anim = group

    def _finish_hide(self) -> None:
        self._anim = None
        # 先挪回锚点再隐藏：窗口隐藏后 move() 不会立刻作用到原生窗口
        if not self._anchor.isNull():
            self.move(self._anchor)
        self._state = "hidden"
        self.setWindowOpacity(1.0)
        self._sync_hover(-1)          # 收起时清掉高亮，下次出现不能带着上次的状态
        self._hover_timer.stop()
        self._restore_window_cursor()
        self.hide()
        self.closed.emit()

    def hide_now(self) -> None:
        """主窗口进托盘 / 退出时立刻收起，不播动画。"""
        was_open = self._state != "hidden"
        self._stop_anim()
        self._state = "hidden"
        self.setWindowOpacity(1.0)
        self._sync_hover(-1)
        self._hover_timer.stop()
        self._restore_window_cursor()
        if not self._anchor.isNull():
            self.move(self._anchor)
        self.hide()
        if was_open:
            self.closed.emit()   # 让齿轮的「已打开」高亮跟着熄灭

    def closeEvent(self, event) -> None:  # noqa: N802
        """系统级关闭（托盘菜单退出等）先播淡出，动画收尾才真正隐藏。"""
        if self._state in ("showing", "shown"):
            event.ignore()
            self.hide_animated()
            return
        super().closeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._hover_timer.stop()
        self._sync_hover(-1)
        self._restore_window_cursor()
        # 被外部强制隐藏（父窗口收起等）时同步状态，避免卡在「开着」
        if self._state not in ("hidden", "hiding"):
            self._state = "hidden"
            self.setWindowOpacity(1.0)
            if not self._anchor.isNull():
                self.move(self._anchor)
            self.closed.emit()

    # ---------------------------------------------------------------- 出现 / 隐藏动画
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 原生窗口这时才真正显示，Qt 之前对 owner 的设置已经落地 ——
        # 在这里重新挂 owner 关系，Windows 才会保证浮层永远压在主窗口之上。
        self._attach_owner()
        self._hover_timer.start()
        self._sync_hover_from_cursor()

    def set_battery_available(self, available: bool) -> None:
        """纯有线鼠标没有电池：低电量提醒 / 阈值两行置灰、控件禁用。"""
        available = bool(available)
        if available == self._battery_ok:
            return
        self._battery_ok = available
        sw = self.switches.get("lowbat")
        if sw is not None:
            sw.set_enabled_state(available)
        self.threshold_seg.set_enabled_state(available)
        if not available and self._row_disabled(self._hover_row):
            self._sync_hover(-1)
        self.update()

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        for sw in self.switches.values():
            sw.set_palette_theme(pal)
        self.theme_seg.set_palette_theme(pal)
        self.threshold_seg.set_palette_theme(pal)
        self.lang_seg.set_palette_theme(pal)
        self.update()

    def theme_mode(self) -> int:
        """0 = 系统，1 = 浅色，2 = 深色。"""
        if self._cfg.follow_system:
            return 0
        return 2 if self._cfg.dark_mode else 1

    def threshold_index(self) -> int:
        """当前阈值在分段控件里的下标；不在预设里（手改配置）就落到最近的档。"""
        level = config.clamp_threshold(self._cfg.low_battery_threshold)
        return min(range(len(self.THRESHOLDS)),
                   key=lambda i: abs(self.THRESHOLDS[i] - level))

    def sync(self) -> None:
        values = {
            "autostart": autostart.is_enabled(),
            "pin": self._cfg.always_on_top,
            "lowbat": bool(self._cfg.low_battery_notify),
        }
        for key, value in values.items():
            self.switches[key].set_checked(value, animate=False)
        # 分段控件的文字也要跟着语言走：切到英文后「系统 / 浅色 / 深色」
        # 必须变成 System / Light / Dark，否则一半中文一半英文
        self.theme_seg.set_labels(self.theme_labels(), keep_index=True)
        self.lang_seg.set_index(0 if i18n.lang() == i18n.ZH else 1, animate=False)
        self.theme_seg.set_index(self.theme_mode(), animate=False)
        self.threshold_seg.set_index(self.threshold_index(), animate=False)
        self.update()

    def set_value(self, key: str, value: bool) -> None:
        if key in self.switches:
            self.switches[key].set_checked(value, animate=True)

    def _grab_parent_backdrop(self) -> QPixmap | None:
        """直接复用主窗口已缓存的静态背景。

        设置浮层整块都盖在主窗口之上，所以「背后」就是主窗口自己。用它的缓存
        而不是抓屏，既不会把浮层自己拍进去，也不会在切换深浅色后残留上一套
        主题的颜色（那是圆角外出现灰圈的根因）。
        """
        parent = self.parentWidget()
        pix = getattr(parent, "_backdrop", None)
        if parent is None or pix is None or pix.isNull():
            return None
        try:
            pos = self.mapToGlobal(QPoint(0, 0)) - parent.mapToGlobal(QPoint(0, 0))
        except RuntimeError:
            return None
        dpr = float(pix.devicePixelRatio() or 1.0)
        src = QRectF(pos.x() * dpr, pos.y() * dpr,
                     self.width() * dpr, self.height() * dpr)
        if not QRectF(pix.rect()).contains(src):
            return None
        shot = pix.copy(src.toRect())
        if shot.isNull():
            return None
        shot.setDevicePixelRatio(1.0)
        return shot

    def _grab_screen_backdrop(self) -> QPixmap | None:
        scr = self.screen() or QApplication.primaryScreen()
        if scr is None:
            return None
        geo = self.frameGeometry()
        shot = scr.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        if shot.isNull() or shot.width() < 8 or shot.height() < 8:
            return None
        shot.setDevicePixelRatio(1.0)
        return shot

    def prepare_backdrop(self) -> None:
        """取一张背后的画面，缩小再放大当作高斯模糊，作为玻璃折射层。"""
        shot = self._grab_parent_backdrop()
        if shot is None:  # 浮层超出了主窗口范围时才退回抓屏
            shot = self._grab_screen_backdrop()
        if shot is None or shot.isNull() or shot.width() < 8 or shot.height() < 8:
            self._blur = None
            return
        small = shot.scaled(max(2, shot.width() // 22), max(2, shot.height() // 22),
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        blur = small.scaled(shot.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        blur.setDevicePixelRatio(1.0)
        self._blur = blur

    def refresh_backdrop(self) -> None:
        """切换主题后调用：主窗口背景已重建，这里同步换掉折射层。"""
        self.prepare_backdrop()
        self.update()

    @staticmethod
    def _paint_row_icon(p: QPainter, key: str, tile: QRectF, color: QColor) -> None:
        box = tile.adjusted(6, 6, -6, -6)
        if key == "autostart":
            # 用 Windows 四格徽标而不是电源符号 ⏻：⏻ 单独看更像「关机」，
            # 四格徽标和 Windows 任务管理器「启动」页一致，语义无歧义。
            W.icon_winlogo(p, box, color)
        elif key == "pin":
            W.icon_pin(p, box, color, False, 1.5)
        elif key == "language":
            # 地球 + 经纬线：语言/区域设置的通用符号（原来这行是「托盘」图标，
            # 关闭行为固定后那一行改成了语言，图标必须一起换）
            W.icon_globe(p, box, color)
        elif key == "theme":
            W.icon_theme(p, box, color)
        elif key == "lowbat":
            W.icon_battery(p, box, color, level=0.3)
        elif key == "lowbat_level":
            W.icon_percent(p, box, color)

    def _paint_card(self, p: QPainter, rect: QRectF, radius: float,
                    hover: bool = False, dim: bool = False) -> None:
        """飞行胶囊：极淡本体 + 亮边 + 下方一圈柔和投影。

        ``dim`` 用于纯有线鼠标下那几行与电量有关的内容：整块降透明度，
        一眼能看出「现在没得调」。
        """
        pal = self._pal
        dark = pal.name == "dark"
        if dim:
            p.save()
            p.setOpacity(0.5)
        W.draw_outer_shadow(p, rect, radius, QColor(0, 0, 0, 70 if dark else 34),
                            layers=5, strength=0.18, dy=0.9)
        path = W.rounded(rect, radius)
        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if dark:
            fill.setColorAt(0.0, QColor(255, 255, 255, 30))
            fill.setColorAt(1.0, QColor(255, 255, 255, 14))
        else:
            fill.setColorAt(0.0, QColor(255, 255, 255, 242))
            fill.setColorAt(1.0, QColor(233, 233, 240, 225))
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPath(path)

        if hover:
            # 悬停提亮：深色下加一层白，浅色下压一点点灰，避免「变白没反应」
            p.setBrush(QColor(255, 255, 255, 34) if dark else QColor(120, 120, 128, 30))
            p.drawPath(path)

        # 深色模式不做「顶部打光」：这道自上而下渐隐的白色高光在深底上就是
        # 一束顶光，直接跳过，卡面保持平整。
        if not dark:
            hl = QLinearGradient(rect.topLeft(),
                                 QPointF(rect.left(), rect.top() + rect.height() * 0.55))
            hl.setColorAt(0.0, QColor(255, 255, 255, 150))
            hl.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(hl)
            p.drawPath(path)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 46 if dark else 240), 1.0))
        p.drawPath(path)
        if dim:
            p.restore()

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        dark = pal.name == "dark"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # 底板留一点透明度做折射，但足够厚，避免主界面的文字在浮层上「透影子」
        # （面板变矮后下缘正好压在灵敏度卡片上，208 会让卡片边缘透出来）
        wash = QColor(pal.surface_window_top)
        wash.setAlpha(216 if dark else 232)
        # 底板外圈加一圈柔和投影，让浮层从背景里浮起来（用户要求弹窗加阴影）
        W.draw_outer_shadow(p, self.CARD, 24, QColor(0, 0, 0, 90 if dark else 55),
                            layers=7, strength=0.3, dy=0.9)
        W.paint_liquid(p, self.CARD, 24, pal, backdrop=self._blur, base=wash)

        # ---------------------------------------------------------------- 标题
        p.setFont(W.app_font(13, QFont.Weight.DemiBold, 0.2))
        p.setPen(QPen(QColor(pal.text_primary)))
        p.drawText(QRectF(28, self.TITLE_Y, 160, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   i18n.T("设置"))

        # 版本号放在同一行的最右端（v1.2.61 起带软件名，v1.3.0 按用户要求放大）
        p.setFont(W.app_font(10.5, QFont.Weight.Normal, 0.2))
        ver = QColor(pal.text_tertiary)
        ver.setAlpha(170 if dark else 210)
        p.setPen(QPen(ver))
        p.drawText(QRectF(self.ROW_X, self.TITLE_Y, self.ROW_W, 22),
                   Qt.AlignRight | Qt.AlignVCenter, f"{i18n.T(setup_core.DISPLAY_NAME)} v{setup_core.VERSION}")

        # ---------------------------------------------------------------- 行胶囊
        pills = [QRectF(self.ROW_X, self.rows_top(i), self.ROW_W, self.ROW_H)
                 for i in range(len(self._rows))]
        for i, pill in enumerate(pills):
            # 只有「点整行就能切开关」的行才给悬停提亮；主题 / 阈值那两行由
            # 右侧分段控件自己响应，整行提亮会让人以为点哪都能选中
            self._paint_card(p, pill, 15, hover=(i == self._hover_row and self._row_clickable(i)))

        for i, (key, title) in enumerate(self._rows):
            pill = pills[i]
            dim = self._row_disabled(i)
            tile = QRectF(pill.left() + 10, pill.top() + 7, 26, 26)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 18 if dim else 26) if dark
                       else QColor(255, 255, 255, 150 if dim else 210))
            p.drawRoundedRect(tile, 8, 8)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 26 if dim else 42) if dark
                          else QColor(255, 255, 255, 200 if dim else 250), 1.0))
            p.drawRoundedRect(tile, 8, 8)
            # 与下方电量图例同一套中性色：浅色卡片上是近黑，深色卡片上是白
            glyph = QColor(pal.text_secondary)
            glyph.setAlpha((235 if dark else 255) if not dim else (86 if dark else 108))
            self._paint_row_icon(p, key, tile, glyph)

            # 每行只有一行标题，垂直居中在胶囊里；右侧控件宽度决定文字区
            text_left = tile.right() + 11
            ctrl_w = (self.SEG_W if key in ("language", "theme", self.THRESHOLD_KEY)
                      else 42)
            text_w = self.ROW_X + self.ROW_W - 12 - ctrl_w - 8 - text_left
            p.setFont(W.app_font(12, QFont.Weight.Medium, 0.2))
            title_color = QColor(pal.text_primary)
            if dim:
                title_color.setAlpha(105 if dark else 120)
            p.setPen(QPen(title_color))
            p.drawText(QRectF(text_left, pill.top(), text_w, pill.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, i18n.T(title))

        # ---------------------------------------------------------------- 电量颜色
        # 和图例做成同一套行胶囊：左缘与上面各行完全对齐，不再单独悬空
        legend = QRectF(self.ROW_X, self.rows_top(len(self._rows)), self.ROW_W, self.ROW_H)
        legend_dim = not self._battery_ok
        self._paint_card(p, legend, 15, dim=legend_dim)
        if legend_dim:
            # 图例的图标/文字是单独画的，跟着一起压暗才不会「卡片灰了字还亮」
            p.save()
            p.setOpacity(0.45)
        tile = QRectF(legend.left() + 10, legend.top() + 7, 26, 26)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26 if dark else 210))
        p.drawRoundedRect(tile, 8, 8)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 42 if dark else 250), 1.0))
        p.drawRoundedRect(tile, 8, 8)
        glyph = QColor(pal.text_secondary)
        glyph.setAlpha(235 if dark else 255)
        W.icon_battery(p, tile.adjusted(6, 6, -6, -6), glyph)

        font = W.app_font(9, QFont.Weight.Normal, 0.2)
        fm = QFontMetricsF(font)
        p.setFont(font)
        cy = legend.center().y()
        x = tile.right() + 11
        p.setPen(QPen(QColor(pal.text_secondary)))
        label = i18n.T("电量")
        p.drawText(QRectF(x, legend.top(), 40, legend.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, label)
        x += fm.horizontalAdvance(label) + 14
        low = config.clamp_threshold(self._cfg.low_battery_threshold)
        warn = low + theme.BATTERY_WARN_SPAN
        for level, text in ((max(1, low - 5), f"≤{low}%"),
                            (low + 8, f"{low + 1}–{warn}%"),
                            (warn + 10, f">{warn}%")):
            p.setPen(Qt.NoPen)
            p.setBrush(theme.battery_color(pal, level, low))
            p.drawRoundedRect(QRectF(x, cy - 4, 8, 8), 2.6, 2.6)
            p.setPen(QPen(QColor(pal.text_secondary)))
            p.drawText(QRectF(x + 10, cy - 7, 50, 14), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += 10 + fm.horizontalAdvance(text) + 9
        if legend_dim:
            p.restore()


# --------------------------------------------------------------------------- 主窗口
class MouseGlassWindow(QWidget):
    def __init__(self, cfg: config.Config, show_handle: int = 0) -> None:
        super().__init__()
        self.cfg = cfg
        self._show_handle = show_handle
        self._pal = self._resolve_palette()
        self._last_sig = None
        self._phase = 0.0
        self._backdrop = None
        self._animating = False
        self._quitting = False
        self._menu_open = False
        #: 设备切换弹窗（挂在窗口里的子控件，见 widgets.DeviceMenu）
        self._device_menu = None
        #: 回报率挡位弹窗（顶层浮层，见 widgets.RatePicker）
        self._rate_picker = None
        #: DPI 挡位管理弹窗（顶层浮层，见 widgets.LevelsPicker）
        self._level_picker = None
        #: 挡位面板打开的时刻：全局钩子会把「打开面板的那一下点击」再送回来，
        #: 短时间内不许据此收起面板
        self._picker_opened_at = 0.0
        self._level_picker_opened_at = 0.0
        self._tray_menu = None
        self._low_armed = True
        self._low_notified = 0.0
        self._low_level = None
        #: 当前鼠标走哪种驱动（web / software），用来同步按钮文案与图标
        self._driver_kind = ""
        #: 编辑模式、驱动打开等临时提示：这段时间内 _sync 不覆盖 caption
        self._caption_hold = 0.0
        #: 点卡片进入的编辑模式（DPI / 回报率各自独立应用）
        self._editing = False

        self.setWindowTitle(i18n.T("三颗豆"))
        self.setWindowIcon(make_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(CARD_W, CARD_H)
        self._apply_pin_flag()

        self.monitor = mon.DeviceHub(cfg.poll_interval, cfg.cache, cfg.device)
        # 键盘 / 耳机这类配件单独一路：只往设备弹窗里补行，不参与切换
        self.accessory = accessories.AccessoryMonitor()
        self._build_ui()
        self._apply_theme(self._pal, first=True)
        self._build_tray()

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._sync)
        self._ui_timer.start(350)

        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._check_theme)
        self._theme_timer.start(1500)

        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._persist)
        self._save_timer.start(30000)

        self._bg_timer = QTimer(self)
        self._bg_timer.timeout.connect(self._tick_bg)
        self._bg_timer.setInterval(70)  # 仅在悬停/激活时运行动画，静态时几乎零开销

        self._ipc_timer = QTimer(self)
        self._ipc_timer.timeout.connect(self._poll_show_event)
        self._ipc_timer.start(320)

        # 只在「打开」那一刻贴边一次，之后不再自己挪动窗口：常驻屏幕角落时
        # 任何后台定时重定位都会在弹窗变化/多屏切换的瞬间让窗口跳一下。
        # 唯一的例外是显示器增删、主屏换人、分辨率变化这类系统环境变更。
        self._watch_screens()

        self._low_timer = QTimer(self)
        self._low_timer.timeout.connect(self._check_low_battery)
        self._low_timer.start(60000)

        # 轮询线程若因异常退出，界面会永久停在旧数据；10 秒内自动拉起
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watch_monitor)
        self._watch_timer.start(10000)

        self._tray_click_timer = QTimer(self)
        self._tray_click_timer.setSingleShot(True)
        self._tray_click_timer.setInterval(260)
        self._tray_click_timer.timeout.connect(self._toggle_window)

        # 点在窗口外 → 收起到托盘。HUD 常驻屏幕角落时窗口本来就没有焦点，
        # 等「窗口失焦」事件是等不到的，只能用全局鼠标钩子捕捉按下的那一刻。
        self._hook_press: tuple[int, int] | None = None
        self._hook_timer = QTimer(self)
        self._hook_timer.setInterval(30)
        self._hook_timer.timeout.connect(self._check_hook_press)
        self._hook_timer.start()
        wineffects.install_mouse_hook(self._on_global_mouse_down)
        # 浮窗（设置面板 / 设备菜单）与主窗口重叠期间，主窗口收不到任何
        # 原生鼠标事件（平台限制，浮窗一显示 Qt 还会把主窗口的子控件
        # 动态原生化，输入彻底断流）——头部按钮的悬停和手型靠这个轮询兜底，
        # 点击则由 _on_press_outside 手动转发。
        self._float_hover_timer = QTimer(self)
        self._float_hover_timer.setInterval(30)
        self._float_hover_timer.timeout.connect(self._poll_float_hover)
        # 浮窗悬停接管用的 Win32 光标句柄（懒加载）
        self._float_hand_hcursor = None
        self._float_arrow_hcursor = None
        # 外部点击后稍等一下再收：托盘图标的点击也算「窗口外」，
        # 等托盘激活信号到了就取消，否则点托盘会「收起又弹回」。
        self._outside_timer = QTimer(self)
        self._outside_timer.setSingleShot(True)
        self._outside_timer.setInterval(160)
        self._outside_timer.timeout.connect(self._hide_by_outside_click)

        self._dock_bottom_right()
        self.monitor.start()
        self.accessory.start()
        QTimer.singleShot(120, self._sync)
        QTimer.singleShot(6000, self._check_low_battery)
        autostart.sync_command()

    # ------------------------------------------------------------------ 构建
    def _build_ui(self) -> None:
        self.header = Header(self)
        self.header.device_menu_requested.connect(self._show_device_menu)
        self.ring = W.BatteryRing(self)
        self.ring.setFixedSize(216, 216)
        self.ring.set_low_threshold(config.clamp_threshold(self.cfg.low_battery_threshold))

        self.card_dpi = W.InfoCard(i18n.T("灵敏度"))
        self.card_rate = W.InfoCard(i18n.T("回报率"))
        self.card_conn = W.InfoCard(i18n.T("连接"))
        self.cards = (self.card_dpi, self.card_rate, self.card_conn)
        self._build_hint_bar()

        # 点卡片即启动编辑；两个模块各自有自己的「应用 / 取消」
        self.card_dpi.edit_requested.connect(lambda: self._enter_edit("dpi"))
        self.card_rate.edit_requested.connect(lambda: self._enter_edit("rate"))
        self.card_dpi.value_committed.connect(lambda v: self._apply_edit("dpi", v))
        self.card_rate.value_committed.connect(lambda v: self._apply_edit("rate", v))
        self.card_dpi.edit_cancelled.connect(self._exit_edit)
        self.card_rate.edit_cancelled.connect(self._exit_edit)
        # 回报率不走数字输入：点卡片直接弹「鼠标内置的刷新率挡位」面板
        self.card_rate.set_picker(True)
        self.card_rate.picker_requested.connect(self._show_rate_picker)
        # 灵敏度编辑态多一个「挡位」入口按钮：点它弹竖向挡位管理面板
        self.card_dpi.set_levels_button(True)
        self.card_dpi.levels_requested.connect(self._show_levels_picker)

        self.btn_primary = W.GlassButton(driver.button_text(driver.WEB), kind="primary",
                                         icon=driver.button_icon(driver.WEB), height=46)
        self.btn_primary.clicked.connect(self._on_primary)
        self.btn_refresh = W.GlassButton(kind="icon", icon="refresh", height=46)
        self.btn_refresh.clicked.connect(self._on_refresh)

        self.caption = Caption(i18n.T("每 2 秒自动刷新"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAD, 12, PAD, 12)
        outer.setSpacing(0)
        outer.addWidget(self.header)
        # 竖向间距用可调的 QSpacerItem 存起来：进入编辑时收紧腾地方，
        # 窗口高度始终固定 470（不再拉高窗口——用户报过编辑态页面错位）
        self._sp_header = QSpacerItem(0, 6, QSizePolicy.Fixed, QSizePolicy.Fixed)
        outer.addItem(self._sp_header)
        outer.addWidget(self.ring, 0, Qt.AlignHCenter)
        self._sp_ring = QSpacerItem(0, 10, QSizePolicy.Fixed, QSizePolicy.Fixed)
        outer.addItem(self._sp_ring)
        outer.addWidget(self.hint_bar)
        # 挡位不再用横向挡位条（LevelsBar），改成点卡片内「挡位」按钮弹竖向
        # 面板（LevelsPicker）——横向条移除后，提示条直接贴到卡片上方，不留空洞

        row = QHBoxLayout()
        row.setSpacing(8)
        for card in self.cards:
            row.addWidget(card, 1)
        outer.addLayout(row)
        self._sp_cards = QSpacerItem(0, 16, QSizePolicy.Fixed, QSizePolicy.Fixed)
        outer.addItem(self._sp_cards)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self.btn_primary, 1)
        btn_row.addWidget(self.btn_refresh, 0)
        outer.addLayout(btn_row)
        self._sp_caption = QSpacerItem(0, 12, QSizePolicy.Fixed, QSizePolicy.Fixed)
        outer.addItem(self._sp_caption)
        outer.addWidget(self.caption)
        outer.addStretch(0)

        self.header.btn_close.clicked.connect(self._hide_to_tray)
        self.header.btn_settings.clicked.connect(self._toggle_settings)
        self.header.set_close_to_tray(self.cfg.close_to_tray)

        self.settings = SettingsPanel(self.cfg, self)
        self.settings.changed.connect(self._on_setting)
        self.settings.theme_changed.connect(self._on_theme_mode)
        self.settings.threshold_changed.connect(self._on_low_threshold)
        self.settings.language_changed.connect(self._on_language)
        self.settings.closed.connect(self._on_settings_closed)
        self.settings.set_palette_theme(self._pal)

    # ------------------------------------------------------------------ 编辑模式
    def _build_hint_bar(self) -> None:
        """卡片正上方的范围提示：DPI 给范围+步进，回报率给范围。

        布局与下方卡片行**同构**：三列（灵敏度 / 回报率 / 连接），每列与
        卡片行等宽、间距一致。DPI 提示放进第 1 列、回报率提示放进第 2 列、
        连接列无提示恒空。每个提示胶囊**撑满自己那一列**（左右边界与对应
        卡片左右边界严格对齐），于是回报率提示精确落在中间「回报率」卡片
        正上方、左右不缩进（用户报「刷新率提示没左右对齐」）。

        高度**跟着内容走**：DPI 提示是两行，回报率提示是单行，固定高度
        32px 时两行会被裁掉第一行顶部。按 label 实际高度（heightForWidth）
        撑开提示条。
        """
        self.hint_bar = QWidget(self)
        lay = QHBoxLayout(self.hint_bar)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setSpacing(8)
        self.hint_dpi = QLabel("")
        self.hint_rate = QLabel("")
        for lab in (self.hint_dpi, self.hint_rate):
            lab.setWordWrap(True)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # 三列容器与卡片行同构（等宽 + 等间距），提示胶囊 stretch=1 撑满列宽
        self._hint_cols: list[QWidget] = []
        for _ in range(3):
            col = QWidget(self.hint_bar)
            inner = QHBoxLayout(col)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(0)
            self._hint_cols.append((col, inner))
            lay.addWidget(col, 1)
        self._hint_cols[0][1].addWidget(self.hint_dpi, 1)
        self._hint_cols[1][1].addWidget(self.hint_rate, 1)
        self.hint_bar.setVisible(False)
        self.hint_bar.setFixedHeight(32)     # 单行时的默认高度
        self._style_hints()

    def _hint_labels(self) -> list:
        """当前有内容的提示胶囊（回报率提示是单行、DPI 提示是两行）。"""
        return [lab for lab in (self.hint_dpi, self.hint_rate)
                if lab.text()]

    def _fit_hint_bar(self) -> None:
        """按提示胶囊的实际行数撑开提示条，保证第一行永远不被裁。

        高度取两个胶囊里更高者的 heightForWidth：DPI 两行约 35px、回报率
        单行约 18px，取 35px 再加底部留白。
        """
        labs = self._hint_labels()
        if not labs:
            return
        # 每个胶囊现在只在它那一列（等宽三列之一）内排版：
        # 列宽 = (内容区 356 - 两处间距 16) / 3 ≈ 113。DPI 两行在 113px 内
        # 完整放下（实测 sizeHint 约 106px），回报率单行 96px，均不被压窄。
        avail = CARD_W - 2 * PAD
        share = int((avail - 2 * 8) / 3)   # 单列宽，与卡片列宽一致
        need = 0
        for lab in labs:
            need = max(need, lab.heightForWidth(share))
        height = need + 4          # 底部留白（提示条布局的下边距）
        if height != self.hint_bar.height():
            self.hint_bar.setFixedHeight(height)

    def _style_hints(self) -> None:
        """范围提示做成胶囊标签：弱化颜色、加边界，比裸蓝色小字更清楚。

        两个胶囊都撑满各自的卡片列宽（左右边界对齐卡片），文字靠左。
        回报率提示只有一行（档位明细在回报率下拉框里）。
        """
        dark = self._pal.name == "dark"
        if dark:
            bg, fg, br = "rgba(255,255,255,24)", "rgba(255,255,255,170)", "rgba(255,255,255,38)"
        else:
            bg, fg, br = "rgba(255,255,255,190)", "rgba(18,24,34,150)", "rgba(18,24,34,26)"
        css = (f"background-color: {bg}; color: {fg}; border: 1px solid {br};"
               f" border-radius: 8px; font-size: 9px; padding: 3px 7px;")
        for lab in (self.hint_dpi, self.hint_rate):
            lab.setStyleSheet(css if lab.text() else "")
            lab.setVisible(bool(lab.text()))
        self._fit_hint_bar()

    def _update_edit_caps(self, info) -> None:
        """按当前这台鼠标的可写能力决定卡片能不能点、提示写什么。"""
        caps = info.caps
        live = bool(info.connected and not info.sleeping)
        dpi_ok = live and caps.dpi_editable
        rate_ok = live and caps.rate_editable
        self.card_dpi.set_editable(dpi_ok)
        self.card_rate.set_editable(rate_ok)
        # 「挡位」入口：表可增删（X7）给完整管理；表只读但当前 DPI 可切
        # （G502，0x2201 fn3 可写）也给按钮——弹「仅切换」面板
        self.card_dpi.set_levels_button(bool(
            live and info.dpi_table and (caps.levels_editable or caps.dpi_editable)))

        def _support_app(writable: bool) -> str:
            """不支持编辑时，这块由哪个软件说了算；空串 = 本软件能改，不挂角标。

            罗技的实时值由 G HUB 的配置驱动（固件拒写 fn2），私有协议
            （CROCIRIS）目前可写，真读到不支持的设备就写「官方软件」兜底。
            狼途这类没有厂商通道的设备（caps.source == "hw"）连官方软件都
            没有，写「按键」——卡片会把它渲染成「按键调节」，告诉用户去哪改。
            """
            if not live or writable:
                return ""
            if caps.source == "hw":
                return "按键"
            return "G HUB" if caps.source == "hidpp" else "官方软件"

        self.card_dpi.set_support_app(_support_app(dpi_ok))
        self.card_rate.set_support_app(_support_app(rate_ok))
        # 回报率走「弹窗选挡位」：不手打数字，列出鼠标内置的各档。
        # 但**只有本软件能写回报率时才给挡位值**——不可编辑（由 G HUB 或官方
        # 软件管理）时给空列表，卡片不可点击、点了也不弹窗（用户明确要求
        # 「无法编辑的不可以点击出弹窗」）。
        self.card_rate.set_pick_values(caps.rate_values if (live and rate_ok) else [])
        # 范围提示**只在可编辑时给**：不可编辑的模块正上方不再出现文字提示
        # （用户明确要求「无法编辑的不再出现提示」），改成卡片右上角常驻
        # 一行「由 XX 软件支持」。
        # 之前按「能不能读到范围」给（G502 回报率固件拒写但 125–1000 Hz 能读），
        # 导致只读模块上方还挂着「可调 …」的提示，看起来能改实则不能，误导。
        self.hint_dpi.setText(caps.dpi_hint() if dpi_ok else "")
        self.hint_rate.setText(caps.rate_hint() if rate_ok else "")
        self._style_hints()
        if self._editing and not (dpi_ok or rate_ok):
            self._exit_edit()

    def _set_edit_layout(self, editing: bool) -> None:
        """编辑态布局：**窗口高度固定不变**（470），靠缩小电量环 + 收紧间距
        腾出提示条和卡片编辑控件的位置，卡片模块整体上移；底部说明文字
        （「每 2 秒自动刷新」）在编辑期间让位隐藏。

        之前是拉高窗口 470→530，编辑一开一关整窗跳动还带出页面错位（用户报）。

        挡位由横向挡位条（26px）改成弹窗后，那 26px 让给电量环
        （168 → 194），提示条正好贴到卡片上方、不留空洞（用户要求）。
        """
        ring = EDIT_RING_D if editing else 216
        if (self.ring.width(), self.ring.height()) != (ring, ring):
            self.ring.setFixedSize(ring, ring)
        for item, h in ((self._sp_header, 3 if editing else 6),
                        (self._sp_ring, 1 if editing else 10),
                        (self._sp_cards, 6 if editing else 16),
                        (self._sp_caption, 0 if editing else 12)):
            item.changeSize(0, h)
            item.invalidate()
        self.caption.setVisible(not editing)
        self.layout().update()
        self.layout().activate()

    def _enter_edit(self, which: str) -> None:
        info = self.monitor.snapshot()
        caps = info.caps
        if not info.connected or not (caps.dpi_editable or caps.rate_editable):
            return
        self._editing = True
        self._set_edit_layout(True)
        self.hint_bar.setVisible(True)
        for card in self.cards:
            card.setFixedHeight(EDIT_CARD_H)
        if caps.dpi_editable:
            self.card_dpi.start_edit(info.dpi or caps.dpi_min, caps, "dpi")
        if caps.rate_editable:
            self.card_rate.start_edit(info.report_rate or 1000, caps, "rate")
        self.layout().activate()
        self._fit_hint_bar()       # 布局跑完后再按实际宽度校一次提示条高度
        self._last_sig = None
        applog.get("main").info("进入编辑模式（点 %s）", which)

    def _exit_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        self.card_dpi.stop_edit()
        self.card_rate.stop_edit()
        for card in self.cards:
            card.setMinimumHeight(78)
            card.setMaximumHeight(16777215)
        self.hint_bar.setVisible(False)
        self._set_edit_layout(False)
        self._last_sig = None

    def _apply_edit(self, kind: str, value: int) -> None:
        """真写回设备（写后读回校验），失败不动卡片、给一行说明。

        挡位表（增删档）不再在这里写——改由挡位弹窗（LevelsPicker）的
        切档 / 增档 / 删档各自独立写回，应用按钮只负责写当前 DPI 数值。
        """
        notes: list[str] = []
        if kind == "dpi":
            ok, msg = self.monitor.set_dpi(value)
            label = f"DPI {value}"
            if msg:
                notes.append(msg)
        else:
            ok, msg = self.monitor.set_report_rate(value)
            label = f"回报率 {value} Hz"
            if msg:
                notes.append(msg)
        self._caption_hold = time.time() + 2.5
        if ok:
            self._exit_edit()
            self._last_sig = None
            self._sync()
            text = f"已写入 {label}"
            if notes:
                text += f"（{'；'.join(notes)}）"
            self.caption.set_text(text)
        else:
            self.caption.set_text(f"写入失败：{'；'.join(notes) or '设备未响应'}")
        QTimer.singleShot(2500, self._restore_caption)
        applog.get("main").info("编辑 %s → %s (%s)", kind, value,
                                "成功" if ok else "失败")

    # ---------------------------------------------------------- 回报率挡位弹窗
    def _show_rate_picker(self) -> None:
        """点回报率卡片 → 弹出鼠标内置的刷新率挡位，当前挡位自动打勾。

        回报率**不允许手动输入数字**：这里列出的每一档都是设备自己上报的
        可选值（caps.rate_values），选哪档就写哪档。
        """
        info = self.monitor.snapshot()
        caps = info.caps
        # 本软件不能编辑回报率（G HUB 管理 / 不支持编辑）时，不弹挡位面板
        if not (info.connected and not info.sleeping) or not caps.rate_editable:
            return
        values = sorted({int(v) for v in (caps.rate_values or []) if int(v) > 0},
                        reverse=True)
        if not values:
            self.caption.set_text("未读取到可用的回报率挡位")
            self._caption_hold = time.time() + 2.5
            QTimer.singleShot(2500, self._restore_caption)
            return
        current = int(info.report_rate or 0)
        if current and current not in values:
            # 设备报的当前值不在表里（老固件常见）也列出来，并标成当前
            values.append(current)
            values.sort(reverse=True)
        old = self._rate_picker
        if old is not None and old.isVisible():
            old.hide_picker()          # 再点一次 = 收起
            return
        picker = W.RatePicker(values, current, self._pal, caps.rate_writable, self)
        picker.chosen.connect(self._on_rate_picked)
        picker.closed.connect(self._on_rate_picker_closed)
        self._rate_picker = picker
        # 摆在回报率卡片正上方（卡片在窗口下半屏，HUD 常贴着屏幕底部）
        # 注意：mapToGlobal 收的是**控件自身**坐标系的点，geometry() 已经是
        # 父坐标系的偏移了，直接拿来用会多加一遍（面板会被推到屏幕外）
        anchor = self.card_rate.mapToGlobal(
            QPoint(self.card_rate.width() // 2, 0))
        picker.show_near(anchor, prefer_above=True)
        self._picker_opened_at = time.time()
        self._update_float_hover_poll()
        applog.get("main").info("回报率挡位面板: %s（当前 %s）", values, current)

    def _on_rate_picked(self, value: int) -> None:
        """选了一档：可写就直接写回设备；不可写给一句说明（去 G HUB 改）。"""
        info = self.monitor.snapshot()
        if not info.caps.rate_writable:
            self._caption_hold = time.time() + 2.5
            self.caption.set_text("回报率由 G HUB 管理，请在 G HUB 中修改")
            QTimer.singleShot(2500, self._restore_caption)
            applog.get("main").info("回报率不可写，仅展示挡位 %s", value)
            return
        self._apply_edit("rate", int(value))

    def _on_rate_picker_closed(self) -> None:
        picker = self.sender()
        if picker is not self._rate_picker:
            # 已经被换掉的那个面板：只清理自己，别把新面板的引用一起带走
            if picker is not None:
                picker.deleteLater()
            return
        self._rate_picker = None
        if picker is not None:
            picker.deleteLater()
        self._update_float_hover_poll()

    def _maybe_close_rate_picker(self, pos) -> None:
        """点在挡位面板以外的地方 → 收起面板（跟设备列表一个规矩）。"""
        picker = self._rate_picker
        if picker is None or not picker.isVisible():
            return
        point = QPoint(int(pos[0]), int(pos[1]))
        if picker.frameGeometry().contains(point):
            return
        # 打开面板的那一次点击，全局钩子会在 ~30ms 后**原样再送回来一次**——
        # 那时候面板是自己刚开的，不能自己把自己关了
        if time.time() - self._picker_opened_at < 0.4:
            return
        picker.hide_picker()

    # ---------------------------------------------------------- DPI 挡位管理弹窗
    def _show_levels_picker(self) -> None:
        """点灵敏度卡里的「挡位」按钮 → 弹出竖向挡位管理面板。

        和回报率挡位面板同一套架构：列出设备当前 DPI 挡位（降序）、当前激活
        档打绿勾；点某档切过去、点行尾「×」删（面板内已弹确认框）、底部
        「+ 增加挡位」入表。切 / 增 / 删都即时写回设备。
        """
        info = self.monitor.snapshot()
        caps = info.caps
        levels = sorted({int(v) for v in info.dpi_table if int(v) > 0}, reverse=True)
        # 表可增删（X7）→ 完整管理；表只读（G502 板载扇区不开放）但能切当前
        # DPI（0x2201 fn3 可写）→ 降级为「仅切换」面板；两样都不行才拒绝。
        if (not (info.connected and not info.sleeping)
                or not levels or not caps.dpi_editable):
            self._caption_hold = time.time() + 2.5
            self.caption.set_text("该设备不支持编辑挡位")
            QTimer.singleShot(2500, self._restore_caption)
            return
        current = int(info.dpi or 0)
        old = self._level_picker
        if old is not None and old.isVisible():
            old.hide_picker()          # 再点一次 = 收起
            return
        picker = W.LevelsPicker(levels, current, caps, self._pal, self,
                                table_writable=caps.levels_writable)
        picker.chosen.connect(self._on_level_chosen)
        picker.removed.connect(self._on_level_removed)
        picker.added.connect(self._on_level_added)
        picker.closed.connect(self._on_level_picker_closed)
        self._level_picker = picker
        # 摆在灵敏度卡片正上方（和回报率面板一个锚定规矩）
        anchor = self.card_dpi.mapToGlobal(
            QPoint(self.card_dpi.width() // 2, 0))
        picker.show_near(anchor, prefer_above=True)
        self._level_picker_opened_at = time.time()
        self._update_float_hover_poll()
        applog.get("main").info("DPI 挡位面板: %s（当前 %s）", levels, current)

    def _write_levels(self, cur_dpi: int | None = None) -> None:
        """把面板当前的挡位表整表写回（升序），当前档指向 cur_dpi 最近的档。"""
        picker = self._level_picker
        if picker is None:
            return
        levels = sorted(picker.levels())
        if not levels:
            return
        info = self.monitor.snapshot()
        target = cur_dpi if cur_dpi is not None else (info.dpi or levels[0])
        ok, msg = self.monitor.set_dpi_levels(levels, target)
        self._caption_hold = time.time() + 2.5
        if ok:
            self._last_sig = None
            self._sync()
            text = "已写入 DPI 挡位"
            if msg:
                text += f"（{msg}）"
        else:
            text = f"挡位写入失败：{msg or '设备未响应'}"
        self.caption.set_text(text)
        QTimer.singleShot(2500, self._restore_caption)
        applog.get("main").info("写回挡位 %s → %s", levels, "成功" if ok else "失败")

    def _on_level_chosen(self, value: int) -> None:
        """切到某档：表可写就整表写回；表只读（G502）就只切当前 DPI。"""
        caps = self.monitor.snapshot().caps
        if not caps.levels_writable:
            ok, msg = self.monitor.set_dpi(value)
            self._caption_hold = time.time() + 2.5
            if ok:
                self._last_sig = None
                self._sync()
                self.caption.set_text(f"已切到 {value} DPI")
            else:
                self.caption.set_text(f"切换失败：{msg or '设备未响应'}")
                pk = self._level_picker
                if pk is not None:      # 勾移回设备实际的当前档
                    pk.set_current(self.monitor.snapshot().dpi or 0)
            QTimer.singleShot(2500, self._restore_caption)
            applog.get("main").info("切换 DPI（表只读）→ %s：%s", value,
                                    "成功" if ok else msg)
            return
        self._write_levels(cur_dpi=value)

    def _on_level_removed(self, value: int) -> None:
        """删掉某档（面板内已确认）：写回剩余挡位表。"""
        applog.get("main").info("删除挡位 %s", value)
        self._write_levels()

    def _on_level_added(self, value: int) -> None:
        """新增某档：写回更新后的挡位表。"""
        applog.get("main").info("新增挡位 %s", value)
        self._write_levels()

    def _on_level_picker_closed(self) -> None:
        picker = self.sender()
        if picker is not self._level_picker:
            if picker is not None:
                picker.deleteLater()
            return
        self._level_picker = None
        if picker is not None:
            picker.deleteLater()
        self._update_float_hover_poll()

    def _maybe_close_level_picker(self, pos) -> None:
        """点在挡位面板以外的地方 → 收起面板（跟回报率面板一个规矩）。"""
        picker = self._level_picker
        if picker is None or not picker.isVisible():
            return
        point = QPoint(int(pos[0]), int(pos[1]))
        if picker.frameGeometry().contains(point):
            return
        if time.time() - self._level_picker_opened_at < 0.4:
            return
        picker.hide_picker()

    def _on_settings_closed(self) -> None:
        self.header.btn_settings.set_icon_active(False)
        self._update_float_hover_poll()

    # ------------------------------------------------------------------ 设备切换
    def _device_rows(self) -> list[dict]:
        """头部计数与设备弹窗共用的行数据（在线鼠标 + 在线配件）。"""
        acc = getattr(self, "accessory", None)
        try:
            acc_rows = acc.rows() if acc is not None else []
        except Exception:  # noqa: BLE001
            acc_rows = []      # 配件是附加信息，扫崩了也不该影响鼠标功能
        return device_menu_rows(self.monitor.devices(), acc_rows)

    def _show_device_menu(self) -> None:
        """点标题开／关设备列表：点一次展开，再点一次收起。"""
        menu = self._device_menu
        if menu is not None and menu.isVisible():
            menu.hide_menu()
            return
        # 键盘 / 耳机排在鼠标后面：只展示、不参与切换（行里 switchable=False）。
        # 跟头部「N台设备」用同一份行数据，数字一定等于弹窗行数。
        rows = self._device_rows()
        if not rows:
            return
        menu = W.DeviceMenu(rows, self._pal, self,
                            low=config.clamp_threshold(self.cfg.low_battery_threshold))
        menu.chosen.connect(self._select_device)
        menu.closed.connect(self._on_device_menu_closed)
        self._device_menu = menu
        self._menu_open = True
        # 贴着标题区底边往下铺一层（锚点是屏幕坐标），右边留 8px
        geo = self.header.geometry()
        anchor = self.mapToGlobal(QPoint(geo.left(), geo.bottom() - 2))
        max_right = float(self.mapToGlobal(QPoint(self.width() - 8, 0)).x())
        menu.show_below(QPoint(anchor.x(), anchor.y()), max_right)
        self._update_float_hover_poll()
        applog.get("main").info("设备列表: 打开 %d 项", len(rows))

    def _on_device_menu_closed(self) -> None:
        self._menu_open = False
        menu, self._device_menu = self._device_menu, None
        if menu is not None:
            menu.deleteLater()
        self.monitor.refresh()
        self._update_float_hover_poll()

    # ------------------------------------------------------------------ 浮窗悬停接管
    _IDC_HAND = 32649
    _IDC_ARROW = 32512

    def _update_float_hover_poll(self) -> None:
        """有浮窗（设置面板 / 设备菜单）开着才需要轮询接管头部悬停。"""
        panel_open = self.settings is not None and self.settings.is_open()
        menu_open = self._device_menu is not None and self._device_menu.isVisible()
        if panel_open or menu_open:
            self._float_hover_timer.start()
            self._poll_float_hover()
        else:
            self._float_hover_timer.stop()
            self._clear_float_hover()

    def _poll_float_hover(self) -> None:
        """轮询光标位置，手动驱动头部按钮的悬停高亮和手型光标。

        浮窗重叠期间主窗口收不到 Enter/Leave/mouseMove/WM_SETCURSOR，
        按钮的 _hover 动画和光标形状都不会发生；这里按真实光标位置补齐。
        光标形状直接走 Win32 SetCursor（Qt 的 override cursor 同样依赖
        被吞掉的消息链路，不可靠）。
        """
        if self._quitting or not self.isVisible():
            self._clear_float_hover()
            return
        pos = QCursor.pos()
        header = self.header
        # 只在光标位于主窗口、且不在浮窗上时接管（浮窗自己管自己的光标）
        in_main = self.rect().contains(self.mapFromGlobal(pos))
        panel = self.settings
        over_panel = (panel is not None and panel.isVisible()
                      and panel.rect().contains(panel.mapFromGlobal(pos)))
        menu = self._device_menu
        over_menu = menu is not None and menu.isVisible() and \
            menu.frameGeometry().contains(pos)
        picker = self._rate_picker
        over_picker = picker is not None and picker.isVisible() and \
            picker.frameGeometry().contains(pos)
        lpicker = self._level_picker
        over_lpicker = lpicker is not None and lpicker.isVisible() and \
            lpicker.frameGeometry().contains(pos)
        manage = (in_main and not over_panel and not over_menu
                  and not over_picker and not over_lpicker)

        over_any = False
        if over_panel:
            # 浮层自己判定哪里可点：行内手型、分段控件、开关都算
            over_any = panel.wants_hand(pos) if panel is not None else False
        elif over_menu:
            over_any = menu.wants_hand(pos) if menu is not None else False
        elif manage:
            for btn in (header.btn_settings, header.btn_close):
                hit = btn.isVisible() and btn.rect().contains(btn.mapFromGlobal(pos))
                over_any = over_any or hit
                target = 1.0 if hit else 0.0
                # 只在目标变化时起动画，避免 30ms 一次反复重启
                if getattr(btn, "_float_target", None) != target:
                    btn._float_target = target
                    btn._animate_to("_hover", target, 120)
            if header._title_clickable() and header.title_hit((pos.x(), pos.y())):
                over_any = True

        if manage or over_panel or over_menu:
            user32 = ctypes.windll.user32
            if self._float_hand_hcursor is None:
                self._float_hand_hcursor = user32.LoadCursorW(None, self._IDC_HAND)
                self._float_arrow_hcursor = user32.LoadCursorW(None, self._IDC_ARROW)
            user32.SetCursor(self._float_hand_hcursor if over_any
                             else self._float_arrow_hcursor)
        elif self._float_hand_hcursor is not None:
            # 离开主窗口（或进入别的浮窗）：不再强制，把光标还成箭头，
            # 后续形状交给目标窗口自己的 WM_SETCURSOR
            user32 = ctypes.windll.user32
            if self._float_arrow_hcursor is None:
                self._float_arrow_hcursor = user32.LoadCursorW(None, self._IDC_ARROW)
            user32.SetCursor(self._float_arrow_hcursor)

    def _clear_float_hover(self) -> None:
        """浮窗全关：把按钮悬停态清零，光标交回正常消息链路。"""
        for btn in (self.header.btn_settings, self.header.btn_close):
            btn._float_target = None
            if btn._hover > 0.001:
                btn._animate_to("_hover", 0.0, 120)
        if self._float_hand_hcursor is not None:
            ctypes.windll.user32.SetCursor(self._float_arrow_hcursor)

    def _maybe_close_device_menu(self, pos) -> None:
        """点在菜单和标题以外的地方 → 收起设备列表。

        标题那一下不在这里处理：标题自己是开关，先关再让它重开就会闪一下。
        """
        menu = self._device_menu
        if menu is None or not menu.isVisible():
            return
        point = QPoint(int(pos[0]), int(pos[1]))
        if menu.frameGeometry().contains(point):
            return
        if self.header.title_hit(pos):
            return
        menu.hide_menu()

    def _select_device(self, key: str) -> None:
        if key == self.monitor.selected:
            return
        self.monitor.select(key)
        self.cfg.device = key
        config.save(self.cfg)
        self._last_sig = None          # 强制下一帧整块重画
        self.monitor.refresh()
        QTimer.singleShot(80, self._sync)

    def _retranslate_tray(self) -> None:
        """切语言后重设托盘菜单：QAction 的文字是存的，不会自己变。"""
        self.tray.setToolTip(i18n.T("三颗豆"))
        self._act_show.setText(i18n.T("显示窗口"))
        self._act_refresh.setText(i18n.T("立即刷新"))
        self._act_hub.setText(i18n.T(driver.button_text(driver.WEB)))
        self._act_auto.setText(i18n.T("开机自动启动"))
        self._act_settings.setText(i18n.T("设置…"))
        self._act_quit.setText(i18n.T("退出"))

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip(i18n.T("三颗豆"))

        menu = QMenu()
        self._act_show = QAction(i18n.T("显示窗口"), menu)
        self._act_show.triggered.connect(self.show_window)
        self._act_refresh = QAction(i18n.T("立即刷新"), menu)
        self._act_refresh.triggered.connect(self._on_refresh)
        self._act_hub = QAction(i18n.T(driver.button_text(driver.WEB)), menu)
        self._act_hub.triggered.connect(self._open_driver)
        self._act_auto = QAction(i18n.T("开机自动启动"), menu)
        self._act_auto.setCheckable(True)
        self._act_auto.setChecked(autostart.is_enabled())
        self._act_auto.triggered.connect(self._set_autostart)
        self._act_settings = QAction(i18n.T("设置…"), menu)
        self._act_settings.triggered.connect(self._toggle_settings)
        self._act_quit = QAction(i18n.T("退出"), menu)
        self._act_quit.triggered.connect(self.quit_app)

        menu.addAction(self._act_show)
        menu.addAction(self._act_refresh)
        menu.addAction(self._act_hub)
        menu.addSeparator()
        menu.addAction(self._act_auto)
        menu.addAction(self._act_settings)
        menu.addSeparator()
        menu.addAction(self._act_quit)

        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        notify.bind_tray(self.tray)
        notify.register_app_identity()

    # ------------------------------------------------------------------ 主题
    def _resolve_palette(self) -> theme.Palette:
        if self.cfg.follow_system:
            return theme.palette_for(theme.system_is_dark())
        return theme.palette_for(bool(self.cfg.dark_mode))

    def _check_theme(self) -> None:
        pal = self._resolve_palette()
        if pal.name != self._pal.name:
            self._apply_theme(pal)

    def _apply_theme(self, pal: theme.Palette, first: bool = False) -> None:
        self._pal = pal
        self.header.set_palette_theme(pal)
        self.ring.set_palette_theme(pal)
        for card in self.cards:
            card.set_palette_theme(pal)
        self.btn_primary.set_palette_theme(pal)
        self.btn_refresh.set_palette_theme(pal)
        self.caption.set_palette_theme(pal)
        self._style_hints()
        self.settings.set_palette_theme(pal)
        for child in self.findChildren(QWidget):
            if hasattr(child, "_glass_cache"):
                child._glass_cache = None
        self._backdrop = None
        self._rebuild_backdrop()
        if pal.name == "dark":
            # 深色不画辉光，动画定时器没活干（构造期还没有这个定时器）
            timer = getattr(self, "_bg_timer", None)
            if timer is not None:
                timer.stop()
        if self.settings.isVisible():  # 浮层开着时，折射层要跟着换，否则会残留旧主题的颜色
            self.settings.refresh_backdrop()
        self.update()
        if not first and self.isVisible():
            wineffects.apply_window_chrome(int(self.winId()), pal.name == "dark")

    # ------------------------------------------------------------------ 绘制
    def _rebuild_backdrop(self) -> None:
        """静态背景（玻璃主体 + 描边）缓存成位图，避免每帧重绘。"""
        pal = self._pal
        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)

        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = CARD_RADIUS
        body_path = W.rounded(body, radius)

        # 上下打光：整窗底色只沿 y 变化，左右同色（斜着走会让左上比右下亮）
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0.0, pal.surface_window_top)
        grad.setColorAt(1.0, pal.surface_window_bottom)
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawPath(body_path)

        # 窗框描边同样走竖向：顶边亮、底边压暗，左右两侧对称
        border = QLinearGradient(body.topLeft(), body.bottomLeft())
        if pal.name == "dark":
            border.setColorAt(0.0, QColor(255, 255, 255, 62))
            border.setColorAt(0.45, QColor(255, 255, 255, 16))
            border.setColorAt(1.0, QColor(255, 255, 255, 30))
        else:
            border.setColorAt(0.0, QColor(255, 255, 255, 220))
            border.setColorAt(1.0, QColor(0, 0, 0, 20))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(border), 1.0))
        p.drawPath(body_path)
        p.end()
        self._backdrop = pix
        self._glow_a_pm = self._make_glow(pal.glow_primary)
        self._glow_b_pm = self._make_glow(pal.glow_secondary)

    @staticmethod
    def _make_glow(color: QColor) -> QPixmap:
        """把径向渐变预渲染成小尺寸贴图，运行时只做缩放贴图，省 CPU。"""
        size = 128
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        g = QPainter(pm)
        g.setRenderHint(QPainter.Antialiasing)
        grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        grad.setColorAt(0.0, color)
        grad.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(),
                                     int(color.alpha() * 0.45)))
        grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        g.setPen(Qt.NoPen)
        g.setBrush(grad)
        g.drawRect(0, 0, size, size)
        g.end()
        return pm

    def _tick_bg(self) -> None:
        self._phase = (self._phase + 0.008) % 6.283
        if self.isVisible():
            self.update()

    def _set_animating(self, on: bool) -> None:
        if on == self._animating:
            return
        self._animating = on
        # 深色没有背景辉光可动，不用为此每 70ms 重绘一次
        if on and self._pal.name != "dark":
            self._bg_timer.start()
        else:
            self._bg_timer.stop()
        self.header.set_animating(on)

    def refresh_cursor(self) -> None:
        """让窗口按当前光标位置重新解析一次鼠标形状。

        Qt 只在窗口收到 WM_SETCURSOR 时才下发 SetCursor；设置浮层（Qt.Popup）
        收起后主窗口收不到这个时机，光标会停在最后一次的形状上 —— 手型黏在
        整块 HUD 上、鼠标从别的窗口移进来也还是手指，就是这个原因。
        """
        wineffects.refresh_cursor(int(self.winId()))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._set_animating(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self.isActiveWindow():
            self._set_animating(False)
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange:
            self._set_animating(self.isActiveWindow() or self.underMouse())

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._backdrop is None:
            self._rebuild_backdrop()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._backdrop)

        # 深色不做背景打光：那两团漂移的彩光正是「顶部被灯照着」的来源，
        # 用户要求去掉，整段不画（浅色保留液态玻璃的氛围）。
        if self._pal.name == "dark":
            return

        body = QRectF(0, 0, self.width(), self.height())
        body_path = W.rounded(body, CARD_RADIUS)

        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.save()
        p.setClipPath(body_path)

        # 上下打光：两团彩光改成「顶部一团 + 底部一团」，都落在水平中线上，
        # 只做极小的左右漂移。原来是左上（0.22, 0.12）+ 右下（0.85, 0.82），
        # 摆成对角线，看上去就是从左上打了一束光。
        ax = body.left() + body.width() * (0.50 + 0.02 * math.sin(self._phase))
        ay = body.top() + body.height() * (0.06 + 0.02 * math.cos(self._phase * 0.8))
        ra = body.width() * 0.72
        p.drawPixmap(QRectF(ax - ra, ay - ra, ra * 2, ra * 2), self._glow_a_pm,
                     QRectF(self._glow_a_pm.rect()))

        bx = body.left() + body.width() * (0.50 - 0.02 * math.cos(self._phase * 0.7))
        by = body.top() + body.height() * (0.94 - 0.02 * math.sin(self._phase * 1.1))
        rb = body.width() * 0.62
        p.drawPixmap(QRectF(bx - rb, by - rb, rb * 2, rb * 2), self._glow_b_pm,
                     QRectF(self._glow_b_pm.rect()))
        p.restore()

    # ------------------------------------------------------------------ 数据同步
    def _sync(self) -> None:
        try:
            self.header.set_devices(self._device_rows())
        except Exception:  # noqa: BLE001
            pass
        info = self.monitor.snapshot()
        sig = (info.connected, info.sleeping, info.battery, info.charging, info.dpi,
               info.report_rate, info.name, info.dpi_level, info.stale, info.message,
               info.firmware, info.is_wireless, info.extra.get("device_key"),
               info.caps.source, info.caps.dpi_editable, info.caps.rate_editable,
               info.caps.levels_writable, tuple(sorted(info.dpi_table)))
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self._update_edit_caps(info)

        # 品牌只在型号行前面拼一段，没品牌（还没扫到任何设备）就不拼，
        # 别再硬写 "CROCIRIS" 冒充——第一行已经固定写着应用名了。
        # 狼途这类品牌名是中文（LANGTU 只是英文界面下的写法），按当前语言翻一次
        brand = i18n.T(info.brand) if info.brand else ""
        if info.connected:
            if info.sleeping:
                status = "休眠中"
            elif info.stale:
                status = "信号弱"
            else:
                status = "充电中" if info.charging else "已连接"
            model = info.display_model
        else:
            status = "未连接"
            model = i18n.T("等待设备")

        # 纯有线：没有电池可说 —— 电环不写「已充满」，低电量那两行设置也置灰
        wired = bool(info.connected and not info.is_wireless)
        self.header.set_state(brand, model, status, info.connected)
        self.header.set_devices(self._device_rows())
        self.ring.set_state(info.battery, info.charging, info.connected, info.sleeping,
                            wired=wired)
        self.settings.set_battery_available(info.connected and not wired)

        if info.connected:
            # 这台设备有没有「读数值」的通道：狼途这类只有标准鼠标集合，
            # 问不到 DPI / 回报率，写「读取中」会让人一直等下去；
            # 副标题写「无法读取」，右上角角标再写「按键调节」——一句说清
            # 「软件读不到」，一句说「去哪改」，别两句都写同一件事
            no_channel = info.caps.source == "hw"
            wait_note = i18n.T("无法读取" if no_channel
                              else ("等待唤醒" if info.sleeping else "读取中"))
            if info.dpi:
                # 档位未知（G HUB 软件配置读不到、板载也对不上）时显示「实时读取」，
                # 绝不拿传感器能力范围（511 档）冒充用户配置的档位数
                if info.dpi_levels:
                    sub = (i18n.stage(info.dpi_level, info.dpi_levels)
                           if info.dpi_level else i18n.stages_total(info.dpi_levels))
                else:
                    sub = i18n.T("实时读取")
                self.card_dpi.set_content(f"{info.dpi}", "DPI", sub)
            else:
                self.card_dpi.set_content("—", "", wait_note)
            if info.report_rate:
                self.card_rate.set_content(
                    f"{info.report_rate}", "Hz", P.rate_latency(info.report_rate),
                )
            else:
                self.card_rate.set_content("—", "", wait_note)
            if info.is_wireless:
                receiver = info.extra.get("receiver", "") or ""
                if "8K" in receiver.upper():
                    sub = i18n.T("8K 接收器")
                elif "RECEIVER" in receiver.upper():
                    sub = i18n.T("接收器")
                else:
                    sub = i18n.T("无线连接")
                self.card_conn.set_content("2.4G", "", sub)
            else:
                self.card_conn.set_content("USB", "", i18n.T("有线连接"))
            self._apply_driver(info)
            bits = []
            if info.firmware:
                bits.append(f"{i18n.T('固件 ')}{info.firmware}")
            if info.serial:
                bits.append(f"SN {info.serial[-6:]}")
            # 没有固件 / 序列号的设备（狼途这类）给 VID:PID：用户报问题时
            # 唯一能对上的标识，也顺便说明「我们确实认出它了」
            if no_channel and info.vid:
                bits.insert(0, f"VID:PID {info.vid:04X}:{info.pid:04X}")
            bits.append(i18n.T("每 2 秒自动刷新"))
            if info.sleeping:
                bits.append(i18n.T("移动鼠标唤醒"))
            if time.time() >= self._caption_hold:
                self.caption.set_text(" · ".join(bits))
        else:
            for card in self.cards:
                card.set_content("—", "", "")
            self._show_rescan_button()
            if time.time() >= self._caption_hold:
                self.caption.set_text(
                    i18n.T(info.message or "未检测到兼容的 CROCIRIS 鼠标"))

        # 托盘提示
        if info.connected and not info.is_wireless:
            # 有线机型没有电量可报，写「100%」反而让人以为是块满电的电池
            self.tray.setToolTip(f"{info.display_model}{i18n.T(' · USB 有线')}")
        elif info.connected and info.battery is not None:
            suffix = (i18n.T(" · 休眠") if info.sleeping
                      else (i18n.T(" · 充电中") if info.charging else ""))
            self.tray.setToolTip(f"{info.display_model} · {info.battery}%{suffix}")
        else:
            self.tray.setToolTip(i18n.T("三颗豆 · 未连接"))

    # ------------------------------------------------------------------ 交互
    def _on_primary(self) -> None:
        if self.monitor.snapshot().connected:
            self._open_driver()
        else:
            self._on_refresh()

    def _show_rescan_button(self) -> None:
        """设备不在时，主按钮退回「重新扫描」。

        必须顺手把 `_driver_kind` 清空：`_apply_driver()` 在「驱动形态没变」时
        会直接 return，不清缓存的话，下一次连上设备按钮会一直卡在「重新扫描」，
        点下去却又是打开网页驱动 —— 文案和实际动作对不上（v1.4.9 修的）。
        图标 / 提示语一起换回来，别留上一个状态的残影。
        """
        self._driver_kind = ""
        # 上一台设备可能是「无官方软件」而被禁用的，切回来必须重新放开
        self.btn_primary.set_enabled_state(True)
        self.btn_primary.set_text(i18n.T("重新扫描"))
        self.btn_primary.set_icon("refresh")
        self.btn_primary.setToolTip(i18n.T("重新扫描设备"))

    def _apply_driver(self, info) -> None:
        """按钮文案 / 图标按驱动形态切换：网页驱动 / 客户端软件 / 无官方软件。"""
        kind = driver.kind_of(info)
        if kind == self._driver_kind:
            return
        self._driver_kind = kind
        text = i18n.T(driver.button_text(kind))
        self.btn_primary.set_text(text)
        self.btn_primary.set_icon(driver.button_icon(kind))
        # 没有官方软件的设备（狼途这类）：按钮留着占位，但禁用——点了没反应
        # 比「点开一个不存在的软件」好。切回别的设备时这里会把状态还原。
        self.btn_primary.set_enabled_state(kind != driver.NONE)
        if kind == driver.NONE:
            self.btn_primary.setToolTip(
                i18n.T("这台鼠标没有官方软件，DPI 和回报率用鼠标上的按键调节"))
        elif kind == driver.SOFTWARE:
            self.btn_primary.setToolTip(
                i18n.T("打开罗技 G HUB") if driver.find_ghub()
                else i18n.T("本机未装 G HUB，点击去官网下载"))
        else:
            self.btn_primary.setToolTip(i18n.T("在浏览器中打开网页驱动"))
        if getattr(self, "_act_hub", None) is not None:
            self._act_hub.setText(text)
            self._act_hub.setEnabled(kind != driver.NONE)

    def _open_driver(self) -> None:
        """西伯利亚 → 浏览器打开网页驱动；罗技 → 拉起 G HUB（没装则开官网）。"""
        info = self.monitor.snapshot()
        if driver.kind_of(info) == driver.NONE:
            return          # 没有官方软件，别打开一个空地址
        opened_local, target = driver.open_driver(info, self.cfg.hub_url)
        if opened_local:
            # 给一句「已经打开了」的反馈，2.5 秒后回到常规说明；
            # _sync 在数据没变时会提前 return，所以这里得主动放开签名
            self._caption_hold = time.time() + 2.5
            self.caption.set_text(i18n.T("已打开罗技 G HUB"))
            QTimer.singleShot(2500, self._restore_caption)
        applog.get("main").info("打开驱动: %s", target)

    def _restore_caption(self) -> None:
        self._caption_hold = 0.0
        self._last_sig = None
        self._sync()

    def _on_refresh(self) -> None:
        self._last_sig = None
        self.monitor.refresh()
        self._sync()

    def _apply_pin_flag(self) -> None:
        # 不用 setWindowFlags：它会重建原生窗口，设置浮层会被一起收掉。
        # 这里只改主窗口自己的 z-order。
        #
        # 关键：设置浮层是主窗口的 owned window（见 SettingsPanel._attach_owner），
        # Windows 保证 owned window 永远显示在 owner 之上，并随 owner 自动
        # 切换置顶状态。所以这里**完全不需要**再对面板做任何 z-order 操作 ——
        # 那样反而会打断 owned 关系带来的原子性，出现「闪一下消失再出现」。
        wineffects.set_topmost(int(self.winId()), self.cfg.always_on_top)

    def _place_settings(self) -> None:
        """把面板摆到齿轮正下方、贴着窗口右缘。"""
        anchor = self.header.btn_settings
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        x = self.x() + self.width() - self.settings.width() - 10
        self.settings.move(x, pos.y() + 6)

    def _toggle_settings(self) -> None:
        """齿轮 / 托盘菜单：面板关着就打开，开着就收起（真正的开关）。"""
        panel = self.settings
        if panel.is_open():
            panel.gear_hidden = True
            panel.hide_animated()
            self._update_float_hover_poll()
            return
        panel.gear_hidden = False
        panel.sync()
        self._place_settings()
        panel.prepare_backdrop()
        panel.show_animated()
        self.header.btn_settings.set_icon_active(True)
        self._update_float_hover_poll()

    def _on_setting(self, key: str, value: bool) -> None:
        if key == "autostart":
            self._set_autostart(value)
        elif key == "pin":
            self.cfg.always_on_top = value
            self._apply_pin_flag()
            config.save(self.cfg)
        elif key == "lowbat":
            self.cfg.low_battery_notify = value
            config.save(self.cfg)
            if value:
                # 刚打开开关：把状态清干净，下一次电量低于阈值就立刻提醒
                self._low_armed = True
                self._low_level = None
                self._low_notified = 0.0

    def _on_low_threshold(self, level: int) -> None:
        """设置页改了阈值：电量颜色分界、图例、环、通知一起跟着换。"""
        self.cfg.low_battery_threshold = config.clamp_threshold(level)
        config.save(self.cfg)
        self.ring.set_low_threshold(self.cfg.low_battery_threshold)
        self._low_armed = True
        self._low_level = None
        self._low_notified = 0.0
        # 图例里的 ≤xx% / xx–yy% 文案是面板自己画的，得让它重画一次
        self.settings.update()

    def _on_language(self, code: str) -> None:
        """切换界面语言：立刻整套换掉，不用重启。

        文案分两类，处理方式不一样：
        * **画出来的**（第一行状态、卡片标题/副标题、设置行、电量环说明）——
          paintEvent 里现取 `i18n.T()`，`update()` 一下就换；
        * **存在控件里的**（QToolButton / QAction / 分段控件文字）—— 文字是
          设进去的，必须显式重设，所以这里逐个 retranslate。
        """
        if code == i18n.lang():
            return
        i18n.set_lang(code)
        self.cfg.language = i18n.lang()
        config.save(self.cfg)

        self.setWindowTitle(i18n.T("三颗豆"))
        self.card_dpi.set_title(i18n.T("灵敏度"))
        self.card_rate.set_title(i18n.T("回报率"))
        self.card_conn.set_title(i18n.T("连接"))
        for card in self.cards:
            card.retranslate()
        self.header.set_close_to_tray(self.cfg.close_to_tray)   # × 的提示跟着语言重设
        self._retranslate_tray()
        self.settings.sync()
        self.settings.update()
        self.header.update()
        # 主按钮 / 说明文字 / 托盘提示都在 _sync 里按语言生成的，
        # 清掉签名再跑一轮，等于用新语言重排整页
        self._driver_kind = ""
        self._last_sig = None
        self._sync()

    def _on_theme_mode(self, mode: int) -> None:
        """0 = 系统，1 = 浅色，2 = 深色。"""
        if mode == 0:
            self.cfg.follow_system = True
        elif mode == 1:
            self.cfg.follow_system = False
            self.cfg.dark_mode = False
        else:
            self.cfg.follow_system = False
            self.cfg.dark_mode = True
        config.save(self.cfg)
        self._apply_theme(self._resolve_palette())

    def _set_autostart(self, on: bool) -> None:
        ok = autostart.set_enabled(on)
        real = autostart.is_enabled()
        if not ok or real != on:
            self.settings.set_value("autostart", real)
        self._act_auto.setChecked(real)

    def _primary_screen(self):
        """主屏（Windows 显示设置里的「主显示器」）。

        千万不能用 self.screen()：那是「窗口当前所在的屏幕」。窗口一旦被
        系统放到副屏上（比如启动时光标在副屏那边），self.screen() 就变成
        副屏，贴边逻辑会把它挪到*副屏*的右下角，接着 3 秒一次的定时器
        又把它重新钉在副屏 —— 越修越偏，再也回不到主屏。只认
        primaryScreen() 才能保证位置唯一。
        """
        screen = QApplication.primaryScreen()
        if screen is not None:
            return screen
        screens = QApplication.screens()
        return screens[0] if screens else None

    def _watch_screens(self) -> None:
        """监听显示器增删 / 主屏切换 / 分辨率变化，重新贴边。

        这是「打开后也会动一下」的唯一例外：这类系统环境变化发生后，原位置
        可能已经不存在（副屏被拔掉、分辨率改小），窗口留在那儿会落到屏幕外。
        除此之外运行期不会有任何自动重定位。
        """
        app = QApplication.instance()
        if app is None:
            return
        for signal in ("screenAdded", "screenRemoved", "primaryScreenChanged"):
            sig = getattr(app, signal, None)
            if sig is not None:
                try:
                    sig.connect(self._dock_bottom_right)
                except (RuntimeError, TypeError):
                    pass
        for screen in QApplication.screens():
            for signal in ("geometryChanged", "availableGeometryChanged",
                           "logicalDotsPerInchChanged"):
                sig = getattr(screen, signal, None)
                if sig is not None:
                    try:
                        sig.connect(self._dock_bottom_right)
                    except (RuntimeError, TypeError):
                        pass

    def _primary_scale(self) -> tuple[float, float]:
        """主屏的「逻辑 / 物理」比例，也就是 Windows 的缩放比（100% → 1.0）。"""
        screen = self._primary_screen()
        phys = wineffects.primary_monitor_rect()
        if screen is None or phys is None:
            return (1.0, 1.0)
        geo = screen.geometry()
        pw = phys[2] - phys[0]
        ph = phys[3] - phys[1]
        if pw <= 0 or ph <= 0:
            return (1.0, 1.0)
        sx = geo.width() / pw
        sy = geo.height() / ph
        return (sx if sx > 0 else 1.0, sy if sy > 0 else 1.0)

    def _phys_to_qt(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
        """物理像素矩形 → Qt 逻辑坐标。

        Win32 给的永远是物理像素；Qt 用的是逻辑像素。两屏缩放比不同时直接
        拿物理值去 move() 会偏，所以按主屏的「逻辑/物理」比例换一次。
        """
        screen = self._primary_screen()
        if screen is None:
            return None
        phys = wineffects.primary_monitor_rect()
        if phys is None:
            return None
        geo = screen.geometry()
        sx, sy = self._primary_scale()
        return (int(round(geo.x() + (rect[0] - phys[0]) * sx)),
                int(round(geo.y() + (rect[1] - phys[1]) * sy)),
                int(round(geo.x() + (rect[2] - phys[0]) * sx)),
                int(round(geo.y() + (rect[3] - phys[1]) * sy)))

    def _flyout_in_qt(self) -> tuple[int, int, int, int] | None:
        """托盘弹窗的位置（Qt 逻辑坐标）。没弹出来时返回 None。"""
        rect = wineffects.tray_flyout_rect()
        return None if rect is None else self._phys_to_qt(rect)

    def _tray_area_in_qt(self) -> tuple[int, int, int, int] | None:
        """主屏任务栏上图托盘标区的位置（Qt 逻辑坐标）。"""
        rect = wineffects.taskbar_tray_rect()
        return None if rect is None else self._phys_to_qt(rect)

    def _dock_bottom_right(self) -> None:
        """把窗口放到主屏右下角（**只在打开窗口时调用**）。

        横向恒定：右边框距屏幕右边缘 160px，任何时候都不变。

        纵向：打开的那一刻如果托盘小窗正好弹着，下边框取「距小窗顶部 12px」，
        免得一打开就被那个最顶层的小窗盖住下半截；否则距任务栏顶部 12px。

        打开之后窗口不再自己移动 —— 没有后台定时器，弹窗弹出/收起也不跟随。
        用户看到的位置就是打开时的位置，不会莫名其妙跳一下。

        **边距按物理像素算**：Qt 的 move() 只认逻辑像素，100% 缩放下 1 逻辑
        = 1 物理，150% 缩放下 1 逻辑 = 1.5 物理。直接把 160 当逻辑像素用，
        2K 屏（常见 125%/150%）上量出来就是 200/240 物理像素，跟 1080p 屏
        对不上。这里先把 160 / 12 折算成逻辑像素再定位，保证任何分辨率、任何
        缩放下，离屏幕右边和任务栏顶部的**实际像素距离**都是 160 / 12。

        Qt 矩形的语义也要注意：QRect.right()/bottom() 是最后一个像素的**下标**，
        矩形的连续右边缘是 right()+1、连续下边缘是 bottom()+1。差一个像素在
        贴边时很明显。
        """
        screen = self._primary_screen()
        if screen is None:
            return

        # 窗口现在挂在别的屏幕上时，先把原生窗口的归属屏幕改掉。
        # 只 move() 是不够的：跨屏 move 时两屏缩放比不同时，Qt 会按旧
        # 屏幕的 DPR 换算坐标，窗口会偏到屏幕外面去。setScreen() 会让
        # Qt 重建原生窗口并按目标屏的 DPR 重新定位。
        handle = self.windowHandle()
        if handle is not None and handle.screen() is not screen:
            try:
                handle.setScreen(screen)
            except (RuntimeError, TypeError):
                pass

        geo = screen.availableGeometry()
        full = screen.geometry()

        scale_x, scale_y = self._primary_scale()
        gap_right = logical_gap(DOCK_RIGHT_GAP, scale_x)
        gap_bottom = logical_gap(DOCK_BOTTOM_GAP, scale_y)

        # 右边框：屏幕右边缘往左 160（物理）px（恒定，不随托盘弹窗左右摆动）
        x = full.right() + 1 - gap_right - self.width()

        flyout = self._flyout_in_qt()
        if flyout is not None:
            # 下边框：弹窗顶部往上 12（物理）px
            y = flyout[1] - gap_bottom - self.height()
        else:
            # 下边框：任务栏顶部外边缘往上 12（物理）px
            y = geo.bottom() + 1 - gap_bottom - self.height()

        # 别跑出屏幕（弹窗很靠左、或屏幕特别小的时候）
        x = max(full.left() + 8, min(x, full.right() + 1 - self.width() - 8))
        y = max(full.top() + 8, y)
        if self.pos() != QPoint(x, y):
            self.move(x, y)

    def hideEvent(self, event) -> None:  # noqa: N802
        # 主窗口都收起来了，设备列表浮窗没理由还飘在屏幕上
        if self._device_menu is not None:
            self._device_menu.hide_menu()
        if self._rate_picker is not None:
            self._rate_picker.hide_picker()
        if self._level_picker is not None:
            self._level_picker.hide_picker()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        wineffects.apply_window_chrome(int(self.winId()), self._pal.name == "dark")
        self._dock_bottom_right()

    # ------------------------------------------------------------------ 低电量提醒
    def _check_low_battery(self) -> None:
        """低于用户设的阈值就弹一次系统通知。

        开关关掉时什么都不做，但会把「已提醒」状态清掉 —— 重新打开开关后
        应该能再提醒一次，而不是记着上次已经报过了。
        """
        if not self.cfg.low_battery_notify:
            self._low_armed = True
            self._low_level = None
            return

        low = config.clamp_threshold(self.cfg.low_battery_threshold)
        info = self.monitor.snapshot()
        # 纯有线鼠标电量恒为 100%，提醒没有意义，也免得以后有人给有线机型
        # 伪造低电量读数时误弹通知
        if info.connected and not info.is_wireless:
            self._low_armed = True
            self._low_level = None
            return
        if not info.connected or info.battery is None or info.charging or info.sleeping:
            if info.charging or (info.connected and info.battery is not None
                                 and info.battery > low + 5):
                self._low_armed = True
                self._low_level = None
            return
        level = info.battery
        if level > low + 5:
            self._low_armed = True
            self._low_level = None
            return
        if level > low:
            return

        now = time.monotonic()
        escalated = self._low_level is None or level < self._low_level - 3
        retry = now - self._low_notified > 1800
        if not (escalated or retry or self._low_armed):
            return
        self._low_armed = False
        self._low_level = level
        self._low_notified = now
        notify.low_battery(info.display_model, level, low)

    # ------------------------------------------------------------------ 显示 / 托盘
    def show_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        # 先贴好边再显示：Windows 默认把新窗口放在鼠标光标所在的那块屏幕上，
        # 光标在副屏时窗口会先在副屏闪一下再被拉回来。提前定位就不会有这一帧。
        self._dock_bottom_right()
        self.show()
        self.raise_()
        self.activateWindow()
        self._apply_pin_flag()
        self._set_animating(True)

    def _hide_to_tray(self) -> None:
        if not self.cfg.close_to_tray:
            self.quit_app()
            return
        if self._device_menu is not None:
            self._device_menu.hide_menu()
        if self._rate_picker is not None:
            self._rate_picker.hide_picker()
        if self._level_picker is not None:
            self._level_picker.hide_picker()
        self.settings.hide_now()
        self.hide()

    # ------------------------------------------------------------------ 外部点击收起
    def _on_global_mouse_down(self, x: int, y: int) -> None:
        """低级鼠标钩子回调：只记下坐标，别的什么都不做。

        钩子是在事件循环的 GetMessage 里被系统回调的，这里碰 UI 有风险，
        真正的处理放到 _check_hook_press（定时器）里做。
        """
        self._hook_press = (x, y)

    def _check_hook_press(self) -> None:
        """消费钩子记下的按下坐标。"""
        pos = self._hook_press
        if pos is None:
            return
        self._hook_press = None
        self._on_press_outside(pos)

    def _on_press_outside(self, pos) -> None:
        """全局钩子送来的按下坐标：浮窗关闭 + 头部点击转发 + 稍后收到托盘。

        浮窗（设置面板 / 设备菜单）与主窗口重叠期间，主窗口收不到原生
        鼠标事件，用户点头部按钮时这次点击会被系统吞掉——所以这里承担
        「点击转发」：落在哪个头部控件上，就替它执行对应的动作。
        """
        if self._quitting or not self.isVisible():
            return
        menu_was_open = self._menu_open
        self._maybe_close_device_menu(pos)
        self._maybe_close_rate_picker(pos)
        self._maybe_close_level_picker(pos)
        point = QPoint(int(pos[0]), int(pos[1]))
        panel = self.settings
        panel_open = panel is not None and panel.is_open()
        header = self.header
        if panel_open:
            # × ：收面板 + 收纳到托盘（等收起动画播完再动主窗口）
            if header.btn_close.isVisible() and header.btn_close.rect().contains(
                    header.btn_close.mapFromGlobal(point)):
                panel.hide_animated()
                QTimer.singleShot(180, self._hide_to_tray)
                return
            # 齿轮：就是开关本身
            if header.btn_settings.isVisible() and header.btn_settings.rect().contains(
                    header.btn_settings.mapFromGlobal(point)):
                panel.gear_hidden = True
                panel.hide_animated()
                self._update_float_hover_poll()
                return
            # 标题区：开设备菜单（菜单本来开着的话上面已经关了，别再重开）
            if (not menu_was_open) and header._title_clickable() and header.title_hit(pos):
                panel.hide_animated()
                QTimer.singleShot(0, self._show_device_menu)
                return
            # 其余面板外区域：只收面板
            if not panel.rect().contains(panel.mapFromGlobal(point)):
                panel.hide_animated()
                self._update_float_hover_poll()
        if self._menu_open:
            return
        if self._tray_menu is not None and self._tray_menu.isVisible():
            return
        if self._pos_in_own_ui(pos):
            return
        if not self._outside_timer.isActive():
            # 已经在倒计时就别再推迟，否则连点会一直拖住不收起
            self._outside_timer.start()

    def _pos_in_own_ui(self, pos) -> bool:
        """屏幕坐标是否落在自己的界面上（主窗口 / 设置浮层 / 托盘弹窗）。

        只有自己画出来的界面才算「自己人」。任务栏的图标区（包括弹出托盘
        的小箭头）**不算**：点那里就是一次外部点击，HUD 要收回到托盘——
        用完托盘顺手把它收走，不用再点别处。
        """
        point = QPoint(int(pos[0]), int(pos[1]))
        if self.rect().contains(self.mapFromGlobal(point)):
            return True
        panel = self.settings
        if panel is not None and panel.isVisible():
            if panel.rect().contains(panel.mapFromGlobal(point)):
                return True
        # 设备切换浮窗（现在是顶层窗口，不挂在主窗口矩形里）
        device_menu = self._device_menu
        if device_menu is not None and device_menu.isVisible():
            if device_menu.frameGeometry().contains(point):
                return True
        # 回报率挡位浮窗：点它上面的选项时，不能把 HUD 一并收走
        picker = self._rate_picker
        if picker is not None and picker.isVisible():
            if picker.frameGeometry().contains(point):
                return True
        # DPI 挡位管理浮窗：点它上面的选项 / 输入框时，也不能把 HUD 收走
        level_picker = self._level_picker
        if level_picker is not None and level_picker.isVisible():
            if level_picker.frameGeometry().contains(point):
                return True
        # 已经弹出来的托盘浮窗：鼠标落在它里面时不要把 HUD 一并收走
        rect = self._flyout_in_qt()
        if rect is not None:
            if rect[0] <= point.x() < rect[2] and rect[1] <= point.y() < rect[3]:
                return True
        return False

    def _hide_by_outside_click(self) -> None:
        """外部点击 → 收起到托盘。

        与「关闭时最小化到托盘」开关无关：这个开关管的是点关闭按钮，
        外部点击一律收起，绝不退出程序。
        """
        if self._quitting or not self.isVisible():
            return
        if self._menu_open:
            return
        if self._tray_menu is not None and self._tray_menu.isVisible():
            return
        self.settings.hide_now()
        self.hide()

    def _on_tray_activated(self, reason) -> None:
        # 点的是托盘图标（也算「窗口外」），别把窗口收起来
        self._outside_timer.stop()
        if reason == QSystemTrayIcon.Trigger:
            # 双击会先送一个 Trigger，延迟一点等双击判定，避免「闪现一下」
            self._tray_click_timer.start()
        elif reason == QSystemTrayIcon.DoubleClick:
            self._tray_click_timer.stop()
            self.show_window()

    def _toggle_window(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.show_window()

    def _poll_show_event(self) -> None:
        if not self._show_handle:
            return
        if ctypes.windll.kernel32.WaitForSingleObject(self._show_handle, 0) == 0:
            self.show_window()

    def _persist(self) -> None:
        snap = self.monitor.cache_snapshot()
        self.cfg.cache = {k: v for k, v in snap.items() if v not in (None, "")}
        config.save(self.cfg)

    def _watch_monitor(self) -> None:
        if self._quitting or self.monitor.is_alive():
            return
        applog.get("main").warning("轮询线程已停止，自动重启")
        self.monitor.start()

    def quit_app(self) -> None:
        self._quitting = True
        if self._device_menu is not None:
            self._device_menu.hide_menu()
        if self._rate_picker is not None:
            self._rate_picker.hide_picker()
        if self._level_picker is not None:
            self._level_picker.hide_picker()
        wineffects.remove_mouse_hook()
        self.settings.hide_now()
        self._persist()
        self.monitor.stop()
        self.accessory.stop()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._quitting and self.cfg.close_to_tray:
            event.ignore()
            self._hide_to_tray()
            return
        self._persist()
        self.monitor.stop()
        self.accessory.stop()
        self.tray.hide()
        super().closeEvent(event)

    # 窗口固定在屏幕右下角，不响应拖动

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        act_refresh = QAction(i18n.T("立即刷新"), menu)
        act_refresh.triggered.connect(self._on_refresh)
        act_hub = QAction(i18n.T(driver.button_text(
            driver.kind_of(self.monitor.snapshot()))), menu)
        act_hub.triggered.connect(self._open_driver)
        act_pin = QAction(i18n.T("取消置顶" if self.cfg.always_on_top
                                 else "窗口置顶"), menu)
        act_pin.triggered.connect(lambda: self._on_setting("pin", not self.cfg.always_on_top))
        act_settings = QAction(i18n.T("设置…"), menu)
        act_settings.triggered.connect(self._toggle_settings)
        act_hide = QAction(i18n.T("最小化到托盘"), menu)
        act_hide.triggered.connect(self._hide_to_tray)
        act_quit = QAction(i18n.T("退出"), menu)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_refresh)
        menu.addAction(act_hub)
        menu.addSeparator()
        menu.addAction(act_pin)
        menu.addAction(act_settings)
        menu.addAction(act_hide)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._menu_open = True
        try:
            menu.exec(event.globalPos())
        finally:
            self._menu_open = False


# --------------------------------------------------------------------------- 单实例
def _acquire_mutex():
    """命名互斥体：避免重复启动多个窗口。"""
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "CrocirisMouseGlassWindow")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle
    except Exception:  # noqa: BLE001
        return 0


def _signal_existing() -> None:
    try:
        kernel32 = ctypes.windll.kernel32
        event = kernel32.OpenEventW(0x0002, False, "CrocirisMouseGlassShow")
        if event:
            kernel32.SetEvent(event)
            kernel32.CloseHandle(event)
    except Exception:  # noqa: BLE001
        pass


def _promote_tray_icon() -> None:
    """把托盘图标从溢出区固定到任务栏（用户手动设置过则不覆盖）。"""
    if sys.platform != "win32":
        return
    try:
        import winreg

        exe = os.path.normcase(sys.executable)
        root_path = r"Control Panel\NotifyIconSettings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path) as root:
            count = winreg.QueryInfoKey(root)[0]
        for index in range(count):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path) as root:
                name = winreg.EnumKey(root, index)
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, root_path + "\\" + name, 0,
                    winreg.KEY_READ | winreg.KEY_SET_VALUE,
                ) as key:
                    path, _ = winreg.QueryValueEx(key, "ExecutablePath")
                    if os.path.normcase(str(path)) != exe:
                        continue
                    try:
                        winreg.QueryValueEx(key, "IsPromoted")
                    except FileNotFoundError:
                        winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
            except OSError:
                continue
    except OSError:
        pass


def _create_show_event():
    try:
        return ctypes.windll.kernel32.CreateEventW(None, False, False, "CrocirisMouseGlassShow")
    except Exception:  # noqa: BLE001
        return 0


def main() -> int:
    log = applog.setup()

    if "--uninstall" in sys.argv:
        import uninstall

        return uninstall.run()

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Crociris.MouseGlass.1")
        except Exception:  # noqa: BLE001
            pass

    mutex = _acquire_mutex()
    if mutex is None:
        # 已有实例在运行。--tray 是开机自启动用的静默模式：此时不应该
        # 把已有实例的窗口唤出来（否则开机就会弹一个窗口出来）。
        if "--tray" not in sys.argv:
            log.info("已有实例在运行，唤出窗口后退出")
            _signal_existing()
        else:
            log.info("已有实例在运行，静默退出")
        return 0
    globals()["_MUTEX"] = mutex

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    # 语言要在建窗口之前定下来：Header / 卡片 / 托盘菜单都在构造时就用
    # T() 取过文案了，晚了会先闪一遍中文
    _early_cfg = config.load()
    i18n.set_lang(_early_cfg.language)
    app.setApplicationName(i18n.T("三颗豆"))
    app.setQuitOnLastWindowClosed(False)

    cfg = config.load()
    log.info("启动 v%s (follow_system=%s dark=%s)", setup_core.VERSION,
             cfg.follow_system, cfg.dark_mode)
    try:
        win = MouseGlassWindow(cfg, _create_show_event())
    except Exception:  # noqa: BLE001
        log.exception("主窗口初始化失败")
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            None, i18n.T("三颗豆"),
            f"{i18n.T('程序启动失败，详情见日志：\n')}{applog.log_path()}",
        )
        return 1
    app.aboutToQuit.connect(lambda: log.info("退出"))
    QTimer.singleShot(2500, _promote_tray_icon)
    if "--tray" not in sys.argv:  # 带 --tray 时只驻留托盘
        win.show_window()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
