#!/usr/bin/env python3
"""run_with_head.py — 用 simulate.py 那套假数据场景, 真实驱动头部舵机.

目的: 在真实的人脸检测/声源定位/NLU 流水线接进来之前, 先验证"权重决策 →
舵机指令"这一段输出链路本身是通的、平滑参数是合理的。用假数据喂
GazeScheduler, 决策结果通过 HeadDriver 真实转到头上。

前提(跟 servo_tuning/使用说明.md、sound_track_head.py 一致):
    1. conda activate face_servo   (这个环境装了 head_sdk, 这个脚本所在的
       gaze_arbiter/venv 里没装, 也不该装 —— gaze_arbiter 本身不依赖 head_sdk,
       只有这个示例脚本需要, 所以单独在 face_servo 环境里跑这一个文件)
    2. 终端另开一个, 跑 servo_tuning/head-sdk-face/.../head_grpc_server.py,
       确认 USB 头部舵机板已连接、gRPC :2543 可连
    3. 本脚本跟 gaze_arbiter 包在同一个 checkout 里, 靠相对路径 import

用法:
    conda activate face_servo
    python examples/run_with_head.py
    python examples/run_with_head.py --invert          # 头转的方向反了
    python examples/run_with_head.py --max-speed 0.3    # 嫌转得太猛

Ctrl+C 退出, 退出前头回中。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gaze_arbiter import GazeScheduler, PersonRegistry, SignalParams, WeightConfig  # noqa: E402
from gaze_arbiter.output import HeadDriver, HeadDriverConfig  # noqa: E402
from simulate import PEOPLE, build_frame  # noqa: E402 — 复用同一套假场景, 不重复写


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--sdk-port", type=int, default=2543)
    parser.add_argument("--invert", action="store_true", help="头转的方向跟目标反了就加这个")
    parser.add_argument("--fov-deg", type=float, default=90.0, help="摄像头水平视场角(度)")
    parser.add_argument("--min-frac", type=float, default=0.15)
    parser.add_argument("--max-frac", type=float, default=0.85)
    parser.add_argument("--smoothing", type=float, default=0.25, help="EMA 平滑系数, 越小越平滑但越滞后")
    parser.add_argument("--max-speed", type=float, default=0.4, help="头部最高转动速度(归一化值/秒)")
    parser.add_argument("--rate-hz", type=float, default=12.0, help="发指令频率")
    parser.add_argument("--min-gaze-s", type=float, default=1.5)
    parser.add_argument("--max-gaze-s", type=float, default=6.0)
    parser.add_argument("--jitter-frac", type=float, default=0.25)
    args = parser.parse_args()

    try:
        from head_sdk import HeadSDK
    except ImportError:
        print("✗ 找不到 head_sdk, 这个脚本要在装了它的环境里跑(conda activate face_servo)。")
        print("  gaze_arbiter 包本身不依赖 head_sdk, 只有这一个示例脚本需要, 属于预期。")
        return 1

    print(f">> 连接头部舵机服务 {args.host}:{args.sdk_port} ...")
    head = HeadSDK(args.host, sdk_port=args.sdk_port)
    if not head.is_connected():
        print("✗ 连不上头部舵机服务, 检查 head_grpc_server.py 是不是还在跑")
        return 1

    driver = HeadDriver(head, HeadDriverConfig(
        fov_deg=args.fov_deg, invert=args.invert,
        min_frac=args.min_frac, max_frac=args.max_frac,
        smoothing=args.smoothing, max_speed=args.max_speed,
    ))
    driver.center()
    print("✓ 已连接, 头回中. 开始用假数据场景驱动, Ctrl+C 停止")

    registry = PersonRegistry(stale_timeout_s=5.0)
    scheduler = GazeScheduler(min_gaze_s=args.min_gaze_s, max_gaze_s=args.max_gaze_s,
                              jitter_frac=args.jitter_frac, rng=random.Random(7))
    sig_params = SignalParams()
    weights = WeightConfig()
    chat_flag: dict = {}

    dt = 1.0 / args.rate_hz
    sim_t = 0.0
    try:
        while True:
            sound = build_frame(sim_t, registry, chat_flag)
            decision = scheduler.tick(registry, sound=sound, sig_params=sig_params,
                                      weights=weights, now=sim_t)
            target = registry.find_by_id(decision.person_id) if decision.person_id else None
            yaw = target.yaw_deg if target is not None else None
            sent = driver.update(yaw, dt)

            label = "(idle)" if target is None else PEOPLE[target.track_id]["label"]
            print(f"\rt={sim_t:6.1f}s  看={label:<28} head_yao={sent:.2f}   ", end="", flush=True)

            time.sleep(dt)
            sim_t += dt
    except KeyboardInterrupt:
        pass
    finally:
        print("\n>> 停止, 头慢速回中(最多 ~3s)...")
        try:
            driver.recenter()
            deadline = time.monotonic() + 3.0
            while not driver.recenter_step(dt):
                if time.monotonic() >= deadline:
                    break
                time.sleep(dt)
            driver.center()
        except Exception:  # noqa: BLE001 — 退出路径, 舵机服务可能已断线, 不卡住退出
            pass
        head.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
