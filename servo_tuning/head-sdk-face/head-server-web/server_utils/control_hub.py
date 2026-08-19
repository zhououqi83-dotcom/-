#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import socket
import struct
import sys
import threading
from urllib.parse import urlsplit

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from server_utils.blendshape_mapping import (
    filter_robot_coefficients,
    merge_canonical_value_maps,
    normalize_coefficient_catalog,
)
from server_utils.external_protocol import build_external_frame
from server_utils.remote_config import build_stream_websocket_url, merge_remote_config
from server_utils.simple_yaml import parse_simple_yaml
from viewer.robot.head_sdk_controller import HeadSdkController


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
STREAM_STATUS_PATH = "/status"
STREAM_FRAME_PATH = "/frame"
STREAM_ROBOT_FORWARDING_PATH = "/robot-forwarding"
ROBOT_RELOAD_MAPPING_PATH = "/robot/reload-bs2servo-mapping"
LEGACY_STREAM_WS_PATHS = {"/external"}
ROBOT_AUTO_CONNECT_INTERVAL_S = 3.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_local_path(base_dir: Path, raw_path: str | None, fallback: Path | None = None) -> Path:
    if raw_path:
        candidate = Path(str(raw_path).strip())
        if candidate.is_absolute():
            return candidate
        return (base_dir / candidate).resolve()
    if fallback is None:
        raise ValueError("fallback path is required")
    return fallback.resolve()


def load_remote_config(config_path: Path) -> dict:
    try:
        content = config_path.read_text(encoding="utf-8")
        return merge_remote_config(parse_simple_yaml(content))
    except Exception as error:
        print(f"[control-hub:py] failed to load config, using defaults: {error}")
        return merge_remote_config()


def load_coefficient_catalog(config_path: Path, runtime_config: dict) -> tuple[dict, Path]:
    asset_url = runtime_config.get("viewer", {}).get("coefficientConfigUrl")
    normalized_asset = str(asset_url).strip() if asset_url is not None else ""
    normalized_asset = normalized_asset.lstrip("./") if normalized_asset else ""
    normalized_asset = normalized_asset.lstrip("/") if normalized_asset else normalized_asset
    resolved_path = (
        (config_path.parent / normalized_asset).resolve()
        if normalized_asset
        else (config_path.parent / "blendshape-config.yaml").resolve()
    )
    content = resolved_path.read_text(encoding="utf-8")
    return normalize_coefficient_catalog(parse_simple_yaml(content)), resolved_path


@dataclass(eq=False)
class SseClient:
    queue: queue.Queue = field(default_factory=queue.Queue)
    closed: bool = False

    def send(self, event: str, data: dict) -> None:
        if self.closed:
            return
        self.queue.put((event, data))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.queue.put(None)


class HubState:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        stream_ws_path: str,
        robot_forward_interval_ms: int,
        coefficient_catalog: dict,
        robot_controller: HeadSdkController,
    ):
        self.host = host
        self.port = port
        self.stream_ws_path = stream_ws_path
        self.robot_forward_interval_ms = robot_forward_interval_ms
        self.coefficient_catalog = coefficient_catalog
        self.robot_controller = robot_controller

        self._lock = threading.RLock()
        self._sse_clients: set[SseClient] = set()
        self._external_ws_connections = 0
        self._external_state = {
            "lastMessageAt": None,
            "lastBlendshapeCount": 0,
            "robotForwardingEnabled": False,
            "lastRobotForwardAt": None,
            "lastRobotForwardCount": 0,
            "lastRobotForwardError": None,
        }
        self._external_frame_values: dict[str, float] = {}
        self._external_robot_forward_timer: threading.Timer | None = None
        self._external_robot_forward_in_flight = False
        self._external_robot_forward_dirty = False
        self._latest_external_robot_blendshapes: dict[str, float] = {}

    def set_default_robot_forwarding(self, enabled: bool) -> None:
        with self._lock:
            self._external_state["robotForwardingEnabled"] = bool(enabled)

    def register_sse_client(self, client: SseClient) -> None:
        with self._lock:
            self._sse_clients.add(client)

    def unregister_sse_client(self, client: SseClient) -> None:
        with self._lock:
            self._sse_clients.discard(client)
        client.close()

    def viewer_count(self) -> int:
        with self._lock:
            return len(self._sse_clients)

    def add_external_ws_client(self) -> None:
        with self._lock:
            self._external_ws_connections += 1
        self.broadcast_stream_status()

    def remove_external_ws_client(self) -> None:
        with self._lock:
            self._external_ws_connections = max(0, self._external_ws_connections - 1)
        self.broadcast_stream_status()

    def create_stream_snapshot(self) -> dict:
        with self._lock:
            return {
                "websocket_path": self.stream_ws_path,
                "websocket_url": build_stream_websocket_url(
                    f"http://{self.host}:{self.port}",
                    self.stream_ws_path,
                ),
                "connections": self._external_ws_connections,
                "last_message_at": self._external_state["lastMessageAt"],
                "last_blendshape_count": self._external_state["lastBlendshapeCount"],
                "robot_forwarding_enabled": self._external_state["robotForwardingEnabled"],
                "last_robot_forward_at": self._external_state["lastRobotForwardAt"],
                "last_robot_forward_count": self._external_state["lastRobotForwardCount"],
                "last_robot_forward_error": self._external_state["lastRobotForwardError"],
            }

    def create_hub_snapshot(self) -> dict:
        return {
            "connected_viewers": self.viewer_count(),
            "stream": self.create_stream_snapshot(),
        }

    def _broadcast(self, event: str, data: dict) -> None:
        with self._lock:
            clients = list(self._sse_clients)
        for client in clients:
            client.send(event, data)

    def broadcast_stream_status(self) -> None:
        self._broadcast("stream_status", self.create_stream_snapshot())

    def apply_external_value_state(self, frame: dict) -> dict[str, float]:
        with self._lock:
            if frame.get("reset_missing") is False:
                self._external_frame_values = merge_canonical_value_maps(
                    self.coefficient_catalog,
                    self._external_frame_values,
                    frame.get("values") or {},
                )
            else:
                self._external_frame_values = dict(frame.get("values") or {})
            return dict(self._external_frame_values)

    def set_external_robot_forwarding_enabled(self, enabled: bool) -> dict:
        timer_to_cancel = None
        should_schedule = False
        latest_values: dict[str, float] = {}

        with self._lock:
            self._external_state["robotForwardingEnabled"] = bool(enabled)
            self._external_state["lastRobotForwardError"] = None
            if not enabled:
                self._external_robot_forward_dirty = False
                timer_to_cancel = self._external_robot_forward_timer
                self._external_robot_forward_timer = None
            elif self._latest_external_robot_blendshapes:
                should_schedule = True
                latest_values = dict(self._latest_external_robot_blendshapes)

        if timer_to_cancel:
            timer_to_cancel.cancel()
        if should_schedule:
            self.schedule_external_robot_forward(latest_values)

        snapshot = self.create_stream_snapshot()
        self._broadcast("stream_status", snapshot)
        return snapshot

    def schedule_external_robot_forward(self, blendshapes: dict[str, float]) -> None:
        with self._lock:
            self._latest_external_robot_blendshapes = dict(blendshapes or {})
            if not self._external_state["robotForwardingEnabled"]:
                return

            self._external_robot_forward_dirty = True
            if self._external_robot_forward_timer or self._external_robot_forward_in_flight:
                return

            self._external_robot_forward_timer = threading.Timer(
                self.robot_forward_interval_ms / 1000.0,
                self.flush_external_robot_forward,
            )
            self._external_robot_forward_timer.daemon = True
            self._external_robot_forward_timer.start()

    def flush_external_robot_forward(self) -> None:
        with self._lock:
            self._external_robot_forward_timer = None

            if not self._external_state["robotForwardingEnabled"] or not self._external_robot_forward_dirty:
                return

            blendshapes = dict(self._latest_external_robot_blendshapes)
            robot_blendshapes = filter_robot_coefficients(
                blendshapes,
                self.coefficient_catalog,
                include_defaults=True,
            )
            if not robot_blendshapes:
                self._external_robot_forward_dirty = False
                self._external_state["lastRobotForwardCount"] = 0
                return

            self._external_robot_forward_dirty = False
            self._external_robot_forward_in_flight = True

        try:
            result = self.robot_controller.set_arkit(robot_blendshapes)
            with self._lock:
                self._external_state["lastRobotForwardAt"] = utc_now_iso()
                self._external_state["lastRobotForwardCount"] = result.get("sent_count", len(robot_blendshapes))
                self._external_state["lastRobotForwardError"] = None
        except Exception as error:
            with self._lock:
                self._external_state["lastRobotForwardError"] = str(error)
        finally:
            should_schedule = False
            latest_values: dict[str, float] = {}
            with self._lock:
                self._external_robot_forward_in_flight = False
                if (
                    self._external_state["robotForwardingEnabled"]
                    and self._external_robot_forward_dirty
                    and self._external_robot_forward_timer is None
                ):
                    should_schedule = True
                    latest_values = dict(self._latest_external_robot_blendshapes)

            self.broadcast_stream_status()
            if should_schedule:
                self.schedule_external_robot_forward(latest_values)

    def process_external_frame(self, frame: dict) -> tuple[dict, dict]:
        current_values = self.apply_external_value_state(frame)
        received_at = utc_now_iso()

        with self._lock:
            self._external_state["lastMessageAt"] = received_at
            self._external_state["lastBlendshapeCount"] = len(current_values)

        self.schedule_external_robot_forward(current_values)

        status = self.create_stream_snapshot()
        event = {
            "ok": True,
            "payload": frame,
            "values": frame.get("values") or {},
            "blendshapes": frame.get("values") or {},
            "bones": frame.get("bones") or {},
            "received_at": received_at,
            "status": status,
        }
        self._broadcast("stream_frame", event)
        self._broadcast("stream_status", status)
        return status, event


class RobotAutoConnector:
    def __init__(
        self,
        *,
        robot_controller: HeadSdkController,
        host: str,
        port: int,
        enabled: bool,
        interval_s: float = ROBOT_AUTO_CONNECT_INTERVAL_S,
    ):
        self.robot_controller = robot_controller
        self.host = str(host).strip()
        self.port = int(port)
        self.enabled = bool(enabled)
        self.interval_s = float(interval_s)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            print("[control-hub:py] robot auto-connect disabled")
            return

        print(
            "[control-hub:py] robot auto-connect enabled: "
            f"{self.host}:{self.port} / interval={self.interval_s:.0f}s"
        )
        self._thread = threading.Thread(
            target=self._run_loop,
            name="robot-auto-connector",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                status = self.robot_controller.status()
                if status.get("connected"):
                    pass
                else:
                    result = self.robot_controller.connect(self.host, self.port)
                    if result.get("connected"):
                        print(
                            "[control-hub:py] robot auto-connect success: "
                            f"{self.host}:{self.port}"
                        )
                    else:
                        print(
                            "[control-hub:py] robot auto-connect failed: "
                            f"{self.host}:{self.port}"
                        )
            except Exception as error:
                print(
                    "[control-hub:py] robot auto-connect failed: "
                    f"{self.host}:{self.port} / {error}"
                )

            self._stop_event.wait(self.interval_s)


def send_websocket_frame(connection: socket.socket, opcode: int, payload: bytes = b"") -> None:
    body = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload)
    if len(body) < 126:
        header = bytes([0x80 | opcode, len(body)])
    elif len(body) < 65536:
        header = bytes([0x80 | opcode, 126]) + struct.pack("!H", len(body))
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack("!Q", len(body))
    connection.sendall(header + body)


def consume_websocket_frames(buffer: bytes, on_frame) -> tuple[bytes, bool]:
    offset = 0
    keep_running = True

    while offset + 2 <= len(buffer):
        first = buffer[offset]
        second = buffer[offset + 1]
        fin = (first & 0x80) != 0
        opcode = first & 0x0F
        masked = (second & 0x80) != 0
        payload_length = second & 0x7F
        header_length = 2

        if not fin:
            raise ValueError("Fragmented websocket frames are not supported")

        if payload_length == 126:
            if offset + 4 > len(buffer):
                break
            payload_length = struct.unpack("!H", buffer[offset + 2 : offset + 4])[0]
            header_length = 4
        elif payload_length == 127:
            if offset + 10 > len(buffer):
                break
            payload_length = struct.unpack("!Q", buffer[offset + 2 : offset + 10])[0]
            header_length = 10

        mask_length = 4 if masked else 0
        frame_length = header_length + mask_length + payload_length
        if offset + frame_length > len(buffer):
            break

        mask_offset = offset + header_length
        payload_offset = mask_offset + mask_length
        payload = bytearray(buffer[payload_offset : payload_offset + payload_length])

        if masked:
            mask = buffer[mask_offset : mask_offset + 4]
            for index in range(payload_length):
                payload[index] ^= mask[index % 4]

        keep_running = on_frame(opcode, bytes(payload))
        offset += frame_length
        if not keep_running:
            break

    return buffer[offset:], keep_running


class HubRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hub_state: HubState | None = None
    runtime_config: dict | None = None

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        print(f"[control-hub:py] {self.address_string()} - {format % args}")

    @property
    def hub(self) -> HubState:
        if self.hub_state is None:
            raise RuntimeError("Hub state is not configured")
        return self.hub_state

    def _send_json(self, status_code: int, data: dict) -> None:
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length > 1024 * 1024:
            raise ValueError("Request body too large")
        raw = self.rfile.read(content_length).decode("utf-8").strip() if content_length else ""
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _write_sse_event(self, event: str, data: dict) -> None:
        message = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(message)
        self.wfile.flush()

    def _matches_stream_ws_path(self, path: str) -> bool:
        return path in {self.hub.stream_ws_path, "/", *LEGACY_STREAM_WS_PATHS}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        path = parsed.path or "/"
        upgrade = str(self.headers.get("Upgrade") or "").lower()

        if upgrade == "websocket" and self._matches_stream_ws_path(path):
            self._handle_websocket_upgrade()
            return

        if path == STREAM_STATUS_PATH:
            self._send_json(200, {"ok": True, "stream": self.hub.create_stream_snapshot()})
            return

        if path == "/events":
            self._handle_sse_events()
            return

        if path == "/robot/status":
            self._send_json(200, {"ok": True, "robot": self.hub.robot_controller.status()})
            return

        self._send_not_found()

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path or "/"

        if path == STREAM_ROBOT_FORWARDING_PATH:
            try:
                raw = self._read_json_body()
                enabled = raw.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be a boolean")
                snapshot = self.hub.set_external_robot_forwarding_enabled(enabled)
                self._send_json(200, {"ok": True, "stream": snapshot})
            except Exception as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return

        if path == STREAM_FRAME_PATH:
            try:
                raw = self._read_json_body()
                frame = build_external_frame(raw, coefficient_catalog=self.hub.coefficient_catalog)
                snapshot, _ = self.hub.process_external_frame(frame)
                self._send_json(200, {"ok": True, "payload": frame, "stream": snapshot})
            except Exception as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return

        if path == "/robot/connect":
            try:
                raw = self._read_json_body()
                robot_config = (self.runtime_config or {}).get("robot", {})
                host = str(raw.get("host") or robot_config.get("host") or "127.0.0.1").strip()
                port = int(raw.get("port") or robot_config.get("port") or 2543)
                status = self.hub.robot_controller.connect(host, port)
                self._send_json(200, {"ok": True, "robot": status})
            except Exception as error:
                self._send_json(500, {"ok": False, "error": str(error)})
            return

        if path == "/robot/disconnect":
            try:
                status = self.hub.robot_controller.disconnect()
                self._send_json(200, {"ok": True, "robot": status})
            except Exception as error:
                self._send_json(500, {"ok": False, "error": str(error)})
            return

        if path == "/robot/set-arkit":
            try:
                raw = self._read_json_body()
                values = build_external_frame(raw, coefficient_catalog=self.hub.coefficient_catalog).get("values") or {}
                blendshapes = filter_robot_coefficients(
                    values,
                    self.hub.coefficient_catalog,
                    include_defaults=True,
                )
                if blendshapes:
                    result = self.hub.robot_controller.set_arkit(blendshapes)
                else:
                    result = self.hub.robot_controller.status()
                    result = {
                        **result,
                        "sent_count": 0,
                        "skipped": True,
                    }
                self._send_json(200, {"ok": True, "robot": result})
            except Exception as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return

        if path == ROBOT_RELOAD_MAPPING_PATH:
            try:
                raw = self._read_json_body()
                result = self.hub.robot_controller.reload_bs2servo_mapping(raw.get("path"))
                self._send_json(200, {"ok": True, "robot": result})
            except Exception as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return

        if path == "/robot/reload-adapter-config":
            try:
                raw = self._read_json_body()
                result = self.hub.robot_controller.reload_adapter_config(raw.get("path"))
                self._send_json(200, {"ok": True, "robot": result})
            except Exception as error:
                self._send_json(400, {"ok": False, "error": str(error)})
            return

        self._send_not_found()

    def _handle_sse_events(self) -> None:
        client = SseClient()
        self.hub.register_sse_client(client)

        try:
            self._send_sse_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            self._write_sse_event(
                "ready",
                {
                    "ok": True,
                    "viewer_count": self.hub.viewer_count(),
                    "snapshot": self.hub.create_hub_snapshot(),
                },
            )

            while True:
                try:
                    item = client.queue.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue

                if item is None:
                    break

                event, data = item
                self._write_sse_event(event, data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unregister_sse_client(client)

    def _handle_websocket_upgrade(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return

        accept = base64.b64encode(hashlib.sha1(f"{key}{WS_GUID}".encode("utf-8")).digest()).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode("utf-8")
        self.connection.sendall(response)
        self.hub.add_external_ws_client()

        keep_running = True
        buffer = b""
        try:
            while keep_running:
                chunk = self.connection.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                buffer, keep_running = consume_websocket_frames(buffer, self._handle_websocket_frame)
        except (ConnectionResetError, OSError, ValueError) as error:
            print(f"[control-hub:py] websocket closed: {error}")
        finally:
            self.hub.remove_external_ws_client()
            try:
                self.connection.close()
            except OSError:
                pass

    def _handle_websocket_frame(self, opcode: int, payload: bytes) -> bool:
        if opcode == 0x1:
            try:
                raw_payload = json.loads(payload.decode("utf-8"))
                frame = build_external_frame(raw_payload, coefficient_catalog=self.hub.coefficient_catalog)
                self.hub.process_external_frame(frame)
            except Exception as error:
                print(f"[control-hub:py] websocket frame ignored: {error}")
            return True

        if opcode == 0x8:
            send_websocket_frame(self.connection, 0x8)
            return False

        if opcode == 0x9:
            send_websocket_frame(self.connection, 0xA, payload)
            return True

        return True

    def _send_not_found(self) -> None:
        self._send_json(
            404,
            {
                "ok": False,
                "error": "Not found",
                "routes": [
                    "/events",
                    "/robot/status",
                    "/robot/connect",
                    "/robot/disconnect",
                    "/robot/set-arkit",
                    ROBOT_RELOAD_MAPPING_PATH,
                    "/robot/reload-adapter-config",
                    "/status",
                    "/robot-forwarding",
                    "/frame",
                    f"{self.hub.stream_ws_path} (websocket)",
                    "/external (websocket legacy alias)",
                ],
            },
        )


class HubHttpServer(ThreadingHTTPServer):
    daemon_threads = True


def build_arg_parser(default_config_path: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python control hub for the remote avatar viewer")
    parser.add_argument(
        "--config",
        default=str(default_config_path),
        help="Path to config.yaml",
    )
    parser.add_argument("--host", help="Override hub bind host")
    parser.add_argument("--port", type=int, help="Override hub bind port")
    parser.add_argument(
        "--adapter-config",
        help="Override robot adapter YAML path",
    )
    return parser


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    default_config_path = root_dir / "server_utils" / "config.yaml"
    parser = build_arg_parser(default_config_path)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    runtime_config = load_remote_config(config_path)
    coefficient_catalog, coefficient_path = load_coefficient_catalog(config_path, runtime_config)

    adapter_config_path = resolve_local_path(
        config_path.parent,
        args.adapter_config or runtime_config.get("backend", {}).get("adapterConfigPath"),
        root_dir / "viewer" / "robot" / "robot-adapter.yaml",
    )

    host = args.host or runtime_config["hub"]["host"]
    port = args.port or runtime_config["hub"]["port"]

    robot_controller = HeadSdkController(
        adapter_config_path=adapter_config_path,
        coefficient_catalog=coefficient_catalog,
    )
    robot_auto_connector = RobotAutoConnector(
        robot_controller=robot_controller,
        host=runtime_config["robot"]["host"],
        port=runtime_config["robot"]["port"],
        enabled=runtime_config["robot"]["avatarControlOnLoad"],
    )

    hub_state = HubState(
        host=host,
        port=port,
        stream_ws_path=runtime_config["stream"]["websocketPath"],
        robot_forward_interval_ms=runtime_config["stream"]["robotForwardIntervalMs"],
        coefficient_catalog=coefficient_catalog,
        robot_controller=robot_controller,
    )
    hub_state.set_default_robot_forwarding(runtime_config["stream"]["controlRobotOnLoad"])

    HubRequestHandler.hub_state = hub_state
    HubRequestHandler.runtime_config = runtime_config
    try:
        server = HubHttpServer((host, port), HubRequestHandler)
    except OSError as error:
        if getattr(error, "errno", None) == 98:
            print(
                "[control-hub:py] failed to bind "
                f"http://{host}:{port}: address already in use"
            )
            print(
                "[control-hub:py] stop the existing hub or change "
                f"'hub.port' in {config_path}"
            )
            raise SystemExit(1) from error
        raise

    print(f"[control-hub:py] config: {config_path}")
    print(f"[control-hub:py] coefficient config: {coefficient_path}")
    print(f"[control-hub:py] adapter config: {adapter_config_path}")
    print(f"[control-hub:py] listening on http://{host}:{port}")
    print(
        "[control-hub:py] stream websocket: "
        f"{build_stream_websocket_url(f'http://{host}:{port}', hub_state.stream_ws_path)}"
    )
    if hub_state.stream_ws_path != "/":
        print(f"[control-hub:py] websocket root alias: ws://{host}:{port}/")
    print(f"[control-hub:py] websocket legacy alias: ws://{host}:{port}/external")
    print(f"[control-hub:py] robot config source: {config_path}")
    robot_auto_connector.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        robot_auto_connector.stop()
        server.server_close()


if __name__ == "__main__":
    main()
