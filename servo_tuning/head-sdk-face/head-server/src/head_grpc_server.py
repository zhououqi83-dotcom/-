import os 
import sys
script_path = os.path.dirname(__file__)
sys.path.append(os.path.join(script_path))
import argparse
import json
import threading
import grpc
from concurrent import futures
import grpc_config.head_service_pb2 as head_service_pb2
import grpc_config.head_service_pb2_grpc as head_service_pb2_grpc

from servo_contrl import UniversalRobot

class HeadArkitStreamServicer(head_service_pb2_grpc.HeadArkitStreamServicer):
    def __init__(self, robot_instance, print_test_data=False):
        self.robot = robot_instance
        self.print_test_data = print_test_data
        self.lock = threading.Lock()
        self.current_client = None          # 当前控制者
        self.locked_to_client = None        # 新增：锁定给哪个客户端（释放后锁定）
        self.client_condition = threading.Condition(lock=self.lock)

    # ====================== SetHeadState ======================
    def SetHeadState(self, request, context):
        client_peer = context.peer()

        with self.lock:
            # 情况1：有锁定，且不是锁定对象 → 拒绝
            if self.locked_to_client is not None and self.locked_to_client != client_peer:
                print(f"Client {client_peer} rejected: Control is locked to {self.locked_to_client}")
                context.set_details("Control is currently locked to another client after release.")
                context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
                return head_service_pb2.Empty()

            # 情况2：没有锁定，或者就是锁定对象 → 允许并更新当前控制者
            if self.current_client != client_peer:
                print(f"Client {client_peer} took control")
            self.current_client = client_peer

            # 如果有锁定，保持锁定状态
            if self.locked_to_client is None:
                self.locked_to_client = client_peer   # 首次控制时建立锁定

        # 执行设置
        try:
            headarkit_text = request.servo_json
            if '\\"' in headarkit_text:
                headarkit_text = headarkit_text.replace('\\"', '"').strip('"')
            
            params = json.loads(headarkit_text)
            if self.print_test_data:
                print(f"gRPC Rx SetHeadState: {params}")
            
            self.robot.set_servos(params)
            return head_service_pb2.Empty()
            
        except Exception as e:
            print(f"Error in SetHeadState: {e}")
            self.robot.reconnect()
            context.set_details(f"Error: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return head_service_pb2.Empty()
    
    def GetHeadState(self, request, context):
        """gRPC method to get all servo values as a JSON string."""
        try:
            data_dict = self.robot.get_robot_status() # Use the adapted method
            # print(f"gRPC Tx GetHeadState: {data_dict}")
            servo_json = json.dumps(data_dict, ensure_ascii=False)
            return head_service_pb2.HeadArkitMessage(servo_json=servo_json)
        except Exception as e:
            print(f"Error in GetHeadState: {e}")
            context.set_details(f"Internal error: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return head_service_pb2.HeadArkitMessage(servo_json="{}")
    
    def ReleaseControl(self, request, context):
        """head1 调用 release_control 后，强制释放并锁定给自己"""
        client_peer = context.peer()
        
        with self.lock:
            if self.current_client is None and self.locked_to_client is None:
                print(f"ReleaseControl by {client_peer}: No active control")
                return head_service_pb2.Empty()

            print(f"Client {client_peer} called ReleaseControl → control released and locked to itself")
            
            # 关键逻辑：
            # 1. 清空当前控制者（强制释放）
            self.current_client = None
            # 2. 把控制权锁定给调用 release 的这个客户端
            self.locked_to_client = client_peer

            self.client_condition.notify_all()
            return head_service_pb2.Empty()
        
def resource_path(relative_path):
    """获取 PyInstaller 打包后的资源文件路径"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def main():
    # --- Argument Parsing ---
    parser = argparse.ArgumentParser(description="Universal Robot gRPC Server")
    parser.add_argument("--config", default="v3", help="Path to the robot's YAML configuration file.")
    parser.add_argument("--ip", default="0.0.0.0", help="IP address to bind the gRPC server to.")
    parser.add_argument("--port", default="2543", help="Port for the gRPC server.")
    parser.add_argument("--print_rx", default="false", help="print test data false or true")
    
    args = parser.parse_args()

    print(f"Attempting to load config: {args.config}")
    print(f"Starting gRPC Server at: {args.ip}:{args.port}")

    # --- Initialization ---
    try:
        if args.config == "v3":
            config_path = resource_path("servo_config_v3.yaml")
            robot = UniversalRobot(config_file=config_path, baudrate=9600)
        else:
            robot = UniversalRobot(config_file=args.config, baudrate=9600)  
        robot.connect_all()
        init_data = robot.get_robot_init()
        print(init_data)
        robot.set_servos(init_data)
    except (FileNotFoundError, ValueError) as e:
        print(f"Fatal error during initialization: {e}")
        return

    # --- Start gRPC Server ---
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    head_service_pb2_grpc.add_HeadArkitStreamServicer_to_server(HeadArkitStreamServicer(robot,args.print_rx), server)
    server.add_insecure_port(f'{args.ip}:{args.port}')
    server.start()
    print("gRPC server started. Press Ctrl+C to stop.")
    
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("Shutdown signal received...")
    finally:
        # --- Graceful Shutdown ---
        server.stop(0)
        robot.close_all()
        print("Server and robot connections shut down gracefully.")

if __name__ == '__main__':
    main()
