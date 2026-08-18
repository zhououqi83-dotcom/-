# gaze_arbiter 平台总结:功能 / 算法 / 使用方法

这份文档是整个平台的入口总结。更细的内容分别在:
`README.md`(架构设计理由、跟 droidcore-temp 的关系)、
`WEB_DASHBOARD.md`(网页版实现过程踩的坑、每个 bug 怎么修的)。

---

## 一、功能概览

一句话:**给机器人装一套"该看谁"的决策系统**——摄像头看到几个人,每个人
按多个维度打分,分数最高的人被注视,注视时长也跟分数挂钩,同时保证不会
"扭来扭去"来回抽风。

| 能力 | 说明 |
|---|---|
| 多人识别与打标签 | 每张检测到的脸分配一个稳定的 `person_id`,跨帧不丢失身份 |
| 六个维度打分 | 脸的大小、多久没被看过、跟声源方向是否吻合、是否面朝机器人、是否在说话、是否被语义模块判定为"在聊天" |
| 最短/最长注视时间 | 硬边界,不会比设定值更短或更长 |
| 注视时长随机抖动 | 避免每次停留时长都精确相等,显得呆板 |
| 防抖动(核心痛点) | 两条硬规则彻底解决"因为声源/人脸信号跳变而来回扭头"的问题(见下面算法一节) |
| 声源定向(独立系统) | 场上没人时, 一有声音就主动转头去正对声源找人, 找到/超时后交还给正常的打分选择(见 2.6 节) |
| 真实摄像头人脸检测 | 接的是 `light_asd_test`(MediaPipe 人脸检测 + Light-ASD 说话人判别) |
| 真实声源方向定位 | 接的是 J7034G4 麦克风阵列板(串口读 `doa_angle`) |
| 真实头部舵机驱动 | 接的是 `servo_tuning` 那套 `head_grpc_server.py` + `head_sdk` |
| 本地/机器人摄像头切换 | 网页上一键切换用哪路摄像头,运行中也能无缝切 |
| 网页可视化 | 不依赖本地图形桌面,浏览器打开就能看效果、点按钮就能连硬件 |

**还没接的**:语义判定"现在在跟谁聊天"(需要接 `nlu_intent` + 对话管理器,
目前 `Person.is_chat_target` 这个信号的写入接口已经留好,只是还没有真实
的语义模块在喂数据)。

---

## 二、核心算法

### 2.1 六个信号怎么打分

每个信号独立算一个 `[0, 1]` 的分数,互不知道对方存在(`gaze_arbiter/signals.py`):

| 信号 | 怎么算 | 形状参数(`SignalParams`) |
|---|---|---|
| `face_size_score` | 脸框面积占画面比例, 除以一个"封顶值"再夹到 [0,1] | `size_saturation=0.35`(占比到 35% 就满分) |
| `novelty_score` | 距离上次被注视过去了多久, 除以"多久算陌生"再夹到 [0,1]; 从没被看过直接满分 | `novelty_saturation_s=20.0`(20 秒没看=满分陌生) |
| `sound_direction_score` | 这个人的方位角跟声源方向的夹角, 用高斯函数算相似度, 没声音时整体是 0 | `sound_tolerance_deg=20.0`(高斯"半宽", 越小越挑剔) |
| `facing_score` | 直接读头部姿态估计给出的"正对摄像头程度", 这里只做夹值 | 无(上游头部姿态模块自己算) |
| `speaking_score` | 二值: 正在说话=1, 否则=0 | 无 |
| `chat_target_score` | 二值: 被外部语义模块标记为"聊天对象"且没过期=1, 否则=0 | 无 |

### 2.2 六个信号怎么合成一个总分

加权求和、再除以权重总和(结果保证落在 `[0,1]`,见 `gaze_arbiter/weights.py`):

```
总分 = (w_size·size + w_novelty·novelty + w_sound·sound
       + w_facing·facing + w_speaking·speaking + w_chat·chat) / 权重总和
```

当前的权重(`WeightConfig`,相对大小才有意义,不要求加起来等于 1):

| 信号 | 权重 |
|---|---|
| 脸大小 | 1.0 |
| 没看过 | 1.5 |
| 声源方向 | 2.0 |
| 面朝机器人 | 1.2 |
| 正在说话 | 1.0 |
| 聊天对象 | **3.0**(最高,对应"要聊天的对象权重高"这个需求) |

为什么用**加权和**而不是加权乘积:乘积会导致任何一项是 0 就把总分乘没
(比如没声音时 `sound=0`,乘法会让"没说话但脸很大又在等着聊天"的人也变成
0 分,不合理)。加权和只是拉低那部分贡献,更符合"这些是互相独立的加分项"
的直觉。另外有个 `MIN_INTEREST_FLOOR=0.03` 兜底,保证只要人在场,总分不会
精确为 0(否则抽样机制会抽不出人来)。

### 2.3 怎么决定"看谁"+"看多久"(`GazeScheduler`)

```
基础时长 = min_gaze_s + (max_gaze_s - min_gaze_s) × (该候选人分数占所有候选人分数总和的比例)
最终时长 = 基础时长 × (1 ± jitter_frac 的随机抖动)
```

分数占比越高的人,停留时间越接近 `max_gaze_s`;占比低但还是被抽中的人,
停留时间越接近 `min_gaze_s`(相当于"扫一眼就走")。`GazeScheduler` 类自身
默认 `min_gaze_s=1.5` 秒、`max_gaze_s=6.0` 秒、`jitter_frac=0.2`(±20%
抖动),`web_dashboard.py` 等脚本里实际构造时把 `jitter_frac` 调到了
`0.25`(±25%)。

### 2.4 防"扭来扭去"的两条硬规则(整套系统的设计起点)

这套系统是从排查"机器人头部因为声源定位来回抖动"这个具体问题起步的,
所以从一开始就把抗抖动做成了硬规则,不是事后补丁:

1. **fixation 时间没到, 绝不重新选目标**——哪怕这一帧算出别人分数更高,
   也要等当前这位的注视时间走完。目标选择和"信号更新频率"完全解耦,
   信号可以很快地跳,但选择本身有个"锁定期"。
2. **候选人多于 1 个时, 强制排除"刚看过的这位"**——避免两人分数很接近时
   被同一个人反复抽中显得"锁死";只剩 1 个人在场时不排除, 持续盯着他。

**副作用(有意为之, 不是 bug)**:场上正好 2 个人时, 规则 2 会导致"选谁"
这件事必然每次都换人(数学上无法不换)。权重的效果不体现在"轮不轮得到
他"上, 而体现在"每次轮到他时**停留多久**"上。

### 2.5 平滑执行(把"决定看谁"变成"头真的转过去")

`GazeScheduler` 只负责"决定看谁、看多久", 不管舵机怎么转。真正驱动
物理头(`gaze_arbiter/output/head_driver.py::HeadDriver`)在上面再叠一层
EMA 平滑 + 限速:

```
目标舵机值 = 目标角度按视场角线性换算成 0~1 的归一化值
EMA平滑值 = smoothing × 目标舵机值 + (1-smoothing) × 上一次的EMA平滑值
实际发送值 = 在"上一次发送值"基础上, 朝EMA平滑值方向最多移动 max_speed×dt
```

> **2026-08-14 折腾史**：这一天陆续给 `HeadDriver` 加过一批改动——修
> "追踪单个移动目标来回过冲"(`yaw_deg` 其实是相对摄像头当前朝向的偏航角,
> 不是绝对角, 加 `frac_to_yaw` 反函数把头当前指向换算回角度再复合)、
> `head_gain` 阻尼系数、`comfort_zone_deg` 舒适区、断开连接/退出时的慢速
> 回中(`recenter`/`recenter_step`)、幽灵目标新鲜度闸门
> (`HEAD_TARGET_FRESH_S`)。用户拿 `~/Downloads/gaze_arbiter` 里 8月6日的
> 旧快照实地对比手感后, 一度决定**全部撤销**换回最简版本; 之后又要回了
> **慢速回中**(避免断开/退出时头瞬间甩回伤舵机), 舒适区也要过一次但很快
> 又要求去掉了——`frac_to_yaw` 绝对角复合、`head_gain`、舒适区、幽灵目标
> 闸门这四项目前都是撤销状态, **没有**在当前 `head_driver.py` 里, 只有
> 慢速回中保留。想确认某个具体特性现在到底在不在, 直接看
> `HeadDriverConfig` 的字段和 `update()`/`recenter()` 的实现最准, 这段
> 历史只用来省得以后重新摸索思路(详见
> `ai日志/claude/2026-08-14.md`)。

两层各管一件事: `GazeScheduler` 的两条硬规则防的是"选择层面"的抖动
(选错人来回换), `HeadDriver` 的 EMA+限速防的是"执行层面"的抽动(舵机
跳变太猛)。默认 `smoothing=0.25`、`max_speed=0.4`(归一化值/秒, 2026-08-14
为保护舵机从 0.6 调低, 嫌太慢可以调高 `HEAD_MAX_SPEED` 环境变量)。

### 2.6 声源定向(`SoundOrientState`, 跟 `GazeScheduler` 完全独立的另一套系统)

2026-08-14 新增。之前"跟声源方向吻合"只是 `GazeScheduler` 六个打分信号
之一(`sound_direction_score`, 权重 2.0)——声音只是让某个**已经在画面里**
的人加分, 不会让头主动转向一个**还没看到人**的方向。用户要的是更直接的
"有声音优先转过去找人"行为, 所以新写了 `gaze_arbiter/sound_orient.py`,
跟 `GazeScheduler` 平级、互不修改, 各自决定"该看哪"，由调用方(网页主循环)
按一条简单规则仲裁谁说了算:

```
声源定向 active?
  ├─ 是 → 头听声源定向的(target_bearing), 不管 GazeScheduler 选的是谁
  └─ 否 → 头听 GazeScheduler 的(decision.person_id 对应的 yaw_deg)
```

`SoundOrientState` 是个小状态机(`gaze_arbiter/sound_orient.py`, 10 个单测):

1. **只在场上没人时响应声音**: `idle`(调用方传入, 等于
   `decision.person_id is None`, 即 `GazeScheduler` 当前没锁定任何人)为
   True 且声源方向是"新的"(之前没在定向, 或者跟当前定向方向差超过
   `redirect_threshold_deg`)才会进入 active、把头指向这个方位角——已经
   有人被锁定/正在看着的时候, 声音再新也不打断, 交给 `GazeScheduler`
   自己的两条防抖规则处理(2.4 节)。
2. **等一小段时间找人**: active 期间, 一旦 `idle` 变成 False(场上出现人,
   `GazeScheduler` 有候选人了)就立刻交还控制权, 让它接手挑目标(它一直在
   后台正常运行, 没被暂停或改动过)。等过 `search_timeout_s`(默认 2.5s)
   还是没人, 也交还控制权, 回到"声音出现之前该在的地方"——不需要专门
   记忆"之前在看哪", 因为 `GazeScheduler` 从没被打断过。

> **2026-08-14 缩小触发范围**: 第一版是"不管当前有没有人被锁定, 新声音
> 一出现就打断", 用户实测后改成上面这版"只在场上没人时才响应声音"——
> 已经在看着人的时候不会再被别处的新声音打断。相应地, 原来判断"声源方向
> 那个位置有没有人脸"(`person_at_bearing`, 按角度容差匹配)这一步也不需要
> 了, 直接用 `GazeScheduler` 的锁定状态(`idle`)判断, 逻辑更简单。

---

## 三、使用方法

### 3.1 环境准备(三套 venv, 各管各的)

| venv | 装了什么 | 谁用 |
|---|---|---|
| `gaze_arbiter/venv` | pytest, pyserial | 单元测试、纯算法模拟(`simulate.py`)、真实声源测试(`run_with_real_sound.py`) |
| `light_asd_test/venv` | opencv, torch, mediapipe, python_speech_features, **以及 grpcio/protobuf/head_sdk** | 所有真实摄像头相关的脚本, **包括 `web_dashboard.py` 和 `run_with_head.py`** |
| `servo_tuning/venv_face_servo` | grpcio, protobuf, head_sdk | 只用来跑常驻的 `head_grpc_server.py`(底层硬件桥接) |

三套 venv 互相独立, 装什么完全看那个脚本实际需要什么重依赖, 核心算法包
(`gaze_arbiter` 本体)本身零重依赖。

### 3.2 跑纯算法(不需要任何硬件)

```bash
cd gaze_arbiter
venv/bin/python -m pytest tests/ -q      # 全部单元测试
venv/bin/python examples/simulate.py     # 60 秒假数据模拟场景
```

### 3.3 跑真实平台(推荐路径): 网页版

这是日常使用的入口, 集齐了人脸检测+声源方向+头部舵机+网页可视化,
**不需要打开任何命令行参数就能启动**, 所有硬件连接都是网页上点按钮触发:

**第一步(常驻服务, 只需开一次): 头部舵机的底层 gRPC 桥接**

```bash
cd servo_tuning/head-sdk-face/head-server/src
../../../venv_face_servo/bin/python head_grpc_server.py --config servoConfig_25DV3_Ula.yaml
```

日志里看到 `Successfully connected to port: /dev/ttyACM0` 就说明连上了。

**第二步: 启动网页**

```bash
cd gaze_arbiter
light_asd_test/venv/bin/python examples/web_dashboard.py
```

浏览器打开 **http://localhost:8642/**(局域网内其他设备用这台机器的 IP
也能访问)。页面上三个按钮, 默认都是关/未连接状态, 点了才会动:

| 按钮 | 位置 | 作用 |
|---|---|---|
| 开启摄像头 / 关闭摄像头 | 摄像头画面卡片 | 真正打开/释放摄像头, 开始/停止人脸检测 |
| 本地摄像头 / 机器人摄像头 | 摄像头画面卡片(开关按钮下面) | 切换用哪一路摄像头, 运行中也能无缝切换 |
| 连接机器人 / 断开机器人 | 声源方向仪表盘卡片 | 真正连接头部舵机服务, 让头跟着 `GazeScheduler` 选中的人转 |
| 开启声源定位 / 关闭声源定位 | 声源方向仪表盘卡片 | 真正打开 J7034G4 麦克风阵列的串口 |

页面上还能看到:每张脸绿框(说话)/红框(没说话)、`GazeScheduler` 选中的
人黄色粗框、声源方向半圆仪表盘(红色指针=声源方向, 圆点=每个人的方位,
金色描边=当前选中)、当前选中目标的六项权重构成条形图。

**常用环境变量**(都是可选, 配置"连接目标是谁", 连不连由按钮决定):

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VIDEO_DEV` / `ROBOT_VIDEO_DEV` | `/dev/video0` / `/dev/video2` | 本地/机器人摄像头设备号 |
| `ROBOT_CAMERA_FLIP` | 开 | 机器人摄像头装反了(180°), 默认自动翻转 |
| `MIC_PORT` / `MIC_BAUD` | `/dev/ttyUSB0` / `115200` | 声源方向串口 |
| `MIC_OFFSET_DEG` / `MIC_INVERT` | `0` / **开** | 声源方向角度校准, 默认已经反转过(实测装置左右反了) |
| `HEAD_HOST` / `HEAD_SDK_PORT` | `127.0.0.1` / `2543` | 头部舵机 gRPC 服务地址 |
| `HEAD_INVERT` | **开** | 头部左右转动方向校准, 默认已经反转过(实测转动方向反了) |
| `HEAD_SMOOTHING` | `0.25` | EMA 平滑系数, 越小越平滑但越滞后 |
| `HEAD_MAX_SPEED` | `0.4` | 最高转动速度, 归一化值/秒(2026-08-14 为保护舵机从 0.6 调低) |
| `RECENTER_EXIT_MAX_S` | `3.0` | 断开连接/退出进程时, 慢速回中的最长阻塞秒数(回中本身约 1~1.5s); 设 `0` 则立即回中并断开 |
| `SOUND_ORIENT_CONFIDENCE` | `0.5` | 声源定向: 置信度低于这个不触发(见 2.6 节) |
| `SOUND_ORIENT_REDIRECT_DEG` | `15.0` | 声源定向中的声源方向偏出这个角度才算"换了地方", 重新计时 |
| `SOUND_ORIENT_SEARCH_TIMEOUT_S` | `2.5` | 转向声源后等人出现的最长时间, 超时放弃、交还控制权 |
| `WEB_PORT` | `8642` | 网页端口 |

> 头部转动用**最简人脸追踪 `HeadDriver`**(EMA 平滑 + 限速, 见
> `gaze_arbiter/output/head_driver.py`): 检测到人脸 → 头平滑跟随 yaw;
> 没有人脸 → 头停在原地不动(没有舒适区, 只要有偏差就跟)。
> **断开连接/退出时头会慢速回中**(`recenter()`/`recenter_step()`, 速度约
> 为 `max_speed×0.4`), 不是瞬间跳回中间, 回中完成才真正断开 gRPC——退出
> 进程时这个回中有界阻塞最多 `RECENTER_EXIT_MAX_S` 秒, 避免 Ctrl+C 卡住
> 不退出。目标丢失没有"新鲜度闸门"这类额外处理(这项目前仍是撤销状态),
> 详见上面 2.5 节的折腾史说明和 `ai日志/claude/2026-08-14.md`。

完整的踩坑记录(cv2 窗口看不到、502 网关错误、时间戳崩溃、控制权锁死
等)见 `WEB_DASHBOARD.md`。

### 3.4 单独测试某一路(排查问题用, 不需要开全套网页)

```bash
# 只测人脸检测(cv2 窗口版, 这台机器上窗口看不到, 排查用网页版更可靠)
light_asd_test/venv/bin/python gaze_arbiter/examples/run_with_face.py

# 只测头部舵机(假数据场景, 不接摄像头, 验证"决策→舵机"这段链路通不通)
light_asd_test/venv/bin/python gaze_arbiter/examples/run_with_head.py

# 只测声源方向(真实麦克风 + 假人脸场景)
gaze_arbiter/venv/bin/python gaze_arbiter/examples/run_with_real_sound.py --port /dev/ttyUSB0
```

### 3.5 核心 API(自己接新的输入/输出源时用)

```python
from gaze_arbiter import PersonRegistry, GazeScheduler, SoundContext, WeightConfig

registry = PersonRegistry(stale_timeout_s=5.0)
scheduler = GazeScheduler(min_gaze_s=1.5, max_gaze_s=6.0, jitter_frac=0.25)

# 每帧: 人脸检测结果喂进来
registry.observe(track_id=7, yaw_deg=12.0, face_area_frac=0.3,
                  facing_score=0.8, is_speaking=False)

# 语义模块判定"现在在跟这个人聊天"时:
registry.set_chat_target(person_id, duration_s=6.0)

# 决策 tick(可以按固定频率调, 也可以事件驱动):
decision = scheduler.tick(registry, sound=SoundContext(doa_deg=10.0, confidence=0.9))
# decision.person_id / decision.duration_s / decision.breakdown(权重构成, 调试用)
```

调参分两组:`WeightConfig`(每个信号有多重要)和 `SignalParams`(每个信号
自己的形状参数),两组分开管理,互不影响。
