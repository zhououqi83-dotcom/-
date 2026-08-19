from __future__ import annotations

import threading
import time
from collections import namedtuple
import logging
from logging import getLogger
from typing import Union, Dict, Type, List, Callable
import json
import math

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc._channel import _InactiveRpcError

from rena2_sdk_api import head_service_pb2
from rena2_sdk_api import head_service_pb2_grpc

import sys
import os
script_path = os.path.dirname(__file__)
sys.path.append(os.path.join(script_path))
from head_sdk.bs2servo import BStoServos, get_bs_dict ,load_yaml_mapping
# ------------------------------------------------------------------------------
# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class HeadSDK:
    """
    HeadSDK 类用于管理与 Droid Robot Head 的连接和交互，控制表情相关舵机。

    此类负责：
    - 通过 gRPC 建立并维护与机器人的连接。
    - 在后台同步舵机状态以保持数据更新。
    - 提供常用函数，用于获取当前舵机位置、发送舵机系数等。
    """
    
    # 舵机状态信息结构
    ServoState = namedtuple('ServoState', ['parameters', 'timestamp'])

    _instances_by_host: Dict[str, "HeadSDK"] = {}

    def __new__(cls: Type[HeadSDK], host: str, sdk_port: int = 2543, force_new: bool = False) -> "HeadSDK":
        key = f"{host}:{sdk_port}"
        if not force_new and key in cls._instances_by_host:
            instance = cls._instances_by_host[key]
            if instance._grpc_connected:
                return instance
            else:
                del cls._instances_by_host[key]

        instance = super().__new__(cls)
        cls._instances_by_host[key] = instance
        return instance

    def __init__(
        self,
        host: str,
        sdk_port: int = 2543,
        force_new: bool = False
    ) -> None:
        """初始化与机器人的连接。

        Args:
            host: 机器人的 IP 地址或主机名。
            sdk_port: gRPC 的 SDK 端口。默认为 2543
        """
        self._logger = getLogger(__name__)

        if hasattr(self, "_initialized") and not force_new:
            return

        # 基本连接属性
        self._host = host
        self._sdk_port = sdk_port
        self._grpc_connected = False
        self._initialized = True
        self._grpc_channel = None
        self._grpc_stub = None
        
        # 线程相关属性
        self._stop_flag = None
        self._sync_thread = None
        self._audit_thread = None
        
        # 状态流相关属性
        self._state_callback = None
        self._state_stream_thread = None
        self._state_stream_running = False
        
        # 舵机状态
        self.state = None

        # 连接到机器人
        self._initialized = True
        self.connect()

    def connect(self) -> None:
        """连接到机器人。"""
        if self._grpc_connected:
            self._logger.warning("Already connected to Head.")
            return

        self._grpc_channel = grpc.insecure_channel(f"{self._host}:{self._sdk_port}")
        self._grpc_stub = head_service_pb2_grpc.HeadArkitStreamStub(self._grpc_channel)
        self._stop_flag = threading.Event()

        try:
            self._get_state()
        except ConnectionError:
            self._logger.error(
                f"Could not connect to Head with IP address {self._host}, "
                "check that the SDK server is running and that the IP is correct."
            )
            self._grpc_connected = False
            return

        self._sync_thread = threading.Thread(target=self._start_sync_in_bg)
        self._sync_thread.daemon = True
        self._sync_thread.start()

        self._audit_thread = threading.Thread(target=self._audit)
        self._audit_thread.daemon = True
        self._audit_thread.start()

        self._grpc_connected = True
        self._logger.info("Connected to Head.")

    def disconnect(self, lost_connection: bool = False) -> None:
        """断开与机器人服务器的连接。

        Args:
            lost_connection: 如果为 True，表示连接意外丢失。
        """
        if self._host in self._instances_by_host:
            del self._instances_by_host[self._host]

        if not self._grpc_connected:
            self._logger.warning("Already disconnected from Head.")
            return

        if hasattr(self, '_stop_flag'):
            self._stop_flag.set()
            if hasattr(self, '_sync_thread') and self._sync_thread.is_alive():
                self._sync_thread.join(timeout=2.0)
                if self._sync_thread.is_alive():
                    self._logger.warning("Sync thread did not stop in time.")
            if hasattr(self, '_audit_thread') and self._audit_thread.is_alive():
                self._audit_thread.join(timeout=2.0)
                if self._audit_thread.is_alive():
                    self._logger.warning("Audit thread did not stop in time.")

        self._grpc_connected = False
        if self._grpc_channel:
            self._grpc_channel.close()
            self._grpc_channel = None

        self._logger.info("Disconnected from Head.")
    # 释放控制
    def release_control(self):
        if not self._grpc_connected:
            return
        try:
            self._grpc_stub.ReleaseControl(head_service_pb2.Empty())
            print("控制已释放，并锁定给自己（其他客户端暂时无法控制）")
        except grpc.RpcError as e:
            print(f"ReleaseControl error: {e.details()}")
            
    def _get_state(self) -> None:
        """获取舵机状态信息"""
        try:
            response = self._grpc_stub.GetHeadState(Empty())
            parameters = json.loads(response.servo_json)
            self.state = self.ServoState(
                parameters=parameters,
                timestamp=time.time()
            )
        except _InactiveRpcError as e:
            self._logger.error(f"gRPC error: {e.code()}: {e.details()}")
            return ConnectionError("Failed to get servo state")
        except json.JSONDecodeError as e:
            self._logger.error(f"JSON decode error: {str(e)}")
            return ConnectionError("Invalid servo state JSON")
        except Exception as e:
            self._logger.error(f"Error getting servo state: {str(e)}")
            return ConnectionError(f"Error getting servo state: {str(e)}")

    def _start_sync_in_bg(self) -> None:
        """在后台同步舵机状态"""
        self._logger.info("Starting state sync thread")
        
        while not self._stop_flag.is_set():
            try:
                response = self._grpc_stub.GetHeadState(Empty())
                parameters = json.loads(response.servo_json)
                self.state = self.ServoState(
                    parameters=parameters,
                    timestamp=time.time()
                )
                time.sleep(0.01)  # 降低 CPU 使用率
            except _InactiveRpcError as e:
                if self._stop_flag.is_set():
                    break
                self._logger.warning(f"gRPC error during state sync: {e.code()}: {e.details()}")
                time.sleep(1.0)
            except json.JSONDecodeError as e:
                if self._stop_flag.is_set():
                    break
                self._logger.warning(f"JSON decode error during state sync: {str(e)}")
                time.sleep(1.0)
            except Exception as e:
                if self._stop_flag.is_set():
                    break
                self._logger.warning(f"Error during state sync: {str(e)}")
                time.sleep(1.0)
        
        self._logger.info("State sync thread stopped")

    def _audit(self) -> None:
        """监控连接状态"""
        self._logger.info("Starting connection audit thread")
        
        while not self._stop_flag.is_set():
            try:
                time.sleep(5.0)
                if not self._grpc_connected:
                    break
                self._grpc_stub.GetHeadState(Empty())
            except _InactiveRpcError as e:
                if self._stop_flag.is_set():
                    break
                self._logger.error(f"gRPC error during connection audit: {e.code()}: {e.details()}")
                self.disconnect(lost_connection=True)
                break
            except Exception as e:
                if self._stop_flag.is_set():
                    break
                self._logger.error(f"Error during connection audit: {str(e)}")
                self.disconnect(lost_connection=True)
                break
        
        self._logger.info("Connection audit thread stopped")

    def is_connected(self) -> bool:
        """检查是否已连接到机器人

        Returns:
            bool: 是否已连接
        """
        return self._grpc_connected
    

    def get_servo_positions(self) -> Dict[str, float]:
        """获取当前舵机位置

        Returns:
            Dict[str, float]: 包含舵机名称和对应位置的字典

        Raises:
            RuntimeError: 如果资产或状态尚未同步
        """
        if not self._grpc_connected:
            return RuntimeError("未连接到机器人")
        
        if not self.state:
            return RuntimeError("舵机状态尚未同步")
        if not self.state:
            return RuntimeError("舵机状态尚未同步")
        
        response = self._grpc_stub.GetHeadState(Empty())
        parameters = json.loads(response.servo_json)
        self.state = self.ServoState(
            parameters=parameters,
            timestamp=time.time()
        )
        
        return self.state.parameters

    def set_servo_positions(self, positions: dict) -> bool:
        if not self._grpc_connected:
            return False

        try:
            servo_json = json.dumps(positions, ensure_ascii=False)
            request = head_service_pb2.HeadArkitMessage(servo_json=servo_json)
            self._grpc_stub.SetHeadState(request)
            return True
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.RESOURCE_EXHAUSTED:
                print(f"Set failed: {e.details()}")
            else:
                print(f"Set error: {e}")
            return False
        except Exception as e:
            print(f"Set failed: {e}")
            return False

    def interpolate_servo_positions(self, target_dict: Dict[str, float], duration: float = 1.0):
        '''
        余弦插值设置位置组并播放
        '''
        if not self._grpc_connected:
            return RuntimeError("未连接到机器人")

        if not self.state:
            self._get_state()

        start_dict = self.state.parameters.copy()
        print(start_dict["head_dian"],target_dict["head_dian"])
        n = int(duration / 0.02)
        data_list = self.generate_curve_points_all(start_dict,target_dict, n)
        print(data_list)
        stop_event = threading.Event()
        self._interpolation_stop_event = stop_event
        self._interpolation_paused = False
        def interpolation_worker():
            for data in data_list:
                if stop_event.is_set():
                    break
                while self._interpolation_paused and not stop_event.is_set():
                    time.sleep(0.02)
                try:
                    self.set_servo_positions(data)
                    print(data)
                except Exception as e:
                    self._logger.error(f"插值发送失败: {e}")
                    break
                time.sleep(0.02)
            self._interpolation_stop_event = None
            self._interpolation_paused = False
            self._logger.info("表情插值完成")

        thread = threading.Thread(target=interpolation_worker)
        thread.daemon = True
        thread.start()
    def generate_curve_points_all(self, current_dict, target_dict, n = 5):
        '''
        两个舵机组（两个表情）之间 生成 n 个中间值 生成正弦曲线 
        current_servo: 当前舵机坐标（字典格式）
        target_servo: 目标舵机坐标（字典格式）
        返回 n 个中间值的列表，每个值为字典格式
        n = 运行时间/0.02 
        '''
        # 初始化结果列表
        result = []

        # 对每个舵机参数生成 n 个中间值
        for key in target_dict:
            target_value = target_dict[key]
            current_value = current_dict[key]
            curve_points = self.generate_curve_points(current_value, target_value , n)
            result.append(curve_points)
            # 将结果转换为目标格式
        intermediate_dicts = []
        for i in range(n): 
            intermediate_dict = {}
            for j, key in enumerate(target_dict):
                intermediate_dict[key] = float(f"{result[j][i]:.2f}")
            intermediate_dicts.append(intermediate_dict)
            # print('intermediate_dicts:',intermediate_dicts)

        # 将字典转换为字符串格式
        # servo_dict_str_list = [json.dumps(d, separators=(',', ':')) for d in intermediate_dicts]
        return intermediate_dicts

    def generate_curve_points(self, start, end, n=5):
        '''
        正弦曲线插值（不使用 numpy）
        start: 起始值
        end: 结束值
        n: 中间插值点数量（不包含起点和终点）
        返回：包含 n 个插值点的列表（不含起点和终点）
        '''
        if n == 0:
            return [end]

        points = []
        mid = (start + end) / 2
        amplitude = abs(end - start) / 2

        # 起点小于终点：正方向插值
        if start < end:
            for i in range(1, n + 1):
                t = i / (n + 1)  # 从 1/(n+1) 到 n/(n+1)
                angle = -math.pi / 2 + t * math.pi  # 从 -pi/2 到 pi/2
                value = mid + amplitude * math.sin(angle)
                points.append(round(value, 3))
        else:
            # 起点大于终点：反方向插值
            for i in range(1, n + 1):
                t = i / (n + 1)
                angle = math.pi / 2 - t * math.pi  # 从 pi/2 到 -pi/2
                value = mid + amplitude * math.sin(angle)
                points.append(round(value, 3))

        return points
    def cancel_interpolation(self) -> bool:
        '''
        取消插值运动
        '''
        if self._interpolation_stop_event:
            self._interpolation_stop_event.set()
            self._logger.info("已取消插值运动")
            return True
        return False

    def pause_interpolation(self) -> bool:
        '''
        暂停插值运动
        '''
        if self._interpolation_stop_event and not self._interpolation_paused:
            self._interpolation_paused = True
            self._logger.info("已暂停插值运动")
            return True
        return False

    def resume_interpolation(self) -> bool:
        '''
        恢复插值运动
        '''
        if self._interpolation_stop_event and self._interpolation_paused:
            self._interpolation_paused = False
            self._logger.info("已恢复插值运动")
            return True
        return False

    
    def start_state_stream(self, callback: Callable[[Dict], None]) -> None:
        """启动状态流，定期将舵机状态传递给回调函数

        Args:
            callback: 状态回调函数，接收状态字典作为参数

        Raises:
            RuntimeError: 如果未连接或请求失败
        """
        if not self._grpc_connected:
            return RuntimeError("未连接到机器人")
        
        if self._state_stream_running:
            self._logger.warning("State stream already running")
            return
        
        self._state_callback = callback
        self._state_stream_thread = threading.Thread(target=self._state_stream_worker)
        self._state_stream_thread.daemon = True
        self._state_stream_running = True
        self._state_stream_thread.start()
        
        self._logger.info("State stream started")

    def stop_state_stream(self) -> None:
        """停止状态流

        Raises:
            RuntimeError: 如果未连接
        """
        if not self._grpc_connected:
            return RuntimeError("未连接到机器人")
        
        if not self._state_stream_running:
            self._logger.warning("State stream not running")
            return
        
        self._state_stream_running = False
        if self._state_stream_thread.is_alive():
            self._state_stream_thread.join(timeout=1.0)
            if self._state_stream_thread.is_alive():
                self._logger.warning("State stream thread did not stop in time.")
        self._logger.info("State stream stopped")

    def _state_stream_worker(self) -> None:
        """状态流工作线程"""
        while self._state_stream_running and not self._stop_flag.is_set():
            try:
                if self.state and self._state_callback:
                    state_dict = {
                        "parameters": self.state.parameters,
                        "timestamp": self.state.timestamp,
                        "system_tic": int(time.time() * 1000)
                    }
                    try:
                        self._state_callback(state_dict)
                    except Exception as e:
                        self._logger.error(f"Callback error: {str(e)}")
                time.sleep(0.1)  # 降低 CPU 使用率
            except Exception as e:
                self._logger.warning(f"Error processing state stream: {str(e)}")
                time.sleep(0.1)
    
    def reload_bs2servo_mapping(self, path:str = None):
        return load_yaml_mapping(path)
    
    def set_arkit_positions(self, positions: Union[List[float], Dict[str, float]]):
        """接收 ARKit 61 list 或字典，转换为舵机服务
        
        Args:
            positions: ARKit blendshape 数据，可以是：
                - List[float]: 61 个 blendshape 值的列表
                - Dict[str, float]: blendshape 名称到值的字典
        """
        # 统一转换为字典
        if isinstance(positions, list):
            bs_dict = get_bs_dict(positions)  # 列表转字典
        elif isinstance(positions, dict):
            bs_dict = positions  # 已经是字典，直接使用
        else:
            raise TypeError(f"positions must be list or dict, got {type(positions)}")
        
        # 转换为舵机数据
        # print("get bs",bs_dict)
        servo_dict_all = BStoServos(bs_dict)
        # print("put servo",servo_dict_all)
        self.set_servo_positions(servo_dict_all)
        self._logger.info("Send over: %s", servo_dict_all)
        return servo_dict_all
    
    
