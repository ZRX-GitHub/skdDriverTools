"""G HUB 软件 profile 的 DPI 档位读取回归测试（不依赖真机 G HUB）。

背景：G502 HERO 在 G HUB 里配了 600 / 1350 两档（软件 profile），但鼠标
板载内存里是出厂档位、HID++ 0x2201 只给 100~25600/50 的 511 档能力范围。
旧实现拿能力表兜底，界面错显示「第 25 档 / 共 511」。这里锁住正确来源：
G HUB settings.db 的 MOUSE_SETTINGS 卡 → dpiTable.levels。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import ghub  # noqa: E402
import logitech as L  # noqa: E402

USER_CARD_ID = "e862bf2f-7ab4-4c8d-84ce-70bfdf04c2e4"
DEFAULT_CARD_ID = "9b85425b-8c71-4b15-808c-1204c08b0000"
PROFILE_ID = "420fd454-0c36-499d-bde4-146823b16147"


def make_cfg(levels=(600, 1350), active=1350, extra_cards=()) -> dict:
    """拼一份 G HUB 真机同构的最小配置。"""
    cards = [
        {"attribute": "MOUSE_SETTINGS", "id": DEFAULT_CARD_ID,
         "name": "DEFAULT_CARD_NAME_MOUSE_SETTINGS", "readOnly": True,
         "mouseSettings": {"dpiTable": {"activeDpi": 2400, "defaultDpi": 2400,
                                        "levels": [1200, 2400, 3200, 6400],
                                        "shiftDpi": 1200}}},
        {"attribute": "MOUSE_SETTINGS", "id": USER_CARD_ID,
         "name": "DEFAULT_CARD_NAME_MOUSE_SETTINGS", "profileId": PROFILE_ID,
         "mouseSettings": {"dpiTable": {"activeDpi": active, "defaultDpi": active,
                                        "levels": list(levels), "shiftDpi": levels[0]}}},
        *extra_cards,
    ]
    return {
        "cards": {"cards": cards},
        "profiles": {"profiles": [{
            "activeForApplication": True, "id": PROFILE_ID,
            "name": "PROFILE_NAME_DEFAULT",
            "assignments": [
                {"cardId": USER_CARD_ID, "slotId": "g502hero_mouse_settings"},
                {"cardId": "ffffffff-ffff-ffff-ffff-ffffffffffff", "slotId": "g502hero_g1_m1"},
            ],
        }]},
    }


def write_db(path: str, cfg: dict | bytes) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("DROP TABLE IF EXISTS data")
        con.execute("CREATE TABLE data (_id INTEGER PRIMARY KEY, _date_created TEXT, file BLOB)")
        blob = cfg if isinstance(cfg, bytes) else json.dumps(cfg).encode("utf-8")
        con.execute("INSERT INTO data VALUES (1, '2026-11-05', ?)", (blob,))
        con.commit()
    finally:
        con.close()


class GhubDpiTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db = tempfile.mkstemp(prefix="ghub_test_", suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.db) and os.remove(self.db))
        # 每个用例都用干净缓存 + 指向临时库
        self.cache_patch = mock.patch.object(ghub, "_CACHE", {
            "sig": None, "path": "", "cfg": None, "empty": False})
        self.path_patch = mock.patch.object(ghub, "CANDIDATES", (self.db,))
        self.cache_patch.start()
        self.path_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.addCleanup(self.path_patch.stop)

    # ------------------------------------------------------------ 解析
    def test_reads_real_shaped_config(self) -> None:
        write_db(self.db, make_cfg())
        cfg = ghub.read_settings()
        self.assertIsInstance(cfg, dict)
        cards = ghub.dpi_cards(cfg)
        self.assertEqual(len(cards), 2)
        user = next(c for c in cards if c["id"] == USER_CARD_ID)
        self.assertEqual(user["levels"], [600, 1350])

    def test_current_1350_is_second_of_two(self) -> None:
        write_db(self.db, make_cfg())
        levels = ghub.dpi_levels_for(1350)
        self.assertEqual(levels, [600, 1350])
        self.assertEqual(L.LogitechMouse._match_level(levels, 1350), (2, 2))
        self.assertEqual(L.LogitechMouse._match_level(levels, 600), (1, 2))

    def test_600_is_first_of_two(self) -> None:
        write_db(self.db, make_cfg(active=600))
        self.assertEqual(ghub.dpi_levels_for(600), [600, 1350])

    def test_factory_default_card_never_impersonates_user_config(self) -> None:
        """2400 只在出厂只读卡里；有用户卡却对不上 → None，不能报 4 档。"""
        write_db(self.db, make_cfg())
        self.assertIsNone(ghub.dpi_levels_for(2400))
        self.assertIsNone(ghub.dpi_levels_for(800))

    def test_readonly_fallback_serves_active_default_card(self) -> None:
        """fallback_readonly：用户卡与实时 DPI 脱节时，用含实时值的只读卡。

        真机场景（v1.3.6）：G HUB 运行中 db 用户卡还停在 [600,1350]，设备
        实时已是 1200（生效配置其实是只读卡 [1200,2400,3200,6400]）。不放宽
        会让编辑挡位条退回板载陈旧数据、和展示态档位数互相矛盾。
        """
        write_db(self.db, make_cfg(active=600))
        self.assertIsNone(ghub.dpi_levels_for(1200))
        self.assertEqual(ghub.dpi_levels_for(1200, fallback_readonly=True),
                         [1200, 2400, 3200, 6400])
        # 用户卡对得上时 fallback 不改变结果
        self.assertEqual(ghub.dpi_levels_for(1350, fallback_readonly=True),
                         [600, 1350])
        # 谁都对不上时依旧 None
        self.assertIsNone(ghub.dpi_levels_for(800, fallback_readonly=True))

    def test_logitech_display_uses_readonly_fallback(self) -> None:
        """logitech 的展示档位数与编辑挡位条都要走 fallback，保持同源。"""
        write_db(self.db, make_cfg(active=600))
        mouse = self._fake_mouse(cur=1200, onboard=(700, 850))
        self.assertEqual(mouse.dpi_levels(), (1, 4))
        self.assertEqual(mouse.levels_table(1200), [1200, 2400, 3200, 6400])

    def test_levels_keep_ghub_order(self) -> None:
        """G HUB 滑块顺序不一定升序，档位序号要跟着它的顺序走。"""
        write_db(self.db, make_cfg(levels=(2400, 800, 1350), active=1350))
        levels = ghub.dpi_levels_for(1350)
        self.assertEqual(levels, [2400, 800, 1350])
        self.assertEqual(L.LogitechMouse._match_level(levels, 1350), (3, 3))

    def test_active_profile_card_wins(self) -> None:
        """两张用户卡都含 1350 时，选活跃 profile 绑定的那张。"""
        other = {"attribute": "MOUSE_SETTINGS", "id": "other-card",
                 "profileId": "another-profile",
                 "mouseSettings": {"dpiTable": {"activeDpi": 1350,
                                               "levels": [400, 1350, 3200]}}}
        write_db(self.db, make_cfg(extra_cards=(other,)))
        self.assertEqual(ghub.dpi_levels_for(1350), [600, 1350])

    def test_dirty_levels_are_filtered(self) -> None:
        cfg = make_cfg()
        cfg["cards"]["cards"][1]["mouseSettings"]["dpiTable"]["levels"] = [
            0, 600, 600, 1350, 99999, None, "800"]
        self.assertEqual(ghub.dpi_cards(cfg)[1]["levels"], [600, 1350])

    # ------------------------------------------------------------ 容错
    def test_missing_db_returns_none(self) -> None:
        os.remove(self.db)
        self.assertIsNone(ghub.read_settings())
        self.assertIsNone(ghub.dpi_levels_for(1350))

    def test_broken_db_returns_none(self) -> None:
        write_db(self.db, b"not a json at all")
        self.assertIsNone(ghub.read_settings())
        self.assertIsNone(ghub.dpi_levels_for(1350))

    def test_wrong_table_returns_none(self) -> None:
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE IF EXISTS data")
        con.execute("CREATE TABLE something_else (x TEXT)")
        con.commit()
        con.close()
        self.assertIsNone(ghub.read_settings())

    def test_cache_refreshes_when_ghub_changes_config(self) -> None:
        write_db(self.db, make_cfg(levels=(600, 1350)))
        self.assertEqual(ghub.dpi_levels_for(1350), [600, 1350])
        write_db(self.db, make_cfg(levels=(400, 800, 1350), active=1350))
        # Windows 文件系统时间分辨率：强制改 mtime 保证签名变化
        st = os.stat(self.db)
        os.utime(self.db, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        self.assertEqual(ghub.dpi_levels_for(1350), [400, 800, 1350])

    # ------------------------------------------------------------ 接入 logitech
    def test_logitech_uses_ghub_before_onboard(self) -> None:
        write_db(self.db, make_cfg())
        mouse = self._fake_mouse(cur=1350)
        self.assertEqual(mouse.dpi_levels(), (2, 2))
        self.assertEqual(mouse.onboard_reads, 0, "G HUB 命中时不该再翻板载扇区")

    def test_logitech_falls_back_to_onboard(self) -> None:
        os.remove(self.db)                       # 没装 G HUB
        mouse = self._fake_mouse(cur=600)
        self.assertEqual(mouse.dpi_levels(), (1, 3))
        self.assertEqual(mouse.onboard_reads, 1)

    def test_logitech_never_reports_511_capability_levels(self) -> None:
        """G HUB 和板载都读不到 → (0, 0)，界面显示「实时读取」。"""
        os.remove(self.db)
        mouse = self._fake_mouse(cur=1350, onboard=[])
        self.assertEqual(mouse.dpi_levels(), (0, 0))
        self.assertEqual(mouse.capability_reads, 0, "不该再去读 0x2201 func1 能力表")

    def _fake_mouse(self, cur: int, onboard=(600, 800, 1200)):
        """不碰 HID 的替身：记录读了多少次能力表 / 板载扇区。"""
        mouse = object.__new__(L.LogitechMouse)
        mouse._onboard_levels = None
        mouse.onboard_reads = 0
        mouse.capability_reads = 0

        def fake_dpi():
            return cur

        def fake_dpi_list(*_a, **_kw):
            mouse.capability_reads += 1
            return list(range(100, 25601, 50))

        def fake_onboard(_cur):
            mouse.onboard_reads += 1
            return list(onboard)

        mouse.dpi = fake_dpi
        mouse._call = fake_dpi_list
        mouse.feature = lambda _fid: 10
        mouse._read_onboard_levels = fake_onboard
        return mouse


if __name__ == "__main__":
    unittest.main()


class GhubWriteLevelsTests(unittest.TestCase):
    """write_levels：写回活跃卡的档位表（升序）+ activeDpi 收敛到最近档。"""

    def setUp(self) -> None:
        fd, self.db = tempfile.mkstemp(prefix="ghub_write_", suffix=".db")
        os.close(fd)
        write_db(self.db, make_cfg(levels=(600, 1350), active=1350))

    def tearDown(self) -> None:
        os.unlink(self.db)
        bak = self.db + ".bak"
        if os.path.isfile(bak):
            os.unlink(bak)

    def test_write_levels_sorted_and_backup(self) -> None:
        ok, msg = ghub.write_levels([2400, 600, 1350], active_dpi=1350,
                                    cur_dpi=600, path=self.db)
        self.assertTrue(ok, msg)
        self.assertTrue(os.path.isfile(self.db + ".bak"))   # 写前有备份
        cfg = ghub.read_settings(path=self.db)
        card = [c for c in ghub.dpi_cards(cfg) if c["id"] == USER_CARD_ID][0]
        self.assertEqual(card["levels"], [600, 1350, 2400])   # 升序去重
        self.assertEqual(card["activeDpi"], 1350)

    def test_write_levels_active_dpi_snaps(self) -> None:
        ok, _ = ghub.write_levels([600, 1350], active_dpi=800,
                                  cur_dpi=600, path=self.db)
        self.assertTrue(ok)
        card = [c for c in ghub.dpi_cards(ghub.read_settings(path=self.db))
                if c["id"] == USER_CARD_ID][0]
        self.assertEqual(card["activeDpi"], 600)   # 800 就近收敛到 600

    def test_write_rejects_empty(self) -> None:
        ok, _ = ghub.write_levels([], cur_dpi=600, path=self.db)
        self.assertFalse(ok)

    def test_write_rejects_missing_card(self) -> None:
        # 空档位表：make_cfg 会把用户卡的 levels 清空，dpi_cards 就抽不出卡
        con = sqlite3.connect(self.db)
        con.execute("UPDATE data SET file = ?", (json.dumps({
            "cards": {"cards": []}, "profiles": {"profiles": []},
        }).encode(),))
        con.commit()
        con.close()
        ok, msg = ghub.write_levels([600], cur_dpi=600, path=self.db)
        self.assertFalse(ok)
        self.assertIn("配置卡", msg)
