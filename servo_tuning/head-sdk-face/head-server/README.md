# Droid Robot Head ServerGrpc

这是Droid Robot机器人面部控制的底层服务端，提供了通过gRPC与机器人控制系统通信的接口。

## 安装要求

- Python 3.6+

- gRPC Python库 
  
  ```shell
  grpcio                   1.65.4
  grpcio-tools             1.65.4
  pip install grpcio==1.65.4 grpcio-tools==1.65.4 -i https://pypi.tuna.tsinghua.edu.cn/simple
  ```

## 生成方式

```shell
pip install pyinstaller # 安装 PyInstaller

pyinstaller --onefile \
  --name Head \
  --collect-submodules google.protobuf \
  --collect-submodules numpy \
  --collect-submodules timecode \
  --collect-submodules serial \
  --collect-all yaml \
  --hidden-import=uuid \
  --hidden-import=yaml \
  --add-data "grpc_config:grpc_config" \
  --add-data "servo_config_v2.yaml:." \
  --add-data "servo_config_v3.yaml:." \
  --add-data "servo_contrl.py:." \
  head_grpc_server.py
```

## 使用方法

直接放入直接使用

```shell
# 运行指令
./Head
# 默认参数 
	--config "*.yaml绝对路径"
	--ip localhost \
	--port 2543
# 参数含义
options:
  -h, --help       show this help message and exit
  --config CONFIG  Path to the robot's YAML configuration file.
  --ip IP          IP address to bind the gRPC server to.
  --port PORT      Port for the gRPC server.

```

## 配置参数

```yaml
controllers:
  - port: /dev/ttyACM1 # 串口地址
  servos: # 舵机基本参数和对应的名称
      - { name: 'left_blink'          , id: 14, jdStart: 90, jdMax: 135, jdMin: 54, fScale: 11.1, fOffSet: 0, pos: 0, dir: 0 }
      - { name: 'left_eye_erect'      , id: 0 , jdStart: 90, jdMax: 117, jdMin: 63, fScale: 11.1, fOffSet: 0, pos: 0, dir: 0 }
      ...
```

## 测试示例

```shell
python test_clien.py
# 发送控制字典
def set_servo_state(params: dict):
    try:
        json_str = json.dumps(params)
        request = head_service_pb2.HeadArkitMessage(servo_json=json_str)
        stub_servo.SetHeadState(request)
        print("已发送舵机指令:", json_str)
    except grpc.RpcError as e:
        print(f"gRPC error: {e.details()}")
# 接收当前舵机的位置
def get_servo_state():
    try:
        response = stub_servo.GetHeadState(head_service_pb2.Empty())
        servo_dict = json.loads(response.servo_json)
        print("当前舵机状态：")
        for k, v in servo_dict.items():
            print(f"  {k}: {v:.3f}")
        return servo_dict
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

```

## 使用注意

### 端口固化

- 用于断开重新连接

```shell
# 获取设备信息
#查看 ttyACM1 /ttyACM0的硬件信息
udevadm info -a -n /dev/ttyACM0 | grep -E "(idVendor|idProduct|serial|ATTRS{manufacturer}|ATTRS{product})"

# 回传
(droid) root@DroidPC0:~/head-server/src# udevadm info -a -n /dev/ttyACM0 | grep -E "(idVendor|idProduct|serial|ATTRS{manufacturer}|ATTRS{product})"
    ATTRS{idProduct}=="5740"
    ATTRS{idVendor}=="0483"
    ATTRS{manufacturer}=="lingYun"
    ATTRS{product}=="cyberGenie"
    ATTRS{serial}=="3669357F3034"
    ATTRS{idProduct}=="0201"
    ATTRS{idVendor}=="1a40"
    ATTRS{product}=="USB 2.0 Hub [MTT]"
    ATTRS{idProduct}=="0002"
    ATTRS{idVendor}=="1d6b"
    ATTRS{manufacturer}=="Linux 6.1.57 xhci-hcd"
    ATTRS{product}=="xHCI Host Controller"
    ATTRS{serial}=="xhci-hcd.4.auto"

# 创建udev规则
sudo nano /etc/udev/rules.d/99-usb-serial.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="0201", ATTRS{serial}=="3669357F3034", SYMLINK+="ttyCyberGenie"

# 重新加载 udev 规则
sudo udevadm control --reload-rules

```

- 修改相关配置
  - servo_config_v3.yaml 文件中的串口地址由/dev/ttyACM0改为/dev/ttyCyberGenie
