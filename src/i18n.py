"""界面双语：中文 / English。

设计上**以中文原文为键**，而不是另造一套 `settings.language.title` 这样的
key：整个代码库里本来就写着一句句中文，包一层 `T("设置")` 就能翻译，改起来
一眼能对上是哪句话；字典里漏掉的条目会原样显示中文，不会出现空白控件，漏译
是「没翻」，不是「坏掉」。

品牌名单独处理：「三颗豆」在英文界面里是 **SKD**（用户指定），所以中文叫
「三颗豆驱动管理」的标题，英文是「SKD Driver Manager」。

用法：
    from i18n import T, devices, set_lang
    p.drawText(rect, T("设置"))            # 纯文案
    T("已写入 {name}: {ok}").format(...)   # 带占位符
"""
from __future__ import annotations

import os

ZH = "zh"
EN = "en"
LANGS = (ZH, EN)

#: 当前语言。启动时由 config 里的 language 字段决定（见 `set_lang`）
_LANG = ZH

#: 中文原文 → 英文。键必须和代码里的字面量**逐字一致**（含空格、全角标点）
TABLE: dict[str, str] = {
    # ---------------------------------------------------------------- 品牌 / 标题
    "三颗豆": "SKD",
    "三颗豆驱动管理": "SKD Driver Manager",
    "三颗豆 · 未连接": "SKD · Not connected",

    # ---------------------------------------------------------------- 头部 / 状态
    "台设备": "devices",
    "正在扫描…": "Scanning…",
    "连接中": "Connecting",
    "已连接": "Connected",
    "未连接": "Not connected",
    "等待设备": "Waiting",
    "休眠中": "Sleeping",
    "信号弱": "Weak signal",
    "充电中": "Charging",
    "未识别鼠标": "No mouse detected",
    "设备未连接": "Device not connected",
    "正在扫描设备…": "Scanning for devices…",
    "鼠标休眠中，移动即可唤醒": "Mouse asleep — move it to wake",
    "鼠标无响应，请轻移鼠标唤醒": "No response — move the mouse to wake it",
    "未检测到兼容鼠标": "No compatible mouse found",
    "未检测到兼容的 CROCIRIS 鼠标": "No compatible CROCIRIS mouse",
    "设备休眠或已断开": "Asleep or disconnected",
    "设备已断开": "Disconnected",
    "轮询线程异常退出": "Polling thread stopped",
    "罗技鼠标已断开": "Logitech mouse disconnected",
    "罗技鼠标已拔出": "Logitech mouse unplugged",
    "罗技鼠标无响应": "Logitech mouse not responding",
    "未检测到罗技鼠标": "No Logitech mouse found",
    "接收器已连接，未找到鼠标": "Receiver connected — no mouse found",

    # ---------------------------------------------------------------- 卡片
    "灵敏度": "Sensitivity",
    # 卡片标题改成短名（Rate）：英文全名 Report rate 在 113px 的卡片里几乎占满，
    # 不支持编辑时右上角还要挂「via G HUB」，长标题会把角标挤成省略号
    "回报率": "Rate",
    "连接": "Connection",
    "电量": "Battery",
    "剩余电量": "Remaining",
    "已充满": "Fully charged",
    "正在充电": "Charging",
    "每 2 秒自动刷新": "Auto refresh every 2s",
    "未读取到可调范围": "Range unavailable",
    "可调 {lo}–{hi} DPI": "Adj. {lo}–{hi} DPI",
    "步进 {step} DPI": "Step {step} DPI",
    "共 {n} 档可选": "{n} presets",
    "可调 {lo}–{hi} Hz": "Adj. {lo}–{hi} Hz",
    "仅支持 {lo} Hz": "Only {lo} Hz",
    "实时读取": "Live",
    "8K 接收器": "8K receiver",
    "有线连接": "Wired",
    "无线连接": "Wireless",
    "2.4G 无线": "2.4 GHz wireless",
    "接收器 · 有线": "Receiver · Wired",
    "接收器": "Receiver",
    "高延迟": "High latency",
    "中延迟": "Medium",
    "低延迟": "Low latency",
    "USB 有线": "USB wired",
    "移动鼠标唤醒": "Move mouse to wake",
    "等待唤醒": "Waiting to wake",
    "读取中": "Reading",
    "固件 ": "FW ",
    " · USB 有线": " · USB",
    " · 休眠": " · Sleep",
    " · 充电中": " · Charging",

    # ---------------------------------------------------------------- 驱动按钮
    "打开官方网页驱动": "Open web driver",
    "打开官方软件驱动": "Open official driver",
    "打开罗技 G HUB": "Open Logitech G HUB",
    "本机未装 G HUB，点击去官网下载": "G HUB not installed — open download page",
    "在浏览器中打开网页驱动": "Open the web driver in your browser",
    "已打开罗技 G HUB": "Opened Logitech G HUB",
    "重新扫描": "Rescan",
    "重新扫描设备": "Rescan devices",

    # ---------------------------------------------------------------- 设置浮层
    "设置": "Settings",
    "开机自动启动": "Launch at startup",
    "窗口置顶": "Always on top",
    "语言": "Language",
    "主题": "Theme",
    "低电量提醒": "Low battery alert",
    "提醒阈值": "Threshold",
    "系统": "System",
    "浅色": "Light",
    "深色": "Dark",
    "中文": "中文",

    # ---------------------------------------------------------------- 托盘 / 关闭
    "显示窗口": "Show window",
    "立即刷新": "Refresh now",
    "取消置顶": "Turn off always on top",
    "最小化到托盘": "Minimize to tray",
    "关闭": "Close",
    "退出": "Quit",
    "设置…": "Settings…",

    # ---------------------------------------------------------------- 编辑 / 挡位
    "应用": "Apply",
    "取消": "Cancel",
    "挡位": "Stages",
    "管理 DPI 挡位（切换 / 增加 / 删除）": "Manage DPI stages",
    "选择回报率挡位": "Choose a report rate",
    "回报率挡位": "Report rate",
    "DPI 挡位": "DPI stages",
    "删除挡位": "Delete stage",
    "+ 增加挡位": "+ Add stage",
    "至少留一档": "Keep at least one stage",
    "请填数值": "Enter a value",
    "超出范围": "Out of range",
    "已存在": "Already exists",
    "已加 ": "Added ",
    "已删 ": "Deleted ",
    "最多 ": "Max ",
    " 档": " stages",
    "档位由 G HUB 管理": "Stages managed by G HUB",
    "G HUB 管理": "G HUB",
    "不支持编辑": "Read-only",
    # 不支持编辑的模块，右上角常驻「由 G HUB 支持」这类角标
    "由 {app} 支持": "via {app}",
    "官方软件": "official app",
    "G HUB": "G HUB",
    # ---- 没有厂商通道的品牌鼠（狼途这类）：只有按键能调，没有官方软件 ----
    "狼途": "LANGTU",
    "按键": "on-device",
    "按键调节": "On device",
    "无法读取": "Can't read",
    "无官方软件": "No official app",
    "无官方软件 · 按键调节": "No official app · use on-device buttons",
    "品牌鼠标": "Mouse",
    # ---- 配件（键盘 / 耳机）：只列在设备弹窗里，不参与切换 ----
    "血手幽灵": "Bloody",
    "键盘": "Keyboard",
    "耳机": "Headset",
    "这台鼠标没有软件调节通道": "This mouse has no software control channel",
    "这台鼠标没有官方软件，DPI 和回报率用鼠标上的按键调节":
        "No companion app for this mouse — set DPI and report rate with its own buttons",
    # 纯有线鼠标没有电池：电量位置统一写「USB供电」（弹窗 + 电量环同一句）
    "USB供电": "USB powered",
    "已写入 ": "Saved ",
    "成功": "OK",
    "失败": "Failed",
    "未读取到可用的回报率挡位": "No report-rate options",
    "回报率由 G HUB 管理，请在 G HUB 中修改": "Report rate is managed in G HUB",
    "该设备不支持编辑挡位": "This device does not support stage editing",
    "挡位写入失败：": "Stage write failed: ",
    "写入失败：": "Write failed: ",
    "设备未响应": "No response from device",
    "已切到 ": "Switched to ",
    "切换失败：": "Switch failed: ",
    "（未连接）": " (offline)",
    "设备列表": "Devices",

    # ---------------------------------------------------------------- 安装程序
    "三颗豆 安装程序": "SKD Setup",
    "安装程序": "Setup",
    "安装 {name} v{ver}": "Install {name} v{ver}",
    "多品牌鼠标驱动管理 · 版本 {ver}": "Multi-brand mouse driver manager · v{ver}",
    "安装位置": "Install location",
    "自动创建 skdDriver 文件夹": "Creates a skdDriver folder",
    "安装到 {path}": "Installing to {path}",
    "安装到当前用户，无需管理员权限": "Installs for the current user — no admin rights needed",
    "更改…": "Browse…",
    "选择安装位置": "Choose install folder",
    "安装": "Install",
    "请稍候": "Please wait",
    "重试": "Retry",
    "完成": "Done",
    "正在安装…": "Installing…",
    "安装完成，正在启动…": "Installed — launching…",
    "安装失败：": "Install failed: ",
    "创建桌面快捷方式": "Create a desktop shortcut",
    "在桌面放置一个图标": "Place an icon on the desktop",
    "登录 Windows 后自动运行": "Run automatically after you sign in",
    "界面语言，安装完成后立即生效": "UI language, applied right after install",
    "将安装到新位置，旧目录里的旧版本会被清理":
        "New location — the old version will be removed",
    "检测到已安装版本，继续将覆盖更新":
        "Existing install found — this will update it",
    # 安装目录校验 / 安装失败的提示（由 setup_core 抛出）
    "安装路径不能为空": "Install path cannot be empty",
    "安装路径必须是绝对路径": "Install path must be absolute",
    "不能直接装到磁盘根目录，请选择其中的子文件夹":
        "Cannot install to a drive root — pick a subfolder",
    "请勿直接安装到系统目录，请选择子文件夹":
        "Do not install into a system folder — pick a subfolder",
    "该目录无法写入：{reason}": "Folder is not writable: {reason}",
    "安装包不完整，缺少主程序文件": "The package is incomplete — app files are missing",
    "主程序没有落到安装目录": "the app was not copied into the install folder",

    # ---------------------------------------------------------------- 通知
    "鼠标": "Mouse",
    "鼠标电量低": "Low mouse battery",
    "电量 {pct}%，已低于 {thr}%，请及时充电":
        "Battery {pct}% — below {thr}%. Please charge it.",
    # 电量恰好等于阈值时说「低于」不准确；提醒阈值由设置页决定，正文写清楚
    "电量 {pct}%，已达设定的 {thr}% 提醒阈值，请及时充电":
        "Battery {pct}% — reached your {thr}% alert threshold. Please charge.",

    # ---------------------------------------------------------------- 日志 / 兜底
    "程序启动失败，详情见日志：\n": "Failed to start. See log:\n",
    "主窗口初始化失败": "Main window failed to initialize",
}


def lang() -> str:
    return _LANG


def is_en() -> bool:
    return _LANG == EN


def set_lang(code: str) -> None:
    """切换当前语言；传进来不认识的值一律退回中文。"""
    global _LANG
    _LANG = EN if str(code).lower().startswith("en") else ZH


def T(text: str) -> str:
    """中文原文 → 当前语言。字典里没有就原样返回（漏译不会变空白）。"""
    if _LANG == ZH or not text:
        return text
    return TABLE.get(text, text)


def _lang_from_langid(langid: int) -> str:
    """Windows LANGID → 语言。只认中文和英文，其它一律中文。

    LANGID 低 10 位是「主语言 ID」：0x04 = 中文（含简繁、各地区的子语言），
    0x09 = 英文。日文 / 德文 / 法文这些没做翻译的系统，装出来是英文界面
    比中文更糟，所以统一回退中文。
    """
    primary = int(langid) & 0x3FF
    if primary == 0x09:
        return EN
    return ZH


def system_default_lang() -> str:
    """系统界面语言 → "zh" / "en"。

    安装器第一次打开时用它做默认值（用户要求「安装时语言默认跟随系统，
    不是中英文的系统默认中文」）。取的是 **UI 语言**（GetUserDefaultUILanguage），
    不是区域格式：一个中文系统装成英文区域，界面照样该是中文。
    """
    try:
        import ctypes

        return _lang_from_langid(int(ctypes.windll.kernel32.GetUserDefaultUILanguage()))
    except Exception:  # noqa: BLE001  —— 非 Windows / 取不到就往下走
        pass
    tag = (os.environ.get("LANG") or os.environ.get("LC_ALL") or "").lower()
    if tag.startswith("zh"):
        return ZH
    if tag.startswith("en"):
        return EN
    return ZH


# ------------------------------------------------------------------ 格式化 helper
def devices(n: int) -> str:
    """「2台设备」/「2 devices」。英文要空格和单复数，中文不能有空格。"""
    if _LANG == ZH:
        return f"{n}台设备"
    return f"{n} device" if n == 1 else f"{n} devices"


def stage(cur: int, total: int) -> str:
    """「第 2 档 / 共 4 档」/「Stage 2 / 4」。"""
    if _LANG == ZH:
        return f"第 {cur} 档 / 共 {total} 档"
    return f"Stage {cur} / {total}"


def stages_total(total: int) -> str:
    """只有总数：「共 4 档」/「4 stages」。"""
    if _LANG == ZH:
        return f"共 {total} 档"
    return f"{total} stage" if total == 1 else f"{total} stages"


def delete_stage_confirm(value: int) -> str:
    if _LANG == ZH:
        return f"确定删除挡位 {value} DPI 吗？"
    return f"Delete the {value} DPI stage?"


def rate_readonly(values: list[int] | str) -> str:
    joined = values if isinstance(values, str) else "/".join(str(v) for v in values)
    if _LANG == ZH:
        return f"回报率不可写，仅展示挡位 {joined}"
    return f"Read-only — options: {joined}"
