from __future__ import annotations

from pathlib import Path
import threading
from typing import Any
import sys

CORE_DIR = Path(__file__).resolve().parent.parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from viewer.robot.robot_adapter import RobotBlendshapeAdapter


IMPORT_ERROR: str | None = None
HeadSDK = None
try:
    from head_sdk import HeadSDK  # type: ignore
except Exception as exc:  # pragma: no cover - depends on local env
    IMPORT_ERROR = str(exc)


def _normalize_port(value: Any) -> int:
    port = int(value)
    if port <= 0:
        raise ValueError("port must be greater than 0")
    return port


class HeadSdkController:
    def __init__(self, *, adapter_config_path: str | Path | None = None, coefficient_catalog: dict | None = None):
        self._lock = threading.RLock()
        self._client = None
        self._client_target = {"host": None, "port": None}
        self._coefficient_catalog = coefficient_catalog or {}
        self._adapter = RobotBlendshapeAdapter(adapter_config_path)

    def _require_sdk(self) -> None:
        if IMPORT_ERROR:
            raise RuntimeError(f"head_sdk unavailable: {IMPORT_ERROR}")

    def _validate_client_connection(self, current_client: Any) -> None:
        if not current_client:
            raise RuntimeError("head_sdk client is not available")

        probe = getattr(current_client, "_get_state", None)
        if not callable(probe):
            raise RuntimeError("head_sdk client does not expose a state probe")

        result = probe()
        if isinstance(result, BaseException):
            raise RuntimeError(str(result))

        if getattr(current_client, "state", None) is None:
            raise RuntimeError("head_sdk state probe did not return servo state")

    def status(self) -> dict[str, Any]:
        with self._lock:
            connected = bool(self._client and getattr(self._client, "is_connected", lambda: False)())
            adapter_status = self._adapter.status()
            return {
                "ok": IMPORT_ERROR is None,
                "connected": connected,
                "target": dict(self._client_target),
                "error": IMPORT_ERROR,
                "adapter_config_path": adapter_status["config_path"],
                "adapter_enabled": adapter_status["enabled"],
                "adapter_error": adapter_status["error"],
            }

    def connect(self, host: str, port: int) -> dict[str, Any]:
        with self._lock:
            self._require_sdk()

            normalized_host = str(host or "127.0.0.1").strip()
            if not normalized_host:
                raise ValueError("host is required")
            normalized_port = _normalize_port(port or 2543)

            if self._client and getattr(self._client, "is_connected", lambda: False)():
                same_host = self._client_target["host"] == normalized_host
                same_port = self._client_target["port"] == normalized_port
                if same_host and same_port:
                    return self.status()
                self._client.disconnect()

            self._client = HeadSDK(normalized_host, normalized_port, force_new=True)
            if not getattr(self._client, "is_connected", lambda: False)():
                self._client = None
                self._client_target["host"] = None
                self._client_target["port"] = None
                raise RuntimeError(f"failed to connect to HeadSDK at {normalized_host}:{normalized_port}")

            try:
                self._validate_client_connection(self._client)
            except Exception:
                try:
                    self._client.disconnect()
                except Exception:
                    pass
                self._client = None
                self._client_target["host"] = None
                self._client_target["port"] = None
                raise

            self._client.release_control()
            self._client_target["host"] = normalized_host
            self._client_target["port"] = normalized_port
            return self.status()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            if self._client and getattr(self._client, "is_connected", lambda: False)():
                self._client.disconnect()
            self._client = None
            self._client_target["host"] = None
            self._client_target["port"] = None
            return self.status()

    def set_arkit(self, blendshapes: dict[str, float]) -> dict[str, Any]:
        with self._lock:
            self._require_sdk()
            if not self._client or not getattr(self._client, "is_connected", lambda: False)():
                raise RuntimeError("robot is not connected")

            if not isinstance(blendshapes, dict) or not blendshapes:
                raise ValueError("blendshapes must be a non-empty object")

            payload = {
                str(key): float(value)
                for key, value in blendshapes.items()
            }
            adapted_payload = self._adapter.apply(payload, self._coefficient_catalog)
            self._client.set_arkit_positions(adapted_payload)
            return {
                **self.status(),
                "sent_count": len(adapted_payload),
            }

    def reload_bs2servo_mapping(self, path: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._require_sdk()
            if not self._client or not getattr(self._client, "is_connected", lambda: False)():
                raise RuntimeError("robot is not connected")

            resolved_path = str(path).strip() or None if path is not None else None
            mapping = self._client.reload_bs2servo_mapping(resolved_path)
            return {
                **self.status(),
                "mapping_path": resolved_path,
                "mapping_count": len(mapping) if isinstance(mapping, dict) else 0,
            }

    def reload_adapter_config(self, path: str | Path | None = None) -> dict[str, Any]:
        with self._lock:
            self._adapter.reload(path)
            return self.status()
