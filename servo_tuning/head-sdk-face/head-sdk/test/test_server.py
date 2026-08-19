#!/usr/bin/env python3
"""
虚拟 Head gRPC 服务（用于本地测试）。

实现与 head-server 一致的 3 个接口：
1) SetHeadState
2) GetHeadState
3) ReleaseControl

默认监听 0.0.0.0:2543，可直接配合 test_head.py / test_client.py 使用。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent import futures
from typing import Dict

import grpc


def _prepare_proto_import() -> None:
    """优先加载仓库内 head-server 的 proto 代码，保证与服务端定义一致。"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
    grpc_config_dir = os.path.join(repo_root, "head-server", "src", "grpc_config")
    if grpc_config_dir not in sys.path:
        sys.path.insert(0, grpc_config_dir)


_prepare_proto_import()
from head_sdk.head_sdk import head_service_pb2 as pb2  
from head_sdk.head_sdk import head_service_pb2_grpc as pb2_grpc  

class FakeHeadArkitStream(pb2_grpc.HeadArkitStreamServicer):
    """内存态假服务，不依赖硬件。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_client = None
        self._state: Dict[str, float] = {
            "head_dian": 0.0,
            "mouth_open": 0.0,
        }

    def SetHeadState(self, request: pb2.HeadArkitMessage, context: grpc.ServicerContext) -> pb2.Empty:
        client_peer = context.peer()
        with self._lock:
            if self._current_client is not None and self._current_client != client_peer:
                context.set_details("Another client is currently controlling the robot")
                context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
                return pb2.Empty()
            if self._current_client is None:
                self._current_client = client_peer

            try:
                text = request.servo_json
                if '\\"' in text:
                    text = text.replace('\\"', '"').strip('"')
                update_data = json.loads(text) if text else {}
                print(f"[fake-head-server] received update from {client_peer}: {update_data}")
                if not isinstance(update_data, dict):
                    raise ValueError("servo_json must be a JSON object")
                for key, value in update_data.items():
                    self._state[str(key)] = float(value)
            except Exception as exc:
                context.set_details(f"Error processing JSON: {exc}")
                context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
                return pb2.Empty()

        return pb2.Empty()

    def GetHeadState(self, request: pb2.Empty, context: grpc.ServicerContext) -> pb2.HeadArkitMessage:
        with self._lock:
            payload = json.dumps(self._state, ensure_ascii=False)
        return pb2.HeadArkitMessage(servo_json=payload)

    def ReleaseControl(self, request: pb2.Empty, context: grpc.ServicerContext) -> pb2.Empty:
        with self._lock:
            self._current_client = None
        return pb2.Empty()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake head gRPC server for tests")
    parser.add_argument("--ip", default="0.0.0.0", help="Bind IP")
    parser.add_argument("--port", default="2543", help="Bind port")
    args = parser.parse_args()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_HeadArkitStreamServicer_to_server(FakeHeadArkitStream(), server)
    server.add_insecure_port(f"{args.ip}:{args.port}")
    server.start()
    print(f"[fake-head-server] listening on {args.ip}:{args.port}")

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("[fake-head-server] shutdown")
    finally:
        server.stop(0)


if __name__ == "__main__":
    main()
