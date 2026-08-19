from head_sdk import HeadSDK

head1 = HeadSDK("localhost")
head2 = HeadSDK("localhost")
print("origin head",head1.get_servo_positions()["head_dian"])
print("1. head1 设置")
head1.set_servo_positions({"head_dian": 0.1})
print("get_1 head",head1.get_servo_positions()["head_dian"])

print("\n2. head2 尝试设置 → 应该失败（head1 还没 release）")
head2.set_servo_positions({"head_dian": 0.8})
print("get_2 head",head2.get_servo_positions()["head_dian"])

print("\n3. head1 release_control")
head1.release_control()

print("\n4. head2 再次尝试设置 → 应该仍然失败（控制权已锁定给 head1）")
head2.set_servo_positions({"head_dian": 0.9})
print("get_2 head",head2.get_servo_positions()["head_dian"])

print("\n5. head1 再次设置 → 应该成功")
head1.set_servo_positions({"head_dian": 0.3})
print("get_1 head",head1.get_servo_positions()["head_dian"])