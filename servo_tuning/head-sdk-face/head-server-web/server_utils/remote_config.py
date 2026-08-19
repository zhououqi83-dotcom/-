from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlparse, urlunparse


DEFAULT_REMOTE_CONFIG = {
    "hub": {
        "publicUrl": "http://127.0.0.1:8765",
        "host": "127.0.0.1",
        "port": 8765,
        "autoConnectOnLoad": True,
    },
    "robot": {
        "host": "127.0.0.1",
        "port": 2543,
        "avatarControlOnLoad": False,
    },
    "stream": {
        "websocketPath": "/ws",
        "controlRobotOnLoad": False,
        "robotForwardIntervalMs": 80,
    },
    "viewer": {
        "expressionResetMs": 1200,
        "coefficientConfigUrl": "./blendshape-config.yaml",
    },
    "backend": {
        "adapterConfigPath": "../viewer/robot/robot-adapter.yaml",
    },
}


def _is_plain_object(value) -> bool:
    return isinstance(value, dict)


def _merge_objects(base: dict, overrides: dict | None) -> dict:
    result = deepcopy(base)
    if not _is_plain_object(overrides):
        return result

    for key, value in overrides.items():
        if _is_plain_object(value) and _is_plain_object(result.get(key)):
            result[key] = _merge_objects(result[key], value)
            continue
        result[key] = value

    return result


def _normalize_string(value, fallback: str) -> str:
    normalized = str(value if value is not None else "").strip()
    return normalized or fallback


def _normalize_port(value, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if port > 0 else fallback


def _normalize_boolean(value, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _normalize_external_path(value, fallback: str) -> str:
    raw = _normalize_string(value, fallback)
    return raw if raw.startswith("/") else f"/{raw}"


def _normalize_relative_path(value, fallback: str) -> str:
    raw = _normalize_string(value, fallback)
    if raw.startswith(("./", "../", "/")):
        return raw
    return f"./{raw}"


def _build_default_hub_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def merge_remote_config(raw_config: dict | None = None) -> dict:
    return normalize_remote_config(_merge_objects(DEFAULT_REMOTE_CONFIG, raw_config or {}))


def normalize_remote_config(raw_config: dict | None = None) -> dict:
    merged = _merge_objects(DEFAULT_REMOTE_CONFIG, raw_config or {})

    hub_host = _normalize_string(merged.get("hub", {}).get("host"), DEFAULT_REMOTE_CONFIG["hub"]["host"])
    hub_port = _normalize_port(merged.get("hub", {}).get("port"), DEFAULT_REMOTE_CONFIG["hub"]["port"])
    hub_public_url = _normalize_string(
        merged.get("hub", {}).get("publicUrl"),
        _build_default_hub_url(hub_host, hub_port),
    )

    return {
        "hub": {
            "publicUrl": hub_public_url,
            "host": hub_host,
            "port": hub_port,
            "autoConnectOnLoad": _normalize_boolean(
                merged.get("hub", {}).get("autoConnectOnLoad"),
                DEFAULT_REMOTE_CONFIG["hub"]["autoConnectOnLoad"],
            ),
        },
        "robot": {
            "host": _normalize_string(
                merged.get("robot", {}).get("host"),
                DEFAULT_REMOTE_CONFIG["robot"]["host"],
            ),
            "port": _normalize_port(
                merged.get("robot", {}).get("port"),
                DEFAULT_REMOTE_CONFIG["robot"]["port"],
            ),
            "avatarControlOnLoad": _normalize_boolean(
                merged.get("robot", {}).get("avatarControlOnLoad"),
                DEFAULT_REMOTE_CONFIG["robot"]["avatarControlOnLoad"],
            ),
        },
        "stream": {
            "websocketPath": _normalize_external_path(
                merged.get("stream", {}).get("websocketPath"),
                DEFAULT_REMOTE_CONFIG["stream"]["websocketPath"],
            ),
            "controlRobotOnLoad": _normalize_boolean(
                merged.get("stream", {}).get("controlRobotOnLoad"),
                DEFAULT_REMOTE_CONFIG["stream"]["controlRobotOnLoad"],
            ),
            "robotForwardIntervalMs": _normalize_port(
                merged.get("stream", {}).get("robotForwardIntervalMs"),
                DEFAULT_REMOTE_CONFIG["stream"]["robotForwardIntervalMs"],
            ),
        },
        "viewer": {
            "expressionResetMs": _normalize_port(
                merged.get("viewer", {}).get("expressionResetMs"),
                DEFAULT_REMOTE_CONFIG["viewer"]["expressionResetMs"],
            ),
            "coefficientConfigUrl": _normalize_relative_path(
                merged.get("viewer", {}).get("coefficientConfigUrl"),
                DEFAULT_REMOTE_CONFIG["viewer"]["coefficientConfigUrl"],
            ),
        },
        "backend": {
            "adapterConfigPath": _normalize_relative_path(
                merged.get("backend", {}).get("adapterConfigPath"),
                DEFAULT_REMOTE_CONFIG["backend"]["adapterConfigPath"],
            ),
        },
    }


def build_stream_websocket_url(hub_url: str | None, websocket_path: str | None = None) -> str:
    fallback_hub_url = DEFAULT_REMOTE_CONFIG["hub"]["publicUrl"]
    target_path = _normalize_external_path(
        websocket_path,
        DEFAULT_REMOTE_CONFIG["stream"]["websocketPath"],
    )

    try:
        parsed = urlparse(hub_url or fallback_hub_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, target_path, "", "", ""))
    except Exception:
        return ""
