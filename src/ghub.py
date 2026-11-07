"""只读解析 Logitech G HUB 的本地鼠标配置，拿到真正的 DPI 档位表。

背景（G502 HERO 实测）：G HUB 里「桌面: 默认」这类**软件 profile** 的 DPI
档位不会写进鼠标板载内存。HID++ 0x2201 只能读到当前 DPI 和传感器能力范围
（100~25600、步进 50，共 511 档）——那是硬件支持的全部档，不是用户配的几档；
0x8100 板载 profile 里则还是出厂档位（600 / 800 / 1200…），也没有 1350。
所以用设备侧任何接口都拼不出 G HUB 里显示的「600、1350 共 2 档」。

真正的数据在 G HUB 自己的配置库里：

    %LOCALAPPDATA%\\LGHUB\\settings.db → 表 data(file) → 一行 JSON
    JSON["cards"]["cards"] 里 attribute == "MOUSE_SETTINGS" 的卡片：
        {"id": "...", "profileId": "...",
         "mouseSettings": {"dpiTable": {"levels": [600, 1350],
                                        "activeDpi": 1350,
                                        "defaultDpi": 1350,
                                        "shiftDpi": 600},
                           "reportRate": {"value": 1000}}}

profile 的 assignments 里 slotId 形如 ``g502hero_mouse_settings`` 的 cardId
指向当前生效的那张卡。本模块只读、不写、不常驻句柄；按文件 mtime+size
缓存解析结果，G HUB 改配置后下一轮轮询自动生效。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import quote

#: G HUB 配置库候选位置（不同版本/安装方式会变）
CANDIDATES = (
    r"%LOCALAPPDATA%\LGHUB\settings.db",
    r"%PROGRAMDATA%\Logishrd\GHUB\settings.db",
    r"%PROGRAMDATA%\LGHUB\settings.db",
    r"%APPDATA%\LGHUB\settings.db",
)

#: 合法 DPI 值范围，滤掉占位槽和脏数据
DPI_MIN, DPI_MAX = 50, 40000

_CACHE: dict[str, object] = {"sig": None, "path": "", "cfg": None, "empty": False}


def _paths() -> list[str]:
    out: list[str] = []
    for raw in CANDIDATES:
        p = os.path.expandvars(raw)
        if p and p not in out and os.path.isfile(p):
            out.append(p)
    return out


def _connect_ro(path: str) -> sqlite3.Connection:
    """只读打开（G HUB 正在运行时库被占用，绝不尝试写入或建 journal）。"""
    posix = Path(path).as_posix()
    uri = "file:" + quote(posix, safe=":/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=0.3)
    con.execute("PRAGMA busy_timeout=200")
    return con


def _load_json(path: str) -> dict | None:
    """读 settings.db 里最新的那行配置 JSON；失败返回 None。"""
    con = None
    try:
        con = _connect_ro(path)
        blob = None
        # 版本差异：数据列有的叫 file，有的叫 data
        for col in ("file", "data", "value", "json"):
            try:
                row = con.execute(
                    f"SELECT {col} FROM data ORDER BY _id DESC LIMIT 1").fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                blob = row[0]
                break
        if blob is None:
            return None
        text = blob.decode("utf-8", "ignore") if isinstance(blob, bytes) else str(blob)
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (OSError, sqlite3.Error, ValueError, UnicodeError):
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass


def read_settings(path: str | None = None) -> dict | None:
    """返回 G HUB 配置 JSON；按文件签名缓存。传 path 时跳过缓存（测试用）。"""
    global _CACHE
    if path is not None:
        return _load_json(path)
    paths = _paths()
    if not paths:
        _CACHE.update(sig=None, path="", cfg=None, empty=True)
        return None
    p = paths[0]
    try:
        st = os.stat(p)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return _CACHE["cfg"] if isinstance(_CACHE["cfg"], dict) else None
    if _CACHE["sig"] == sig and _CACHE["path"] == p:
        return _CACHE["cfg"] if isinstance(_CACHE["cfg"], dict) else None
    cfg = _load_json(p)
    _CACHE.update(sig=sig, path=p, cfg=cfg, empty=cfg is None)
    return cfg


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("cards", "profiles", "items", "values"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def _clean_levels(raw) -> list[int]:
    """保持 G HUB 里的原始顺序（档位序号要跟 G HUB 一致，不能排序）。"""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        v = int(v)
        if DPI_MIN <= v <= DPI_MAX and v not in out:
            out.append(v)
    return out


def dpi_cards(cfg: dict | None) -> list[dict]:
    """抽出所有 MOUSE_SETTINGS 卡：{id, profileId, levels, activeDpi, readOnly}。"""
    if not cfg:
        return []
    out: list[dict] = []
    for card in _as_list(cfg.get("cards")):
        if not isinstance(card, dict) or card.get("attribute") != "MOUSE_SETTINGS":
            continue
        ms = card.get("mouseSettings") or {}
        table = ms.get("dpiTable") or {}
        levels = _clean_levels(table.get("levels"))
        if not levels:
            continue
        active = table.get("activeDpi")
        out.append({
            "id": str(card.get("id") or ""),
            "profileId": str(card.get("profileId") or ""),
            "levels": levels,
            "activeDpi": active if isinstance(active, (int, float)) else None,
            "readOnly": bool(card.get("readOnly")),
        })
    return out


def _active_profile_ids(cfg: dict | None) -> list[str]:
    """当前生效的 profile id：桌面默认 / 自动切换上次使用，按优先级排。"""
    if not cfg:
        return []
    ids: list[str] = []
    last = cfg.get("profiles/auto_switching/s_last_used_profile")
    if isinstance(last, str) and last:
        ids.append(last)
    elif isinstance(last, dict):
        for key in ("id", "profileId", "value"):
            if isinstance(last.get(key), str) and last[key]:
                ids.append(last[key])
                break
    for prof in _as_list(cfg.get("profiles")):
        if isinstance(prof, dict) and prof.get("activeForApplication"):
            pid = str(prof.get("id") or "")
            if pid and pid not in ids:
                ids.append(pid)
    return ids


def _assigned_card_ids(cfg: dict | None, profile_ids: list[str]) -> list[str]:
    """活跃 profile 里 slotId 以 _mouse_settings 结尾的 cardId。"""
    if not cfg or not profile_ids:
        return []
    out: list[str] = []
    for prof in _as_list(cfg.get("profiles")):
        if not isinstance(prof, dict) or str(prof.get("id") or "") not in profile_ids:
            continue
        for a in _as_list(prof.get("assignments")):
            if not isinstance(a, dict):
                continue
            slot = str(a.get("slotId") or "").lower()
            cid = str(a.get("cardId") or "")
            if slot.endswith("mouse_settings") and cid:
                out.append(cid)
    return out


def dpi_levels_for(cur_dpi: int | None, cfg: dict | None = None,
                   fallback_readonly: bool = False) -> list[int] | None:
    """当前 DPI 对应的 G HUB 档位表；拿不到返回 None。

    选卡优先级：
      1. 活跃 profile 绑定的 MOUSE_SETTINGS 卡（levels 含当前 DPI）
      2. activeDpi == 当前 DPI 的用户卡（带 profileId）
      3. 其它 levels 含当前 DPI 的卡
    当前 DPI 不在任何卡里时返回 None——说明这份配置不是这只鼠标在用的，
    宁可让界面显示「实时读取」，也不要拿旧配置或 511 档能力表硬凑。

    fallback_readonly=True（界面显示用）：用户卡全部对不上当前 DPI 时，
    再放宽到「levels 含当前 DPI 的任意卡（含出厂只读卡）」。真机上 G HUB
    运行中会把 db 里的 activeDpi 与设备实时值脱节（内存态没 flush），
    此时设备实际生效的往往就是那张只读默认卡（如 G502 [1200,2400,3200,
    6400]）；不放宽会让编辑挡位条退回板载陈旧数据，和展示态「第 1 档 /
    共 4」互相矛盾。写回路径仍走 write_levels 的活跃用户卡，不受影响。
    """
    if cfg is None:
        cfg = read_settings()
    cards = dpi_cards(cfg)
    if not cards:
        return None
    if cur_dpi is None:
        # 读不到当前 DPI 时，最多给活跃卡自己那份档位表
        preferred = _assigned_card_ids(cfg, _active_profile_ids(cfg))
        for cid in preferred:
            for c in cards:
                if c["id"] == cid:
                    return c["levels"]
        return None

    def matches(c: dict) -> bool:
        return any(abs(v - cur_dpi) <= 60 for v in c["levels"])

    preferred = _assigned_card_ids(cfg, _active_profile_ids(cfg))
    for cid in preferred:
        for c in cards:
            if c["id"] == cid and matches(c):
                return c["levels"]
    user = [c for c in cards if c["profileId"] and not c["readOnly"]]
    # 有用户配置卡却都对不上当前 DPI：可能是 G HUB 关了、换了设备或手动改了
    # DPI。此时宁可显示「实时读取」，也别拿出厂默认卡凑一个错误的档位数。
    if user and not any(matches(c) for c in user):
        if not fallback_readonly:
            return None
        # 设备实时 DPI 只出现在只读卡里 → 它才是当前生效配置，拿它显示
        for c in cards:
            if matches(c):
                return c["levels"]
        return None
    pool = user or cards
    for c in pool:
        if c["activeDpi"] is not None and abs(c["activeDpi"] - cur_dpi) <= 60 \
                and matches(c):
            return c["levels"]
    for c in pool:
        if matches(c):
            return c["levels"]
    return None


def write_levels(levels: list[int], active_dpi: int | None = None,
                 cur_dpi: int | None = None,
                 path: str | None = None) -> tuple[bool, str]:
    """把新档位表写回 G HUB 配置库（写前备份，事务写入）。

    - levels 升序去重后写进**当前生效的那张** MOUSE_SETTINGS 卡的
      ``mouseSettings.dpiTable.levels``；active_dpi 同步更新成最接近的档位
    - G HUB 正在运行时：SQLite 层面能写进去（WAL），但 G HUB 内存里有缓存，
      要重启 G HUB 才会看到新值；它在这次之后保存的任何设置都可能把本次
      改动覆盖回去 —— 由调用方把这一点提示给用户
    - 返回 (是否写入, 说明)；path 仅测试用，省略时自动定位配置库
    """
    vals = sorted({int(v) for v in levels if DPI_MIN <= int(v) <= DPI_MAX})
    if not vals:
        return False, "档位表为空"
    path = path or (_paths() or [""])[0]
    if not path or not os.path.isfile(path):
        return False, "未找到 G HUB 配置库"
    cfg = _load_json(path)
    if not isinstance(cfg, dict):
        return False, "配置库解析失败"

    # 定位要改的卡：优先活跃卡，其次当前 DPI 能对上的用户卡
    cards = dpi_cards(cfg)
    target_id = ""
    preferred = _assigned_card_ids(cfg, _active_profile_ids(cfg))
    for cid in preferred:
        if any(c["id"] == cid for c in cards):
            target_id = cid
            break
    if not target_id and cur_dpi is not None:
        for c in cards:
            if c["profileId"] and not c["readOnly"] and \
                    any(abs(v - cur_dpi) <= 60 for v in c["levels"]):
                target_id = c["id"]
                break
    if not target_id:
        return False, "没找到当前生效的 G HUB 档位配置卡"

    changed = False
    for card in _as_list(cfg.get("cards")):
        if not isinstance(card, dict) or str(card.get("id") or "") != target_id:
            continue
        ms = card.setdefault("mouseSettings", {})
        table = ms.setdefault("dpiTable", {})
        table["levels"] = vals
        if active_dpi is not None:
            best = min(vals, key=lambda v: abs(v - int(active_dpi)))
            table["activeDpi"] = best
        changed = True
        break
    if not changed:
        return False, "没找到当前生效的 G HUB 档位配置卡"

    text = json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
    con = None
    try:
        shutil.copy2(path, path + ".bak")          # 写前备份，覆盖旧备份
        posix = Path(path).as_posix()
        uri = "file:" + quote(posix, safe=":/") + "?mode=rw"
        con = sqlite3.connect(uri, uri=True, timeout=1.5)
        con.execute("PRAGMA busy_timeout=1500")
        col = "file"
        for name in ("file", "data", "value", "json"):
            names = {r[1] for r in con.execute("PRAGMA table_info('data')")}
            if name in names:
                col = name
                break
        row = con.execute(
            f"SELECT _id FROM data ORDER BY _id DESC LIMIT 1").fetchone()
        if not row:
            return False, "配置库结构不符"
        con.execute(f"UPDATE data SET {col} = ? WHERE _id = ?",
                    (text.encode("utf-8"), row[0]))
        con.commit()
        _CACHE.update(sig=None, path="", cfg=None, empty=False)   # 失效缓存
        return True, ""
    except sqlite3.OperationalError as exc:
        return False, f"G HUB 正在占用配置库（{exc}）"
    except (OSError, sqlite3.Error, ValueError) as exc:
        return False, f"写入 G HUB 配置失败：{exc}"
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
