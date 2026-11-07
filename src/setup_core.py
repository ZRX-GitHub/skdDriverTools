"""安装 / 卸载的公共逻辑：路径、快捷方式、注册表。"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from ctypes import wintypes

import i18n

APP_EXE = "skdDriver.exe"
#: v1.4.28 之前主程序叫 MouseGlass.exe。**只用于升级兼容**：安装时要杀掉
#: 还在跑的旧进程、清掉旧目录里残留的旧 exe，也要能认出老安装包的 payload。
#: 新装的一切（快捷方式、自启动、DisplayIcon、卸载命令）只用 APP_EXE。
LEGACY_APP_EXE = "MouseGlass.exe"
#: 安装文件夹名：不论用户把程序装到哪个位置，程序本体永远待在一个名叫
#: skdDriver 的文件夹里（用户要求）。这样用户选的目录只是「父目录」，
#: 里面长什么样跟老版本无关，卸载时也只需要删这一个文件夹。
APP_FOLDER = "skdDriver"
#: 安装目录里的独立卸载程序（v1.4.6 起随包安装，不依赖 `_internal`）
UNINSTALL_EXE = "uninstall.exe"
DISPLAY_NAME = "三颗豆"
PUBLISHER = "三颗豆驱动工具"
VERSION = "1.4.28"
SHORTCUT_NAME = "三颗豆"
#: 软件描述：exe 属性 + 卸载列表的 Comments
DESCRIPTION = ("本软件适用不同品牌鼠标驱动的管理，例如DPI、刷新率、电量的管理。"
               "本软件已开源，禁止私自商用")

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MouseGlass"

CREATE_NO_WINDOW = 0x08000000
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"

# 安装目录解析：
#   安装程序显式指定（set_install_dir） > 注册表记录 > 已安装程序自身目录 > 默认目录
_install_override = ""
_registered_cache: str | None = None


def default_install_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", APP_FOLDER)


def with_app_folder(path: str) -> str:
    """用户选的目录 → 真正的安装目录（永远以 skdDriver 结尾）。

    已经叫 skdDriver 就原样返回，避免 `skdDriver\\skdDriver`；空串原样返回，
    交给 `validate_install_dir` 报「路径不能为空」。
    """
    text = normalize_install_dir(path)
    if not text:
        return ""
    if os.path.basename(text).lower() == APP_FOLDER.lower():
        return text
    return os.path.join(text, APP_FOLDER)


def preferred_install_dir() -> str:
    """安装器打开时预填哪个目录。

    已经是 skdDriver 的（这一版装过的）沿用原位置；老版本记着的是
    `...\\MouseGlass`，那个文件夹名过期了 —— 取它的**父目录**再接 skdDriver
    （`E:\\Program Files\\MouseGlass` → `E:\\Program Files\\skdDriver`）。
    这样用户当初特意装在别的盘 / 别的目录，升级不会被悄悄搬回 C 盘。
    """
    current = install_dir()
    if os.path.basename(current).lower() == APP_FOLDER.lower():
        return current
    parent = os.path.dirname(current)
    if parent and os.path.isdir(parent):
        return with_app_folder(parent)
    return default_install_dir()


def normalize_install_dir(path: str) -> str:
    """规范化安装目录：绝对路径、去掉尾部分隔符与引号。"""
    text = (path or "").strip().strip('"').strip()
    if not text:
        return ""
    text = os.path.expandvars(os.path.expanduser(text))
    try:
        text = os.path.abspath(text)
    except (OSError, ValueError):
        return ""
    while len(text) > 3 and text.endswith((os.sep, "/")):
        text = text[:-1]
    return text


def set_install_dir(path: str) -> None:
    """安装程序在内存里指定安装目录（优先于注册表记录）。"""
    global _install_override
    _install_override = normalize_install_dir(path)


def registered_install_dir() -> str:
    """注册表里记录的安装位置（用户可能改过路径）。"""
    global _registered_cache
    if _registered_cache is not None:
        return _registered_cache
    value = ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            raw, _ = winreg.QueryValueEx(key, "InstallLocation")
        value = normalize_install_dir(str(raw))
    except OSError:
        value = ""
    _registered_cache = value
    return value


def install_dir() -> str:
    """当前生效的安装目录。"""
    if _install_override:
        return _install_override
    recorded = registered_install_dir()
    if recorded:
        return recorded
    # 已安装的主程序 / 卸载程序：自身所在目录就是安装目录
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        if os.path.basename(exe).lower() in (APP_EXE.lower(), LEGACY_APP_EXE.lower(),
                                            UNINSTALL_EXE.lower()):
            return os.path.dirname(exe)
    return default_install_dir()


def installed_exe() -> str:
    return os.path.join(install_dir(), APP_EXE)


def is_installed() -> bool:
    return os.path.exists(installed_exe())


def validate_install_dir(path: str) -> str:
    """检查安装目录是否可用；返回空字符串表示没问题，否则是错误提示。

    提示按安装器当前语言生成（`i18n.T`）——安装器把语言切换放在自己脸上，
    校验失败的文案不能还钉死在中文。

    检查的是**最终目录**（`with_app_folder` 之后、真正落盘的那个），不是
    路径框里那串父目录：能不能写、是不是系统目录，跟多一层 skdDriver 无关。
    传进来的已经带 skdDriver 时（路径框里的值）这一步是幂等的。
    """
    text = normalize_install_dir(path)
    if not text:
        return i18n.T("安装路径不能为空")
    if not os.path.isabs(text):
        return i18n.T("安装路径必须是绝对路径")
    target = with_app_folder(text)
    drive = os.path.splitdrive(target)[0]
    if drive and target.rstrip("\\/").lower() == drive.rstrip("\\/").lower():
        return i18n.T("不能直接装到磁盘根目录，请选择其中的子文件夹")
    forbidden = {
        os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")),
        os.path.normcase(os.environ.get("ProgramFiles", "")),
        os.path.normcase(os.environ.get("ProgramFiles(x86)", "")),
        os.path.normcase(os.environ.get("ProgramData", "")),
        os.path.normcase(os.path.expanduser("~")),
    }
    if os.path.normcase(target) in {f for f in forbidden if f}:
        return i18n.T("请勿直接安装到系统目录，请选择子文件夹")
    try:
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, ".mouseglass_write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as exc:
        reason = getattr(exc, "strerror", "") or str(exc)
        return i18n.T("该目录无法写入：{reason}").format(reason=reason)
    return ""


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MouseGlass")


def config_file() -> str:
    """主程序的配置文件（与 `config.py` 的 CONFIG_PATH 同一个位置）。"""
    return os.path.join(config_dir(), "config.json")


def read_config_language() -> str:
    """已装版本用的界面语言；没装过 / 没这个字段就返回空串。"""
    try:
        with open(config_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return ""
    value = str((data or {}).get("language") or "") if isinstance(data, dict) else ""
    return value if value in i18n.LANGS else ""


def write_config_language(code: str) -> bool:
    """把安装器里选的语言写进主程序配置，装完启动就是这套语言。

    只改 `language` 一个键，其余配置（窗口位置、阈值、设备记忆…）原样保留，
    升级安装不会把用户调好的设置抹掉。写失败不抛——语言写不进去不该让安装失败。
    """
    if code not in i18n.LANGS:
        return False
    path = config_file()
    data: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    data["language"] = code
    tmp = path + ".tmp"
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


# --------------------------------------------------------------------------- 已知文件夹
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    guid = _GUID()
    ctypes.memmove(ctypes.byref(guid), uuid.UUID(text).bytes_le, 16)
    return guid


def known_folder(folder_id: str) -> str:
    try:
        path = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(_guid(folder_id)), 0, None, ctypes.byref(path)
        )
        if result != 0 or not path.value:
            return ""
        value = path.value
        ctypes.windll.ole32.CoTaskMemFree(path)
        return value
    except Exception:  # noqa: BLE001
        return ""


def start_menu_link() -> str:
    programs = known_folder(FOLDERID_PROGRAMS) or os.path.join(
        os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"
    )
    return os.path.join(programs, f"{SHORTCUT_NAME}.lnk")


def desktop_link() -> str:
    desktop = known_folder(FOLDERID_DESKTOP) or os.path.join(
        os.path.expanduser("~"), "Desktop"
    )
    return os.path.join(desktop, f"{SHORTCUT_NAME}.lnk")


#: 软件改名前的旧快捷方式名——升级安装时顺手清掉，免得开始菜单里出现两个入口
LEGACY_SHORTCUT_NAMES = ("鼠标信息提示.lnk",)


def create_shortcut(link: str, target: str, workdir: str = "", desc: str = "") -> bool:
    """用 WScript.Shell 生成 .lnk（路径通过环境变量传入，避免中文转义问题）。"""
    try:
        os.makedirs(os.path.dirname(link), exist_ok=True)
    except OSError:
        pass
    env = dict(os.environ)
    env.update(
        MG_LINK=link,
        MG_TARGET=target,
        MG_WORKDIR=workdir or os.path.dirname(target),
        MG_DESC=desc or DISPLAY_NAME,
    )
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:MG_LINK);"
        "$s.TargetPath=$env:MG_TARGET;"
        "$s.WorkingDirectory=$env:MG_WORKDIR;"
        "$s.IconLocation=($env:MG_TARGET + ',0');"
        "$s.Description=$env:MG_DESC;"
        "$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            env=env,
            creationflags=CREATE_NO_WINDOW,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.exists(link)


def remove_shortcuts() -> None:
    for link in (start_menu_link(), desktop_link()):
        try:
            if os.path.exists(link):
                os.remove(link)
        except OSError:
            pass


def remove_legacy_shortcuts() -> None:
    """清掉改名前遗留的快捷方式（开始菜单 + 桌面）。"""
    for folder in (os.path.dirname(start_menu_link()), os.path.dirname(desktop_link())):
        for name in LEGACY_SHORTCUT_NAMES:
            link = os.path.join(folder, name)
            try:
                if os.path.exists(link):
                    os.remove(link)
            except OSError:
                pass


# --------------------------------------------------------------------------- 注册表
def uninstall_command(target: str = "") -> tuple[str, str]:
    """返回 (卸载命令, 静默卸载命令)。

    装了独立卸载程序就用它（哪怕主程序被删坏也能卸掉）；老安装包没有
    uninstall.exe，退回「主程序 --uninstall」。
    """
    target = normalize_install_dir(target) or install_dir()
    uninstaller = os.path.join(target, UNINSTALL_EXE)
    if os.path.exists(uninstaller):
        return f'"{uninstaller}"', f'"{uninstaller}" /quiet'
    exe = os.path.join(target, APP_EXE)
    return f'"{exe}" --uninstall', f'"{exe}" --uninstall --quiet'


def register_uninstall(target: str = "") -> None:
    import winreg

    global _registered_cache

    target = normalize_install_dir(target) or install_dir()
    exe = os.path.join(target, APP_EXE)
    uninstall_cmd, quiet_cmd = uninstall_command(target)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            values = {
                "DisplayName": DISPLAY_NAME,
                "DisplayVersion": VERSION,
                "Publisher": PUBLISHER,
                "Comments": DESCRIPTION,
                "DisplayIcon": f"{exe},0",
                "InstallLocation": target,
                "UninstallString": uninstall_cmd,
                "QuietUninstallString": quiet_cmd,
                "NoModify": 1,
                "NoRepair": 1,
                "EstimatedSize": max(1, tree_size_kb(target)),
            }
            for name, value in values.items():
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, kind, value)
        _registered_cache = target
    except OSError:
        pass


def unregister_uninstall() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


# --------------------------------------------------------------------------- 安装动作
def kill_running() -> None:
    """新旧两个进程名都要杀。

    从旧版升级时，正在跑的仍是 `MouseGlass.exe`；只按新名字 taskkill 会让它
    一直占着安装目录，接着复制 `_internal` 就会失败（或者装完了旧进程还挂着
    旧代码，用户看到的仍是旧界面）。
    """
    for name in (APP_EXE, LEGACY_APP_EXE):
        try:
            subprocess.run(["taskkill", "/F", "/IM", name], creationflags=CREATE_NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                           check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def wait_process_gone(name: str = "", timeout: float = 8.0) -> bool:
    """等新旧两个进程都退出；传了 name 就只等那一个（测试/特殊场景用）。"""
    names = (name,) if name else (APP_EXE, LEGACY_APP_EXE)
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = False
        for item in names:
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {item}"],
                    capture_output=True, text=True, errors="replace",
                    creationflags=CREATE_NO_WINDOW, timeout=10, check=False,
                )
                if item.lower() in (result.stdout or "").lower():
                    alive = True
            except (OSError, subprocess.SubprocessError):
                pass
        if not alive:
            return True
        time.sleep(0.25)
    return False


def icon_dir(target_dir: str) -> str:
    """图标所在目录：跟着 Qt 运行时一起待在 `_internal\\assets` 里。

    安装根目录只留 `skdDriver.exe` / `uninstall.exe` / `_internal` 三样东西，
    不再把一个孤零零的 `app.ico` 摆在用户面前（v1.4.8）。
    """
    internal = os.path.join(target_dir, "_internal")
    if os.path.isdir(internal):
        return os.path.join(internal, "assets")
    # 老的单文件安装（没有 _internal）退到独立 assets 子目录，同样不放根目录
    return os.path.join(target_dir, "assets")


def copy_app_icon(target_dir: str) -> None:
    """把图标安置到 `_internal/assets`，供通知/任务栏使用（AUMID 的 IconUri）。

    onedir 产物里 PyInstaller 已经把 `assets/app.ico` 打进 `_internal` 了，
    这里只在缺失时补一份（老包 / 单文件包），**绝不往安装根目录写**。
    """
    dest_dir = icon_dir(target_dir)
    dest = os.path.join(dest_dir, "app.ico")
    if os.path.exists(dest):
        return
    candidates = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, "assets", "app.ico"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates += [
        os.path.join(here, "..", "assets", "app.ico"),
        os.path.join(here, "assets", "app.ico"),
    ]
    for src in candidates:
        if os.path.exists(src):
            try:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(src, dest)
            except OSError:
                pass
            return


def remove_stray_icon(target_dir: str) -> None:
    """删掉旧版本遗留在安装根目录的 `app.ico`（升级安装时清掉）。"""
    for name in ("app.ico",):
        try:
            item = os.path.join(target_dir, name)
            if os.path.exists(item):
                os.remove(item)
        except OSError:
            pass



def remove_dir_later(path: str) -> None:
    """进程退出后再删除安装目录。"""
    try:
        subprocess.Popen(
            ["cmd", "/c", "ping", "-n", "3", "127.0.0.1", ">nul", "&", "rmdir", "/s", "/q", path],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def cleanup_old_install(path: str) -> None:
    """旧安装目录里只删我们放进去的东西，其余内容保留。"""
    if not path or not os.path.isdir(path):
        return
    internal = os.path.join(path, "_internal")
    if os.path.isdir(internal):        # onedir 版的 Qt 运行时
        shutil.rmtree(internal, ignore_errors=True)
    for name in (APP_EXE, LEGACY_APP_EXE, UNINSTALL_EXE, "app.ico"):
        try:
            item = os.path.join(path, name)
            if os.path.exists(item):
                os.remove(item)
        except OSError:
            pass
    # 单文件安装遗留的图标目录：删掉图标后目录空了才清（用户自己往里面放过东西就留着）
    assets = os.path.join(path, "assets")
    try:
        if os.path.isdir(assets):
            try:
                os.remove(os.path.join(assets, "app.ico"))
            except OSError:
                pass
            os.rmdir(assets)
    except OSError:
        pass
    try:
        os.rmdir(path)  # 只有空目录才删得掉
    except OSError:
        pass


def refresh_shell_icons() -> None:
    """通知桌面刷新图标缓存：exe 内嵌图标换了内容后，桌面/开始菜单
    常常仍显示旧缓存（Windows 按 .lnk 路径缓存图标位图），必须广播
    SHCNE_ASSOCCHANGED 才会重读。失败静默——刷新只是锦上添花。
    """
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass


def tree_size_kb(path: str) -> int:
    """安装目录整棵树的大小（KB）。多文件版含 `_internal`，只算 exe 会差十倍。"""
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return int(total / 1024)


def _copy_payload(payload: str, target: str) -> str:
    """把安装包里的 payload 落到安装目录，返回主程序路径。

    两种 payload 都认：
      * **目录**（v1.4.5 起，onedir 多文件）——整树复制，`_internal` 里是 Qt 运行时；
        复制前先清掉旧的 `_internal`，否则跨版本升级会残留上一版的插件/DLL；
      * **单个 exe**（老的单文件包）——直接覆盖复制，兼容旧安装包。
    文件被占用时重试，装完立刻启动的场景下 12 次足够。
    """
    exe = os.path.join(target, APP_EXE)
    os.makedirs(target, exist_ok=True)
    if os.path.isdir(payload):
        internal = os.path.join(target, "_internal")
        if os.path.isdir(internal):
            shutil.rmtree(internal, ignore_errors=True)
        for attempt in range(12):
            try:
                shutil.copytree(payload, target, dirs_exist_ok=True)
                break
            except OSError:
                if attempt == 11:
                    raise
                time.sleep(0.35)
        return exe

    for attempt in range(12):
        try:
            shutil.copy2(payload, exe)
            break
        except OSError:
            if attempt == 11:
                raise
            time.sleep(0.35)
    return exe


def perform_install(payload: str, autostart_on: bool = True, desktop: bool = True,
                   target_dir: str = "") -> str:
    """把主程序装到指定目录，并写好快捷方式、自启动与卸载信息。

    `target_dir` 是**父目录**：真正落盘的位置永远是 `<父目录>\\skdDriver`
    （`with_app_folder`）。调用方传进来的已经是 skdDriver 时原样使用，
    静默安装 `--dir D:\\Apps` 和界面里选目录得到同一个结果。
    """
    if not payload or not os.path.exists(payload):
        raise RuntimeError(i18n.T("安装包不完整，缺少主程序文件"))

    import autostart as autostart_mod

    target = with_app_folder(normalize_install_dir(target_dir) or preferred_install_dir())
    previous = registered_install_dir() or default_install_dir()

    kill_running()
    wait_process_gone()
    os.makedirs(target, exist_ok=True)
    exe = _copy_payload(payload, target)
    if not os.path.exists(exe):
        raise RuntimeError(i18n.T("主程序没有落到安装目录"))
    # 就地升级（新旧同一目录）时 copytree(dirs_exist_ok=True) 只覆盖不删除，
    # 旧版那个 MouseGlass.exe 会一直留在安装目录里 —— 用户会看到两个「主程序」，
    # 还可能双击到旧的那个。新 exe 落盘确认后立刻清掉它。
    legacy_exe = os.path.join(target, LEGACY_APP_EXE)
    if os.path.normcase(legacy_exe) != os.path.normcase(exe) and os.path.exists(legacy_exe):
        try:
            os.remove(legacy_exe)
        except OSError:
            pass

    register_uninstall(target)
    create_shortcut(start_menu_link(), exe)
    copy_app_icon(target)
    remove_stray_icon(target)      # 旧版把 app.ico 放在根目录，升级时清掉
    remove_legacy_shortcuts()

    desktop_link_path = desktop_link()
    if desktop:
        create_shortcut(desktop_link_path, exe)
    elif os.path.exists(desktop_link_path):
        try:
            os.remove(desktop_link_path)
        except OSError:
            pass

    if autostart_on:
        # 自启动只驻留托盘、不弹主窗口（与设置浮层里的开关行为一致）
        autostart_mod.set_command(f'"{exe}" {autostart_mod.TRAY_ARG}')
    else:
        autostart_mod.clear()

    refresh_shell_icons()
    # 换了安装位置：把旧目录里的旧版本清掉（只删自己的文件）
    if previous and os.path.normcase(previous) != os.path.normcase(target):
        cleanup_old_install(previous)
    return exe


def default_payload() -> str:
    """安装包内被安装的 payload 路径。

    v1.4.5 起 payload 是**目录**（主程序 exe + `_internal`），返回这个目录；
    老的单文件安装包仍返回 exe 路径，两种包都能装。主程序 exe 新旧两个名字
    都认，免得拿旧包 / 旧产物调试时找不到 payload。
    """
    names = (APP_EXE, LEGACY_APP_EXE)
    base = getattr(sys, "_MEIPASS", None)
    if base:
        root = os.path.join(base, "payload")
        if os.path.isdir(root) and any(os.path.exists(os.path.join(root, n)) for n in names):
            return root          # 目录式 payload（v1.4.5+）；老包的 payload 也是目录
        if os.path.isfile(root):
            return root          # 极端情况：payload 本身就是一个 exe
        for name in names:
            single = os.path.join(root, name)
            if os.path.isfile(single):
                return single
    here = os.path.dirname(os.path.abspath(__file__))
    # 开发态：优先 onedir 产物目录（dist/skd），其次老的单文件产物
    folder = os.path.join(here, "..", "dist", "skd")
    if any(os.path.exists(os.path.join(folder, n)) for n in names):
        return os.path.abspath(folder)
    for candidate in tuple(
        os.path.join(here, "..", "dist", n) for n in names
    ) + tuple(
        os.path.join(here, "..", n) for n in names
    ):
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return ""
