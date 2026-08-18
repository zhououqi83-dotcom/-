"""face_source.py — 把 light_asd_test 的人脸检测/头部朝向/说话人判别结果,
转成 Person 字段, 写进 PersonRegistry。

跟 `sound_source.py`/`head_driver.py` 一样的原则: 这里只有不需要摄像头/
mediapipe/torch 的纯函数, 全部可以在 gaze_arbiter 自己的 venv 里单独测试
(`tests/test_face_source.py`)。真正跑摄像头+模型的代码在
`examples/run_with_face.py` 里, 需要用 `light_asd_test/venv` 运行(那边装了
opencv/torch/mediapipe 这些重依赖, gaze_arbiter 自己的 venv 里没有也不该装)。

**两种"yaw"不要混淆**, 这是接这一路输入最容易搞错的地方:
  · `Person.yaw_deg`(方位角) —— 这个人在画面里偏左偏右多少度, 由人脸框
    中心点的像素位置换算而来(`bbox_center_yaw_deg`)。决定 GazeScheduler
    选中他之后头该转到哪个角度、声源方向匹配用的也是这个。
  · 头部姿态的 yaw/pitch(朝向角) —— 这个人的脸朝向哪, 由
    light_asd_test/Light-ASD/live_demo.py 里 MediaPipe 的
    facial_transformation_matrix 算出来(`t.yaw`/`t.pitch`)。只用来算
    `facing_score`("是不是在看机器人"), 跟这个人站在画面里哪个位置无关。
    live_demo.py 的 README 里写了这套朝向角符号还没校准, 这里原样透传,
    校准需要在 light_asd_test 那边的 `rotation_matrix_to_euler_deg()` 里改。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..registry import PersonRegistry


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def bbox_area_frac(bbox: tuple, frame_w: float, frame_h: float) -> float:
    """人脸框面积 / 画面面积. bbox=(x1,y1,x2,y2), 像素坐标(跟
    light_asd_test/Light-ASD/live_demo.py::detect_faces_mediapipe 返回格式一致)."""
    x1, y1, x2, y2 = bbox
    area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    frame_area = frame_w * frame_h
    if frame_area <= 0:
        return 0.0
    return _clamp01(area / frame_area)


def bbox_center_yaw_deg(bbox: tuple, frame_w: float, *,
                        fov_deg: float = 90.0, invert: bool = False) -> float:
    """人脸框中心的像素位置 -> 机器人本体系方位角(0=正前, 左负右正).

    线性针孔近似(画面正中=0°, 最左=-fov_deg/2, 最右=+fov_deg/2), 不是严格的
    透视投影, 但对"这个人大概在哪个方向"这个用途够用——跟 output/head_driver.py
    的 `yaw_to_frac` 是同一套简化思路, 互为反函数关系(那边角度转舵机行程,
    这里像素位置转角度)。
    """
    if fov_deg <= 0:
        raise ValueError("fov_deg 必须 > 0")
    x1, _, x2, _ = bbox
    cx = (x1 + x2) / 2.0
    raw = cx / frame_w if frame_w > 0 else 0.5   # 0=最左, 1=最右
    raw = _clamp01(raw)
    yaw = (raw - 0.5) * fov_deg
    if invert:
        yaw = -yaw
    return yaw


def facing_score_from_pose(yaw_deg: float, pitch_deg: float, *,
                           yaw_th: float = 30.0, pitch_th: float = 20.0) -> float:
    """头部姿态(朝向角) -> "面朝机器人"的连续分数 [0,1].

    连续版本的 light_asd_test/Light-ASD/live_demo.py 里那个二值判定
    (`abs(yaw) <= FACING_YAW_TH and abs(pitch) <= FACING_PITCH_TH`): 用椭圆
    归一化距离, 正对时(0,0)是 1.0, 到达阈值边界时正好是 0.0, 越过边界仍是 0.0
    (不会变成负数)。默认阈值直接沿用 live_demo.py 里已经调过的
    `FACING_YAW_TH=30` / `FACING_PITCH_TH=20`,两边保持一致。
    """
    if yaw_th <= 0 or pitch_th <= 0:
        raise ValueError("yaw_th/pitch_th 必须 > 0")
    d = math.hypot(yaw_deg / yaw_th, pitch_deg / pitch_th)
    return _clamp01(1.0 - d)


def is_speaking_from_score(score: float, threshold: float = 0.0) -> bool:
    """ASD 打分 -> 是否判定为"正在说话". 阈值跟 live_demo.py 画绿/红框用的
    `t.last_score > 0` 一致(见该文件 `speaking = t.last_score > 0`)。"""
    return score > threshold


@dataclass
class FaceSourceConfig:
    fov_deg: float = 90.0          # 摄像头水平视场角, 跟 output/head_driver.py 保持一致才有意义
    invert: bool = False           # 左右反了就设 True
    yaw_th: float = 30.0           # facing_score 的偏航容忍角度, 沿用 live_demo.py 的 FACING_YAW_TH
    pitch_th: float = 20.0         # facing_score 的俯仰容忍角度, 沿用 live_demo.py 的 FACING_PITCH_TH
    speaking_threshold: float = 0.0


def observe_face_track(
    registry: PersonRegistry,
    track: Any,
    *,
    frame_w: float,
    frame_h: float,
    cfg: FaceSourceConfig,
    now: Optional[float] = None,
):
    """把一个人脸跟踪对象的当前状态写进 PersonRegistry, 返回对应 Person.

    `track` 只要求有 `.id` / `.bbox` / `.yaw` / `.pitch` / `.last_score` 这几个
    属性, 天然兼容 light_asd_test/Light-ASD/live_demo.py::FaceTrack, 不需要
    真的 import 那个类——用假对象(比如 SimpleNamespace)一样能测。
    """
    yaw_deg = bbox_center_yaw_deg(track.bbox, frame_w, fov_deg=cfg.fov_deg, invert=cfg.invert)
    face_area = bbox_area_frac(track.bbox, frame_w, frame_h)
    facing = facing_score_from_pose(track.yaw, track.pitch, yaw_th=cfg.yaw_th, pitch_th=cfg.pitch_th)
    speaking = is_speaking_from_score(track.last_score, cfg.speaking_threshold)
    return registry.observe(
        track_id=track.id,
        yaw_deg=yaw_deg,
        face_area_frac=face_area,
        facing_score=facing,
        is_speaking=speaking,
        now=now if now is not None else time.monotonic(),
    )


__all__ = [
    "FaceSourceConfig",
    "bbox_area_frac",
    "bbox_center_yaw_deg",
    "facing_score_from_pose",
    "is_speaking_from_score",
    "observe_face_track",
]
