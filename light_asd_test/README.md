# 防误触发 - 音视频说话人判别 测试平台

本目录是"是否对着设备说话"判别方案的技术验证环境：人脸检测/头部朝向/嘴部张合用
MediaPipe Face Landmarker（Apache 2.0，可商用），说话置信度打分用 Light-ASD
（代码MIT，**但预训练权重训练自非商用数据集，目前仅限内部研究测试，不能直接用于商用产品**）。

---

## 目录结构

```
light_asd_test/
├── venv/                    # Python虚拟环境(已装好所有依赖)
├── bin/ffmpeg, bin/ffprobe  # 免sudo的静态ffmpeg(音视频录制/转码用)
├── mediapipe_models/
│   └── face_landmarker.task # MediaPipe人脸关键点+朝向+表情模型
├── Light-ASD/
│   ├── weight/               # 说话人判别模型权重(AVA原版 + TalkSet微调版)
│   ├── live_demo.py          # 实时检测主程序
│   └── demo/                 # 录制/测试产生的视频都存这里
├── live_demo.sh              # 【实时检测】入口脚本
├── live_test.sh              # 【录制+批量分析】入口脚本
├── preview.py                # 摄像头预览(被live_test.sh调用)
├── analyze_result.py         # 对某次录制结果单独生成分析报告
├── 6DRepNet / 6DRepNet360     # 头部朝向模型(已clone，权重下载暂未打通，见下方"已知问题")
└── mismatch_test/            # 之前做音画不匹配测试用的音频样本
```

---

## 场景一：实时检测界面（推荐，日常测试用这个）

弹出摄像头窗口，实时显示每张人脸的说话判定(绿框=说话/红框=未说话)、置信度分数、
头部朝向角度(yaw/pitch)、嘴部张合度。支持同时多人入镜，各自独立打分。

```bash
cd light_asd_test
./live_demo.sh
```

**退出**：窗口聚焦后按 `Q`

**可选环境变量**：

```bash
AUDIO_DEV=hw:0,6 ./live_demo.sh      # 换成阵列麦克风(默认是内置麦克风 hw:0,0)
VIDEO_DEV=/dev/video1 ./live_demo.sh # 换摄像头
CHECKPOINT=weight/pretrain_AVA_CVPR.model ./live_demo.sh  # 换成AVA原版权重(默认TalkSet微调版)
```

---

## 场景二：录制一段 + 生成可视化结果视频 + 打分报告

适合需要保存证据视频、或者要跟音频能量VAD做量化对比时用。

```bash
cd light_asd_test
./live_test.sh 15      # 15是录制秒数，不传默认也是15
```

流程：摄像头预览确认位置(按空格开始) → 倒计时3秒 → 录制N秒 → 自动跑模型 → 打印逐秒打分报告
→ 输出可视化视频路径(`Light-ASD/demo/live_xxx/pyavi/video_out.avi`，绿框/红框标好的)

同样支持 `AUDIO_DEV` / `VIDEO_DEV` / `CHECKPOINT` 环境变量。

---

## 单独看某次结果的分析报告

```bash
venv/bin/python analyze_result.py <videoName>   # videoName对应 Light-ASD/demo/<videoName>/ 目录
```

---

## 已知问题 / 待办

1. **头部朝向角度符号未校准**：`live_demo.py` 里 `rotation_matrix_to_euler_deg()` 转出来的
   yaw/pitch 正负方向可能和直觉相反，需要实测时转头/点头观察数字变化方向，不对就在该函数里改符号。
2. **6DRepNet / 6DRepNet360 权重下载未打通**：官方Nextcloud(cloud.ovgu.de)链接已失效，
   Google Drive备用链接下载中断。目前项目里的头部朝向功能走的是MediaPipe的方案，
   这两个repo可以先不管，除非后续要单独对比精度。
3. **Light-ASD 权重不可商用**：`weight/` 下两个 `.model` 文件训练自 AVA-ActiveSpeaker /
   TalkSet(含VoxCeleb2、LRS3)，这些数据集是研究专用授权。当前定位是"内部技术验证工具"，
   真要产品化，需要用自己采集的数据重新训练同架构模型(代码本身MIT可以随便用)。
4. 没有GPU，CPU上大概率跑不满25fps，画面卡顿属于正常现象。
