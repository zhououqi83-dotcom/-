# Droid Robot Head Servo Tuning 舵机调试平台

这是 Droid Robot 机器人面部舵机调试平台，通过 WebSocket 与 gRPC 服务通信，打通云平台仿生人和机器人链路，实现bs系数和舵机系数的实时转换与控制。

## 目录结构

```text
servo_tuning/
├── servo_server.py      # WebSocket 服务端
├── servo_tuning.html    # 前端页面
├── run_servo_tuning.sh  # 快速启动脚本
└── README.md            # 当前主文档
```

## 环境要求

- Python 3.6+
- gRPC Python 库
- WebSocket 库

## 依赖说明

本项目依赖 `head-sdk` 进行 gRPC 通信：
在SDK目录下运行以下命令，安装SDK
```bash
pip install .
```

## 启动方式

### 使用启动脚本（推荐）

```bash
cd ./servo_tuning
bash run_servo_tuning.sh
```

脚本会启动：
- HTTP 服务：`http://localhost:8081/servo_tuning.html`
- WebSocket 服务：`ws://localhost:8766`

### 手动启动

终端 1 - 启动 HTTP 服务：

```bash         
cd /home/droid/桌面/servo_tuning
python3 -m http.server 8081
```

终端 2 - 启动 WebSocket 服务：

```bash
cd /home/droid/桌面/servo_tuning
python3 servo_server.py --port 8766 
```

## 前端页面功能

### 数字人连接区

- 连接/断开数字人控制
- 数字人 WebSocket 地址配置

### bs系数控制区

- 加载bs系数-舵机系数的映射文件
- 连接/断开云平台仿生人控制
- **Blendshapes 控制舵机** 开关：控制是否将 Blendshape 数据转换后发送到机器人
- 单个bs系数滑块控制，拖动滑块会控制云平台机器人，同时通过映射关系控制舵机系数

### 舵机调试区

- 舵机状态刷新
- **机器人控制** 开关：控制是否禁用所有bs系数和舵机系数控制
- 单个舵机滑块控制
- 导出/导入舵机配置，重置舵机数值为初始数值

## WebSocket 数据格式

### 客户端发送给服务端


**获取舵机状态**

```json
{
  "type": "get_servo_state"
}
```

**设置单个舵机**

```json
{
  "type": "set_servo",
  "name": "left_blink",
  "value": 0.5
}
```

**批量设置舵机**

```json
{
  "type": "set_servo_batch",
  "servos": {
    "left_blink": 0.5,
    "right_blink": 0.5
  }
}
```

**发送 Blendshape 数据**

```json
{
  "type": "set_blendshape",
  "blendshapes": {
    "EyeBlinkLeft": 0.5,
    "JawOpen": 0.3
  },
  "disabled_servos": ["left_blink"]
}
```

### 服务端发送给客户端


**舵机状态**

```json
{
  "type": "servo_state",
  "servos": {
    "left_blink": 0.5
  },
  "connected": true
}
```

**设置结果响应**

```json
{
  "type": "set_servo_response",
  "name": "left_blink",
  "value": 0.5,
  "success": true
}
```

## 故障排除

### 提示 `ModuleNotFoundError: No module named 'head_sdk'`

请确保已安装 head-sdk：


### 无法连接到机器人

1. 检查机器人 gRPC 服务是否启动
2. 确认 IP 和端口配置正确
3. 检查防火墙设置

### WebSocket 连接失败

1. 确认 servo_server.py 已启动
2. 检查端口是否被占用
3. 确认前端配置的 WebSocket 地址正确

### 提示“Another client is currently controlling the robot“

结束机器人头其他控制程序
