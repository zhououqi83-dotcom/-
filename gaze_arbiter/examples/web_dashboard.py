#!/usr/bin/env python3
"""web_dashboard.py — 把 gaze_arbiter 的实时效果做成一个网页, 用浏览器看.

背景: cv2.imshow() 弹出的本地窗口在这台机器上不一定看得到(远程连接/桌面
渲染环境的问题, 排查起来跟 gaze_arbiter 本身无关), 干脆做成网页——只要能
打开浏览器(哪怕是局域网里另一台电脑/手机), 就一定能看到, 不依赖这台机器
本地的图形桌面环境。

**必须用 light_asd_test 自己的 venv 跑**(原因跟 run_with_face.py 一样:
opencv/torch/mediapipe 这些重依赖只在那边装了):

    light_asd_test/venv/bin/python gaze_arbiter/examples/web_dashboard.py
    MIC_PORT=/dev/ttyUSB0 light_asd_test/venv/bin/python gaze_arbiter/examples/web_dashboard.py

启动后打开浏览器访问 http://<这台机器的IP或localhost>:8642/ ,页面上:
  · 左边: 实时摄像头画面, 跟 run_with_face.py 一样每张脸绿/红框+朝向,
    GazeScheduler 选中的人黄色粗框。
  · 右边: **声源方向仪表盘**(半圆形, 0°=正前=顶部中间, 左负右正), 红色
    指针指向当前声源方向, 每张脸也在弧上画一个小点(说话中=绿色, 选中的人
    有金色描边), 一眼能看出"声音方向跟哪张脸对上了"。
  · 下面: 当前选中目标的六个权重分量条形图(脸大/没看过/声源/朝向/说话/
    聊天对象), 调参时看这个最直接。

不需要装任何新依赖: 后端就是 Python 内置的 http.server(MJPEG 视频流用
`multipart/x-mixed-replace`, 状态数据用一个轮询的 JSON 接口), 前端是一个
内嵌在这个文件里的 HTML/CSS/JS, 不依赖任何 CDN, 局域网内直接能用。

跟 run_with_face.py 关系: 检测/跟踪/打分/画框这套逻辑基本是同一份(包括同样
原样复制、需要手动同步的 `score_tracks()`), 只是把"发指令渲染到 cv2 窗口"
换成了"编码成 JPEG 塞进 HTTP 响应"。两边目前是独立的两份循环代码, 没有共享
——如果以后出现第三个消费方, 值得把主循环抽出来共享, 现在先不做这个抽象。

**可选: 同时真实驱动头部舵机**(自然摇头, 不再是假数据), 设置 `HEAD_HOST`
环境变量就会启用, 不设就跟以前一样(只在网页上看效果, 头不动):

    # 终端 1(常驻): 底层 gRPC 服务, 打通电脑跟机器人头的串口通信
    servo_tuning/venv_face_servo/bin/python servo_tuning/head-sdk-face/head-server/src/head_grpc_server.py \
        --config servoConfig_25DV3_Ula.yaml

    # 终端 2: 这个脚本, 加 HEAD_HOST 就会真的转头
    HEAD_HOST=127.0.0.1 MIC_PORT=/dev/ttyUSB0 light_asd_test/venv/bin/python gaze_arbiter/examples/web_dashboard.py

用的是 `output/head_driver.py` 里同一套 EMA+限速平滑(跟 `run_with_head.py`
一样), 只是目标角度不再来自假数据, 而是每一帧真实人脸算出来的方位角
(`bbox_center_yaw_deg`)——这也是为什么 `face_source.py` 和 `head_driver.py`
两边的 `yaw_deg` 要用同一套"本体系角度"约定: 人脸在画面里的方位角, 跟
头该转到的角度, 天然就是同一个数字, 中间不需要再转换一次。

`head_sdk`(以及它依赖的 grpcio/protobuf)额外装进了 `light_asd_test/venv`
里(跟 opencv/torch/mediapipe 没有冲突, 装之前专门检查过版本), 只有开这个
功能才用得到, 不开就完全不影响原来的人脸检测这部分。

Ctrl+C 停止, 退出前头会回中。
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

HERE = os.path.dirname(os.path.abspath(__file__))
GAZE_ARBITER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
LASD_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "light_asd_test", "Light-ASD"))

sys.path.insert(0, GAZE_ARBITER_ROOT)
sys.path.insert(0, LASD_DIR)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import python_speech_features  # noqa: E402
import torch  # noqa: E402

from live_demo import (  # noqa: E402 — 复用 light_asd_test 现成的检测/跟踪/模型, 不重新实现
    ASD,
    AUDIO_SR,
    DEVICE,
    FACE_LANDMARKER_MODEL,
    MAX_FACES,
    MAX_MISSED,
    UPDATE_EVERY_SEC,
    VIDEO_FPS,
    WINDOW_SEC,
    AudioStreamer,
    FaceTrack,
    crop_face,
    detect_faces_mediapipe,
    match_tracks,
)
from mediapipe.tasks import python as mp_python  # noqa: E402
from mediapipe.tasks.python import vision as mp_vision  # noqa: E402

from gaze_arbiter import (  # noqa: E402
    GazeScheduler,
    PersonRegistry,
    SignalParams,
    SoundContext,
    SoundOrientConfig,
    SoundOrientState,
    WeightConfig,
    compute_interest,
)
from gaze_arbiter.input.face_source import FaceSourceConfig, observe_face_track  # noqa: E402
from gaze_arbiter.input.sound_source import J7034DoaReader, SoundSourceConfig  # noqa: E402
from gaze_arbiter.output.head_driver import HeadDriver, HeadDriverConfig  # noqa: E402

# 覆盖 live_demo.py 里的默认值(0.1s)——profile 过 score_tracks() 单人一次
# 打分里视觉编码器(3D CNN)本身就要 35~100ms(这台机器没有 GPU, 2026-08-19
# 测的), 5 个人同时到打分点时 0.1s 一轮根本来不及跑完, 是"多人时卡"的根因
# (batch 合并调用测过没用, 瓶颈是纯计算量)。调大打分间隔直接按比例砍总
# 计算量, 代价是 is_speaking 信号刷新变慢(仍然是新鲜的, 只是没有原来
# 那么灵敏)。
UPDATE_EVERY_SEC = float(os.environ.get("ASD_UPDATE_INTERVAL_S", "0.3"))

PORT = int(os.environ.get("WEB_PORT", "8642"))
# 断开连接/退出进程时慢速回中的最长阻塞秒数(回中本身约 1~1.5s, 留余量; 设 0 立即断开)
RECENTER_EXIT_MAX_S = float(os.environ.get("RECENTER_EXIT_MAX_S", "3.0"))

# 一帧误检(阴影/反光/纹理)跟真脸走的是完全一样的检测路径, 刚出现就立刻注册进
# PersonRegistry 的话会自带满分新鲜度权重(novelty_score, 见 weights.py), 有可能
# 被 GazeScheduler 选中并锁定注视方向好几秒——表现成"头突然转到没人的地方"。
# 要求 FaceTrack 连续命中够这么多帧(见 live_demo.py::FaceTrack.hits/mark_missed)
# 才注册, 从源头过滤掉这类一闪而过的误检(同一个门槛也用在 run_with_face.py 里)。
MIN_CONFIRM_HITS = 3


def score_tracks(tracks, audio: AudioStreamer, asd: ASD) -> None:
    """给每个够格的 track 打一次 ASD 分数.

    跟 `light_asd_test/Light-ASD/live_demo.py::main()`(以及 `run_with_face.py`
    里同一份复制)算的是同一件事, 但这里把多个人的模型前向合并成了一次 batch
    调用——CPU 上跑模型, 单次调用的固定开销(Python/张量构造/kernel launch)
    不小, 原来是几个人来几次串行调用, 人一多主循环就卡一下(2026-08-19 定位:
    多人时明显卡顿的主因); 同一时刻"到期"的 track 大概率处于同一个稳定
    WINDOW_SEC 窗口长度(只有刚出现、缓冲区还没攒满的 track 例外, 数量少),
    按窗口长度分组后堆成 batch 一起过模型, 结果跟逐个跑数值上完全一致(模型
    在 eval() 模式, BatchNorm/GRU 都不会跨样本互相影响), 只是调用次数从 N
    次变成"分组数"次(绝大多数情况下就是 1 次)。
    """
    now = time.time()
    due = [t for t in tracks
           if (now - t.last_update) >= UPDATE_EVERY_SEC and len(t.video_buf) >= int(VIDEO_FPS * 0.4)]
    if not due:
        return
    for t in due:
        t.last_update = now  # 不管这轮最终有没有真的打上分, 都按周期计时, 跟原来行为一致

    araw = audio.get_last_seconds(WINDOW_SEC)
    if len(araw) <= AUDIO_SR * 0.3:
        return
    mfcc = python_speech_features.mfcc(araw, AUDIO_SR, numcep=13, winlen=0.025, winstep=0.010)

    # 按 v 的帧数分组——同一 batch 里所有样本的时间长度必须一致, 模型不支持
    # 变长 batch。mfcc 是同一段音频, 组内的 a 也完全相同(只是切片长度一样)。
    groups: dict[int, list] = {}
    for t in due:
        vfeat = np.array(t.video_buf)[-int(VIDEO_FPS * WINDOW_SEC):]
        length = min((mfcc.shape[0] - mfcc.shape[0] % 4) / 100, vfeat.shape[0] / VIDEO_FPS)
        if length <= 0.2:
            continue
        n_v = int(round(length * VIDEO_FPS))
        n_a = int(round(length * 100))
        groups.setdefault(n_v, []).append((t, vfeat[-n_v:], n_a))

    for items in groups.values():
        n_a = items[0][2]
        a_batch = np.repeat(mfcc[:n_a, :][None, :, :], len(items), axis=0)
        v_batch = np.stack([v for _, v, _ in items], axis=0)
        with torch.no_grad():
            inputA = torch.FloatTensor(a_batch).to(DEVICE)
            inputV = torch.FloatTensor(v_batch).to(DEVICE)
            embedA = asd.model.forward_audio_frontend(inputA)
            embedV = asd.model.forward_visual_frontend(inputV)
            out = asd.model.forward_audio_visual_backend(embedA, embedV)
            scores = asd.lossAV.forward(out, labels=None)  # numpy, 形状 [len(items) * T]
        scores = scores.reshape(len(items), -1)  # -> [len(items), T], 每行对应一个 track
        for (t, _, _), s in zip(items, scores):
            t.last_score = float(s[-1])


class SharedState:
    """检测循环(生产者线程只有一个: 主线程) 和 HTTP handler(多个并发线程)
    之间共享的状态, 靠一把锁保护. 分两类:

      · 最新一帧画面 + 检测结果(主循环写, handler 读)
      · "期望摄像头/头部舵机是开还是关"这两个开关(handler 写, 主循环读)
        ——这就是"连接机器人"/"开启摄像头"两个按钮实际生效的地方: 按钮点击
        只是把这两个 bool 改一下, 真正去开摄像头/连头部服务的动作还是在
        主循环里做(硬件资源该归谁管理、谁操作, 不能跨线程乱来)。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._state: dict = {"people": [], "sound": {}, "gaze_target_person_id": None,
                             "fps": 0.0, "mic_connected": False, "mic_error": None,
                             "camera_active": False, "camera_error": None, "camera_source": "local",
                             "head_connected": False, "head_error": None}
        self._camera_desired = False
        self._head_desired = False
        self._sound_desired = False
        # 眼动优先默认开着(跟 head_driver.HeadDriverConfig 的默认值一致),
        # EYE_FIRST=0 可以把这个默认值改成关——按钮点了之后就以按钮为准。
        self._eye_first_desired = os.environ.get("EYE_FIRST", "1") not in ("0", "false", "False")
        self._camera_source = "local"  # "local" | "robot"

    def update(self, jpeg: bytes, state: dict) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._state = state

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def latest_state(self) -> dict:
        with self._lock:
            return self._state

    def set_camera_desired(self, v: bool) -> None:
        with self._lock:
            self._camera_desired = v

    def camera_desired(self) -> bool:
        with self._lock:
            return self._camera_desired

    def set_head_desired(self, v: bool) -> None:
        with self._lock:
            self._head_desired = v

    def set_camera_source(self, v: str) -> None:
        with self._lock:
            self._camera_source = v

    def camera_source(self) -> str:
        with self._lock:
            return self._camera_source

    def head_desired(self) -> bool:
        with self._lock:
            return self._head_desired

    def set_sound_desired(self, v: bool) -> None:
        with self._lock:
            self._sound_desired = v

    def sound_desired(self) -> bool:
        with self._lock:
            return self._sound_desired

    def set_eye_first_desired(self, v: bool) -> None:
        with self._lock:
            self._eye_first_desired = v

    def eye_first_desired(self) -> bool:
        with self._lock:
            return self._eye_first_desired


SHARED = SharedState()


def make_placeholder_jpeg(text: str) -> bytes:
    """摄像头没开的时候, /video_feed 也要能吐出点东西, 不然浏览器里 <img>
    标签就是一个加载失败的空洞。生成一张纯色 + 文字的占位图。"""
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    img[:] = (26, 27, 34)  # 跟页面背景色 #1a1b22 附近, 不要太突兀
    cv2.putText(img, text, (40, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (139, 147, 163), 2)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else b""


CAMERA_OFF_JPEG = make_placeholder_jpeg("camera off - click start")


INDEX_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>gaze_arbiter 实时演示</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: -apple-system, "Noto Sans CJK SC", sans-serif;
         background:#0f1115; color:#e6e6e6; }
  header { padding: 12px 20px; background:#181b22; border-bottom:1px solid #2a2e38; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header p { margin:4px 0 0; font-size:12px; color:#8b93a3; }
  .layout { display:flex; flex-wrap:wrap; gap:16px; padding:16px; }
  .video-col { flex: 2 1 480px; min-width:320px; }
  .side-col { flex: 1 1 320px; min-width:300px; display:flex; flex-direction:column; gap:16px; }
  .card { background:#181b22; border:1px solid #2a2e38; border-radius:10px; padding:14px; }
  .card h2 { font-size:13px; margin:0 0 10px; color:#8b93a3; font-weight:600;
             text-transform:uppercase; letter-spacing:.04em; }
  #video { width:100%; border-radius:8px; display:block; background:#000; }
  #gauge { width:100%; height:auto; display:block; }
  .status-line { font-size:12px; color:#8b93a3; margin-top:8px; }
  .status-line b { color:#e6e6e6; }
  table.people { width:100%; border-collapse:collapse; font-size:12px; }
  table.people th, table.people td { text-align:left; padding:4px 6px; border-bottom:1px solid #2a2e38; }
  table.people th { color:#8b93a3; font-weight:500; }
  .badge { display:inline-block; padding:1px 6px; border-radius:4px; font-size:11px; }
  .badge.gaze { background:#f5c518; color:#20232b; font-weight:700; }
  .badge.speaking { background:#2ecc71; color:#0d2113; }
  .bar-row { display:flex; align-items:center; gap:8px; margin-bottom:6px; font-size:12px; }
  .bar-label { width:64px; color:#8b93a3; flex-shrink:0; }
  .bar-track { flex:1; height:10px; background:#242833; border-radius:5px; overflow:hidden; }
  .bar-fill { height:100%; background:linear-gradient(90deg,#4f8cff,#7fd1ff); }
  .bar-val { width:34px; text-align:right; color:#c8ccd6; flex-shrink:0; }
  .empty-hint { color:#5b6272; font-size:12px; }
  .card-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
  .card-header h2 { margin:0; }
  .toggle-btn { font-size:12px; padding:5px 12px; border-radius:6px; border:1px solid #3a3f4d;
                background:#242833; color:#e6e6e6; cursor:pointer; }
  .toggle-btn:hover { background:#2d323f; }
  .toggle-btn:disabled { opacity:0.5; cursor:default; }
  .toggle-btn.on { background:#1f6b3d; border-color:#2ecc71; }
  .toggle-btn.error { border-color:#ff5566; color:#ff8a97; }
  .source-switch { display:flex; gap:6px; margin-bottom:10px; }
  .source-btn { flex:1; font-size:12px; padding:6px; border-radius:6px; border:1px solid #3a3f4d;
                background:#181b22; color:#8b93a3; cursor:pointer; }
  .source-btn:hover { background:#242833; }
  .source-btn.active { background:#4f8cff; border-color:#4f8cff; color:#0d1420; font-weight:600; }
  .legend { display:flex; flex-wrap:wrap; gap:10px 16px; margin-top:10px; font-size:11px; color:#8b93a3; }
  .legend .item { display:flex; align-items:center; gap:5px; }
  .legend .swatch { width:14px; height:14px; border-radius:50%; flex-shrink:0; display:inline-block; }
  .legend .swatch.dot-idle { background:#5b6272; }
  .legend .swatch.dot-speaking { background:#2ecc71; }
  .legend .swatch.dot-gaze { background:#5b6272; box-shadow:0 0 0 3px #f5c518; }
  .legend .swatch.line-sound { width:16px; height:3px; border-radius:2px; background:#ff5566; }
</style>
</head>
<body>
<header>
  <h1>gaze_arbiter 实时演示</h1>
  <p>人脸检测(light_asd_test) + 声源方向(J7034G4) → GazeScheduler 决策, 每 150ms 刷新一次</p>
</header>
<div class="layout">
  <div class="video-col">
    <div class="card">
      <div class="card-header">
        <h2>摄像头画面</h2>
        <button class="toggle-btn" id="cameraBtn" onclick="toggleCamera()">开启摄像头</button>
      </div>
      <div class="source-switch">
        <button class="source-btn" id="sourceLocalBtn" onclick="setCameraSource('local')">本地摄像头</button>
        <button class="source-btn" id="sourceRobotBtn" onclick="setCameraSource('robot')">机器人摄像头</button>
      </div>
      <img id="video" src="/video_feed" alt="video stream">
      <div class="status-line" id="cameraStatus">摄像头未开启</div>
    </div>
  </div>
  <div class="side-col">
    <div class="card">
      <div class="card-header">
        <h2>声源方向仪表盘</h2>
        <button class="toggle-btn" id="headBtn" onclick="toggleHead()">连接机器人</button>
      </div>
      <button class="toggle-btn" id="soundBtn" onclick="toggleSound()" style="width:100%;margin-bottom:10px;">开启声源定位</button>
      <button class="toggle-btn" id="eyeFirstBtn" onclick="toggleEyeFirst()" style="width:100%;margin-bottom:10px;">开启眼动优先</button>
      <canvas id="gauge" width="320" height="200"></canvas>
      <div class="status-line" id="soundStatus">等待数据...</div>
      <div class="legend">
        <span class="item"><span class="swatch line-sound"></span>声源方向(麦克风阵列读数)</span>
        <span class="item"><span class="swatch dot-idle"></span>场上的人(方位角)</span>
        <span class="item"><span class="swatch dot-speaking"></span>正在说话的人</span>
        <span class="item"><span class="swatch dot-gaze"></span>金色描边 = 当前被选中注视的人</span>
      </div>
      <div class="status-line">刻度 -90°~90° = 相对机器人正前方(0°)的左右方位角, 半圆顶部朝前</div>
      <div class="status-line" id="headStatus">头部舵机: 未连接</div>
      <div class="status-line" id="gazeModeStatus">眼动优先: 需要先连接机器人</div>
    </div>
    <div class="card">
      <h2>当前选中目标 · 权重构成</h2>
      <div id="breakdown"><p class="empty-hint">还没有选中任何人</p></div>
    </div>
    <div class="card">
      <h2>场上所有人</h2>
      <table class="people" id="peopleTable">
        <thead><tr><th>ID</th><th>方位</th><th>状态</th></tr></thead>
        <tbody><tr><td colspan="3" class="empty-hint">还没检测到人脸</td></tr></tbody>
      </table>
    </div>
  </div>
</div>
<script>
const SIGNAL_LABELS = {
  size: "脸大小", novelty: "没看过", sound: "声源", facing: "朝向", speaking: "说话"
};

function drawGauge(ctx, w, h, state) {
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h - 20, r = Math.min(w, h - 30) / 2 - 10;

  // 半圆弧背景 + 刻度
  ctx.strokeStyle = "#2a2e38";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, r, Math.PI, 2 * Math.PI);
  ctx.stroke();

  ctx.fillStyle = "#5b6272";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "center";
  [-90, -45, 0, 45, 90].forEach(deg => {
    const rad = (deg - 90) * Math.PI / 180;
    const x1 = cx + Math.cos(rad) * (r - 6), y1 = cy + Math.sin(rad) * (r - 6);
    const x2 = cx + Math.cos(rad) * r, y2 = cy + Math.sin(rad) * r;
    ctx.strokeStyle = "#3a3f4d";
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    const lx = cx + Math.cos(rad) * (r + 14), ly = cy + Math.sin(rad) * (r + 14);
    ctx.fillText(deg + "°", lx, ly);
  });

  // 每张脸在弧上的小点
  (state.people || []).forEach(p => {
    const yaw = Math.max(-90, Math.min(90, p.yaw_deg));
    const rad = (yaw - 90) * Math.PI / 180;
    const x = cx + Math.cos(rad) * r, y = cy + Math.sin(rad) * r;
    ctx.beginPath();
    ctx.arc(x, y, p.is_gaze_target ? 7 : 5, 0, 2 * Math.PI);
    ctx.fillStyle = p.is_speaking ? "#2ecc71" : "#5b6272";
    ctx.fill();
    if (p.is_gaze_target) {
      ctx.strokeStyle = "#f5c518";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  });

  // 声源方向指针
  const sound = state.sound || {};
  if (sound.doa_deg !== null && sound.doa_deg !== undefined) {
    const yaw = Math.max(-90, Math.min(90, sound.doa_deg));
    const rad = (yaw - 90) * Math.PI / 180;
    const x = cx + Math.cos(rad) * (r - 4), y = cy + Math.sin(rad) * (r - 4);
    ctx.strokeStyle = "#ff5566";
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
  }

  ctx.beginPath();
  ctx.arc(cx, cy, 4, 0, 2 * Math.PI);
  ctx.fillStyle = "#e6e6e6";
  ctx.fill();
}

function renderBreakdown(container, people) {
  const target = people.find(p => p.is_gaze_target);
  if (!target || !target.breakdown) {
    container.innerHTML = '<p class="empty-hint">还没有选中任何人</p>';
    return;
  }
  const b = target.breakdown;
  let html = `<div class="status-line">当前选中: <b>${target.person_id}</b>(总分 ${b.total.toFixed(2)})</div>`;
  for (const key of ["size", "novelty", "sound", "facing", "speaking"]) {
    const v = b[key] || 0;
    html += `<div class="bar-row">
      <div class="bar-label">${SIGNAL_LABELS[key]}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(v * 100).toFixed(0)}%"></div></div>
      <div class="bar-val">${v.toFixed(2)}</div>
    </div>`;
  }
  container.innerHTML = html;
}

function renderPeopleTable(tbody, people) {
  if (!people.length) {
    tbody.innerHTML = '<tr><td colspan="3" class="empty-hint">还没检测到人脸</td></tr>';
    return;
  }
  tbody.innerHTML = people.map(p => {
    const badges = [];
    if (p.is_gaze_target) badges.push('<span class="badge gaze">GAZE</span>');
    if (p.is_speaking) badges.push('<span class="badge speaking">说话</span>');
    return `<tr><td>${p.person_id}</td><td>${p.yaw_deg.toFixed(0)}°</td><td>${badges.join(" ")}</td></tr>`;
  }).join("");
}

let lastState = {};
let cameraPending = false;
let headPending = false;

async function toggleCamera() {
  if (cameraPending) return;
  cameraPending = true;
  const btn = document.getElementById("cameraBtn");
  btn.disabled = true;
  try {
    const action = lastState.camera_active ? "stop" : "start";
    await fetch(`/api/camera/${action}`, { method: "POST" });
  } finally {
    cameraPending = false;
    btn.disabled = false;
  }
}

let sourcePending = false;

async function setCameraSource(source) {
  if (sourcePending || lastState.camera_source === source) return;
  sourcePending = true;
  document.getElementById("sourceLocalBtn").disabled = true;
  document.getElementById("sourceRobotBtn").disabled = true;
  try {
    await fetch(`/api/camera/source/${source}`, { method: "POST" });
  } finally {
    sourcePending = false;
    document.getElementById("sourceLocalBtn").disabled = false;
    document.getElementById("sourceRobotBtn").disabled = false;
  }
}

async function toggleHead() {
  if (headPending) return;
  headPending = true;
  const btn = document.getElementById("headBtn");
  btn.disabled = true;
  try {
    const action = lastState.head_connected ? "disconnect" : "connect";
    await fetch(`/api/head/${action}`, { method: "POST" });
  } finally {
    headPending = false;
    btn.disabled = false;
  }
}

let soundPending = false;

async function toggleSound() {
  if (soundPending) return;
  soundPending = true;
  const btn = document.getElementById("soundBtn");
  btn.disabled = true;
  try {
    const action = lastState.mic_connected ? "stop" : "start";
    await fetch(`/api/sound/${action}`, { method: "POST" });
  } finally {
    soundPending = false;
    btn.disabled = false;
  }
}

let eyeFirstPending = false;

async function toggleEyeFirst() {
  if (eyeFirstPending) return;
  eyeFirstPending = true;
  const btn = document.getElementById("eyeFirstBtn");
  btn.disabled = true;
  try {
    const action = lastState.eye_first ? "stop" : "start";
    await fetch(`/api/eye_first/${action}`, { method: "POST" });
  } finally {
    eyeFirstPending = false;
    btn.disabled = !lastState.head_connected;
  }
}

function updateButtons(state) {
  const cameraBtn = document.getElementById("cameraBtn");
  cameraBtn.textContent = state.camera_active ? "关闭摄像头" : "开启摄像头";
  cameraBtn.classList.toggle("on", !!state.camera_active);
  cameraBtn.classList.toggle("error", !state.camera_active && !!state.camera_error);

  const headBtn = document.getElementById("headBtn");
  headBtn.textContent = state.head_connected ? "断开机器人" : "连接机器人";
  headBtn.classList.toggle("on", !!state.head_connected);
  headBtn.classList.toggle("error", !state.head_connected && !!state.head_error);

  const soundBtn = document.getElementById("soundBtn");
  soundBtn.textContent = state.mic_connected ? "关闭声源定位" : "开启声源定位";
  soundBtn.classList.toggle("on", !!state.mic_connected);
  soundBtn.classList.toggle("error", !state.mic_connected && !!state.mic_error);

  const eyeFirstBtn = document.getElementById("eyeFirstBtn");
  eyeFirstBtn.textContent = state.eye_first ? "关闭眼动优先" : "开启眼动优先";
  eyeFirstBtn.classList.toggle("on", !!state.eye_first);

  const cameraStatus = document.getElementById("cameraStatus");
  cameraStatus.textContent = state.camera_active
    ? "摄像头运行中"
    : (state.camera_error ? ("摄像头未开启: " + state.camera_error) : "摄像头未开启");

  document.getElementById("sourceLocalBtn").classList.toggle("active", state.camera_source === "local");
  document.getElementById("sourceRobotBtn").classList.toggle("active", state.camera_source === "robot");
}

async function poll() {
  try {
    const res = await fetch("/state");
    const state = await res.json();
    lastState = state;

    const canvas = document.getElementById("gauge");
    drawGauge(canvas.getContext("2d"), canvas.width, canvas.height, state);

    const sound = state.sound || {};
    const soundStatus = document.getElementById("soundStatus");
    if (!state.mic_connected) {
      soundStatus.textContent = state.mic_error
        ? ("串口未连接: " + state.mic_error)
        : "未开启声源定位";
    } else if (sound.doa_deg === null || sound.doa_deg === undefined) {
      soundStatus.textContent = "当前没有有效声音";
    } else {
      soundStatus.textContent = `方位 ${sound.doa_deg.toFixed(0)}°   置信度 ${(sound.confidence || 0).toFixed(2)}   fps ${state.fps.toFixed(1)}`;
    }

    const headStatus = document.getElementById("headStatus");
    headStatus.textContent = state.head_connected
      ? `头部舵机: 已连接, head_yao=${state.head_yao.toFixed(2)}`
      : (state.head_error ? ("头部舵机: 未连接(" + state.head_error + ")") : "头部舵机: 未连接");

    // 眼动优先: 直观显示这一帧是"只瞟眼珠"还是"在转头", 以及眼珠偏向哪边.
    // 调 EYE_ONLY_DEG / HEAD_ENGAGE_DEG 两个阈值时对着这行看最直接。
    const gazeModeStatus = document.getElementById("gazeModeStatus");
    if (!state.head_connected) {
      gazeModeStatus.textContent = "眼动优先: 需要先连接机器人";
      gazeModeStatus.classList.remove("on");
    } else if (!state.eye_first) {
      gazeModeStatus.textContent = "眼动优先: 已关闭(按钮开启, 只转头)";
      gazeModeStatus.classList.remove("on");
    } else {
      const eye = state.eye_frac;
      // eye_frac: 0=最右, 0.5=正中, 1=最左(约定见 servo_tuning/config/ULA_new.yaml)
      const dir = Math.abs(eye - 0.5) < 0.02 ? "正中" : (eye < 0.5 ? "偏右" : "偏左");
      gazeModeStatus.textContent = state.gaze_mode === "head"
        ? `眼动优先: 转头中(眼珠回中) eye=${eye.toFixed(2)}`
        : `眼动优先: 只动眼珠(头不动) eye=${eye.toFixed(2)} ${dir}`;
      gazeModeStatus.classList.toggle("on", state.gaze_mode === "eye");
    }

    updateButtons(state);
    renderBreakdown(document.getElementById("breakdown"), state.people || []);
    renderPeopleTable(document.querySelector("#peopleTable tbody"), state.people || []);
  } catch (e) {
    // 页面还没连上后端时会短暂报错, 忽略, 下一轮重试
  }
  setTimeout(poll, 150);
}
poll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:  # noqa: A003 — 静音访问日志, 避免刷屏
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._serve_index()
        elif self.path == "/video_feed":
            self._serve_mjpeg()
        elif self.path == "/state":
            self._serve_state()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        """按钮点击打的接口. 只负责改"期望状态", 真正连接/断开的动作在主循环
        里做(见 main() 里的 `want_camera`/`want_head` 那两段), 所以这里
        不管硬件, 立刻就能返回。"""
        actions = {
            "/api/camera/start": lambda: SHARED.set_camera_desired(True),
            "/api/camera/stop": lambda: SHARED.set_camera_desired(False),
            "/api/camera/source/local": lambda: SHARED.set_camera_source("local"),
            "/api/camera/source/robot": lambda: SHARED.set_camera_source("robot"),
            "/api/head/connect": lambda: SHARED.set_head_desired(True),
            "/api/head/disconnect": lambda: SHARED.set_head_desired(False),
            "/api/sound/start": lambda: SHARED.set_sound_desired(True),
            "/api/sound/stop": lambda: SHARED.set_sound_desired(False),
            "/api/eye_first/start": lambda: SHARED.set_eye_first_desired(True),
            "/api/eye_first/stop": lambda: SHARED.set_eye_first_desired(False),
        }
        action = actions.get(self.path)
        if action is None:
            self.send_error(404)
            return
        action()
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_index(self) -> None:
        body = INDEX_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_state(self) -> None:
        body = json.dumps(SHARED.latest_state()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_mjpeg(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            while True:
                jpeg = SHARED.latest_jpeg()
                if jpeg is not None:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 浏览器关掉了页面/切走了标签页, 正常情况, 不用管


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def draw_overlay(frame, tracks, registry: PersonRegistry, decision, *,
                 sound: SoundContext, sig_params: SignalParams, weights: WeightConfig,
                 now: float) -> None:
    """跟 run_with_face.py 里同一段画框逻辑(绿/红框 + 黄色 GAZE 高亮)。

    人脸框正上方额外画一行"注意力总分"(compute_interest() 的 total, 就是
    GazeScheduler 用来加权抽人的那个分数)——每个人都实时算一份、不只是当前
    被看的那位, 方便直接在画面上对照 weights.py 的六项打分看权重是怎么起
    作用的。"""
    for t in tracks:
        x1, y1, x2, y2 = [int(v) for v in t.bbox]
        speaking = t.last_score > 0
        color = (0, 255, 0) if speaking else (0, 0, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
        p = registry.find_by_track(t.id)
        if p is not None:
            interest = compute_interest(p, now=now, sound=sound, sig_params=sig_params, weights=weights)
            cv2.putText(frame, f"attn={interest.total:.2f}", (x1, max(y1 - 36, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
        cv2.putText(frame, f"#{t.id} {t.last_score:+.2f}", (x1, max(y1 - 12, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        if p is not None and decision.person_id is not None and p.person_id == decision.person_id:
            cv2.rectangle(frame, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (0, 255, 255), 4)
            cv2.putText(frame, "GAZE", (x1, min(y2 + 30, frame.shape[0] - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    if not tracks:
        cv2.putText(frame, "no face detected", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)


def build_state(registry: PersonRegistry, decision, sound: SoundContext,
                fps: float, head_driver=None, *,
                camera_active: bool = False, camera_error: str | None = None,
                camera_source: str = "local", head_error: str | None = None,
                mic_connected: bool = False, mic_error: str | None = None,
                eye_first_desired: bool = True,
                sound_orient_bearing: float | None = None) -> dict:
    people = []
    for p in registry.people:
        is_target = decision.person_id is not None and p.person_id == decision.person_id
        breakdown = None
        if is_target and decision.breakdown is not None:
            b = decision.breakdown
            breakdown = {"size": b.size, "novelty": b.novelty, "sound": b.sound,
                        "facing": b.facing, "speaking": b.speaking, "total": b.total}
        people.append({
            "person_id": p.person_id, "track_id": p.track_id, "yaw_deg": p.yaw_deg,
            "face_area_frac": p.face_area_frac, "facing_score": p.facing_score,
            "is_speaking": p.is_speaking, "is_gaze_target": is_target, "breakdown": breakdown,
        })
    return {
        "people": people,
        "sound": {"doa_deg": sound.doa_deg, "confidence": sound.confidence},
        "gaze_target_person_id": decision.person_id,
        "fps": fps,
        "mic_connected": mic_connected,
        "mic_error": mic_error,
        "camera_active": camera_active,
        "camera_error": camera_error,
        "camera_source": camera_source,
        "head_connected": head_driver is not None,
        "head_error": head_error,
        "head_yao": head_driver.sent_frac if head_driver is not None else None,
        # 眼动优先: 当前是"只动眼珠"(eye)还是"眼珠回中+转头"(head), 以及眼珠
        # 位置(0=最右, 0.5=正中, 1=最左)。eye_first 关掉时 mode 恒为 "eye"、
        # eye_frac 恒为 0.5, 页面上会显示成"已关闭"。
        "gaze_mode": head_driver.mode if head_driver is not None else None,
        "eye_frac": head_driver.eye_frac if head_driver is not None else None,
        # 按钮"开启眼动优先"点了就是期望值, 不需要连着头才有意义——没连接时
        # 这里反映的就是下次连接会用哪个初始值, 连接后主循环会持续把这个值
        # 同步进 head_driver.cfg.eye_first, 点按钮随时切换不用重新连接。
        "eye_first": eye_first_desired,
        # 声源定向(见 gaze_arbiter/sound_orient.py)当前是否接管了头部朝向,
        # 非 None = 正在转向/停留在这个声源方位角, 不受 GazeScheduler 控制
        "sound_orient_bearing": sound_orient_bearing,
    }


def _open_camera(video_path):
    """开摄像头. video_path 可以是设备号(int)或设备路径(str, 比如
    /dev/video0 或 /dev/v4l/by-id/... 持久路径). 成功返回 (cap, None),
    失败返回 (None, 错误信息)."""
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, VIDEO_FPS)
    if not cap.isOpened():
        cap.release()
        return None, "无法打开摄像头"
    return cap, None


def _connect_head(head_host: str, sdk_port: int, face_cfg: FaceSourceConfig):
    """连头部舵机服务. 成功返回 (head_client, head_driver, None),
    失败返回 (None, None, 错误信息)."""
    from head_sdk import HeadSDK  # 延迟 import: 不点"连接机器人"就不需要装 head_sdk
    head_client = HeadSDK(head_host, sdk_port=sdk_port)
    if not head_client.is_connected():
        return None, None, "连不上头部舵机服务(检查 head_grpc_server.py 是不是还在跑)"
    head_client.release_control()  # 不调这个, set_servo_positions 会一直报"控制权被锁"
    head_driver = HeadDriver(head_client, HeadDriverConfig(
        fov_deg=face_cfg.fov_deg,  # 跟人脸方位角用同一套视场角约定, 见文件开头说明
        # 默认开着: 实测头部左右转动方向跟人脸方位反了, 设 HEAD_INVERT=0 可以关掉
        invert=os.environ.get("HEAD_INVERT", "1") not in ("0", "false", "False"),
        smoothing=float(os.environ.get("HEAD_SMOOTHING", "0.25")),
        max_speed=float(os.environ.get("HEAD_MAX_SPEED", "0.4")),
        # 眼动优先: 目标偏得少就只瞟眼珠、头不动; 偏得多才眼珠回中+转头。
        # 初始值取页面"开启眼动优先"按钮当前的期望状态(默认由 EYE_FIRST 环境
        # 变量播种, 见 SharedState.__init__); 连接后按钮随时切换不用重连, 见
        # 主循环里那段 head_driver.cfg.eye_first = SHARED.eye_first_desired()。
        eye_first=SHARED.eye_first_desired(),
        eye_only_deg=float(os.environ.get("EYE_ONLY_DEG", "12.0")),
        head_engage_deg=float(os.environ.get("HEAD_ENGAGE_DEG", "20.0")),
    ))
    head_driver.center()
    return head_client, head_driver, None


def main() -> int:
    # 默认用 /dev/v4l/by-id/ 下的持久路径而不是 /dev/videoN 这种编号——
    # 编号是内核按 USB 枚举顺序分配的, 重启后顺序可能变(踩过一次: 重启后
    # video0/video2 对应的物理摄像头互换了, local/robot 直接串了), by-id
    # 路径是根据 USB 设备的厂商/序列号生成的, 不受枚举顺序影响, 稳定指向
    # 同一个物理摄像头。可以用 `ls /dev/v4l/by-id/` 查当前机器上的实际名字。
    video_dev_local = os.environ.get(
        "VIDEO_DEV", "/dev/v4l/by-id/usb-Azurewave_HD_Camera_0x0001-video-index0")
    video_dev_robot = os.environ.get(
        "ROBOT_VIDEO_DEV", "/dev/v4l/by-id/usb-DHZJ-220328-ZW_SPCA2650_AV_Camera_01.00.00-video-index0")
    # 机器人头里那颗 SPCA2650 物理装反了 180°, 这里翻转过来。
    robot_camera_flip = os.environ.get("ROBOT_CAMERA_FLIP", "1") not in ("0", "false", "False")
    # 本地摄像头目前不需要翻转(HD Camera 装的方向是正的), 留这个开关只是
    # 防万一以后换了个装反的摄像头/被重新摆放, 不用改代码, 设环境变量就行。
    local_camera_flip = os.environ.get("LOCAL_CAMERA_FLIP", "0") not in ("0", "false", "False")
    audio_dev = os.environ.get("AUDIO_DEV", "hw:0,0")
    checkpoint = os.environ.get("CHECKPOINT", os.path.join(LASD_DIR, "weight", "finetuning_TalkSet.model"))
    mic_port = os.environ.get("MIC_PORT", "/dev/ttyUSB0")
    mic_baud = int(os.environ.get("MIC_BAUD", "115200"))
    head_host = os.environ.get("HEAD_HOST", "127.0.0.1")
    head_sdk_port = int(os.environ.get("HEAD_SDK_PORT", "2543"))

    print("加载 MediaPipe Face Landmarker + Light-ASD 模型...")
    base_options = mp_python.BaseOptions(model_asset_path=FACE_LANDMARKER_MODEL)
    landmarker_options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options, num_faces=MAX_FACES,
        output_face_blendshapes=True, output_facial_transformation_matrixes=True,
        running_mode=mp_vision.RunningMode.VIDEO,
    )
    landmarker = mp_vision.FaceLandmarker.create_from_options(landmarker_options)

    asd = ASD()
    asd.loadParameters(checkpoint)
    asd.eval()
    print(f"权重: {checkpoint}  设备: {DEVICE}")

    print(f"启动麦克风采集 ({audio_dev}) ...")
    audio = AudioStreamer(audio_dev)

    doa_reader = None
    mic_error: str | None = None
    sound_cfg = SoundSourceConfig(
        offset_deg=float(os.environ.get("MIC_OFFSET_DEG", "0.0")),
        # 默认开着: 实测声源方向跟人脸方位左右反了, 设 MIC_INVERT=0 可以关掉
        invert=os.environ.get("MIC_INVERT", "1") not in ("0", "false", "False"),
    )

    face_cfg = FaceSourceConfig()  # fov_deg/yaw_th/pitch_th 用默认值, 跟摄像头实际视场角不符时再调

    registry = PersonRegistry(stale_timeout_s=3.0)
    scheduler = GazeScheduler(min_gaze_s=1.5, max_gaze_s=6.0, jitter_frac=0.25, rng=random.Random(7))
    sig_params = SignalParams()
    weights = WeightConfig()

    # 声源定向: 跟 GazeScheduler(基于人脸打分选目标)完全独立的另一套系统,
    # 只管"场上没人的时候头该不该主动转去找声源", 见 gaze_arbiter/sound_orient.py。
    sound_orient = SoundOrientState(SoundOrientConfig(
        confidence_threshold=float(os.environ.get("SOUND_ORIENT_CONFIDENCE", "0.5")),
        redirect_threshold_deg=float(os.environ.get("SOUND_ORIENT_REDIRECT_DEG", "15.0")),
        search_timeout_s=float(os.environ.get("SOUND_ORIENT_SEARCH_TIMEOUT_S", "2.5")),
    ))

    # 摄像头/头部舵机现在都是"页面打开时默认关, 点按钮才连"——这两个变量
    # 就是主循环里"当前实际状态", 跟 SHARED 里"期望状态"(按钮设的)对比,
    # 状态不一致就在下面的循环里去连/断。
    cap = None
    current_source = "local"  # 当前 cap 实际用的是哪个源, cap 是 None 时这个值没意义
    head_client = None
    head_driver = None
    head_recentering = False  # 断开流程: 先慢速回中, 完成后才真正断开
    camera_error: str | None = None
    head_error: str | None = None

    SHARED.update(CAMERA_OFF_JPEG, build_state(
        registry, scheduler.tick(registry, now=time.monotonic()), SoundContext(),
        0.0, None, camera_active=False, camera_error=None, head_error=None,
    ))

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"\n>> 网页已启动: http://localhost:{PORT}/  (局域网内其他设备用这台机器的 IP 也能访问)")
    print(">> 页面上点\"开启摄像头\"/\"连接机器人\"/\"开启声源定位\"才会真的动. Ctrl+C 停止\n")

    tracks: list = []
    stream_start = time.time()  # 只在这里设一次, 全程不重置, 见下面摄像头(重新)打开那段的注释
    fps_t0 = time.time()
    fps_count = 0
    disp_fps = 0.0
    last_head_t = time.monotonic()

    try:
        while True:
            # ── 同步摄像头的"期望状态" vs "实际状态" ──────────────────
            # 三种情况都在这一块处理: 开(cap None -> 有)、关(cap 有 -> None)、
            # 切换源(cap 一直有, 但 local/robot 换了 -> 关旧的开新的)。
            want_camera = SHARED.camera_desired()
            want_source = SHARED.camera_source()
            if not want_camera:
                if cap is not None:
                    print(">> 关闭摄像头")
                    cap.release()
                    cap = None
                    tracks = []
                    camera_error = None
            elif cap is None or want_source != current_source:
                if cap is not None:
                    print(f">> 切换摄像头源: {current_source} -> {want_source}")
                    cap.release()
                    cap = None
                else:
                    label = "本地" if want_source == "local" else "机器人"
                    print(f">> 打开摄像头({label})...")
                video_path = video_dev_local if want_source == "local" else video_dev_robot
                cap, err = _open_camera(video_path)
                camera_error = err
                current_source = want_source  # 无论成功失败都记下来, 避免同一个坏源反复重试刷日志
                if err:
                    print(f"✗ {err}")
                    SHARED.set_camera_desired(False)  # 打开失败就把期望状态弹回去
                else:
                    print("✓ 摄像头已打开")
                    tracks = []
                    # 注意: 不能在这里重置 stream_start! `landmarker` 这个 MediaPipe
                    # 检测器对象是脚本启动时创建的一个, 关/开/切换摄像头源都不会重新
                    # 创建它, 它内部要求喂给它的时间戳(timestamp_ms)必须一直递增——
                    # 如果这里重置 stream_start, 下面 timestamp_ms 会往回跳, 直接抛
                    # "Input timestamp must be monotonically increasing" 崩掉整个进程
                    # (真实踩过这个坑: 开→关→开摄像头, 第二次打开后第一帧检测就崩了)。
                    # stream_start 必须从脚本启动到退出全程只设一次(见下面 main() 顶部)。

            # ── 同步头部舵机的"期望状态" vs "实际状态" ─────────────────
            want_head = SHARED.head_desired()
            if want_head and head_driver is None:
                print(f">> 连接头部舵机服务 {head_host}:{head_sdk_port} ...")
                try:
                    head_client, head_driver, err = _connect_head(head_host, head_sdk_port, face_cfg)
                except Exception as e:  # noqa: BLE001 — head_sdk 内部异常五花八门, 都当连接失败处理
                    head_client, head_driver, err = None, None, str(e)
                head_error = err
                if err:
                    print(f"✗ {err}")
                    SHARED.set_head_desired(False)
                else:
                    print("✓ 头部舵机已连接, 头回中")
            elif not want_head and head_driver is not None:
                if head_recentering:
                    pass  # 已经在慢速回中, 下面的推进块会继续处理
                else:
                    print(">> 断开头部舵机(先慢速回中, 完成后自动断开)...")
                    try:
                        head_driver.recenter()
                        head_recentering = True
                    except Exception:  # noqa: BLE001 — 回中启动失败就直接断开, 不卡住
                        try:
                            head_driver.center()
                        except Exception:
                            pass
                        head_client.disconnect()
                        head_client = None
                        head_driver = None
                        head_error = None

            # ── 眼动优先: 按钮点了随时生效, 不用重新连接头部舵机 ─────────
            if head_driver is not None and not head_recentering:
                want_eye_first = SHARED.eye_first_desired()
                if head_driver.cfg.eye_first and not want_eye_first:
                    head_driver.recenter_eyes()  # 关闭前把眼珠收回正中, 不然会停在偏着的位置
                head_driver.cfg.eye_first = want_eye_first

            # ── 头部慢速回中的推进(不管摄像头开没开都要跑, 否则摄像头关着
            # 时回中会卡住——用这里单独取的 loop_now, 不依赖下面摄像头帧
            # 处理那段才会更新的 now, 之前踩过"摄像头关着 now 不刷新导致
            # 回中卡死"这个坑) ──────────────────────────────────────────
            if head_driver is not None and head_recentering:
                loop_now = time.monotonic()
                dt = max(1e-3, loop_now - last_head_t)
                last_head_t = loop_now
                try:
                    done = head_driver.recenter_step(dt)
                except Exception:  # noqa: BLE001 — 回中途中舵机服务断了就直接结束
                    done = True
                if done:
                    print("✓ 慢速回中完成, 断开头部舵机")
                    try:
                        head_driver.center()  # 确保最后发一次精确回中
                    except Exception:
                        pass
                    head_client.disconnect()
                    head_client = None
                    head_driver = None
                    head_error = None
                    head_recentering = False

            # ── 同步声源方向串口的"期望状态" vs "实际状态" ──────────────
            want_sound = SHARED.sound_desired()
            if want_sound and doa_reader is None:
                print(f">> 打开麦克风阵列串口(声源方向) {mic_port} ...")
                doa_reader = J7034DoaReader(mic_port, mic_baud)
                doa_reader.start()
                time.sleep(0.3)
                err = doa_reader.last_error()
                if err is not None:
                    print(f"✗ 打不开声源方向串口: {err}")
                    doa_reader.stop()
                    doa_reader = None
                    mic_error = err
                    SHARED.set_sound_desired(False)  # 打开失败就把期望状态弹回去, 不然会一直重试刷日志
                else:
                    print("✓ 声源方向串口已连接")
                    mic_error = None
            elif not want_sound and doa_reader is not None:
                print(">> 关闭声源方向串口")
                doa_reader.stop()
                doa_reader = None
                mic_error = None

            if cap is None:
                # 摄像头没开: 用占位画面, 状态照常更新(mic/head 的状态该显示还是要显示),
                # 稍微睡一下避免在没事可干的时候空转刷 CPU。
                decision = scheduler.tick(registry, now=time.monotonic())
                state = build_state(registry, decision, SoundContext(), 0.0, head_driver,
                                    camera_active=False, camera_error=camera_error,
                                    camera_source=want_source, head_error=head_error,
                                    mic_connected=doa_reader is not None and doa_reader.last_error() is None,
                                    mic_error=doa_reader.last_error() if doa_reader is not None else mic_error,
                                    eye_first_desired=SHARED.eye_first_desired())
                SHARED.update(CAMERA_OFF_JPEG, state)
                time.sleep(0.1)
                continue

            ret, frame = cap.read()
            if not ret:
                print("摄像头读取失败, 自动关闭")
                cap.release()
                cap = None
                camera_error = "摄像头读取失败"
                SHARED.set_camera_desired(False)
                continue
            if (current_source == "robot" and robot_camera_flip) or (
                    current_source == "local" and local_camera_flip):
                # 装反的那颗摄像头跟正常方向差了 180°(上下+左右都反了, 等价
                # 于整张画面转半圈), 在最早期就翻转过来——后面的人脸检测/画框/
                # 编码全部基于翻转之后的画面, 坐标系统一, 不会出现"框对不上脸"
                # 的问题。哪颗摄像头需要翻转看的是物理朝向, 不是 local/robot
                # 这个标签本身, 摄像头被重新安装/更换后到 main() 顶部改
                # ROBOT_CAMERA_FLIP / LOCAL_CAMERA_FLIP 这两个环境变量。
                frame = cv2.flip(frame, -1)  # -1 = 上下和左右同时翻转(等价旋转180°)
            frame_h, frame_w = frame.shape[:2]

            timestamp_ms = int((time.time() - stream_start) * 1000)
            detections = detect_faces_mediapipe(landmarker, frame, timestamp_ms)

            pairs, used_t, unmatched_dets = match_tracks(tracks, detections)
            for ti, di in pairs:
                tracks[ti].update(detections[di])
                tracks[ti].missed = 0
                gray = crop_face(frame, detections[di]["bbox"])
                if gray is not None:
                    tracks[ti].video_buf.append(gray)
            for ti, t in enumerate(tracks):
                if ti not in used_t:
                    t.mark_missed()
            tracks = [t for t in tracks if t.missed <= MAX_MISSED]
            for di in unmatched_dets:
                nt = FaceTrack(detections[di])
                gray = crop_face(frame, detections[di]["bbox"])
                if gray is not None:
                    nt.video_buf.append(gray)
                tracks.append(nt)

            score_tracks(tracks, audio, asd)

            now = time.monotonic()
            # 摄像头装在眼球机构里, 眼动优先接管转开眼珠时摄像头也跟着转——
            # 修正必须用头+眼的合成角度, 不能只用头的角度(见 camera_yaw_deg)。
            camera_yaw_now = head_driver.camera_yaw_deg if head_driver is not None else 0.0
            for t in tracks:
                if t.hits < MIN_CONFIRM_HITS:
                    # 刚出现、还没连续命中够帧数——先不注册进 PersonRegistry(见
                    # MIN_CONFIRM_HITS 的注释)。
                    continue
                observe_face_track(registry, t, frame_w=frame_w, frame_h=frame_h, cfg=face_cfg,
                                   camera_yaw_deg=camera_yaw_now, now=now)
            sound = doa_reader.latest_sound_context(sound_cfg) if doa_reader is not None else SoundContext()
            decision = scheduler.tick(registry, sound=sound, sig_params=sig_params, weights=weights, now=now)

            # 声源定向只在场上没人(GazeScheduler 当前没锁定任何人)时才响应
            # 声音, 见 SoundOrientState.tick 的说明。
            orient_bearing = sound_orient.tick(
                doa_deg=sound.doa_deg, confidence=sound.confidence,
                idle=decision.person_id is None, now=now,
            )

            if head_driver is not None and not head_recentering:
                dt = max(1e-3, now - last_head_t)  # 摄像头/模型速度不固定, 用实际经过的时间算限速, 不能假设固定帧率
                last_head_t = now
                if orient_bearing is not None:
                    # 声源定向接管: 场上没人, 头转去正对声源找人
                    head_driver.update(orient_bearing, dt)
                else:
                    target = registry.find_by_id(decision.person_id) if decision.person_id else None
                    head_driver.update(target.yaw_deg if target is not None else None, dt)

            draw_overlay(frame, tracks, registry, decision,
                        sound=sound, sig_params=sig_params, weights=weights, now=now)

            fps_count += 1
            if time.time() - fps_t0 >= 1.0:
                disp_fps = fps_count / (time.time() - fps_t0)
                fps_count = 0
                fps_t0 = time.time()

            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                state = build_state(registry, decision, sound, disp_fps, head_driver,
                                    camera_active=True, camera_error=None,
                                    camera_source=current_source, head_error=head_error,
                                    mic_connected=doa_reader is not None and doa_reader.last_error() is None,
                                    mic_error=doa_reader.last_error() if doa_reader is not None else mic_error,
                                    eye_first_desired=SHARED.eye_first_desired(),
                                    sound_orient_bearing=orient_bearing)
                SHARED.update(buf.tobytes(), state)
    except KeyboardInterrupt:
        pass
    finally:
        if cap is not None:
            cap.release()
        audio.stop()
        if doa_reader is not None:
            doa_reader.stop()
        if head_driver is not None:
            try:
                # 退出也慢速回中(有界阻塞最多 RECENTER_EXIT_MAX_S 秒), 避免
                # 进程一退舵机停在奇怪角度, 下次上电猛地一跳伤到机器人
                head_driver.recenter()
                deadline = time.monotonic() + RECENTER_EXIT_MAX_S
                while not head_driver.recenter_step(0.05):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                head_driver.center()
            except Exception:  # noqa: BLE001 — 退出路径, 舵机服务可能已经断线, 不因为回中失败而卡住退出
                pass
        if head_client is not None:
            head_client.disconnect()
        landmarker.close()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
