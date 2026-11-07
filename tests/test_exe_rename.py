"""主程序改名 skdDriver.exe + exe 版本信息精简的回归测试。

v1.4.28 把主程序从 `MouseGlass.exe` 改成 `skdDriver.exe`，同时去掉
版本资源里的「文件说明」「公司」。这里锁住三类容易回归的点：

1. 新名字必须贯穿安装落点、自启动、payload 探测；
2. **升级兼容**：旧进程要杀、旧 exe 要删、老 payload 要认得出，
   否则老用户装完会在目录里看到两个主程序；
3. 三个 exe 的版本资源里不许再出现 CompanyName / FileDescription。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import autostart  # noqa: E402
import setup_core  # noqa: E402


class NewNameTests(unittest.TestCase):
    def test_app_exe_is_skddriver(self) -> None:
        self.assertEqual(setup_core.APP_EXE, "skdDriver.exe")
        self.assertEqual(setup_core.LEGACY_APP_EXE, "MouseGlass.exe")
        self.assertNotEqual(setup_core.APP_EXE.lower(), setup_core.LEGACY_APP_EXE.lower())

    def test_installed_exe_uses_new_name(self) -> None:
        with mock.patch.object(setup_core, "_install_override", r"D:\Apps\skdDriver"):
            self.assertEqual(os.path.basename(setup_core.installed_exe()),
                             "skdDriver.exe")

    def test_autostart_fallback_uses_new_name(self) -> None:
        """开发态（非 frozen）的回退路径也别再指向 MouseGlass.exe。"""
        with mock.patch("os.path.exists", return_value=True):
            cmd = autostart.current_command()
        self.assertIn("skdDriver.exe", cmd)
        self.assertNotIn("MouseGlass.exe", cmd)

    def test_quickstart_command_uses_installed_exe(self) -> None:
        """快捷方式 / 自启动都取 perform_install 返回的路径，名字随 APP_EXE。"""
        with open(os.path.join(ROOT, "src", "uninstall_exe.py"), encoding="utf-8") as fh:
            src = fh.read()
        # 卸载器不该自己硬编码旧 exe 名
        self.assertNotIn("MouseGlass.exe", src)


class UpgradeCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="skd_rename_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kill_running_covers_both_names(self) -> None:
        calls: list[list[str]] = []
        with mock.patch.object(setup_core.subprocess, "run",
                               side_effect=lambda cmd, **kw: calls.append(cmd)):
            setup_core.kill_running()
        # taskkill /F /IM <name>
        killed = [c[c.index("/IM") + 1] for c in calls if "/IM" in c]
        self.assertEqual(sorted(killed),
                         sorted([setup_core.APP_EXE, setup_core.LEGACY_APP_EXE]))

    def test_wait_process_gone_checks_both_names(self) -> None:
        """旧进程还在时不能提前返回 True。timeout 调小避免真等 8 秒。"""
        def fake_run(cmd, **kw):
            filt = cmd[cmd.index("/FI") + 1]          # "IMAGENAME eq xxx"
            name = filt.split("eq", 1)[1].strip()
            out = name if name == setup_core.LEGACY_APP_EXE else ""
            return mock.Mock(stdout=out)
        with mock.patch.object(setup_core.subprocess, "run", side_effect=fake_run):
            self.assertFalse(setup_core.wait_process_gone(timeout=0.02))
        with mock.patch.object(setup_core.subprocess, "run",
                               return_value=mock.Mock(stdout="")):
            self.assertTrue(setup_core.wait_process_gone(timeout=0.02))

    def test_cleanup_old_install_removes_legacy_exe(self) -> None:
        for name in (setup_core.APP_EXE, setup_core.LEGACY_APP_EXE,
                     setup_core.UNINSTALL_EXE):
            with open(os.path.join(self.tmp, name), "wb") as fh:
                fh.write(b"MZ")
        with open(os.path.join(self.tmp, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("user file")
        setup_core.cleanup_old_install(self.tmp)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, setup_core.APP_EXE)))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, setup_core.LEGACY_APP_EXE)))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "keep.txt")))

    def test_inplace_upgrade_removes_legacy_exe(self) -> None:
        """同一目录升级：copytree 只覆盖不删除，旧 exe 必须被显式清掉。"""
        payload = os.path.join(self.tmp, "payload")
        os.makedirs(os.path.join(payload, "_internal"), exist_ok=True)
        with open(os.path.join(payload, setup_core.APP_EXE), "wb") as fh:
            fh.write(b"MZ new")
        target = os.path.join(self.tmp, setup_core.APP_FOLDER)
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, setup_core.LEGACY_APP_EXE), "wb") as fh:
            fh.write(b"MZ old")

        with mock.patch.object(setup_core, "kill_running"), \
                mock.patch.object(setup_core, "wait_process_gone", return_value=True), \
                mock.patch.object(setup_core, "register_uninstall"), \
                mock.patch.object(setup_core, "create_shortcut", return_value=True), \
                mock.patch.object(setup_core, "copy_app_icon"), \
                mock.patch.object(setup_core, "remove_stray_icon"), \
                mock.patch.object(setup_core, "remove_legacy_shortcuts"), \
                mock.patch.object(setup_core, "start_menu_link",
                                  return_value=os.path.join(self.tmp, "start.lnk")), \
                mock.patch.object(setup_core, "desktop_link",
                                  return_value=os.path.join(self.tmp, "desktop.lnk")), \
                mock.patch.object(setup_core, "refresh_shell_icons"), \
                mock.patch.object(setup_core, "cleanup_old_install"), \
                mock.patch.object(setup_core, "registered_install_dir", return_value=""), \
                mock.patch("autostart.set_command", return_value=True), \
                mock.patch("autostart.clear", return_value=True):
            exe = setup_core.perform_install(payload, target_dir=target)

        self.assertEqual(os.path.basename(exe), setup_core.APP_EXE)
        self.assertTrue(os.path.isfile(exe))
        self.assertFalse(os.path.exists(os.path.join(target,
                                                    setup_core.LEGACY_APP_EXE)))

    def test_default_payload_recognizes_legacy_name(self) -> None:
        root = os.path.join(self.tmp, "payload")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, setup_core.LEGACY_APP_EXE), "wb") as fh:
            fh.write(b"MZ")
        with mock.patch.object(sys, "_MEIPASS", self.tmp, create=True):
            self.assertEqual(os.path.normcase(setup_core.default_payload()),
                             os.path.normcase(root))

    def test_install_dir_accepts_legacy_exe(self) -> None:
        """从残留的旧 exe 启动时，也要能认出自己所在的安装目录。"""
        old = os.path.join(self.tmp, setup_core.LEGACY_APP_EXE)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", old, create=True), \
                mock.patch.object(setup_core, "registered_install_dir", return_value=""):
            self.assertEqual(os.path.normcase(setup_core.install_dir()),
                             os.path.normcase(self.tmp))


class VersionResourceTests(unittest.TestCase):
    """「文件说明」「公司」两行来自版本资源的 FileDescription / CompanyName。"""

    @staticmethod
    def _text(path: str) -> str:
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_build_script_does_not_emit_company_or_description(self) -> None:
        src = self._text(os.path.join(ROOT, "build.py"))
        self.assertNotIn("StringStruct('CompanyName'", src)
        self.assertNotIn("StringStruct('FileDescription'", src)
        # 但版本资源本身还得写，别把整个 write_version_file 删了
        self.assertIn("StringStruct('ProductName'", src)
        self.assertIn("StringStruct('FileVersion'", src)

    def test_templates_have_no_company_or_description(self) -> None:
        for name in ("version_info_skd.txt", "version_info_skd-setup.txt",
                     "version_info_skd-uninstall.txt"):
            text = self._text(os.path.join(ROOT, name))
            self.assertNotIn("CompanyName", text, name)
            self.assertNotIn("FileDescription", text, name)
            self.assertIn("FileVersion", text, name)

    def test_generated_version_file_has_no_company_or_description(self) -> None:
        """真调一次生成器（不跑 PyInstaller），确认产出的模板也干净。"""
        import build
        path = build.write_version_file("skd-probe.exe", "skd-probe-1.0.exe")
        try:
            text = self._text(path)
            self.assertNotIn("CompanyName", text)
            self.assertNotIn("FileDescription", text)
            self.assertIn("StringStruct('ProductName'", text)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
