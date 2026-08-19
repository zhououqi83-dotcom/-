# 工程说明

## 总体说明

## head-sdk 工程

### 快速开始

```shell
cd head-sdk
pip install rena2_sdk_api-0.1.0-py3-none-any.whl
pip install -e . # 测试
```

```python
# 导入SDK
from time import sleep
head = HeadSDK("localhost")

# 获取当前的舵机位置
print(head.get_servo_positions()["head_dian"])

# 设置舵机位置 -- 直接舵机控制
head.set_servo_positions({"head_dian":0.4})
print(head.get_servo_positions()["head_dian"])

# 设置blendshape系数 -- blendshape控制
mapping_new = head.reload_bs2servo_mapping("test_mappings.yaml") # 重新加载映射关系，可以不使用
data = head.set_arkit_positions({"eyeBlinkLeft":0.5})
```

- 详细看 ./head-sdk/README.md

## head-server 工程

### 快速开始

- 有启动文件

```shell
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
  --print_rx       打开打印接收到信息用于测试
```

- 代码启动

```shell
pip install grpcio==1.65.4 grpcio-tools==1.65.4 -i https://pypi.tuna.tsinghua.edu.cn/simple
cd head-server/src
python head_grpc_server.py --config "地址" --ip "127.0.0.1" --port "2543" --print_rx true
```

- 详细看 ./head-server/README.md

## head-server-web 工程

### 快速开始

```shell
cd head-server-web
npm install
# 确保在有head-sdk的环境
bash run.sh
# cd test
# python test_* 相关的单独测试文件
```

- 详细看 ./head-server-web/README.md
