#!/usr/bin/env python3
"""run_with_head_fake.py — run_with_head.py 的零依赖版, 假头部客户端 + 终端可视化.

用假的 head_client(只打印, 不连硬件)代替真实 HeadSDK, 只需要 gaze_arbiter/venv
本身(pytest/pyserial, 零重依赖), 不用装 head_sdk/连硬件也能立刻验证
HeadDriver(最简人脸追踪: EMA 平滑 + 限速)的行为对不对。真机验证用
run_with_head.py。

用法:
    gaze_arbiter/venv/bin/python examples/run_with_head_fake.py
    gaze_arbiter/venv/bin/python examples/run_with_head_fake.py --max-speed 1.0

Ctrl+C 退出。
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


class FakeHeadClient:
    """只打印、不连硬件. 接口跟 HeadSDK 一样, 驱动分不出区别."""

    def __init__(self) -> None:
        self.last_positions: dict = {}

    def release_control(self) -> None:
        pass

    def set_servo_positions(self, positions: dict) -> None:
        self.last_positions.update(positions)

    def disconnect(self) -> None:
        pass


def _bar(frac: float, width: int = 40) -> str:
    """把 0~1 的 frac 画成一条文字进度条, 直观看头转到哪."""
    pos = max(0, min(width - 1, round(frac * (width - 1))))
    return "[" + "-" * pos + "●" + "-" * (width - 1 - pos) + "]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--invert", action="store_true")
    parser.add_argument("--fov-deg", type=float, default=90.0)
    parser.add_argument("--min-frac", type=float, default=0.15)
    parser.add_argument("--max-frac", type=float, default=0.85)
    parser.add_argument("--smoothing", type=float, default=None,
                         help="EMA 平滑系数, 越小越平滑但越滞后(默认 0.25)")
    parser.add_argument("--max-speed", type=float, default=None,
                         help="最高转动速度, 归一化值/秒(默认 0.6)")
    parser.add_argument("--rate-hz", type=float, default=12.0)
    parser.add_argument("--min-gaze-s", type=float, default=1.5)
    parser.add_argument("--max-gaze-s", type=float, default=6.0)
    parser.add_argument("--jitter-frac", type=float, default=0.25)
    parser.add_argument("--speed", type=float, default=1.0, help="模拟时间倍速, 想看快一点就调大")
    parser.add_argument("--duration-s", type=float, default=None, help="跑指定的模拟秒数后自动停止(不传就一直跑到 Ctrl+C)")
    args = parser.parse_args()

    driver_cfg_kwargs = dict(
        fov_deg=args.fov_deg, invert=args.invert,
        min_frac=args.min_frac, max_frac=args.max_frac,
    )
    if args.smoothing is not None:
        driver_cfg_kwargs["smoothing"] = args.smoothing
    if args.max_speed is not None:
        driver_cfg_kwargs["max_speed"] = args.max_speed
    driver = HeadDriver(FakeHeadClient(), HeadDriverConfig(**driver_cfg_kwargs))
    driver.center()
    print("✓ 假头部客户端就绪(不连硬件, HeadDriver 最简人脸追踪). Ctrl+C 停止\n")

    registry = PersonRegistry(stale_timeout_s=5.0)
    scheduler = GazeScheduler(min_gaze_s=args.min_gaze_s, max_gaze_s=args.max_gaze_s,
                              jitter_frac=args.jitter_frac, rng=random.Random(7))
    sig_params = SignalParams()
    weights = WeightConfig()
    chat_flag: dict = {}

    dt = 1.0 / args.rate_hz
    sim_t = 0.0
    try:
        while args.duration_s is None or sim_t < args.duration_s:
            sound = build_frame(sim_t, registry, chat_flag)
            decision = scheduler.tick(registry, sound=sound, sig_params=sig_params,
                                      weights=weights, now=sim_t)
            target = registry.find_by_id(decision.person_id) if decision.person_id else None
            yaw = target.yaw_deg if target is not None else None
            sent = driver.update(yaw, dt)

            label = "(idle)" if target is None else PEOPLE[target.track_id]["label"]
            print(f"\rt={sim_t:6.1f}s  看={label:<28} {_bar(sent)} frac={sent:.3f}   ",
                  end="", flush=True)

            time.sleep(dt / max(args.speed, 0.01))
            sim_t += dt
    except KeyboardInterrupt:
        pass
    finally:
        print("\n>> 停止, 头慢速回中(打印, 不连硬件)...")
        driver.recenter()
        while not driver.recenter_step(dt):
            pass
        driver.center()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
