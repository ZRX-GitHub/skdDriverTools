"""应用配置与持久化。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

APP_NAME = "CROCIRIS Mouse Glass"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MouseGlass")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_HUB_URL = "https://hub.crociris.com/connect"

#: 低电量提醒的阈值范围（百分比）与默认值
THRESHOLD_MIN = 5
THRESHOLD_MAX = 50
THRESHOLD_DEFAULT = 15


def clamp_threshold(value: object) -> int:
    """把任意输入收进 [THRESHOLD_MIN, THRESHOLD_MAX]，非法值回默认。"""
    try:
        level = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return THRESHOLD_DEFAULT
    return max(THRESHOLD_MIN, min(THRESHOLD_MAX, level))


#: 上次成功写入的内容，用于跳过重复写盘
_last_payload: str | None = None


@dataclass
class Config:
    x: int | None = None
    y: int | None = None
    always_on_top: bool = True
    close_to_tray: bool = True
    follow_system: bool = True
    dark_mode: bool = False
    hub_url: str = DEFAULT_HUB_URL
    poll_interval: float = 2.0
    #: 低电量提醒开关 + 阈值（低于该百分比才提醒）
    low_battery_notify: bool = True
    low_battery_threshold: int = THRESHOLD_DEFAULT
    #: 当前显示哪台鼠标："crociris"（西伯利亚）/ "logitech" / "basic"（狼途这类
    #: 没有私有协议、只能认出来的品牌鼠）；点标题切换后记住
    device: str = "crociris"
    #: 界面语言："zh" / "en"。设置页「语言」那一行切换，启动时生效
    language: str = "zh"
    cache: dict = field(default_factory=dict)


def load() -> Config:
    global _last_payload
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        known = {f for f in Config.__dataclass_fields__}
        cfg = Config(**{k: v for k, v in raw.items() if k in known})
        # 旧配置文件没有这两个字段，或者手改成了奇怪的值，统一收口
        if cfg.device not in ("crociris", "logitech", "basic"):
            cfg.device = "crociris"
        if cfg.language not in ("zh", "en"):
            cfg.language = "zh"
        # 关闭按钮的行为固定为「最小化到托盘」（用户要求不再提供开关）；
        # 老配置里存过 false 的，读进来直接纠回来，免得行为/提示自相矛盾
        cfg.close_to_tray = True
        cfg.low_battery_notify = bool(cfg.low_battery_notify)
        cfg.low_battery_threshold = clamp_threshold(cfg.low_battery_threshold)
        try:
            # 记下当前内容，避免启动后第一次持久化做无意义的写入
            _last_payload = json.dumps(asdict(cfg), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            _last_payload = None
        return cfg
    except (OSError, ValueError):
        return Config()


def save(cfg: Config) -> bool:
    """原子写入：先写 .tmp 再替换，避免断电/崩溃留下半截 JSON。

    内容与上次写入完全一致时直接跳过（轮询会周期性调用，避免无意义写盘）。
    返回是否真的写了。
    """
    global _last_payload
    try:
        payload = json.dumps(asdict(cfg), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return False
    if payload == _last_payload:
        return False
    tmp = CONFIG_PATH + ".tmp"
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    _last_payload = payload
    return True
