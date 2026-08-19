import socket
import time
import logging
import os
from pylivelinkface import PyLiveLinkFace
from head_sdk import HeadSDK
from head_sdk.bs2servo import BStoServos, get_bs_dict_PyLive, load_yaml_mapping

# 配置日志记录，移除了所有小图标
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MimicTest")

class StandaloneMimic:
    def __init__(self, host="127.0.0.1", sdk_port=2543, udp_ip="0.0.0.0", udp_port=8001, mapping_path=None):
        self.udp_ip = udp_ip
        self.udp_port = udp_port
        self.udp_socket = None
        self.live_face = PyLiveLinkFace()
        # 存储外部传入的映射文件路径
        self.mapping_path = mapping_path 

        logger.info(f"正在初始化 HeadSDK (连接到 {host}:{sdk_port})...")
        self.head_sdk = HeadSDK(host=host, sdk_port=sdk_port)

    def start(self):
        try:
            self.head_sdk.connect()
            
            # 如果提供了外部路径且文件存在，则加载指定的映射文件
            if self.mapping_path and os.path.exists(self.mapping_path):
                load_yaml_mapping(self.mapping_path)
                logger.info(f"已成功加载自定义映射文件: {self.mapping_path}")
            elif self.mapping_path:
                logger.warning(f"指定的映射文件路径不存在: {self.mapping_path}")

            logger.info("========== HeadSDK 连接成功，已获取硬件控制权 ==========")
        except Exception as e:
            logger.error(f"HeadSDK 连接失败: {e}")
            return

        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.settimeout(1.0)
            self.udp_socket.bind((self.udp_ip, self.udp_port))
            logger.info(f"========== UDP 监听已启动: {self.udp_ip}:{self.udp_port} ==========")
            logger.info("========== 请在 iPhone 的 LiveLinkFace App 中将目标 IP 设置为此电脑的 IP，端口 8008 ==========")
        except Exception as e:
            logger.error(f"绑定 UDP 失败: {e}")
            return

        self._run_loop()

    def _run_loop(self):
        logger.info("========== 开始实时表情模仿 (按 Ctrl+C 退出) ==========")
        frame_count = 0
        
        try:
            while True:
                try:
                    data, addr = self.udp_socket.recvfrom(2048)
                except socket.timeout:
                    continue

                success, live_link_data = PyLiveLinkFace.decode(data)
                if success:
                    bs_dict = get_bs_dict_PyLive(live_link_data)
                    if not bs_dict:
                        continue

                    # 将映射文件路径传递给 BStoServos 函数
                    servo_target_dict = BStoServos(bs_dict, yaml_file=self.mapping_path)
                    
                    if servo_target_dict:
                        self.head_sdk.set_servo_positions(servo_target_dict)
                        
                        frame_count += 1
                        if frame_count % 60 == 0:
                            logger.info(f"正常运行中 | 已处理 {frame_count} 帧 | 来自 {addr}")

        except KeyboardInterrupt:
            logger.info("程序正在退出...")
        except Exception as e:
            logger.error(f"运行过程中发生错误: {e}")
        finally:
            self.stop()

    def stop(self):
        if self.udp_socket:
            self.udp_socket.close()
        try:
            self.head_sdk.release_control()
        except:
            pass
        logger.info("已安全退出")

if __name__ == "__main__":
    # 您可以在这里方便地修改路径
    # 示例路径：
    # mapping_file = "/home/droid/miniconda3/envs/face_servo/lib/python3.10/site-packages/head_sdk/servo_mappings.yaml"
    mapping_file = "config/ULA_new.yaml"
    # 如果您的 yaml 文件就在当前脚本目录下，也可以使用：
    # current_dir = os.path.dirname(os.path.abspath(__file__))
    # mapping_file = os.path.join(current_dir, "servo_mappings.yaml")

    mimic_app = StandaloneMimic(mapping_path=mapping_file)
    mimic_app.start()
