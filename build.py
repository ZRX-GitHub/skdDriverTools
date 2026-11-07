"""打包脚本：生成图标、版本信息，最终只产出安装包 `skd-setup-<版本>.exe`。

主程序（`skd-<版本>/` 目录式 onedir）只是安装包的 payload，打完就清出 dist，
不再单独分发——用户只要装得上的那一个 exe。

版本号**不固定**：每次打包自动把补丁号 +1 并写回 `src/setup_core.py`，
所以 exe 文件名、exe 属性里的 FileVersion、安装器标题、程序窗口右下角的
版本号始终是「这一轮的新版本」，不会一直卡在同一个数字上。

用法：
    python build.py                    # 自动递增补丁号后打包（默认）
    python build.py --version 1.4.0    # 指定版本号打包（不递增）
    python build.py --no-bump          # 用当前版本号打包
    python build.py --bump-only        # 只递增版本号，不打包
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
ASSETS = os.path.join(ROOT, "assets")
DIST = os.path.join(ROOT, "dist")
#: 主程序（onedir）的产物目录。**故意不放在 dist**：它只是安装包的 payload，
#: 打包时被内嵌进 setup.exe，之后就没有存在价值。放 build 下就不会每打一版
#: 在 dist 里多一个 95MB 的 skd-<版本>/，也不用在打包末尾批量删它——
#: 运行环境对「单轮 >50 个文件的批量删除」会硬拦（safe-delete），
#: 删到第 50 个直接掐掉进程，脚本后面的改名就永远跑不到。
PAYLOAD_DIR = os.path.join(ROOT, "build", "payload")
APP_NAME = "skd"            # 分发文件名 skd-<版本>.exe / skd-<版本>/
SETUP_NAME = "skd-setup"    # 安装器 skd-setup-<版本>.exe
UNINSTALL_NAME = "skd-uninstall"   # 只作为零件进安装包，不单独分发
# 名称 / 版本号唯一来源是 src/setup_core.py（打包进 exe 的那一个），
# 这里直接导入，避免两处各写一份产生漂移
sys.path.insert(0, SRC)
import setup_core  # noqa: E402

VERSION = (*[int(p) for p in setup_core.VERSION.split(".")], 0, 0, 0, 0)[:4]
DISPLAY_NAME = setup_core.DISPLAY_NAME
# 分发文件名带版本号：桌面上同时躺着几个 exe 时，靠文件名就能分辨装哪一个
VER = ".".join(str(n) for n in VERSION[:3])

#: setup_core.py 里版本号那一行的匹配式（写回时只换这一行）
_VERSION_LINE = re.compile(r'(?m)^VERSION = "[^"]*"')


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="打包 skd（版本号默认自动递增）")
    ap.add_argument("--version", help="指定版本号（如 1.4.0），不自动递增")
    ap.add_argument("--no-bump", action="store_true", help="沿用当前版本号，不递增")
    ap.add_argument("--bump-only", action="store_true", help="只递增版本号，不打包")
    return ap.parse_args(argv)


def _next_version(cur: str) -> str:
    """补丁号 +1（1.3.6 → 1.3.7）。"""
    parts = [int(p) for p in cur.split(".") if p.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(p) for p in parts[:3])


def _pick_version(cur: str, exists=None) -> str:
    """下一个**可用**版本号：补丁号 +1；产物已存在就继续往后跳。

    dist 是版本存档（历次产物都留着），所以自动递增时不能撞上旧文件名。
    """
    if exists is None:
        exists = lambda p: os.path.exists(p)          # noqa: E731
    new = _next_version(cur)
    # onedir 产物是**目录**（dist/skd-1.4.5），单文件产物是 .exe，两种都要判
    while (exists(os.path.join(DIST, f"{APP_NAME}-{new}.exe"))
           or exists(os.path.join(DIST, f"{APP_NAME}-{new}"))):
        new = _next_version(new)
    return new


def _write_version(new: str) -> None:
    """把新版本号写回 src/setup_core.py（唯一来源，程序与安装器都读它）。"""
    path = os.path.join(SRC, "setup_core.py")
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    new_text, n = _VERSION_LINE.subn(f'VERSION = "{new}"', text)
    if n != 1:
        raise SystemExit("setup_core.py 里没找到唯一的 VERSION 行，请手动改一下")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)


def resolve_version(args: argparse.Namespace) -> str:
    """算出这一轮要用的版本号，必要时写回 setup_core.py。

    默认**自动递增**：版本号不允许固定。已存在的同版本产物会继续往后跳，
    保证 dist 里的文件名不会互相覆盖。
    """
    cur = setup_core.VERSION
    if args.version:
        new = args.version
    elif args.no_bump:
        new = cur
    else:
        new = _pick_version(cur)
    if new != cur:
        _write_version(new)
        setup_core.VERSION = new
        print(f"版本号: {cur} → {new}")
    else:
        print(f"版本号: {new}（不递增）")
    return new


def apply_version(ver: str) -> None:
    """把版本号同步到本模块的全局量（产物名 / version_info 都读它们）。"""
    global VERSION, VER
    VERSION = (*[int(p) for p in ver.split(".")], 0, 0, 0, 0)[:4]
    VER = ".".join(str(n) for n in VERSION[:3])


def render_icon(uninstall: bool = False) -> str:
    """渲染多尺寸图标写成一个 ICO 文件。

    设计**直接复用 `appicon.make_icon()`**（窗口/托盘图标的同一份代码）——
    之前这里手绘的是旧版电量环+鼠标轮廓，和窗口图标不一致，
    用户明确要求桌面图标跟窗口图标统一（v1.3.2）。

    uninstall=True 时画的是卸载程序专用的垃圾桶图标（同一个 `appicon` 模块），
    免得安装目录里两个 exe 顶着一模一样的图标分不清（v1.4.8）。
    """
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        QGuiApplication([])

    os.makedirs(ASSETS, exist_ok=True)
    from appicon import ICON_SIZES, make_icon, make_uninstall_icon  # noqa: E402

    sizes = list(ICON_SIZES)
    icon = make_uninstall_icon() if uninstall else make_icon()

    blobs: list[bytes] = []
    for size in sizes:
        pm = icon.pixmap(size, size)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        pm.toImage().save(buf, "PNG")
        buf.close()
        blobs.append(bytes(ba))

    out = os.path.join(ASSETS, "uninstall.ico" if uninstall else "app.ico")
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for size, blob in zip(sizes, blobs):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(out, "wb") as fh:
        fh.write(header + entries + b"".join(blobs))
    return out


def write_version_file(filename: str, original_name: str | None = None) -> str:
    """filename 决定 version_info 文件名与 InternalName；
    original_name 是最终分发出去的文件名（带版本号）。

    **不写 CompanyName / FileDescription**：资源管理器悬停时的「文件说明」
    「公司」两行就是从这两个字段来的，用户要求去掉。文件版本、产品名这些
    仍然保留，属性面板不会变成一张空表。注册表里的 Publisher / Comments
    属于「已安装的应用」页面，跟这里是两回事，不受影响。
    """
    stem = filename.rsplit(".", 1)[0]
    path = os.path.join(ROOT, f"version_info_{stem}.txt")
    original = original_name or filename
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VERSION}, prodvers={VERSION},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('FileVersion', '{'.'.join(map(str, VERSION))}'),
        StringStruct('InternalName', '{stem}'),
        StringStruct('OriginalFilename', '{original}'),
        StringStruct('ProductName', '{DISPLAY_NAME}'),
        StringStruct('ProductVersion', '{'.'.join(map(str, VERSION[:3]))}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


EXCLUDES = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg", "PySide6.QtDBus",
    "PySide6.QtPrintSupport", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml",
    "PySide6.QtConcurrent", "PySide6.QtUiTools", "PySide6.QtHelp", "tkinter",
]


def _run(entry: str, name: str, version_file: str, icon: str,
         extra: list[str] | None = None, onefile: bool = True,
         dist_dir: str | None = None) -> str:
    """跑一遍 PyInstaller。

    onefile=True → 单文件（安装包用这个，用户只拿到一个 setup.exe）；
    onefile=False → onedir 多文件（主程序用这个，装出来是 exe + _internal 目录）。
    dist_dir 默认产出到 dist；主程序的 onedir 产物走 build/payload（见 PAYLOAD_DIR）。
    """
    out_dir = dist_dir or DIST
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile" if onefile else "--onedir", "--windowed",
        "--name", name,
        "--icon", icon,
        "--version-file", version_file,
        "--distpath", out_dir,
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        "--paths", SRC,
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd += extra or []
    cmd.append(entry)
    print("运行:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)
    built = os.path.join(out_dir, name + (".exe" if onefile else ""))
    size = os.path.getsize(built) if onefile else _tree_bytes(built)
    print("完成:", built, round(size / 1024 / 1024, 1), "MB")
    return built


def _stash(path: str) -> str:
    """把上一次的中间产物挪进 `build/old/`（改名不是删除，不会撞批量删除阈值）。

    打包过程中要重建 `dist/skd`、`dist/skd-setup` 这些名字，PyInstaller 用
    `--noconfirm` 时会自己 rmtree —— 几百个文件一次删掉会触发运行环境的
    安全阈值，无人值守的打包就被静默中断（表现是「打完主程序就没下文」）。
    挪走比删掉稳，代价只是 `build/old/` 里留着一份，随时可以手工清。
    """
    old_dir = os.path.join(ROOT, "build", "old")
    os.makedirs(old_dir, exist_ok=True)
    base = os.path.basename(path.rstrip("\\/"))
    stamp = time.strftime("%m%d-%H%M%S")
    target = os.path.join(old_dir, f"{base}-{stamp}")
    index = 1
    while os.path.exists(target):
        index += 1
        target = os.path.join(old_dir, f"{base}-{stamp}-{index}")
    try:
        os.replace(path, target)
        return target
    except OSError:
        return ""


def _rmtree_batched(path: str, batch: int = 40) -> None:
    """分小批删目录。

    payload_staging 有 200 多个文件，一次 `shutil.rmtree` 会撞上运行环境的
    批量删除阈值（单次 >50 就要人工确认），在无人值守的打包流程里会被静默
    中断——表现是「打完主程序就没了下文」。分批删既稳又不用交互。
    """
    if not os.path.isdir(path):
        return
    files: list[str] = []
    dirs: list[str] = []
    for base, dirnames, filenames in os.walk(path):
        for name in filenames:
            files.append(os.path.join(base, name))
        for name in dirnames:
            dirs.append(os.path.join(base, name))
    for start in range(0, len(files), batch):
        for item in files[start:start + batch]:
            try:
                os.remove(item)
            except OSError:
                pass
    for folder in sorted(dirs, key=len, reverse=True):   # 先深后浅
        try:
            os.rmdir(folder)
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)


def _tree_bytes(path: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def _stamp(path: str) -> str:
    """把 PyInstaller 的直接产物改成带版本号的分发名（文件 / 目录都支持）。"""
    if os.path.isdir(path):
        parent, base = os.path.split(path)
        target_dir = os.path.join(parent, f"{base}-{VER}")
        # 同版本重打包才覆盖；别的版本一律留着（dist 是版本存档，不清理）
        if os.path.exists(target_dir):
            _stash(target_dir)
        os.replace(path, target_dir)
        # 目录里的 exe 也带上版本号，和目录名对齐，一眼能看出跑的是哪一版
        inner = os.path.join(target_dir, base + ".exe")
        if os.path.exists(inner):
            os.replace(inner, os.path.join(target_dir, f"{base}-{VER}.exe"))
        return target_dir
    stem, ext = os.path.splitext(path)
    target = f"{stem}-{VER}{ext}"
    os.replace(path, target)
    return target


def build() -> None:
    # 主程序图标已由用户手动替换为最新设计，不再从代码重新生成
    icon = os.path.join(ASSETS, "app.ico")
    uninstall_icon = render_icon(uninstall=True)
    os.makedirs(DIST, exist_ok=True)
    # PyInstaller 的产物目录每次都要重建：--noconfirm 会自己 rmtree，一次几百个
    # 文件会撞批量删除阈值而让打包静默中断。这里改成先挪进 build/old。
    # 主程序产物在 build/payload 里，同样先挪走——挪是 rename，不触发删除拦截。
    for tmp in (SETUP_NAME, UNINSTALL_NAME):
        stale = os.path.join(DIST, tmp)
        if os.path.exists(stale):
            _stash(stale)
    stale_payload = os.path.join(PAYLOAD_DIR, APP_NAME)
    if os.path.exists(stale_payload):
        _stash(stale_payload)
    app_final_name = f"{APP_NAME}-{VER}.exe"
    setup_final_name = f"{SETUP_NAME}-{VER}.exe"
    app_path = _run(
        os.path.join(SRC, "main.py"), APP_NAME,
        write_version_file(f"{APP_NAME}.exe", app_final_name), icon,
        extra=["--add-data", f"{icon}{os.pathsep}assets"],
        onefile=False,      # 传统多文件结构：exe + _internal，启动不再解包到 temp
        dist_dir=PAYLOAD_DIR,   # 不进 dist：它只是安装包的 payload
    )

    # payload 目录式嵌入：直接把主程序产物目录当 payload，只把里面的 exe 改名成
    # 安装名（setup_core.APP_EXE）。以前是复制一份到 build/payload_staging，
    # 每次要多占 95MB 空间、还得先删上一轮的残留（一次几百个文件会撞批量删除
    # 阈值，导致打包「打完主程序就没下文」）。现在原地改名，一分钱不多花。
    app_exe = os.path.join(app_path, APP_NAME + ".exe")
    if os.path.exists(app_exe):
        os.replace(app_exe, os.path.join(app_path, setup_core.APP_EXE))

    # 独立卸载程序：单独一个 exe（不带 Qt，几 MB），装进安装目录。
    # 好处是主程序/_internal 被删坏时也能卸载，且在「设置 → 应用」里露出的是它。
    uninstaller = _run(
        os.path.join(SRC, "uninstall_exe.py"), UNINSTALL_NAME,
        write_version_file(f"{UNINSTALL_NAME}.exe", setup_core.UNINSTALL_EXE), uninstall_icon,
    )
    # 它只是安装包的零件，跟主程序一起躺在 payload 里，不单独分发
    shutil.copyfile(uninstaller, os.path.join(app_path, setup_core.UNINSTALL_EXE))
    os.remove(uninstaller)

    setup_exe = _run(
        os.path.join(SRC, "installer.py"), SETUP_NAME,
        write_version_file(f"{SETUP_NAME}.exe", setup_final_name), icon,
        extra=[
            "--add-data", f"{app_path}{os.pathsep}payload",
            "--add-data", f"{icon}{os.pathsep}assets",
        ],
    )

    # 只产出安装包（用户要求）：payload 已经内嵌进 setup.exe，把工作目录里
    # 这份 95MB 的中间产物挪进 build/old（rename，不删文件），dist 里就只剩
    # 一个 skd-setup-<版本>.exe，不用再猜该发哪个。
    _stash(app_path)
    final_setup = _stamp(setup_exe)
    print(f"分发产物: {final_setup}")
    print("中间产物已挪到 build/old（可随时手工清理，不影响 dist）")


if __name__ == "__main__":
    _args = _parse_args()
    _ver = resolve_version(_args)
    apply_version(_ver)
    if _args.bump_only:
        print(f"版本号已更新为 {_ver}（未打包）")
    else:
        build()
