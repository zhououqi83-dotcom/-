#!/usr/bin/env python3
"""
WebSocket server for Servo Tuning.

Provides real-time servo control through gRPC:
1. Web clients connect via WebSocket to get servo state
2. Pushes current servo positions dynamically (supports 15 or 25 servos)
3. Handles servo control commands from web UI
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

import grpc
from google.protobuf.empty_pb2 import Empty
import websockets

# 从 rena2_sdk_api 获取 gRPC proto 和 stub
from rena2_sdk_api import head_service_pb2, head_service_pb2_grpc
from head_sdk import bs2servo
from head_sdk.bs2servo import BStoServos, load_yaml_mapping, get_bs_dict, FaceBlendShape61

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CONNECTED_CLIENTS = set()


class ServoController:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._channel = None
        self._stub = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """连接到机器人"""
        try:
            self._channel = grpc.insecure_channel(f"{self.host}:{self.port}")
            self._stub = head_service_pb2_grpc.HeadArkitStreamStub(self._channel)

            # 测试连接 - 调用 GetHeadState
            response = self._stub.GetHeadState(Empty())
            self._connected = True
            logger.info(f"Connected to robot at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._connected = False
            self._channel = None
            self._stub = None
            return False

    def disconnect(self) -> None:
        """断开连接"""
        self._connected = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
        self._channel = None
        self._stub = None
        logger.info("Disconnected from robot")

    def release_control(self) -> bool:
        """尝试释放机器人控制权"""
        if not self._channel or not self._stub:
            return False
        try:
            self._stub.ReleaseControl(Empty())
            logger.info("Released robot control")
            return True
        except Exception as e:
            logger.error(f"Error releasing control: {e}")
            return False

    def get_servo_state(self) -> Optional[Dict[str, float]]:
        """获取当前舵机状态"""
        if not self._connected or not self._stub:
            return None
        try:
            response = self._stub.GetHeadState(Empty())
            return json.loads(response.servo_json)
        except Exception as e:
            logger.error(f"Error getting servo state: {e}")
            self._connected = False
            return None

    def set_servo_single(self, name: str, value: float) -> bool:
        """设置单个舵机"""
        if not self._connected or not self._stub:
            return False
        try:
            servo_json = json.dumps({name: float(value)})
            request = head_service_pb2.HeadArkitMessage(servo_json=servo_json)
            self._stub.SetHeadState(request)
            return True
        except Exception as e:
            logger.error(f"Error setting servo {name}: {e}")
            return False

    def set_servo_batch(self, servo_dict: Dict[str, float]) -> bool:
        """批量设置舵机"""
        if not self._connected or not self._stub:
            return False
        try:
            servo_json = json.dumps(servo_dict)
            request = head_service_pb2.HeadArkitMessage(servo_json=servo_json)
            self._stub.SetHeadState(request)
            return True
        except Exception as e:
            logger.error(f"Error setting servo batch: {e}")
            return False

    def get_status_payload(self) -> Dict[str, Any]:
        """获取状态负载"""
        return {
            "type": "connection_status",
            "connected": self._connected,
            "host": self.host,
            "port": self.port
        }


SERVO_CONTROLLER: Optional[ServoController] = None


async def register_client(websocket) -> None:
    CONNECTED_CLIENTS.add(websocket)
    logger.info(f"Client connected. Total: {len(CONNECTED_CLIENTS)}")
    await send_json_safe(websocket, SERVO_CONTROLLER.get_status_payload())


async def unregister_client(websocket) -> None:
    CONNECTED_CLIENTS.discard(websocket)
    logger.info(f"Client disconnected. Total: {len(CONNECTED_CLIENTS)}")


async def send_json_safe(websocket, payload: Dict[str, Any]) -> None:
    try:
        await websocket.send(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        logger.error(f"Failed to send: {exc}")


async def broadcast_servo_state() -> None:
    """广播舵机状态给所有客户端"""
    if not CONNECTED_CLIENTS:
        return

    state = SERVO_CONTROLLER.get_servo_state()
    if state is None:
        payload = {
            "type": "servo_state",
            "servos": {},
            "connected": False
        }
    else:
        payload = {
            "type": "servo_state",
            "servos": state,
            "connected": True
        }

    message = json.dumps(payload, ensure_ascii=False)
    tasks = [send_json_safe(client, payload) for client in CONNECTED_CLIENTS]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def handle_client(websocket, path=None) -> None:
    await register_client(websocket)

    # 发送初始舵机状态
    state = SERVO_CONTROLLER.get_servo_state()
    if state is not None:
        await send_json_safe(websocket, {
            "type": "servo_state",
            "servos": state,
            "connected": True
        })
    else:
        await send_json_safe(websocket, {
            "type": "servo_state",
            "servos": {},
            "connected": False
        })

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON: {message[:200]}")
                continue

            response = await handle_message(data, websocket)
            if response:
                await send_json_safe(websocket, response)

    except websockets.exceptions.ConnectionClosed:
        logger.info("Connection closed normally")
    except Exception as exc:
        logger.error(f"Error handling client: {exc}")
    finally:
        await unregister_client(websocket)


async def handle_message(data: Dict[str, Any], websocket) -> Optional[Dict[str, Any]]:
    """处理客户端消息"""
    if not isinstance(data, dict):
        return None

    msg_type = data.get("type")

    if msg_type == "connect":
        # 连接/重连到机器人
        host = data.get("host", SERVO_CONTROLLER.host)
        port = data.get("port", SERVO_CONTROLLER.port)

        SERVO_CONTROLLER.disconnect()
        SERVO_CONTROLLER.host = host
        SERVO_CONTROLLER.port = port

        # 先连接，再尝试释放控制权（可能之前有其他客户端持有）
        success = SERVO_CONTROLLER.connect()
        if success:
            SERVO_CONTROLLER.release_control()  # 尝试释放可能被其他客户端持有的控制权
        return {
            "type": "connection_status",
            "connected": success,
            "host": SERVO_CONTROLLER.host,
            "port": SERVO_CONTROLLER.port
        }

    elif msg_type == "disconnect":
        # 断开连接
        SERVO_CONTROLLER.disconnect()
        return {
            "type": "connection_status",
            "connected": False,
            "host": SERVO_CONTROLLER.host,
            "port": SERVO_CONTROLLER.port
        }

    elif msg_type == "get_servo_state":
        # 获取舵机状态
        state = SERVO_CONTROLLER.get_servo_state()
        if state is not None:
            return {
                "type": "servo_state",
                "servos": state,
                "connected": True
            }
        else:
            return {
                "type": "servo_state",
                "servos": {},
                "connected": False
            }

    elif msg_type == "set_servo":
        # 设置单个舵机
        name = data.get("name")
        value = data.get("value")
        if name is not None and value is not None:
            success = SERVO_CONTROLLER.set_servo_single(name, float(value))
            return {
                "type": "set_servo_response",
                "name": name,
                "value": value,
                "success": success
            }

    elif msg_type == "set_servo_batch":
        # 批量设置舵机
        servos = data.get("servos", {})
        if servos:
            servo_dict = {k: float(v) for k, v in servos.items()}
            success = SERVO_CONTROLLER.set_servo_batch(servo_dict)
            return {
                "type": "set_servo_batch_response",
                "success": success
            }

    elif msg_type == "load_mapping":
        # 加载映射文件（空路径则使用默认）
        yaml_file = data.get("yaml_file") or None
        try:
            load_yaml_mapping(yaml_file)
            # 从 bs2servo.mapping_dict 提取实际被映射使用的 bs 系数名
            bs_names_in_mapping = set()
            for servo_rules in bs2servo.mapping_dict.values():
                bs_names_in_mapping.update(servo_rules.keys())
            bs_names = sorted(list(bs_names_in_mapping))
            logger.info(f"映射中的 bs 系数: {bs_names}")
            return {
                "type": "mapping_loaded",
                "success": True,
                "blendshapes": bs_names
            }
        except Exception as e:
            logger.error(f"加载映射失败: {e}")
            return {"type": "mapping_loaded", "success": False, "error": str(e)}

    elif msg_type == "set_blendshape":
        # blendshape 控制：转换为舵机值、发送机器人、并返回 servo 值
        bs_dict = data.get("blendshapes", {})
        disabled_servos = data.get("disabled_servos", [])  # 获取禁用的舵机列表
        if not bs_dict:
            return {"type": "set_blendshape_response", "success": False}
        try:
            servo_dict = BStoServos(bs_dict)
            # 过滤掉禁用的舵机
            if disabled_servos:
                servo_dict = {k: v for k, v in servo_dict.items() if k not in disabled_servos}
            success = SERVO_CONTROLLER.set_servo_batch(servo_dict)
            return {
                "type": "set_blendshape_response",
                "success": success,
                "servos": servo_dict
            }
        except Exception as e:
            logger.error(f"Error in set_blendshape: {e}")
            return {"type": "set_blendshape_response", "success": False, "error": str(e)}

    elif msg_type == "list_yaml_files":
        # 检测当前虚拟环境路径，读取环境目录下所有 yaml 文件
        try:
            # 获取当前虚拟环境路径
            venv_path = os.environ.get('VIRTUAL_ENV')
            if not venv_path:
                # 尝试通过 sys.prefix 检测
                venv_path = sys.prefix

            yaml_files = []
            # 在虚拟环境目录中递归搜索所有 yaml 文件
            if os.path.isdir(venv_path):
                for root, dirs, files in os.walk(venv_path):
                    # 跳过 __pycache__ 和其他缓存目录
                    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', '.pytest_cache', 'node_modules')]
                    for f in files:
                        if f.endswith('.yaml') or f.endswith('.yml'):
                            yaml_files.append(os.path.join(root, f))
                logger.info(f"虚拟环境: {venv_path}, 找到 {len(yaml_files)} 个 yaml 文件")
            else:
                logger.warning(f"虚拟环境路径无效: {venv_path}")

            return {
                "type": "yaml_file_list",
                "files": yaml_files,
                "venv_path": venv_path
            }
        except Exception as e:
            logger.error(f"扫描 yaml 文件失败: {e}")
            return {"type": "yaml_file_list", "files": [], "error": str(e)}

    elif msg_type == "read_yaml_file":
        # 读取 yaml 文件内容
        yaml_path = data.get("yaml_path")
        if not yaml_path:
            return {"type": "yaml_content", "success": False, "error": "未提供文件路径"}
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return {"type": "yaml_content", "success": True, "content": content, "path": yaml_path}
        except Exception as e:
            logger.error(f"读取 yaml 文件失败: {e}")
            return {"type": "yaml_content", "success": False, "error": str(e)}

    elif msg_type == "save_yaml_file":
        # 保存 yaml 文件内容
        yaml_path = data.get("yaml_path")
        content = data.get("content")
        if not yaml_path or content is None:
            return {"type": "yaml_save_result", "success": False, "error": "参数不完整"}
        try:
            with open(yaml_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"保存 yaml 文件成功: {yaml_path}")
            return {"type": "yaml_save_result", "success": True}
        except Exception as e:
            logger.error(f"保存 yaml 文件失败: {e}")
            return {"type": "yaml_save_result", "success": False, "error": str(e)}

    return None


async def state_poll_loop() -> None:
    """定期轮询舵机状态并广播（已禁用，改为按需读取）"""
    while False:  # 禁用连续轮询
        await asyncio.sleep(0.1)
        if CONNECTED_CLIENTS and SERVO_CONTROLLER.is_connected:
            await broadcast_servo_state()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Servo Tuning WebSocket Server")
    parser.add_argument("--host", default="localhost", help="WebSocket bind host")
    parser.add_argument("--port", type=int, default=8766, help="WebSocket bind port")
    parser.add_argument(
        "--robot-host",
        default=os.environ.get("HEAD_SDK_HOST", "localhost"),
        help="Robot host",
    )
    parser.add_argument(
        "--robot-port",
        type=int,
        default=int(os.environ.get("HEAD_SDK_PORT", "2543")),
        help="Robot port",
    )
    args = parser.parse_args()

    global SERVO_CONTROLLER
    SERVO_CONTROLLER = ServoController(args.robot_host, args.robot_port)

    # 不要在启动时连接，等用户请求

    logger.info(f"Starting Servo Tuning WebSocket Server")
    logger.info(f"Listening on ws://{args.host}:{args.port}")
    logger.info(f"Robot target: {args.robot_host}:{args.robot_port} (not connected until requested)")

    server = await websockets.serve(
        handle_client,
        args.host,
        args.port,
        ping_interval=30,
        ping_timeout=10,
    )

    # 启动状态轮询任务
    asyncio.create_task(state_poll_loop())

    logger.info("Server is running. Press Ctrl+C to stop.")

    try:
        await asyncio.Future()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    finally:
        SERVO_CONTROLLER.disconnect()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    try:
        import websockets
    except ImportError:
        print("Please install websockets first: pip install websockets")
        raise SystemExit(1)

    asyncio.run(main())
