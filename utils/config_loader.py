"""加载 config.yaml，环境变量覆盖敏感字段。"""

import os
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _apply_env(cfg: dict) -> dict:
    """环境变量覆盖 telegram 与 proxy 配置。"""
    tg = cfg.setdefault("telegram", {})
    env_map = {
        "API_ID": ("api_id", int),
        "API_HASH": ("api_hash", str),
        "STRING_SESSION": ("string_session", str),
        "FORWARD_TO_CHANNEL": ("forward_to_channel", str),
        "TARGET_CHANNEL": ("target_channel", str),
    }
    for env_key, (cfg_key, cast) in env_map.items():
        val = os.environ.get(env_key)
        if val:
            tg[cfg_key] = cast(val) if cast is int else val

    proxy = cfg.get("proxy") or {}
    if os.environ.get("PROXY_TYPE"):
        proxy["type"] = os.environ["PROXY_TYPE"]
    if os.environ.get("PROXY_HOST"):
        proxy["host"] = os.environ["PROXY_HOST"]
    if os.environ.get("PROXY_PORT"):
        proxy["port"] = int(os.environ["PROXY_PORT"])
    if os.environ.get("PROXY_USER"):
        proxy["username"] = os.environ["PROXY_USER"]
    if os.environ.get("PROXY_PASS"):
        proxy["password"] = os.environ["PROXY_PASS"]
    if proxy:
        cfg["proxy"] = proxy
    return cfg


def build_proxy(proxy_cfg: dict | None):
    """将 YAML proxy 段转换为 Telethon/PySocks 元组。"""
    if not proxy_cfg or not proxy_cfg.get("host"):
        if os.environ.get("HTTP_PROXY"):
            parts = os.environ["HTTP_PROXY"].split(":")
            if len(parts) >= 3:
                import socks
                return (socks.HTTP, parts[1][2:], int(parts[2]), None, None)
        return None
    import socks
    ptype = (proxy_cfg.get("type") or "SOCKS5").upper()
    sock_type = socks.SOCKS5 if ptype == "SOCKS5" else socks.HTTP
    return (
        sock_type,
        proxy_cfg["host"],
        int(proxy_cfg["port"]),
        proxy_cfg.get("username"),
        proxy_cfg.get("password"),
    )


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        example = config_path.parent / "config.yaml.example"
        raise FileNotFoundError(
            f"配置文件不存在: {config_path}\n请复制 {example.name} 为 config.yaml 并填写参数。"
        )
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return _apply_env(cfg)
