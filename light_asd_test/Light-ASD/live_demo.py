#!/usr/bin/env python3
"""实时摄像头+麦克风 Light-ASD 演示。
人脸检测/头部朝向/嘴部张合改用 MediaPipe Face Landmarker(Apache 2.0，明确可商用)，
不再依赖训练自 WIDER FACE(非商用授权) 的 S3FD。
支持同时多人入镜，每张脸独立打分：画面中每个人脸框会随各自判定结果
实时变绿(说话)/变红(未说话)，并显示置信度分数、头部朝向角度、嘴部张合度。
按 Q 退出。
"""
import os
import sys
import time
import math
import itertools
import argparse
import threading
import subprocess
from collections import deque

import cv2
import numpy as np
import torch
import python_speech_features
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from ASD import ASD

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = os.path.join(HERE, '..', 'bin', 'ffmpeg')
FACE_LANDMARKER_MODEL = os.path.join(HERE, '..', 'mediapipe_models', 'face_landmarker.task')

DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
VIDEO_FPS = 25
AUDIO_SR = 16000
WINDOW_SEC = 0.6          # 用于打分的滑动窗口长度(越短对"停止说话"越灵敏，但略低于官方最短测试窗口1秒)
UPDATE_EVERY_SEC = 0.1    # 打分刷新间隔
CROP_SCALE = 0.40         # 与训练时 Columbia_test.py 的 cropScale 保持一致
IOU_MATCH_TH = 0.3        # 多人跟踪时，帧间人脸框匹配的最小IOU
MAX_MISSED = 15           # 人脸连续多少帧没匹配上就丢弃这个跟踪(约0.5-0.6秒)
MAX_FACES = 5             # 最多同时跟踪几张脸
FACING_YAW_TH = 30        # |yaw|超过这个角度就判定"没对准设备"(度)
FACING_PITCH_TH = 20      # |pitch|超过这个角度就判定"没对准设备"(度)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--videoIndex', type=int, default=0, help='/dev/video<N>')
    p.add_argument('--audioDevice', type=str, default='hw:0,0', help='ALSA设备，如 hw:0,0 / hw:0,6')
    p.add_argument('--pretrainModel', type=str, default='weight/finetuning_TalkSet.model')
    return p.parse_args()


def rotation_matrix_to_euler_deg(R):
    """3x3旋转矩阵 -> (pitch, yaw, roll) 角度制。
    注意：MediaPipe canonical face model的坐标轴约定和直觉方向可能有正负号差异，
    建议实际测试时转头/点头观察数值变化方向，需要的话在这里加负号校准。"""
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    singular = sy < 1e-6
    if not singular:
        pitch = math.atan2(R[2, 1], R[2, 2])
        yaw = math.atan2(-R[2, 0], sy)
        roll = math.atan2(R[1, 0], R[0, 0])
    else:
        pitch = math.atan2(-R[1, 2], R[1, 1])
        yaw = math.atan2(-R[2, 0], sy)
        roll = 0.0
    return math.degrees(pitch), math.degrees(yaw), math.degrees(roll)


def get_blendshape(blendshapes, name):
    for b in blendshapes:
        if b.category_name == name:
            return b.score
    return 0.0


def detect_faces_mediapipe(landmarker, frame_bgr, timestamp_ms):
    """返回每张脸: {'bbox':(x1,y1,x2,y2), 'yaw':, 'pitch':, 'roll':, 'mouth_open':}"""
    h, w = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect_for_video(mp_image, timestamp_ms)

    faces = []
    for i, lm in enumerate(result.face_landmarks):
        xs = [p.x * w for p in lm]
        ys = [p.y * h for p in lm]
        bbox = (min(xs), min(ys), max(xs), max(ys))

        pitch = yaw = roll = 0.0
        if result.facial_transformation_matrixes:
            mat = np.array(result.facial_transformation_matrixes[i])
            pitch, yaw, roll = rotation_matrix_to_euler_deg(mat[:3, :3])

        mouth_open = 0.0
        if result.face_blendshapes:
            mouth_open = get_blendshape(result.face_blendshapes[i], 'jawOpen')

        faces.append({'bbox': bbox, 'yaw': yaw, 'pitch': pitch, 'roll': roll, 'mouth_open': mouth_open})
    return faces


class AudioStreamer:
    """后台线程持续从麦克风读取16kHz单声道PCM，维护一个环形缓冲区。"""

    def __init__(self, device, seconds=5):
        self.buf = deque(maxlen=AUDIO_SR * seconds)
        self.lock = threading.Lock()
        self.proc = subprocess.Popen(
            [FFMPEG, '-f', 'alsa', '-i', device, '-ac', '1', '-ar', str(AUDIO_SR),
             '-f', 's16le', '-loglevel', 'quiet', '-'],
            stdout=subprocess.PIPE)
        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        chunk_bytes = 1024 * 2  # 1024 samples * 2 bytes (int16)
        while self.running:
            data = self.proc.stdout.read(chunk_bytes)
            if not data:
                break
            samples = np.frombuffer(data, dtype=np.int16)
            with self.lock:
                self.buf.extend(samples.tolist())

    def get_last_seconds(self, seconds):
        n = int(AUDIO_SR * seconds)
        with self.lock:
            data = list(self.buf)[-n:]
        return np.array(data, dtype=np.int16)

    def stop(self):
        self.running = False
        self.proc.terminate()
        self.thread.join(timeout=1)


def crop_face(frame_bgr, bbox):
    """按训练时同样的裁剪方式：先按cropScale扩边裁出224x224人脸，再取中心112x112灰度图。"""
    x1, y1, x2, y2 = bbox
    s = max(x2 - x1, y2 - y1) / 2
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    bsi = int(s * (1 + 2 * CROP_SCALE))
    padded = cv2.copyMakeBorder(frame_bgr, bsi, bsi, bsi, bsi, cv2.BORDER_CONSTANT, value=(110, 110, 110))
    mx, my = mx + bsi, my + bsi
    y0, y1c = int(my - s), int(my + s * (1 + 2 * CROP_SCALE))
    x0, x1c = int(mx - s * (1 + CROP_SCALE)), int(mx + s * (1 + CROP_SCALE))
    face = padded[max(y0, 0):y1c, max(x0, 0):x1c]
    if face.size == 0:
        return None
    face = cv2.resize(face, (224, 224))
    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    gray = gray[56:168, 56:168]  # 224中心的112x112
    return gray


def iou(boxA, boxB):
    xA, yA = max(boxA[0], boxB[0]), max(boxA[1], boxB[1])
    xB, yB = min(boxA[2], boxB[2]), min(boxA[3], boxB[3])
    inter = max(0, xB - xA) * max(0, yB - yA)
    if inter == 0:
        return 0.0
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    return inter / float(areaA + areaB - inter)


class FaceTrack:
    """每个入镜的人脸独立维护一段视频特征缓冲区和打分状态。"""
    _id_gen = itertools.count(1)

    def __init__(self, face):
        self.id = next(FaceTrack._id_gen)
        self.bbox = face['bbox']
        self.yaw = face['yaw']
        self.pitch = face['pitch']
        self.mouth_open = face['mouth_open']
        self.video_buf = deque(maxlen=int(VIDEO_FPS * 3))
        self.last_score = 0.0
        self.last_update = 0.0
        self.missed = 0
        self.hits = 1   # 连续命中帧数(缺一帧就清零, 见 mark_missed)，
                         # 下游(gaze_arbiter)靠这个过滤"只出现一帧就消失"的误检

    def update(self, face):
        self.bbox = face['bbox']
        self.yaw = face['yaw']
        self.pitch = face['pitch']
        self.mouth_open = face['mouth_open']
        self.hits += 1

    def mark_missed(self):
        """这一帧没匹配上检测框: 记一次 miss, 并把"连续命中"清零——
        误检往往一闪而过, 断档过一次就不该再算进"确认过的脸"里。"""
        self.missed += 1
        self.hits = 0


def match_tracks(tracks, detections):
    """贪心IOU匹配：返回 (matched_pairs, unmatched_det_idxs)。detections是face dict列表。"""
    candidates = []
    for ti, t in enumerate(tracks):
        for di, d in enumerate(detections):
            v = iou(t.bbox, d['bbox'])
            if v >= IOU_MATCH_TH:
                candidates.append((v, ti, di))
    candidates.sort(reverse=True)
    used_t, used_d = set(), set()
    pairs = []
    for v, ti, di in candidates:
        if ti in used_t or di in used_d:
            continue
        used_t.add(ti)
        used_d.add(di)
        pairs.append((ti, di))
    unmatched_dets = [di for di in range(len(detections)) if di not in used_d]
    return pairs, used_t, unmatched_dets


def main():
    args = parse_args()
    os.chdir(HERE)

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
    asd.loadParameters(args.pretrainModel)
    asd.eval()
    print(f"权重: {args.pretrainModel}  设备: {DEVICE}")

    print("启动麦克风采集...")
    audio = AudioStreamer(args.audioDevice)

    cap = cv2.VideoCapture(args.videoIndex)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, VIDEO_FPS)

    if not cap.isOpened():
        print("无法打开摄像头")
        sys.exit(1)

    tracks = []  # 当前跟踪中的所有人脸(FaceTrack列表)
    fps_t0 = time.time()
    fps_count = 0
    disp_fps = 0.0
    stream_start = time.time()

    print("实时检测已启动(支持多人)，窗口聚焦后按 Q 退出")
    window = "Light-ASD 实时检测"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("摄像头读取失败")
                break

            timestamp_ms = int((time.time() - stream_start) * 1000)
            detections = detect_faces_mediapipe(landmarker, frame, timestamp_ms)

            # 帧间人脸匹配：延续已有跟踪，新脸建新跟踪，消失的脸计数丢弃
            pairs, used_t, unmatched_dets = match_tracks(tracks, detections)
            for ti, di in pairs:
                tracks[ti].update(detections[di])
                tracks[ti].missed = 0
                gray = crop_face(frame, detections[di]['bbox'])
                if gray is not None:
                    tracks[ti].video_buf.append(gray)
            for ti, t in enumerate(tracks):
                if ti not in used_t:
                    t.mark_missed()
            tracks = [t for t in tracks if t.missed <= MAX_MISSED]
            for di in unmatched_dets:
                nt = FaceTrack(detections[di])
                gray = crop_face(frame, detections[di]['bbox'])
                if gray is not None:
                    nt.video_buf.append(gray)
                tracks.append(nt)

            now = time.time()
            araw = None  # 音频只需按需取一次，多张脸共用同一份麦克风音频
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
                            # 只取窗口最后1帧代表"当前"，而不是对整个窗口取平均，
                            # 否则刚开口/刚停顿时会被窗口里更早的静音/说话状态拉低响应速度
                            t.last_score = float(score[-1])

            # 绘制：每张脸各自的框+分数
            disp = frame
            for t in tracks:
                x1, y1, x2, y2 = [int(v) for v in t.bbox]
                speaking = t.last_score > 0
                color = (0, 255, 0) if speaking else (0, 0, 255)
                cv2.rectangle(disp, (x1, y1), (x2, y2), color, 3)
                label = f"#{t.id} {t.last_score:+.2f} {'SPEAKING' if speaking else 'silent'}"
                cv2.putText(disp, label, (x1, max(y1 - 12, 20)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                facing = abs(t.yaw) <= FACING_YAW_TH and abs(t.pitch) <= FACING_PITCH_TH
                facing_color = (255, 200, 0) if facing else (0, 140, 255)  # 青蓝=对准 / 橙色=没对准
                facing_label = "FACING DEVICE" if facing else "NOT FACING"
                cv2.putText(disp, facing_label, (x1, min(y2 + 26, disp.shape[0] - 26)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, facing_color, 2)
                pose_label = f"yaw {t.yaw:+.0f} pitch {t.pitch:+.0f}  mouth {t.mouth_open:.2f}"
                cv2.putText(disp, pose_label, (x1, min(y2 + 50, disp.shape[0] - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, facing_color, 1)
            if not tracks:
                cv2.putText(disp, "no face detected", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

            fps_count += 1
            if now - fps_t0 >= 1.0:
                disp_fps = fps_count / (now - fps_t0)
                fps_count = 0
                fps_t0 = now
            cv2.putText(disp, f"{disp_fps:.1f} fps   Q=quit", (20, disp.shape[0] - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            cv2.imshow(window, disp)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        cap.release()
        audio.stop()
        landmarker.close()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
