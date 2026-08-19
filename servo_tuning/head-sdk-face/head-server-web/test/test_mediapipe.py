#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path

from mapping_utils import build_stream_payload


BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "face_landmarker_v2_with_blendshapes.task"
IMAGE_PATH = BASE_DIR / "test.jpg"
VIDEO_PATH = BASE_DIR / "test.mp4"
DEFAULT_SERVER_URL = "ws://127.0.0.1:8765/ws"
SMOOTHING_ALPHA = 0.35
HEAD_LIMITS = {
    "headPitch": 0.45,
    "headYaw": 0.65,
    "headRoll": 0.35,
}
MEDIAPIPE_BLENDSHAPES = [
    "_neutral",
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeLookDownLeft",
    "eyeLookDownRight",
    "eyeLookInLeft",
    "eyeLookInRight",
    "eyeLookOutLeft",
    "eyeLookOutRight",
    "eyeLookUpLeft",
    "eyeLookUpRight",
    "eyeSquintLeft",
    "eyeSquintRight",
    "eyeWideLeft",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawOpen",
    "jawRight",
    "mouthClose",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthFunnel",
    "mouthLeft",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthPucker",
    "mouthRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "noseSneerLeft",
    "noseSneerRight",
]


def build_image_landmarker():
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=False,
    )
    return vision.FaceLandmarker.create_from_options(options)


def build_video_landmarker():
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    return vision.FaceLandmarker.create_from_options(options)


def clamp_signed_unit(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


def zero_payload() -> dict[str, float]:
    payload = {name: 0.0 for name in MEDIAPIPE_BLENDSHAPES}
    payload.update({
        "headPitch": 0.0,
        "headYaw": 0.0,
        "headRoll": 0.0,
    })
    return payload


def matrix_to_head_pose(matrix) -> dict[str, float]:
    import numpy as np

    if matrix is None:
        return {
            "headPitch": 0.0,
            "headYaw": 0.0,
            "headRoll": 0.0,
        }

    rotation = np.array(matrix, dtype=np.float64).reshape(4, 4)[:3, :3]
    sy = math.sqrt(rotation[0, 0] ** 2 + rotation[1, 0] ** 2)
    singular = sy < 1e-6

    if not singular:
        pitch = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(-rotation[2, 0], sy)
        roll = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        pitch = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = math.atan2(-rotation[2, 0], sy)
        roll = 0.0

    return {
        "headPitch": clamp_signed_unit(pitch / HEAD_LIMITS["headPitch"]),
        "headYaw": clamp_signed_unit(yaw / HEAD_LIMITS["headYaw"]),
        "headRoll": clamp_signed_unit(roll / HEAD_LIMITS["headRoll"]),
    }


def smooth_values(previous: dict[str, float] | None, current: dict[str, float], alpha: float) -> dict[str, float]:
    if previous is None:
        return dict(current)
    return {
        key: previous.get(key, 0.0) + (current.get(key, 0.0) - previous.get(key, 0.0)) * alpha
        for key in current.keys()
    }


async def send_payload(server_url: str, payload: dict) -> None:
    import websockets

    async with websockets.connect(server_url) as websocket:
        await websocket.send(json.dumps(payload))


async def run_image_mode(server_url: str) -> None:
    import cv2
    import mediapipe as mp

    image = cv2.imread(str(IMAGE_PATH))
    if image is None:
        raise RuntimeError(f"failed to read image: {IMAGE_PATH}")

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    with build_image_landmarker() as landmarker:
        result = landmarker.detect(mp_image)

    if not result.face_blendshapes:
        raise RuntimeError("no blendshapes detected in image mode")

    values = {entry.category_name: float(entry.score) for entry in result.face_blendshapes[0]}
    payload = build_stream_payload(values)
    await send_payload(server_url, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def run_video_mode(server_url: str) -> None:
    import cv2
    import mediapipe as mp
    import websockets

    cap = cv2.VideoCapture(str(VIDEO_PATH))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video: {VIDEO_PATH}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0

    frame_interval = 1.0 / fps
    frame_index = 0
    smoothed_values: dict[str, float] | None = None
    loop = asyncio.get_running_loop()
    playback_start = loop.time()

    async with websockets.connect(server_url) as websocket:
        with build_video_landmarker() as landmarker:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                target_time = frame_index * frame_interval
                wait_time = target_time - (loop.time() - playback_start)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int(target_time * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                values = zero_payload()
                if result.face_blendshapes:
                    for blendshape in result.face_blendshapes[0]:
                        values[blendshape.category_name] = float(blendshape.score)
                if result.facial_transformation_matrixes:
                    values.update(matrix_to_head_pose(result.facial_transformation_matrixes[0]))

                smoothed_values = smooth_values(smoothed_values, values, SMOOTHING_ALPHA)
                payload = build_stream_payload(smoothed_values)
                await websocket.send(json.dumps(payload))

                frame_index += 1

    cap.release()


async def main() -> None:
    parser = argparse.ArgumentParser(description="MediaPipe test sender for the platform")
    parser.add_argument("--mode", choices=("image", "video"), default="image")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    args = parser.parse_args()

    if args.mode == "image":
        await run_image_mode(args.server_url)
        return
    await run_video_mode(args.server_url)


if __name__ == "__main__":
    asyncio.run(main())
