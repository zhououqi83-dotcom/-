from head_sdk import HeadSDK
import time

# head = HeadSDK(host="localhost",sdk_port=1121)
# head = HeadSDK("192.168.10.11")
head = HeadSDK("localhost")
print("head_0 初始化直接连接",head.is_connected())
time.sleep(1)
head.disconnect()
print("head_0 断开连接",head.is_connected())
time.sleep(1)
head.connect()
print("head_0 复联",head.is_connected())
time.sleep(1)
head_1 = HeadSDK("localhost")
print("head_1 初始化直接连接",head.is_connected())
time.sleep(1)
head_1.release_control()
print("释放所有控制连接",head.is_connected())
time.sleep(1)
head_1 = HeadSDK("localhost")
print("head_1 初始化直接连接",head.is_connected())


