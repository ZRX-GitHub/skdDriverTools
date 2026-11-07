"""安装落盘逻辑回归测试（纯文件操作，不碰注册表、不装真机）。

v1.4.5 把主程序从单文件改成 onedir 多文件结构，安装包里的 payload 变成
「目录」。这里锁住两条路径都还能装：目录 payload（新版）与单 exe payload（旧包兼容），
以及旧目录清理不会漏掉 `_internal`。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import setup_core  # noqa: E402

#: 主程序自带的卸载入口（老安装包没有 uninstall.exe 时用它）
APP_UNINSTALL_FLAG = "--uninstall"


def _make_payload(root: str) -> str:
    """造一个 onedir 风格的 payload 目录：exe + _internal/PySide6/*.dll。"""
    payload = os.path.join(root, "payload")
    os.makedirs(os.path.join(payload, "_internal", "PySide6"), exist_ok=True)
    with open(os.path.join(payload, setup_core.APP_EXE), "wb") as fh:
        fh.write(b"MZ fake exe")
    with open(os.path.join(payload, "_internal", "base_library.zip"), "wb") as fh:
        fh.write(b"zip")
    with open(os.path.join(payload, "_internal", "PySide6", "Qt6Core.dll"), "wb") as fh:
        fh.write(b"dll" * 100)
    return payload


class PayloadInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="skd_install_")
        self.target = os.path.join(self.tmp, "MouseGlass")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dir_payload_copies_whole_tree(self) -> None:
        payload = _make_payload(self.tmp)
        exe = setup_core._copy_payload(payload, self.target)
        self.assertEqual(os.path.normcase(exe),
                         os.path.normcase(os.path.join(self.target, setup_core.APP_EXE)))
        self.assertTrue(os.path.isfile(exe))
        # Qt 运行时必须一起落地，否则 onedir 的 exe 起不来
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "_internal", "PySide6", "Qt6Core.dll")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "_internal", "base_library.zip")))

    def test_dir_payload_drops_stale_internal(self) -> None:
        """跨版本升级要先清旧 `_internal`，否则上一版的 DLL 会残留。"""
        payload = _make_payload(self.tmp)
        os.makedirs(os.path.join(self.target, "_internal"), exist_ok=True)
        stale = os.path.join(self.target, "_internal", "OldPlugin.dll")
        with open(stale, "wb") as fh:
            fh.write(b"old")
        setup_core._copy_payload(payload, self.target)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "_internal", "PySide6", "Qt6Core.dll")))

    def test_dir_payload_installs_uninstaller(self) -> None:
        """payload 里的 uninstall.exe 要跟着落到安装目录。"""
        payload = _make_payload(self.tmp)
        with open(os.path.join(payload, setup_core.UNINSTALL_EXE), "wb") as fh:
            fh.write(b"MZ uninst")
        setup_core._copy_payload(payload, self.target)
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, setup_core.UNINSTALL_EXE)))
        # 有独立卸载程序时，「设置 → 应用」里就指向它
        cmd, quiet = setup_core.uninstall_command(self.target)
        self.assertIn(setup_core.UNINSTALL_EXE, cmd)
        self.assertNotIn(APP_UNINSTALL_FLAG, cmd)
        self.assertIn(setup_core.UNINSTALL_EXE, quiet)

    def test_uninstall_command_falls_back_to_app(self) -> None:
        """老安装包没有 uninstall.exe，退回「主程序 --uninstall」。"""
        os.makedirs(self.target, exist_ok=True)
        with open(os.path.join(self.target, setup_core.APP_EXE), "wb") as fh:
            fh.write(b"x")
        cmd, quiet = setup_core.uninstall_command(self.target)
        self.assertIn(setup_core.APP_EXE, cmd)
        self.assertIn(APP_UNINSTALL_FLAG, cmd)
        self.assertIn(APP_UNINSTALL_FLAG, quiet)

    def test_cleanup_removes_uninstaller(self) -> None:
        os.makedirs(self.target, exist_ok=True)
        for name in (setup_core.APP_EXE, setup_core.UNINSTALL_EXE, "app.ico"):
            with open(os.path.join(self.target, name), "wb") as fh:
                fh.write(b"x")
        setup_core.cleanup_old_install(self.target)
        self.assertFalse(os.path.exists(self.target))

    def test_legacy_single_exe_payload_still_works(self) -> None:
        """老的单文件安装包（payload 就是一个 exe）要能继续装。"""
        legacy = os.path.join(self.tmp, "legacy.exe")
        with open(legacy, "wb") as fh:
            fh.write(b"MZ single file")
        exe = setup_core._copy_payload(legacy, self.target)
        self.assertTrue(os.path.isfile(exe))
        with open(exe, "rb") as fh:
            self.assertEqual(fh.read(), b"MZ single file")

    def test_tree_size_counts_internal(self) -> None:
        payload = _make_payload(self.tmp)
        with open(os.path.join(payload, "_internal", "PySide6", "Qt6Core.dll"), "wb") as fh:
            fh.write(b"d" * 4096)      # 大小要够，否则 int(KB) 会取整成 0
        # 只算 exe 会得到 0KB；带上 _internal 才能到 4KB
        self.assertGreaterEqual(setup_core.tree_size_kb(payload), 4)

    def test_cleanup_old_install_removes_internal(self) -> None:
        os.makedirs(os.path.join(self.target, "_internal"), exist_ok=True)
        for rel in (setup_core.APP_EXE, "app.ico",
                    os.path.join("_internal", "x.dll")):
            with open(os.path.join(self.target, rel), "wb") as fh:
                fh.write(b"x")
        setup_core.cleanup_old_install(self.target)
        # 全是我们的东西 → 整个目录应该被删干净
        self.assertFalse(os.path.exists(self.target))

    def test_cleanup_keeps_user_files(self) -> None:
        os.makedirs(self.target, exist_ok=True)
        with open(os.path.join(self.target, setup_core.APP_EXE), "wb") as fh:
            fh.write(b"x")
        mine = os.path.join(self.target, "我的笔记.txt")
        with open(mine, "w", encoding="utf-8") as fh:
            fh.write("别删我")
        setup_core.cleanup_old_install(self.target)
        self.assertTrue(os.path.exists(mine))
        self.assertFalse(os.path.exists(os.path.join(self.target, setup_core.APP_EXE)))


class IconPlacementTests(unittest.TestCase):
    """v1.4.8：`app.ico` 收进 `_internal/assets`，安装根目录只剩 exe 和 `_internal`。

    用户明确要求根目录别摆图标文件——它只是通知/任务栏要用的资源，
    不是给人点的东西，混在两个 exe 中间只会让人以为那是快捷方式。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="skd_icon_")
        os.makedirs(os.path.join(self.tmp, "assets"), exist_ok=True)
        with open(os.path.join(self.tmp, "assets", "app.ico"), "wb") as fh:
            fh.write(b"ico-bytes")
        self.target = os.path.join(self.tmp, "MouseGlass")
        os.makedirs(os.path.join(self.target, "_internal", "PySide6"), exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_icon_goes_into_internal_assets(self) -> None:
        with mock.patch.object(sys, "_MEIPASS", self.tmp, create=True):
            setup_core.copy_app_icon(self.target)
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "_internal", "assets", "app.ico")))
        self.assertFalse(os.path.exists(os.path.join(self.target, "app.ico")))

    def test_existing_icon_is_not_overwritten(self) -> None:
        """PyInstaller 已经把图标打进 `_internal/assets`，别每次安装都重写。"""
        dest = os.path.join(self.target, "_internal", "assets", "app.ico")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"packed")
        with mock.patch.object(sys, "_MEIPASS", self.tmp, create=True):
            setup_core.copy_app_icon(self.target)
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"packed")

    def test_single_file_install_uses_assets_subdir(self) -> None:
        """没有 `_internal` 的老式单文件安装，图标进 `assets\\`，同样不进根目录。"""
        legacy = os.path.join(self.tmp, "Legacy")
        os.makedirs(legacy, exist_ok=True)
        with mock.patch.object(sys, "_MEIPASS", self.tmp, create=True):
            setup_core.copy_app_icon(legacy)
        self.assertTrue(os.path.isfile(os.path.join(legacy, "assets", "app.ico")))
        self.assertFalse(os.path.exists(os.path.join(legacy, "app.ico")))

    def test_stray_root_icon_removed(self) -> None:
        """升级安装：旧版留在根目录的 app.ico 要被清掉。"""
        stray = os.path.join(self.target, "app.ico")
        with open(stray, "wb") as fh:
            fh.write(b"old")
        setup_core.remove_stray_icon(self.target)
        self.assertFalse(os.path.exists(stray))

    def test_notify_prefers_internal_assets(self) -> None:
        """通知的 AUMID 图标要跟着新位置走，否则低电量通知会没图标。"""
        import notify

        dest = os.path.join(self.target, "_internal", "assets", "app.ico")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"ico")
        with mock.patch.object(setup_core, "install_dir", return_value=self.target):
            self.assertEqual(os.path.normcase(notify.icon_path()),
                             os.path.normcase(dest))


class DefaultPayloadTests(unittest.TestCase):
    """default_payload 要能认出「payload 是目录」和「payload 是单个 exe」两种包。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="skd_meipass_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prefers_dir_payload(self) -> None:
        payload = _make_payload(self.tmp)
        with mock.patch.object(sys, "_MEIPASS", self.tmp, create=True):
            self.assertEqual(os.path.normcase(setup_core.default_payload()),
                             os.path.normcase(payload))

    def test_falls_back_to_legacy_exe(self) -> None:
        """老包的 payload 目录里只有一个 exe，同样要能被认出来。"""
        root = os.path.join(self.tmp, "payload")
        os.makedirs(root, exist_ok=True)
        legacy = os.path.join(root, setup_core.APP_EXE)
        with open(legacy, "wb") as fh:
            fh.write(b"MZ")
        with mock.patch.object(sys, "_MEIPASS", self.tmp, create=True):
            self.assertEqual(os.path.normcase(setup_core.default_payload()),
                             os.path.normcase(root))


class AppFolderTests(unittest.TestCase):
    """v1.4.21：不管装到哪儿，程序本体都落在一个叫 skdDriver 的文件夹里。

    用户选的位置只是「父目录」——`D:\\Apps` → `D:\\Apps\\skdDriver`。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="skd_folder_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_appends_folder(self) -> None:
        self.assertEqual(
            os.path.normcase(setup_core.with_app_folder(self.tmp)),
            os.path.normcase(os.path.join(self.tmp, setup_core.APP_FOLDER)))

    def test_does_not_double_wrap(self) -> None:
        """已经是 skdDriver 就别再套一层（界面里选回当前目录时会走到这）。"""
        once = setup_core.with_app_folder(self.tmp)
        self.assertEqual(setup_core.with_app_folder(once), once)

    def test_case_insensitive(self) -> None:
        odd = os.path.join(self.tmp, "SKDDRIVER")
        self.assertEqual(os.path.normcase(setup_core.with_app_folder(odd)),
                         os.path.normcase(odd))

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(setup_core.with_app_folder(""), "")

    def test_default_dir_ends_with_folder(self) -> None:
        self.assertTrue(setup_core.default_install_dir().lower()
                        .endswith(setup_core.APP_FOLDER.lower()))

    def test_validate_accepts_drive_root_parent(self) -> None:
        """选磁盘根目录也合法：真正装的是 `D:\\skdDriver` 这一层。"""
        drive = os.path.splitdrive(self.tmp)[0] + os.sep
        with mock.patch.object(setup_core, "APP_FOLDER", "skdDriver_probe"):
            # 只验路径判断，不真往根目录写（写不了会报权限，与规则无关）
            with mock.patch("os.makedirs"), mock.patch("builtins.open", mock.mock_open()), \
                    mock.patch("os.remove"):
                self.assertEqual(setup_core.validate_install_dir(drive), "")

    def test_install_lands_in_app_folder(self) -> None:
        """perform_install 的落点永远是 `<父目录>\\skdDriver\\skdDriver.exe`。"""
        payload = _make_payload(self.tmp)
        with mock.patch.object(setup_core, "kill_running"), \
                mock.patch.object(setup_core, "wait_process_gone", return_value=True), \
                mock.patch.object(setup_core, "register_uninstall"), \
                mock.patch.object(setup_core, "create_shortcut", return_value=True), \
                mock.patch.object(setup_core, "refresh_shell_icons"), \
                mock.patch.object(setup_core, "cleanup_old_install"), \
                mock.patch.object(setup_core, "registered_install_dir", return_value=""), \
                mock.patch("autostart.set_command", return_value=True), \
                mock.patch("autostart.clear", return_value=True):
            exe = setup_core.perform_install(payload, target_dir=self.tmp)
        self.assertEqual(
            os.path.normcase(os.path.dirname(exe)),
            os.path.normcase(os.path.join(self.tmp, setup_core.APP_FOLDER)))
        self.assertTrue(os.path.isfile(exe))


class UninstallerTests(unittest.TestCase):
    """独立卸载程序的纯逻辑：取消不能动手，静默开关要认全。"""

    def test_cancel_does_nothing(self) -> None:
        import uninstall_exe

        touched: list[str] = []
        with mock.patch.object(uninstall_exe, "confirm", return_value=False), \
                mock.patch.object(setup_core, "kill_running",
                                  side_effect=lambda: touched.append("kill")), \
                mock.patch.object(setup_core, "unregister_uninstall",
                                  side_effect=lambda: touched.append("reg")):
            self.assertEqual(uninstall_exe.uninstall(quiet=False), 0)
        self.assertEqual(touched, [])       # 点了取消就一个动作都不许有

    def test_parse_quiet_flags(self) -> None:
        import uninstall_exe

        for flag in ("/quiet", "--quiet", "/silent", "/S", "/VERYSILENT"):
            self.assertTrue(uninstall_exe.parse_quiet([flag]), flag)
        self.assertFalse(uninstall_exe.parse_quiet([]))
        self.assertFalse(uninstall_exe.parse_quiet(["/help"]))


if __name__ == "__main__":
    unittest.main()
