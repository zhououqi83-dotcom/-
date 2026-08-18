#!/usr/bin/env python3
"""run_with_face.py — 接入 light_asd_test 的实时人脸检测+头部朝向+说话人判别,
喂给 GazeScheduler, 肉眼验证"选中的人对不对"。

**必须用 light_asd_test 自己的 venv 跑**(那边装了 opencv/torch/mediapipe 这些
重依赖, gaze_arbiter 自己的 venv 里没有, 也不该装 —— 见 README "架构"一节的
原则: input/ 适配层的重依赖各自独立, 核心决策层永远保持轻量):

    light_asd_test/venv/bin/python gaze_arbiter/examples/run_with_face.py

直接复用 light_asd_test/Light-ASD/live_demo.py 里已经跑通的检测/跟踪/模型
(`detect_faces_mediapipe` / `FaceTrack` / `match_tracks` / `crop_face` /
`AudioStreamer` / `ASD`), **不改动那个文件、不重新实现这些逻辑**——新写的
只有 gaze_arbiter/input/face_source.py 里那几个"FaceTrack 状态 → Person 字段"
的纯函数, 和下面这个脚本里的胶水代码。

例外: 每帧给每个 track 打 ASD 分数那一小段(音频特征提取+过模型)是
`live_demo.py::main()` 内联写的, 不是独立函数, 没法直接 import——
`score_tracks()` 是从那边**原样复制**过来的, 如果 `live_demo.py` 那段逻辑
以后改了, 这里要记得手动同步(标注在函数文档字符串里, 方便以后 grep 到)。

画面效果: 跟原版 live_demo.py 一样每张脸有绿/红框+分数+朝向, 额外把
GazeScheduler 当前选中的那张脸加一层黄色粗框 + "GAZE" 标签。

**可选: 同时接入真实声源方向(J7034G4)**, 设置 `MIC_PORT` 环境变量就会启用,
不设就还是只用人脸这几路信号(向后兼容, 默认行为不变):

    MIC_PORT=/dev/ttyUSB0 light_asd_test/venv/bin/python gaze_arbiter/examples/run_with_face.py
    # 方向反了 / 0°跟正前方对不上, 校准同 sound_track_head.py / run_with_real_sound.py 的思路:
    MIC_PORT=/dev/ttyUSB0 MIC_INVERT=1 MIC_OFFSET_DEG=10 light_asd_test/venv/bin/python gaze_arbiter/examples/run_with_face.py

用法:
    light_asd_test/venv/bin/python gaze_arbiter/examples/run_with_face.py
    VIDEO_DEV=/dev/video1 light_asd_test/venv/bin/python gaze_arbiter/examples/run_with_face.py

按 Q 退出。
"""
from __future__ import annotations

import os
import random
import sys
import time

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

from gaze_arbiter import GazeScheduler, PersonRegistry, SignalParams, SoundContext, WeightConfig  # noqa: E402
from gaze_arbiter.input.face_source import FaceSourceConfig, observe_face_track  # noqa: E402
from gaze_arbiter.input.sound_source import J7034DoaReader, SoundSourceConfig  # noqa: E402


def score_tracks(tracks, audio: AudioStreamer, asd: ASD) -> None:
    """给每个够格的 track 打一次 ASD 分数.

    **原样复制自 `light_asd_test/Light-ASD/live_demo.py::main()` 里那段内联
    逻辑**(音频特征提取 + 过 Light-ASD 模型), 因为那段代码写在 main() 函数
    体内、不是独立函数, 没法直接 import 复用。如果 live_demo.py 那段逻辑
    以后有调整(比如换了打分窗口长度), 记得同步到这里。
    """
    now = time.time()
    araw = None  # 音频只需按需取一次, 多张脸共用同一份麦克风音频
    for t in tracks:
        if (now - t.last_update) >= UPDATE_EVERY_SEC and len(t.video_buf) >= int(VIDEO_FPS * 0.4):
            t.last_update = now
            vfeat = np.array(t.video_buf)[-int(VIDEO_FPS * WINDOW_SEC):]
            if araw is None:
                araw = audio.get_last_seconds(WINDOW_SEC)
            if len(araw) > AUDIO_SR * 0.3:
                mfcc = python_speech_features.mfcc(
                    araw, AUDIO_SR, numcep=13, winlen=0.025, winstep=0.010)
                length = min((mfcc.shape[0] - mfcc.shape[0] % 4) / 100, vfeat.shape[0] / VIDEO_FPS)
                if length > 0.2:
                    a = mfcc[:int(round(length * 100)), :]
                    v = vfeat[-int(round(length * VIDEO_FPS)):]
                    with torch.no_grad():
                        inputA = torch.FloatTensor(a).unsqueeze(0).to(DEVICE)
                        inputV = torch.FloatTensor(v).unsqueeze(0).to(DEVICE)
                        embedA = asd.model.forward_audio_frontend(inputA)
                        embedV = asd.model.forward_visual_frontend(inputV)
                        out = asd.model.forward_audio_visual_backend(embedA, embedV)
                        score = asd.lossAV.forward(out, labels=None)
                    t.last_score = float(score[-1])


def main() -> int:
    # 跟 live_demo.sh 保持同样的环境变量约定(VIDEO_DEV=/dev/videoN), 这里直接解析出设备号数字
    video_dev = os.environ.get("VIDEO_DEV", "/dev/video0")
    video_index = int("".join(filter(str.isdigit, video_dev)) or "0")
    audio_dev = os.environ.get("AUDIO_DEV", "hw:0,0")
    checkpoint = os.environ.get(
        "CHECKPOINT",
        os.path.join(LASD_DIR, "weight", "finetuning_TalkSet.model"),
    )

    print("加载 MediaPipe Face Landmarker + Light-ASD 模型...")
    base_options = mp_python.BaseOptions(model_asset_path=FACE_LANDMARKER_MODEL)
    landmarker_options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        num_faces=MAX_FACES,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
        running_mode=mp_vision.RunningMode.VIDEO,
    )
    landmarker = mp_vision.FaceLandmarker.create_from_options(landmarker_options)

    asd = ASD()
    asd.loadParameters(checkpoint)
    asd.eval()
    print(f"权重: {checkpoint}  设备: {DEVICE}")

    print(f"启动麦克风采集 ({audio_dev}) ...")
    audio = AudioStreamer(audio_dev)

    mic_port = os.environ.get("MIC_PORT")
    doa_reader = None
    sound_cfg = SoundSourceConfig(
        offset_deg=float(os.environ.get("MIC_OFFSET_DEG", "0.0")),
        invert=os.environ.get("MIC_INVERT", "") not in ("", "0", "false", "False"),
    )
    if mic_port:
        print(f">> 打开麦克风阵列串口(声源方向) {mic_port} ...")
        doa_reader = J7034DoaReader(mic_port, int(os.environ.get("MIC_BAUD", "115200")))
        doa_reader.start()
        time.sleep(0.3)
        if doa_reader.last_error() is not None:
            print(f"✗ 打不开声源方向串口: {doa_reader.last_error()}(继续跑, 但不会有声源方向这个信号)")
            doa_reader.stop()
            doa_reader = None
        else:
            print("✓ 声源方向串口已连接")

    cap = cv2.VideoCapture(video_index)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, VIDEO_FPS)
    if not cap.isOpened():
        print("无法打开摄像头")
        return 1

    registry = PersonRegistry(stale_timeout_s=3.0)
    scheduler = GazeScheduler(min_gaze_s=1.5, max_gaze_s=6.0, jitter_frac=0.25,
                              rng=random.Random(7))
    sig_params = SignalParams()
    weights = WeightConfig()
    face_cfg = FaceSourceConfig()  # fov_deg/yaw_th/pitch_th 用默认值, 跟摄像头实际视场角不符时再调

    tracks = []
    stream_start = time.time()
    fps_t0 = time.time()
    fps_count = 0
    disp_fps = 0.0

    print("实时检测已启动, 窗口聚焦后按 Q 退出")
    window = "gaze_arbiter + Light-ASD"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("摄像头读取失败")
                break
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
                    t.missed += 1
            tracks = [t for t in tracks if t.missed <= MAX_MISSED]
            for di in unmatched_dets:
                nt = FaceTrack(detections[di])
                gray = crop_face(frame, detections[di]["bbox"])
                if gray is not None:
                    nt.video_buf.append(gray)
                tracks.append(nt)

            score_tracks(tracks, audio, asd)

            now = time.monotonic()
            for t in tracks:
                observe_face_track(registry, t, frame_w=frame_w, frame_h=frame_h,
                                   cfg=face_cfg, now=now)
            sound = doa_reader.latest_sound_context(sound_cfg) if doa_reader is not None else SoundContext()
            decision = scheduler.tick(registry, sound=sound, sig_params=sig_params,
                                      weights=weights, now=now)

            disp = frame
            for t in tracks:
                x1, y1, x2, y2 = [int(v) for v in t.bbox]
                speaking = t.last_score > 0
                color = (0, 255, 0) if speaking else (0, 0, 255)
                cv2.rectangle(disp, (x1, y1), (x2, y2), color, 3)
                cv2.putText(disp, f"#{t.id} {t.last_score:+.2f}", (x1, max(y1 - 12, 20)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

                p = registry.find_by_track(t.id)
                if p is not None and p.person_id == decision.person_id:
                    cv2.rectangle(disp, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (0, 255, 255), 4)
                    cv2.putText(disp, "GAZE", (x1, min(y2 + 76, disp.shape[0] - 4)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

            if not tracks:
                cv2.putText(disp, "no face detected", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

            if doa_reader is not None:
                doa_str = f"{sound.doa_deg:+.0f}deg" if sound.doa_deg is not None else "--"
                sound_w = f"{decision.breakdown.sound:.2f}" if decision.breakdown is not None else "--"
                cv2.putText(disp, f"sound_yaw={doa_str}  sound_weight={sound_w}",
                            (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

            fps_count += 1
            if time.time() - fps_t0 >= 1.0:
                disp_fps = fps_count / (time.time() - fps_t0)
                fps_count = 0
                fps_t0 = time.time()
            cv2.putText(disp, f"{disp_fps:.1f} fps   Q=quit", (20, disp.shape[0] - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            cv2.imshow(window, disp)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        audio.stop()
        if doa_reader is not None:
            doa_reader.stop()
        landmarker.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
