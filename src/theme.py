"""主题令牌：跟随 Windows 系统浅色/深色模式。

命名按「用途」而不是「位置/外观」（见 docs/DESIGN-SYSTEM-v2.md §2.1）：
组件层只引用语义令牌，不再出现裸色值；改色只动这里。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor


def _c(hex_str: str, alpha: int = 255) -> QColor:
    color = QColor(hex_str)
    color.setAlpha(alpha)
    return color


@dataclass(frozen=True)
class Palette:
    """一整套语义令牌。

    分组：表面（surface/*）→ 文字（text/*）→ 品牌（accent/*）→ 状态（status/*）
    → 光效（glow/*）。`surface_glass_*` 是 paint_glass 的语义位（玻璃本体目前由
    绘制函数按深浅内部计算，字段留给需要精确控色的浮层）。
    """

    name: str

    # ---- 表面 surface/* ----
    surface_window_top: QColor
    surface_window_bottom: QColor
    surface_card: QColor
    surface_card_hover: QColor
    card_border: QColor
    surface_glass_top: QColor
    surface_glass_bottom: QColor
    glass_border_top: QColor
    glass_border_bottom: QColor
    #: 弹窗的不透明底板（列表面板 / 设置浮层都要压住背后内容）
    surface_glass_solid: QColor
    #: 浮层外阴影基色（E2 海拔的唯一来源）
    surface_overlay: QColor
    divider: QColor

    # ---- 文字 text/* ----
    text_primary: QColor
    text_secondary: QColor
    text_tertiary: QColor

    # ---- 品牌 accent/* ----
    accent: QColor
    #: 当「正文/小字文字色」用时：浅色主题必须加深到 WCAG AA
    #: （#007AFF 对白底约 4.0:1，不足 4.5:1）
    accent_text: QColor
    accent_soft: QColor
    focus_ring: QColor

    # ---- 状态 status/* ----
    success: QColor
    warning: QColor
    danger: QColor

    # ---- 光效 glow/* ----
    glow_primary: QColor
    glow_secondary: QColor


DARK = Palette(
    name="dark",
    # 深色不做「顶部打光」：上下几乎同色（只留 3 点差异保证不糊成一块死黑），
    # 否则顶上亮、底下暗，一眼就是有盏灯从上面照下来。
    surface_window_top=_c("#1A1A1E", 255),
    surface_window_bottom=_c("#171719", 255),
    surface_card=_c("#FFFFFF", 14),
    surface_card_hover=_c("#FFFFFF", 24),
    card_border=_c("#FFFFFF", 26),
    surface_glass_top=_c("#FFFFFF", 64),
    surface_glass_bottom=_c("#FFFFFF", 18),
    glass_border_top=_c("#FFFFFF", 120),
    glass_border_bottom=_c("#FFFFFF", 26),
    surface_glass_solid=_c("#2D2D31", 252),
    surface_overlay=_c("#000000", 90),
    divider=_c("#FFFFFF", 22),
    text_primary=_c("#FFFFFF", 242),
    text_secondary=_c("#FFFFFF", 150),
    text_tertiary=_c("#FFFFFF", 96),
    accent=_c("#5AC8FA"),
    accent_text=_c("#5AC8FA"),          # 深底上原色已达 AA，无需加深
    accent_soft=_c("#0A84FF", 40),
    focus_ring=_c("#5AC8FA", 120),
    success=_c("#34C759"),
    warning=_c("#FF9F0A"),
    danger=_c("#FF453A"),
    glow_primary=_c("#0A84FF", 24),
    glow_secondary=_c("#BF5AF2", 16),
)

LIGHT = Palette(
    name="light",
    surface_window_top=_c("#FFFFFF", 255),
    surface_window_bottom=_c("#F3F3F7", 255),
    surface_card=_c("#FFFFFF", 232),
    surface_card_hover=_c("#FFFFFF", 252),
    card_border=_c("#0E0E12", 20),
    surface_glass_top=_c("#FFFFFF", 216),
    surface_glass_bottom=_c("#FFFFFF", 132),
    glass_border_top=_c("#FFFFFF", 240),
    glass_border_bottom=_c("#101014", 26),
    surface_glass_solid=_c("#F2F2F7", 253),
    surface_overlay=_c("#101014", 34),
    divider=_c("#101014", 22),
    text_primary=_c("#0E0E12", 240),
    text_secondary=_c("#0E0E12", 150),
    text_tertiary=_c("#0E0E12", 100),
    accent=_c("#007AFF"),
    accent_text=_c("#0066D6"),          # 白底小字要达 AA：深一档
    accent_soft=_c("#007AFF", 26),
    focus_ring=_c("#007AFF", 120),
    success=_c("#23A94A"),              # 浅底上 #34C759 偏浅，压深一档
    warning=_c("#B36B00"),              # 白底上的琥珀文字/描边加深，保证可读
    danger=_c("#D92B21"),               # 白底上的错误文字加深一档
    glow_primary=_c("#007AFF", 17),
    glow_secondary=_c("#AF52DE", 11),
)


#: 兼容旧引用的常量；新代码请用 Palette 上的 success / warning / danger 令牌
GREEN = "#34C759"
RED = "#FF453A"
AMBER = "#FF9F0A"

#: 低电量提醒的默认阈值（百分比）；实际值可在设置里改，见 config
LOW_BATTERY = 15
#: 电量颜色分档：≤阈值是红，阈值以上再 15 个点内是琥珀，往上是绿
BATTERY_WARN_SPAN = 15


def battery_color(palette: Palette, level: int | None, low: int = LOW_BATTERY) -> QColor:
    """电量 → 颜色。low 是可配置的低电量阈值，红/黄档跟着它一起走。"""
    if level is None:
        return palette.text_tertiary
    if level <= low:
        return QColor(palette.danger)
    if level <= low + BATTERY_WARN_SPAN:
        return QColor(palette.warning)
    return QColor(palette.success)


def palette_for(dark: bool) -> Palette:
    return DARK if dark else LIGHT


def system_is_dark() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except OSError:
        return True
