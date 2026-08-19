from head_sdk import HeadSDK

head2 = HeadSDK("localhost")
head2.release_control()
print("\n2. head2 尝试设置 → 应该失败（head1 还没 release）")
head2.set_servo_positions({"head_dian": 0.8})
print("get_2 head",head2.get_servo_positions()["head_dian"])

print("\n4. head2 再次尝试设置 → 应该仍然失败（控制权已锁定给 head1）")
head2.set_servo_positions({"head_dian": 0.9})
print("get_2 head",head2.get_servo_positions()["head_dian"])

import time
import random


while True:
    time.sleep(1)
    # 生成一个 [0.0, 1.0] 的随机浮点数（包含0和1）
    num = random.uniform(0, 1)
    print("++++++",num)
    head2.set_servo_positions({"head_dian": num})
    print("get_2 head",head2.get_servo_positions()["head_dian"])