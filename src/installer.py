"""安装程序：把主程序安装到用户目录（或用户自选目录），并创建快捷方式与卸载项。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

import setup_core as core
import i18n
import theme
import widgets as W
from appicon import make_icon

#: 高度从 358 加到 402：多出来的 44px 是「语言」那一行（与设置浮层同样的行高）
PANEL_W, PANEL_H = 424, 402
PAD_X = 24
ROWS = [
    ("auto", "开机自动启动", "登录 Windows 后自动运行"),
    ("desktop", "创建桌面快捷方式", "在桌面放置一个图标"),
]
ROW_Y = 186
ROW_H = 44
#: 语言行排在两个开关下面，样式与开关行一致（左侧标题 + 右侧分段控件）
LANG_I = len(ROWS)
LANG_Y = ROW_Y + LANG_I * ROW_H
SEG_W = 138
BTN_Y = ROW_Y + ROW_H * (len(ROWS) + 1) + 18
HINT_Y = BTN_Y + 40

PATH_LABEL_Y = 112
FIELD_Y = 130
FIELD_H = 32
BROWSE_W = 76
# 底部两个按钮等宽（取消 104/安装 136 各被用户指过不齐），
# 总宽 248 与旧布局一致，锚点不用挪
BTN_W = 120


class PathField(QWidget):
    """只读的安装路径框：显示路径，点击即弹出目录选择。"""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pal = theme.DARK
        self._path = ""
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def set_palette_theme(self, pal: theme.Palette) -> None:
        self._pal = pal
        self.update()

    def set_path(self, text: str) -> None:
        if text != self._path:
            self._path = text
            self.update()

    def path(self) -> str:
        return self._path

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.surface_card_hover) if self._hover else QColor(pal.surface_card))
        p.drawPath(W.rounded(rect, 9))
        p.setBrush(Qt.NoBrush)
        border = QColor(pal.text_tertiary) if self._hover else QColor(pal.card_border)
        p.setPen(QPen(border, 1.0))
        p.drawPath(W.rounded(rect, 9))

        W.icon_folder(p, QRectF(11, rect.center().y() - 7, 14, 14),
                      QColor(pal.text_secondary))

        font = W.app_font(10, QFont.Weight.Normal, 0.1)
        p.setFont(font)
        fm = QFontMetricsF(font)
        text_rect = QRectF(34, rect.top(), rect.width() - 34 - 10, rect.height())
        p.setPen(QPen(QColor(pal.text_secondary)))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self._path, Qt.ElideMiddle, int(text_rect.width())))


class SetupWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._pal = theme.DARK if theme.system_is_dark() else theme.LIGHT
        self._state = "idle"
        self._status = ""      # 状态行文案，**存中文原文**，画的时候再翻
        self._error = ""       # 安装异常原文（setup_core 已按当前语言生成）
        self._ok_key = "安装"   # 主按钮文案的中文原文，重试/完成后会换
        self._drag = None
        self._install_dir = core.preferred_install_dir()
        self._default_dir = core.default_install_dir()

        # 装错版本是最常见的失误，任务栏 / Alt-Tab 里也带上版本号
        self.setWindowTitle(self._title_text())
        self.setWindowIcon(make_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(PANEL_W, PANEL_H)

        self.icon_pm = make_icon().pixmap(44, 44)

        self.sw_auto = W.Switch(True, self)
        self.sw_auto.move(PANEL_W - PAD_X - self.sw_auto.width(),
                          ROW_Y + (ROW_H - self.sw_auto.height()) // 2)
        self.sw_desktop = W.Switch(True, self)
        self.sw_desktop.move(PANEL_W - PAD_X - self.sw_desktop.width(),
                             ROW_Y + ROW_H + (ROW_H - self.sw_desktop.height()) // 2)

        # 语言切换：与设置浮层同一个分段控件，切完整个安装器立刻换语言
        self.lang_seg = W.Segment(["中文", "English"], self._lang_index(), SEG_W, parent=self)
        self.lang_seg.move(PANEL_W - PAD_X - SEG_W,
                           LANG_Y + (ROW_H - self.lang_seg.height()) // 2)
        self.lang_seg.changed.connect(self._on_language)

        self.path_field = PathField(self)
        self.path_field.setFixedSize(PANEL_W - PAD_X * 2 - BROWSE_W - 8, FIELD_H)
        self.path_field.move(PAD_X, FIELD_Y)
        self.path_field.set_path(self._install_dir)
        self.path_field.clicked.connect(self._choose_dir)

        self.btn_browse = W.GlassButton(i18n.T("更改…"), height=FIELD_H, parent=self)
        self.btn_browse.setFixedWidth(BROWSE_W)
        self.btn_browse.move(PANEL_W - PAD_X - BROWSE_W, FIELD_Y)
        self.btn_browse.clicked.connect(self._choose_dir)

        self.btn_ok = W.GlassButton(i18n.T("安装"), kind="primary", height=38, parent=self)
        self.btn_ok.setFixedWidth(BTN_W)
        self.btn_ok.move(PANEL_W - PAD_X - BTN_W, BTN_Y)
        self.btn_ok.clicked.connect(self._start_install)

        self.btn_cancel = W.GlassButton(i18n.T("取消"), height=38, parent=self)
        self.btn_cancel.setFixedWidth(BTN_W)
        self.btn_cancel.move(self.btn_ok.x() - 8 - BTN_W, BTN_Y)
        self.btn_cancel.clicked.connect(self.close)

        for widget in (self.sw_auto, self.sw_desktop, self.lang_seg):
            widget.set_palette_theme(self._pal)
        for widget in (self.btn_cancel, self.btn_ok, self.btn_browse):
            widget.set_palette_theme(self._pal)
        self.path_field.set_palette_theme(self._pal)

        self._refresh_status()

        self._center()

    # ------------------------------------------------------------------ 语言
    @staticmethod
    def _title_text() -> str:
        return i18n.T("安装 {name} v{ver}").format(
            name=i18n.T(core.DISPLAY_NAME), ver=core.VERSION)

    @staticmethod
    def _lang_index() -> int:
        return 0 if i18n.lang() == i18n.ZH else 1

    def _on_language(self, index: int) -> None:
        """分段控件切语言：先换全局语言，再把「存在控件里」的文字重设一遍。"""
        code = i18n.EN if index == 1 else i18n.ZH
        # 允许被外部直接调用（比如按 --lang 预设），控件位置也要跟着对上
        self.lang_seg.set_index(index, animate=False)
        if code == i18n.lang():
            return
        i18n.set_lang(code)
        self.setWindowTitle(self._title_text())
        self.btn_browse.set_text(i18n.T("更改…"))
        self.btn_cancel.set_text(i18n.T("取消"))
        self.btn_ok.set_text(i18n.T(self._ok_key))
        if self._state == "error" and not self._error:
            # 路径校验的提示是 setup_core 按旧语言生成的，重算一次换成新语言
            self._status = core.validate_install_dir(self._install_dir) or self._status
        self.update()

    # ------------------------------------------------------------------ 状态
    def set_install_dir(self, path: str) -> None:
        """外部（如 --dir 参数）指定安装目录：同样按「父目录 + skdDriver」落位。"""
        target = core.with_app_folder(core.normalize_install_dir(path))
        if not target:
            return
        self._install_dir = target
        self.path_field.set_path(target)
        self._status = ""
        self._refresh_status()
        self.update()

    def _refresh_status(self) -> None:
        if self._status:
            return
        if self._install_dir != self._default_dir:
            self._status = "将安装到新位置，旧目录里的旧版本会被清理"
        elif core.is_installed():
            self._status = "检测到已安装版本，继续将覆盖更新"

    # ------------------------------------------------------------------ 绘制
    def _center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2 - 40)

    def paintEvent(self, event) -> None:  # noqa: N802
        pal = self._pal
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = W.rounded(body, 24)
        # 上下打光：安装器窗口底色只沿 y 变化
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0.0, pal.surface_window_top)
        grad.setColorAt(1.0, pal.surface_window_bottom)
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawPath(path)

        # 顶部柔光
        p.save()
        p.setClipPath(path)
        glow = QLinearGradient(QPointF(body.left(), body.top()), QPointF(body.left(), body.top() + 150))
        base = QColor(pal.accent)
        base.setAlpha(30 if pal.name == "dark" else 22)
        glow.setColorAt(0.0, base)
        glow.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        p.setBrush(glow)
        p.drawRect(body)
        p.restore()

        border = QLinearGradient(body.topLeft(), body.bottomLeft())
        if pal.name == "dark":
            border.setColorAt(0.0, QColor(255, 255, 255, 62))
            border.setColorAt(1.0, QColor(255, 255, 255, 24))
        else:
            border.setColorAt(0.0, QColor(255, 255, 255, 225))
            border.setColorAt(1.0, QColor(0, 0, 0, 20))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(border, 1.0))
        p.drawPath(path)

        # 图标 + 标题
        p.drawPixmap(QRectF(PAD_X, 20, 44, 44), self.icon_pm, QRectF(self.icon_pm.rect()))
        p.setFont(W.app_font(16, QFont.Weight.DemiBold, -0.2))
        p.setPen(QPen(pal.text_primary))
        p.drawText(QRectF(PAD_X + 56, 20, 240, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   i18n.T(core.DISPLAY_NAME))
        p.setFont(W.app_font(9, QFont.Weight.Normal, 0.4))
        p.setPen(QPen(QColor(pal.text_tertiary)))
        p.drawText(QRectF(PAD_X + 56, 43, 300, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   i18n.T("多品牌鼠标驱动管理 · 版本 {ver}").format(ver=core.VERSION))

        # 说明行
        p.setFont(W.app_font(9, QFont.Weight.Normal, 0.2))
        fm = QFontMetricsF(p.font())
        if self._state == "error":
            color = QColor("#FF453A")
        elif self._state in ("installing", "done"):
            color = QColor(pal.accent)
        else:
            color = QColor(pal.text_tertiary)
        p.setPen(QPen(color))
        if self._state == "error" and self._error:
            text = i18n.T("安装失败：") + self._error
        elif self._status:
            text = i18n.T(self._status)
        else:
            text = i18n.T("安装到 {path}").format(path=self._install_dir)
        p.drawText(QRectF(PAD_X, 76, self.width() - PAD_X * 2, 16),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(text, Qt.ElideMiddle, int(self.width() - PAD_X * 2)))

        # 分隔线
        line = QColor(255, 255, 255, 16) if pal.name == "dark" else QColor(0, 0, 0, 12)
        p.setPen(QPen(line, 1.0))
        p.drawLine(QPointF(PAD_X, 104), QPointF(self.width() - PAD_X, 104))
        p.drawLine(QPointF(PAD_X, 176), QPointF(self.width() - PAD_X, 176))
        p.drawLine(QPointF(PAD_X, LANG_Y + ROW_H + 6), QPointF(self.width() - PAD_X, LANG_Y + ROW_H + 6))

        # 安装位置标题
        p.setFont(W.app_font(9, QFont.Weight.Medium, 0.2))
        p.setPen(QPen(QColor(pal.text_secondary)))
        p.drawText(QRectF(PAD_X, PATH_LABEL_Y, 200, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, i18n.T("安装位置"))
        # 右侧小字：说明不管选哪儿都会多出一层 skdDriver 文件夹
        # （用户要求「不论安装到哪里，都自动创建安装文件夹 skdDriver」）
        p.setFont(W.app_font(8, QFont.Weight.Normal, 0.3))
        p.setPen(QPen(QColor(pal.text_tertiary)))
        p.drawText(QRectF(PAD_X, PATH_LABEL_Y, self.width() - PAD_X * 2, 14),
                   Qt.AlignRight | Qt.AlignVCenter, i18n.T("自动创建 skdDriver 文件夹"))

        # 选项行
        for i, (_key, title, hint) in enumerate(ROWS):
            y = ROW_Y + i * ROW_H
            p.setFont(W.app_font(11, QFont.Weight.Medium, 0.2))
            p.setPen(QPen(pal.text_primary))
            p.drawText(QRectF(PAD_X, y + 6, 260, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       i18n.T(title))
            p.setFont(W.app_font(9, QFont.Weight.Normal, 0.2))
            p.setPen(QPen(QColor(pal.text_tertiary)))
            p.drawText(QRectF(PAD_X, y + 22, 260, 14), Qt.AlignLeft | Qt.AlignVCenter,
                       i18n.T(hint))

        # 语言行：文字区只到分段控件左边，再多就会压上去（英文副标题更长）
        p.setFont(W.app_font(11, QFont.Weight.Medium, 0.2))
        p.setPen(QPen(pal.text_primary))
        p.drawText(QRectF(PAD_X, LANG_Y + 6, 220, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   i18n.T("语言"))
        p.setFont(W.app_font(9, QFont.Weight.Normal, 0.2))
        p.setPen(QPen(QColor(pal.text_tertiary)))
        p.drawText(QRectF(PAD_X, LANG_Y + 22, 220, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   i18n.T("界面语言，安装完成后立即生效"))

        # 底部提示
        p.setFont(W.app_font(8, QFont.Weight.Normal, 0.3))
        p.setPen(QPen(QColor(pal.text_tertiary)))
        p.drawText(QRectF(PAD_X, HINT_Y, 300, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, i18n.T("安装到当前用户，无需管理员权限"))

    # ------------------------------------------------------------------ 路径
    def _choose_dir(self) -> None:
        if self._state == "installing":
            return
        start = self._install_dir
        if not os.path.isdir(start):
            start = os.path.dirname(start) or self._default_dir
        chosen = QFileDialog.getExistingDirectory(
            self, i18n.T("选择安装位置"), start,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not chosen:
            return
        # 选的目录只是「装到哪儿」，程序本体永远落进它下面的 skdDriver 文件夹
        new_dir = core.with_app_folder(core.normalize_install_dir(chosen))
        if new_dir == self._install_dir:
            return
        self._install_dir = new_dir
        self.path_field.set_path(new_dir)
        self._status = ""
        self._refresh_status()
        self.update()

    # ------------------------------------------------------------------ 安装
    def _start_install(self) -> None:
        if self._state == "installing":
            return
        if self._state == "done":
            QApplication.quit()
            return

        problem = core.validate_install_dir(self._install_dir)
        if problem:
            self._state = "error"
            self._error = ""            # 校验类提示已经是当前语言的整句
            self._status = problem
            self.update()
            return

        self._state = "installing"
        self._status = "正在安装…"
        self._error = ""
        self._ok_key = "请稍候"
        self.btn_ok.set_text(i18n.T(self._ok_key))
        self.btn_ok.set_enabled_state(False)
        self.btn_cancel.set_enabled_state(False)
        self.btn_browse.set_enabled_state(False)
        self.lang_seg.set_enabled_state(False)
        self.path_field.setEnabled(False)
        self.update()
        QTimer.singleShot(90, self._do_install)

    def _do_install(self) -> None:
        exe = ""
        try:
            exe = core.perform_install(
                core.default_payload(),
                autostart_on=self.sw_auto.is_checked(),
                desktop=self.sw_desktop.is_checked(),
                target_dir=self._install_dir,
            )
        except Exception as exc:  # noqa: BLE001
            self._state = "error"
            self._error = str(exc)
            self._status = ""
            self._ok_key = "重试"
            self.btn_ok.set_text(i18n.T(self._ok_key))
            self.btn_ok.set_enabled_state(True)
            self.btn_cancel.set_enabled_state(True)
            self.btn_browse.set_enabled_state(True)
            self.lang_seg.set_enabled_state(True)
            self.path_field.setEnabled(True)
            self.update()
            return

        # 安装器里选的语言落进主程序配置，装完启动就是这套语言（写失败不影响安装）
        core.write_config_language(i18n.lang())

        self._state = "done"
        self._status = "安装完成，正在启动…"
        self._error = ""
        self._ok_key = "完成"
        self.btn_ok.set_text(i18n.T(self._ok_key))
        self.btn_ok.set_enabled_state(True)
        self.update()
        QTimer.singleShot(650, lambda: self._launch(exe))

    def _launch(self, exe: str) -> None:
        try:
            subprocess.Popen([exe], close_fds=True)
        except OSError:
            pass
        QApplication.quit()

    # ------------------------------------------------------------------ 交互
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape and self._state != "installing":
            self.close()
        super().keyPressEvent(event)


def initial_language() -> str:
    """安装器打开时用哪种语言。

    用户要求「安装时语言默认跟随系统，系统不是中英文就默认中文」。所以全新
    安装跟着 Windows 的 UI 语言走；**已经装过一次的机器沿用上次选的语言**——
    中文系统里特意切到英文的用户，升级一次就被打回中文会很恼火。
    """
    saved = core.read_config_language()
    if saved in i18n.LANGS:
        return saved
    return i18n.system_default_lang()


def silent_install(target: str = "", lang: str = "") -> int:
    """无人值守安装（供脚本调用）。"""
    try:
        core.perform_install(core.default_payload(), target_dir=target)
        core.write_config_language(lang or initial_language())
        return 0
    except Exception as exc:  # noqa: BLE001
        try:
            log = os.path.join(os.environ.get("TEMP", "."), "skd-setup.log")
            with open(log, "w", encoding="utf-8") as fh:
                # 只写 repr 时看不出是哪一行炸的，静默安装没法排查；连堆栈一起落盘
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {exc!r}\n")
                fh.write(traceback.format_exc())
        except OSError:
            pass
        return 1


def dir_arg() -> str:
    """命令行里指定的安装目录：--dir "D:\\Apps"（程序会落进其中的 skdDriver 文件夹）。"""
    args = sys.argv[1:]
    if "--dir" in args:
        index = args.index("--dir")
        if index + 1 < len(args):
            return core.normalize_install_dir(args[index + 1])
    return ""


def lang_arg() -> str:
    """命令行里指定的界面语言：--lang en（静默安装时用，留空即跟随系统）。"""
    args = sys.argv[1:]
    if "--lang" in args:
        index = args.index("--lang")
        if index + 1 < len(args):
            value = args[index + 1].strip().lower()
            if value in i18n.LANGS:
                return value
    return ""


def main() -> int:
    if "--silent" in sys.argv:
        return silent_install(dir_arg(), lang_arg())
    # 语言必须在建窗口之前定好：标题、按钮文字都是构造函数里按当前语言取的
    i18n.set_lang(lang_arg() or initial_language())
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(f"{i18n.T(core.DISPLAY_NAME)} {i18n.T('安装程序')}")
    app.setQuitOnLastWindowClosed(True)
    window = SetupWindow()
    preset = dir_arg()
    if preset:
        window.set_install_dir(preset)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
