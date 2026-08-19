# Droid Robot Head SDK

## 简介

这是一个基于gRPC的机器人仿生人头控制SDK，提供了简单易用的API接口来控制Droid机器人的人头。SDK封装了底层的gRPC调用，使开发者可以更方便地获取机器人状态和发送控制命令。

## 功能特点

- 简单的连接管理
- 获取机器人配置和状态信息
- 支持位置控制
- 支持状态流实时监控
- 提供高级控制功能，如轨迹规划和预设动作
- 包含实用工具函数

## 安装要求

- Python 3.6+
- gRPC Python库

## 快速开始

### 基本使用

```python
# 导入SDK
from time import sleep
head = HeadSDK("localhost")

# 获取当前的舵机位置
print(head.get_servo_positions()["head_dian"])
# 设置舵机位置
head.set_servo_positions({"head_dian":0.4})
print(head.get_servo_positions()["head_dian"])

# 插值设置舵机位置
head.interpolate_servo_positions({"head_dian":0.22},2)
sleep(1)
# 停止插值
head.pause_interpolation()
sleep(0.5)
# head.interpolate_servo_positions({"head_dian":0.9},1)
# sleep(1)
# 重启插值
head.resume_interpolation()
sleep(1)
# arkit 表情系数控制
print("set_arkit_positions before",head.get_servo_positions())
head.set_arkit_positions([0.0281256158,0.1871456504,0.1077243909,0.0000000000,0.0000000000,0.1239606813,0.0000000000,0.0281756762,0.1875451505,0.0094851423,0.0000000000,0.0000000000,0.1239025965,0.0000000000,0.0168656334,0.0000000000,0.0139776571,0.0169174355,0.0203074608,0.0265404191,0.0784664378,0.0000000000,0.0029044650,0.0000000000,0.0000000000,0.0078552375,0.0123107219,0.0213157404,0.0220622495,0.0671969503,0.0737786368,0.0534307137,0.0133373234,0.2229741961,0.1604369730,0.0678287670,0.0668828636,0.0257842243,0.0241978634,0.0218678191,0.0223953072,0.0000000000,0.0000000000,0.3249226511,0.3292692006,0.3292803466,0.0454454720,0.0711027682,0.0761528760,0.0836517140,0.0918825418,0.0000000025,0.0784421787,0.0373346396,-0.0092058359,-0.0057561616,0.1145667285,-0.0006623614,0.0653749257,0.1145665944,0.0075172251])
sleep(1)
print("set_arkit_positions over",head.get_servo_positions())

```

## API参考

### HeadSDK

基础客户端类，提供与机器人控制服务器的通信功能，以及简单的规划和发送。

- `__init__(host="localhost",sdk_port"2543")` - 初始化客户端
- `connect()` - 连接到服务器
- `disconnect()` - 断开连接
- `_get_state()` - 获取舵机状态信息
- `_start_sync_in_bg()` - 在后台同步舵机状态
- `get_servo_positions()` - 获取当前舵机位置
- `set_servo_positions()` - 发送舵机系数
- `start_state_stream(callback)` - 启动状态流
- `stop_state_stream()` - 停止状态流
- `interpolate_servo_positions(target_dict, duration)` - 余弦插值设置位置组并播放
- `cancel_interpolation()` - 取消插值运动
- `pause_interpolation()` - 暂停插值运动
- `resume_interpolation()` - 恢复插值运动
- `set_arkit_positions()` - 用arkit 61 系数进行控制

### 工具函数

- `generate_curve_points_all(current_dict, target_dict, n)` - 生成平滑轨迹

## 示例程序

SDK包含两个示例程序：

- `test/test_cline.py` - 连接测试
- `test/test_head.py` - 控制测试

## 注意事项

- 使用前请确保机器人控制服务器已启动
- 发送命令前请检查关节限位，避免超出安全范围
- 建议使用紧急停止函数处理异常情况

## 许可证

Copyright (c) 2024 DroidUP Robot, Inc. All rights reserved.
