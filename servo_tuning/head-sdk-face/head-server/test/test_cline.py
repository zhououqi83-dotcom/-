import grpc
import json
import os
import sys

# 设置路径
script_dir = os.path.dirname(__file__)
sys.path.extend([
    script_dir,
    os.path.join(script_dir, 'src/grpc_config')
])

import rena2_sdk_api.head_service_pb2 as head_service_pb2
import rena2_sdk_api.head_service_pb2_grpc as head_service_pb2_grpc

# 配置目标地址
ROBOT_IP_CLI = "localhost"
PORT_STATE = 2543

# 创建 gRPC 通道和 stub
channel_servo = grpc.insecure_channel(f'{ROBOT_IP_CLI}:{PORT_STATE}')
stub_servo = head_service_pb2_grpc.HeadArkitStreamStub(channel_servo)

# 读取当前舵机值
def get_servo_state():
    try:
        response = stub_servo.GetHeadState(head_service_pb2.Empty())
        servo_dict = json.loads(response.servo_json)
        # print("当前舵机状态：")
        # for k, v in servo_dict.items():
        #     print(f"  {k}: {v:.3f}")
        return servo_dict
    except grpc.RpcError as e:
        print(f"gRPC error: {e.details()}")

# 设置舵机值
def set_servo_state(params: dict):
    try:
        json_str = json.dumps(params)
        request = head_service_pb2.HeadArkitMessage(servo_json=json_str)
        stub_servo.SetHeadState(request)
        # print("已发送舵机指令:", json_str)
    except grpc.RpcError as e:
        print(f"gRPC error: {e.details()}")

# 释放控制
def release_control():
    try:
        response = stub_servo.ReleaseControl(head_service_pb2.Empty())
        print("控制已释放")
        return response
    except grpc.RpcError as e:
        print(f"gRPC error in ReleaseControl: {e.details()}")
        return None

# 主流程：读取 → 设置 → 再读取
if __name__ == "__main__":
    import time
    release_control()
    print("【1】初始状态")
    get_servo_state()
    print("\n【2】设置 head_dian = 1")
    set_servo_state({"head_dian": 1})
    time.sleep(5)
    print("\n【2-1】设置 head_dian = 0.8")
    set_servo_state({"head_dian": 0.8})
    time.sleep(5)
    print("\n【3】更新后状态")
    get_servo_state()
    release_control()
