# TalkingHead Core

轻量化仿生人仿真平台，已经按职责重组为一个接收端下发到多个控制模块的结构。

## 目录结构

```text
core
├── main.py
├── run.sh
├── server_utils/
│   ├── config.yaml
│   └── blendshape-config.yaml -> ../viewer/digital/demo/public/blendshape-config.yaml
├── viewer/
│   ├── digital/
│   └── robot/
└── test/
```

各目录职责：

- `server_utils/config.yaml`
  - 共享通信配置和默认开关
- `server_utils/`
  - 接收 HTTP / SSE / WebSocket 数据并做分发
- `viewer/digital/`
  - 数字人显示端、前端资源、数字人系数映射
- `viewer/robot/`
  - `HeadSDK` 连接、机器人适配层配置
- `test/`
  - 两类测试：MediaPipe 输入测试、动作库流式发送测试

## 启动

只启动接收端：

```bash
cd core
./run.sh --backend-only
```

同时启动接收端和数字人：

```bash
cd core
./run.sh
```

也可以手动分别启动：

```bash
cd core
python3 main.py
```

```bash
cd core/viewer/digital
npm run remote:view
```

## 共享配置

- `core/server_utils/config.yaml`
  - `hub`: 接收端监听地址、端口、前端默认连接地址
  - `robot`: `HeadSDK` 地址、端口、默认是否自动连接
  - `stream`: WebSocket 路径、是否默认转发给机器人
  - `viewer`: 前端表情回收和数字人系数配置
  - `backend`: 机器人适配层配置路径

前端静态目录中的 `viewer/digital/demo/public/config.yaml` 链接到 `server_utils/config.yaml`，避免前后端出现两份运行配置。

## 对外接口

- `GET /events`
- `GET /status`
- `POST /frame`
- `POST /robot-forwarding`
- `GET /robot/status`
- `POST /robot/connect`
- `POST /robot/disconnect`
- `POST /robot/set-arkit`
- `POST /robot/reload-bs2servo-mapping`
- `POST /robot/reload-adapter-config`
- `ws://<hub>/ws`

## 测试

MediaPipe 测试，图片或视频二选一：

```bash
cd core/test
python3 test_mediapipe.py --mode image
python3 test_mediapipe.py --mode video
```

动作库流式发送：

```bash
cd core/test
python3 send_motion.py stream wave_right
```

## 验证

```bash
cd core/viewer/digital
npm test
```

```bash
cd core
python3 -m py_compile \
  main.py \
  server_utils/control_hub.py \
  server_utils/remote_config.py \
  server_utils/blendshape_mapping.py \
  server_utils/external_protocol.py \
  server_utils/simple_yaml.py \
  viewer/robot/head_sdk_controller.py \
  viewer/robot/robot_adapter.py \
  test/send_motion.py \
  test/motion_stream.py \
  test/test_mediapipe.py
```
