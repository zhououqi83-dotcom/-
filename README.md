# attention — 机器人多人注视 / 头部转向系统

给机器人装一套"该看谁、该怎么转头看"的完整链路：摄像头看到几个人 → 给每个人打分 →
决定接下来看谁、看多久 → 转头(先转眼珠还是先转脖子)看过去，同时接一路声源方向、
一路说话人判别做辅助信号。

这份文档覆盖整个 `attention/` 目录（不只是 `gaze_arbiter/` 算法本身），是新接手/隔一阵
回来看这个项目时应该先读的入口文档。算法细节的推导和公式在 `gaze_arbiter/PLATFORM.md`
里有更详细的版本，这里给的是整体骨架 + 怎么跑起来。

---

## 一、系统架构

`attention/` 里其实是三个各自独立、通过进程边界/网络协议连接起来的子系统：

```
┌─────────────────────────────────────────────────────────────────┐
│                        gaze_arbiter (决策核心)                    │
│  examples/web_dashboard.py —— 唯一的常驻入口, 网页仪表盘 + 主循环   │
│                                                                     │
│  感知              决策               输出                        │
│  ┌──────────┐    ┌──────────────┐   ┌──────────────────┐        │
│  │人脸检测/追踪│──▶│PersonRegistry │──▶│GazeScheduler      │       │
│  │(MediaPipe) │   │(身份+状态)     │   │(该看谁/看多久)      │       │
│  │说话人判别   │   └──────┬───────┘   └────────┬──────────┘       │
│  │(Light-ASD) │          │                     │                  │
│  └──────────┘    weights.py 打分            HeadDriver            │
│                   (5个信号加权和)          (眼动优先/纯转头+死区)    │
│                                                     │              │
└─────────────────────────────────────────────────────┼──────────────┘
                                                        │ gRPC (2543端口)
                          ┌─────────────────────────────▼──────────────┐
                          │   servo_tuning (硬件驱动层, 独立进程)        │
                          │   head_grpc_server.py → servo_contrl.py    │
                          │   → USB串口 → 25路舵机(头+脸部表情+眼球)     │
                          └─────────────────────────────────────────────┘

                    ┌───────────────────────────────────────┐
                    │  声源方向: J7034G4麦克风阵列(独立USB串口) │
                    │  读 doa_angle, 转成本体系角度, 两条用途:  │
                    │  ①辅助打分信号 ②没人时主动转头找声源      │
                    └───────────────────────────────────────┘
```

**三个子系统的边界很刻意**：
- `gaze_arbiter/` 是"独立算法原型"——核心的 `signals/weights/scheduler` 三层完全不依赖摄像头/麦克风/舵机，用假数据（`examples/simulate.py`）就能单独验证决策逻辑对不对。
- `servo_tuning/` 是硬件驱动层，`gaze_arbiter` 通过 gRPC 连接它，不直接碰串口。这一层也是舵机映射标定工具（`servo_tuning.html` 等）所在的地方，跟注视决策是两套用途共享同一个硬件驱动。
- `light_asd_test/` 提供人脸检测+说话人判别的模型能力（MediaPipe + Light-ASD），`gaze_arbiter` 直接 `import` 它的检测函数复用，依赖（opencv/torch/mediapipe）已经合并进 `servo_face` 这一个 conda 环境（见"三、新机器上手清单"），不需要再单独维护一份 `light_asd_test/venv`。

---

## 二、核心机制

### 2.1 感知层：从画面到"场上有哪些人"

1. **检测**：`light_asd_test/Light-ASD/live_demo.py::detect_faces_mediapipe()` 用 MediaPipe Face Landmarker（VIDEO 模式）逐帧检测人脸框、头部姿态（yaw/pitch/roll）、嘴部张合度。
2. **跟踪 + 误检过滤**：`FaceTrack` 按 IOU 匹配帧间的人脸框。为了防止阴影/反光/纹理这类单帧误检被立刻当成合法目标（曾经导致"头突然转到没人的地方"），新检测要**连续命中 `MIN_CONFIRM_HITS=3` 帧**才会真正注册进 `PersonRegistry`。
3. **说话人判别**：Light-ASD 用音频 MFCC + 视觉编码器对每个跟踪目标打分，`t.last_score > 0` 判定为"正在说话"。
4. **位置换算**：`face_source.py::bbox_center_yaw_deg()` 把人脸框在画面里的位置，按线性针孔近似换算成本体系方位角。**关键修正**：摄像头是装在眼球机构里的，头转开或者眼珠动了都会带着光轴偏——所以这一步要加上 `HeadDriver.camera_yaw_deg`（头角度+眼相对头的角度）才是真正的本体系角度，不能只按"画面正中=正前方"算。

### 2.2 决策层：给每个人打分，决定看谁、看多久

`gaze_arbiter/gaze_arbiter/` 下的三个模块，纯逻辑、不碰硬件：

- **`signals.py`**：五个独立打分函数，各自返回 `[0,1]`——脸大小、没看过多久（novelty）、跟声源方向是否吻合、朝向机器人的程度、是否在说话。
- **`weights.py`**：五个信号加权求和（不是加权乘积，避免任何一项是0就把总分乘没），除以权重总和落在 `[0,1]`，有个 `MIN_INTEREST_FLOOR=0.03` 兜底避免抽样抽不出人。（历史上还有过"聊天对象"这个第六个信号，靠外部语义模块判定，已从打分公式里移除——`PersonRegistry.set_chat_target()` 这套底层写入接口还留着，为以后接语义/对话模块留了口子，但目前调了也不影响打分。）
- **`scheduler.py`**（`GazeScheduler`）：按权重加权随机抽一个人作为目标，注视时长按"这个人权重占比"在 `min_gaze_s~max_gaze_s` 之间插值（占比越高看得越久），叠加一层随机抖动避免机械感。两条控制切换的规则：
  1. **fixation 未到期默认不重选**——防止"稍微风吹草动就换人"式的抖动。
  2. **高分抢占（例外）**：如果有人的分数持续、明显超过当前正在看的人（`preempt_margin=1.3` 倍以上，且连续保持 `preempt_confirm_s=0.4` 秒），允许提前打断切换，不用死等当前这轮走完。

### 2.3 输出层：把目标角度转成舵机指令

`gaze_arbiter/gaze_arbiter/output/head_driver.py::HeadDriver`：

- **眼动优先（`eye_first`，默认开）**：模仿人的注视方式——目标偏得少（残差 ≤`eye_only_deg=12°`）只转眼珠、头不动；偏得多（残差 ≥`head_engage_deg=20°`）眼珠回中同时转头；两个阈值间是滞回带防止来回切换模式。头和眼珠的指令合并成一帧发送（9600 baud 带宽有限，多发一帧就多占一次带宽）。
- **纯转头模式（`eye_first=False`）**：没有眼珠兜底，改用**死区**（`head_dead_zone_deg=5.0°`，2026-08-26 新加）——目标角度变化在死区以内直接忽略、头完全不动，否则人脸检测的抖动/人的自然小幅晃动会 100% 转成头部动作，显得"过度扭头"。
- **平滑+限速**：两种模式共用同一套 EMA(`smoothing`) + 限速(`max_speed`，归一化值/秒) 组合，防止目标突变时头猛地甩过去（这套经验是从 `servo_tuning/sound_track_head.py` 的声源追踪场景里验证过再搬过来的）。
- **断开/退出**：`recenter()`/`recenter_step()` 慢速回中，不是瞬间跳回，避免舵机受伤。

### 2.4 声源方向（独立系统）

`gaze_arbiter/gaze_arbiter/sound_orient.py` + `input/sound_source.py`：读 J7034G4 麦克风阵列的 `doa_angle`，转成本体系角度。有两条用途：① 作为 `signals.py` 里 `sound_direction_score` 的输入，跟人脸方位角比对匹配度；② **场上没人时主动接管**——一有声音就转头去正对声源方向找人，找到人或搜索超时后交还给正常的打分决策。

### 2.5 硬件驱动层（`servo_tuning/`）

- **`head_grpc_server.py`**：gRPC 服务，加载某个机型的 `servoConfig_*.yaml`（舵机ID/行程/方向标定），接收 `SetHeadState` 指令，clamp 到每个舵机的机械限位后经串口下发。目前只支持单一机型静态配置（机型切换的网页功能已经在 2026-08-25 被移除，如果要换机型需要手动改 `--config` 参数重启这个服务）。
- **`servo_contrl.py`**：串口帧协议、断线重连。
- 舵机映射有**两层**：`servoConfig_*.yaml`（硬件层：舵机名→ID/PWM行程/方向）和 `config/*.yaml` 如 `ULA_new.yaml`（语义层：ARKit blendshape→舵机目标值，只有 `servo_tuning.html`/`test_mimic.py` 这类表情映射工具用，`gaze_arbiter` 的注视/转头这条链路不经过它）。

---

## 三、新机器 / 新同事首次上手清单

拿到这份代码后跑不起来，大概率是漏了下面几样——都是"不算源码、`git clone` 不会自动带过来"或者"跟这台机器强绑定"的东西：

1. **装 Python 环境**：跑 `bash install_conda_env.sh`，会建一个叫 `servo_face` 的 conda 环境，把 opencv/torch/mediapipe/grpcio 这些依赖装好（版本锁死成实测能跑通的组合，不要自己升级）。主流程（`start.sh`/`web_dashboard.py`）**不需要**再单独装 `light_asd_test/venv`——那是这个项目更早期的环境隔离方式，`install_conda_env.sh` 已经把它要的东西合并进 `servo_face` 里了。**例外**：`light_asd_test/live_demo.sh`/`live_test.sh` 这两个独立跑检测的脚本（4.4节）里硬编码了 `venv/bin/python`，如果要用这两个脚本，要么另外建一份 `light_asd_test/venv`，要么跳过脚本直接用 `servo_face` 的 python 跑 `light_asd_test/Light-ASD/live_demo.py`（见 4.4 节）。
2. **装 ffmpeg**：`light_asd_test/bin/`（ffmpeg 可执行文件）体积太大没有跟着仓库走，需要自己装：`sudo apt install ffmpeg`，或者找原来给你这份代码的人要那个 `bin/` 目录。装完确认 `ffmpeg`/`ffprobe` 在 `PATH` 里能直接调用，或者把它们放进 `light_asd_test/bin/` 目录（`live_demo.py` 里两条路径都会尝试）。
3. **确认摄像头/舵机设备路径**：`VIDEO_DEV`、`ROBOT_VIDEO_DEV`、舵机串口这些配置里写的默认值是**原来那台机器的 USB by-id 路径**，换一台电脑几乎肯定对不上。开机后先用 `ls /dev/v4l/by-id/` 和 `ls /dev/serial/by-id/` 查一下这台机器实际的设备名，通过环境变量覆盖（见下面"环境变量参考"），不要直接用默认值硬跑。
4. **确认舵机标定配置对应你手上的机型**：仓库里带的是 Ula/G01/G02/L01 几个机型的 `servoConfig_*.yaml`，`start.sh` 默认用的是 Ula 那份——如果你手上是别的机型，得改 `--config` 参数指到对应文件，标定数据（舵机ID/行程/方向）不对会导致表情做反或者撞到机械限位。
5. **Light-ASD 模型权重**：`finetuning_TalkSet.model`/`pretrain_AVA_CVPR.model` 现在跟着仓库一起走了（`git clone` 会带上），不用额外下载。

---

## 四、启动方法

### 4.1 一键启动（推荐，日常用这个）

```bash
cd attention
bash start.sh
```

会依次做：① 用 `conda activate servo_face` 激活环境 → ② 拉起 `head_grpc_server.py`（后台，端口2543）→ ③ 现测这台机器内置麦克风的声卡编号（每次重启 card 编号可能变，不写死）→ ④ 前台跑 `web_dashboard.py`（端口8642）。`Ctrl+C` 停止会自动带走头部舵机服务。

浏览器打开 `http://localhost:8642/`（局域网内其他设备用这台机器的IP）。

首次跑需要先装环境：`bash install_conda_env.sh`（装进 conda 环境 `servo_face`，含 opencv/torch/mediapipe/grpcio 等重依赖）。

### 4.2 单独重启某一部分

两个服务是独立进程，改了哪部分代码只需要重启对应的进程（Python 改动只在进程重新 import 那一刻生效，改磁盘文件对已运行的进程无效）：

- 只改了 `gaze_arbiter/` 或 `web_dashboard.py`：只重启 `web_dashboard.py`，`head_grpc_server.py` 不用动（尤其是它正握着真实串口连接时，没必要冒重启触发硬件复位的风险）。
- 只改了 `servo_contrl.py`/`head_grpc_server.py`：只重启舵机服务，网页不用重启，重新点一下"连接机器人"即可。

```bash
# 手动单独拉起网页(需要先 conda activate servo_face)
HEAD_HOST=127.0.0.1 AUDIO_DEV=hw:0,0 python gaze_arbiter/examples/web_dashboard.py

# 手动单独拉起舵机服务
python servo_tuning/head-sdk-face/head-server/src/head_grpc_server.py \
  --config servo_tuning/head-sdk-face/head-server/src/servoConfig_25DV3_Ula.yaml
```

### 4.3 不接硬件，纯算法验证

```bash
cd gaze_arbiter
python examples/simulate.py       # 假数据跑通整条决策链路, 肉眼看输出符不符合直觉
```

### 4.4 light_asd_test 独立跑（不接注视决策，仅验证检测/说话人判别本身）

```bash
cd light_asd_test
VIDEO_DEV=/dev/video0 AUDIO_DEV=hw:0,0 bash live_demo.sh
```
`live_demo.sh` 硬编码用的是 `light_asd_test/venv/bin/python`，这份仓库**没有**带这个 venv（历史遗留路径，`servo_face` 已经覆盖了它要的依赖）。两个选择：① 另建一份 `light_asd_test/venv` 装同样依赖；② 跳过脚本，直接用 `servo_face` 环境的 python 跑（更省事，推荐）：

```bash
conda activate servo_face
cd light_asd_test/Light-ASD
python live_demo.py --videoIndex 0 --audioDevice hw:0,0 --pretrainModel weight/finetuning_TalkSet.model
```

---

## 五、使用方法（网页仪表盘）

打开网页后主要有这几个开关，每个都是独立的"期望状态 vs 实际状态"同步（点按钮只是记一下期望值，真正的连接/断开动作在主循环里做）：

| 按钮/功能 | 作用 |
|---|---|
| 开启摄像头 | 开始跑人脸检测（本地摄像头或机器人自带摄像头，两者可热切换不用重启） |
| 连接机器人 | 连上 `head_grpc_server.py`，之后注视决策的结果才会真正转头 |
| 开启声源定位 | 打开麦克风阵列串口，读声源方向；场上没人时会自动转头找声音 |
| 开启眼动优先 | 运行时随时可切换（不用重连机器人）：开=小幅度只瞟眼珠、大幅度才转头；关=只转头，靠死区过滤小抖动 |

页面上还实时显示：每个人的方位角/状态、当前选中目标的打分明细（五个信号各自的分数）、声源方向仪表盘、眼动优先当前是"只动眼珠"还是"转头中"。

---

## 六、环境变量参考

启动前用环境变量覆盖默认值，比如 `HEAD_INVERT=0 bash start.sh`（但 `start.sh` 目前没有转发所有变量，更精细的调参建议用 3.2 节手动启动网页那种方式）。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `HEAD_HOST` | `127.0.0.1` | 头部舵机 gRPC 服务地址 |
| `HEAD_SDK_PORT` | `2543` | 头部舵机 gRPC 端口 |
| `HEAD_INVERT` | `1` | 头部转动方向是否反相（实测跟人脸方位角方向相反，默认开） |
| `HEAD_SMOOTHING` | `0.25` | 头部 EMA 平滑系数，越小越平滑但越滞后 |
| `HEAD_MAX_SPEED` | `0.4` | 头部最高转速（归一化值/秒），保护舵机用，别调太高 |
| `EYE_FIRST` | `1` | 眼动优先默认是否开启（网页按钮的初始值，之后可实时切换） |
| `EYE_ONLY_DEG` | `12.0` | 眼动优先：残差小于它只动眼珠 |
| `HEAD_ENGAGE_DEG` | `20.0` | 眼动优先：残差大于它才转头 |
| `MIC_PORT` | `/dev/ttyUSB0` | 麦克风阵列串口设备 |
| `MIC_BAUD` | `115200` | 麦克风阵列波特率 |
| `MIC_INVERT` / `MIC_OFFSET_DEG` | — | 声源方向左右反了/角度对不上时校准用 |
| `AUDIO_DEV` | `hw:0,0` | Light-ASD 用的麦克风设备（跟声源定位的 `MIC_PORT` 是两路不同硬件） |
| `VIDEO_DEV` / `ROBOT_VIDEO_DEV` | 各自的 by-id 路径 | 本地摄像头 / 机器人摄像头设备，写死 by-id 避免重启后 `/dev/videoN` 编号漂移导致串台 |
| `LOCAL_CAMERA_FLIP` / `ROBOT_CAMERA_FLIP` | `0` / `1` | 摄像头装反了要不要翻转画面（机器人那颗默认装反，已经开着） |
| `WEB_PORT` | `8642` | 网页仪表盘端口 |
| `SOUND_ORIENT_*` | 见代码 | 没人时主动转头找声源那套逻辑的置信度阈值/搜索超时 |

`head_dead_zone_deg`（纯转头模式的死区，默认 `5.0°`）目前**没有**对应的环境变量，只能改 `HeadDriverConfig` 的默认值或在代码里传参——后续如果要频繁调这个值，可以照着其他参数的样子加一个环境变量。

---

## 七、已知限制 / 待办

- **`facing_score`（朝向机器人程度）尚未真正校准**：这个信号的写入依赖头部姿态估计模块，目前上游数据链路还没接真实校准好的模型，长期可能导致大部分人这一项分数长期偏低。
- **`eye_fov_deg`（眼珠满行程对应视角，默认40°）是估计值**，眼珠实际能转多大角度需要上真机标定。
- **舵机板首次打开串口会触发 DTR 复位**，重新枚举后设备节点可能变化（`/dev/ttyACM0`→`ttyACM1`），配置文件用的是 `by-id` 稳定路径能自动跟上，但复位瞬间如果恰好在写指令，可能导致半截帧被误解析成随机角度（抽搐）。目前的缓解措施是开机/关闭走缓慢归位（`SERVO_STARTUP_RAMP_S`/`SERVO_SHUTDOWN_RAMP_S`），根治方案（在打开串口时抑制 DTR）还没做。
- **CH340（麦克风阵列）和舵机板目前可能共用同一个供电不足的 USB Hub**，实测出现过舵机复位瞬间把整个 Hub 拖垮、摄像头和麦克风一并掉线的情况，建议接口都用主机原生口。
- **两份新机型（G01_11）的舵机标定配置文件**（`servo_tuning/config/G01_10.yaml`、`servoConfig_25DV3_G01_11.yaml`）还没有提交进 git，机型切换的网页功能也已经移除，目前只有 Ula 这一个机型能直接用。

---

## 八、相关文档

- `gaze_arbiter/PLATFORM.md` —— 注视决策算法的详细公式推导、参数表
- `servo_tuning/使用说明.md` / `声音追踪使用说明.md` —— 舵机映射标定工具、声源追踪脚本的具体用法
- `light_asd_test/README.md` —— 人脸检测/说话人判别独立跑的说明
