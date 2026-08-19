from head_sdk import HeadSDK
from time import sleep
head = HeadSDK("localhost")
head.release_control()
# 获取当前的舵机位置
data = head.get_servo_positions()
if type(data) == dict:
    if data!= {}:
        print(data["head_dian"])
print(data)
# 设置舵机位置
head.set_servo_positions({"head_dian":0.4})
data = head.get_servo_positions()
if type(data) == dict:
    if data!= {}:
        print(data["head_dian"])

# 插值设置舵机位置
head.interpolate_servo_positions({"head_dian":0.22},2)
sleep(1)
# 停止插值
head.pause_interpolation()
sleep(0.5)
# 重启插值
head.resume_interpolation()
sleep(1)
# arkit 表情系数控制
print("set_arkit_positions before",head.get_servo_positions())
print("sart_set_arkit_positions === =>")
data = head.set_arkit_positions([0.0281256158,0.1871456504,0.1077243909,0.0000000000,0.0000000000,0.1239606813,0.0000000000,0.0281756762,0.1875451505,0.0094851423,0.0000000000,0.0000000000,0.1239025965,0.0000000000,0.0168656334,0.0000000000,0.0139776571,0.0169174355,0.0203074608,0.0265404191,0.0784664378,0.0000000000,0.0029044650,0.0000000000,0.0000000000,0.0078552375,0.0123107219,0.0213157404,0.0220622495,0.0671969503,0.0737786368,0.0534307137,0.0133373234,0.2229741961,0.1604369730,0.0678287670,0.0668828636,0.0257842243,0.0241978634,0.0218678191,0.0223953072,0.0000000000,0.0000000000,0.3249226511,0.3292692006,0.3292803466,0.0454454720,0.0711027682,0.0761528760,0.0836517140,0.0918825418,0.0000000025,0.0784421787,0.0373346396,-0.0092058359,-0.0057561616,0.1145667285,-0.0006623614,0.0653749257,0.1145665944,0.0075172251])
print("set_arkit_positions data === =>",data)
sleep(1)
print("set_arkit_positions over",head.get_servo_positions())
print("reload_bs2servo_mapping === =>")
mapping_old = head.reload_bs2servo_mapping()
data = head.set_arkit_positions({"eyeBlinkLeft":0.5})
print("reload_bs2servo_mapping old === =>",mapping_old)
print("set_arkit_positions over",head.get_servo_positions()["left_blink"])
mapping_new = head.reload_bs2servo_mapping("test_mappings.yaml")
data = head.set_arkit_positions({"eyeBlinkLeft":0.5})
print("reload_bs2servo_mapping new === =>",mapping_new)
print("set_arkit_positions over",head.get_servo_positions()["left_blink"])

