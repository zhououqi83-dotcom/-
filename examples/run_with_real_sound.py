#!/usr/bin/env python3
"""run_with_real_sound.py — 真实声源方向 + 假的人脸场景, 验证"声源方向匹配"
这个信号接了真实硬件之后效果对不对。

背景: 人脸检测/头部姿态那条链路(light_asd_test)还没改成实时流水线, 但声源
定位这条已经有现成的、可以直接读的协议(J7034G4 板子自己吐 doa_angle 文本)。
所以先把"真实声音 + 假人脸"这个组合跑通, 比等一整条摄像头流水线搭完再测
更快拿到反馈, 也能在真人脸接进来之前先验证声源方向这个信号的角度换算、
校准参数(--offset-deg/--invert)对不对。

拿 examples/simulate.py 里那 4 个固定方位角的假人当"画面里的人"
(yaw=-40°/-10°/15°/50°, 见 simulate.py::PEOPLE), 声源方向不再是脚本编的,
是从真实麦克风阵列读出来的 —— 对着某个假人的方位角说话/拍手, 看
GazeScheduler 是否真的把注意力转过去、`sound` 那一项权重是否顺势变高。

用法:
    python examples/run_with_real_sound.py --port /dev/ttyUSB0
    # 头(这里是假人)转错方向了, 加 --invert; 0°跟正前方对不上, 用 --offset-deg 校准
    python examples/run_with_real_sound.py --port /dev/ttyUSB0 --invert --offset-deg 10

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
from gaze_arbiter.input import J7034DoaReader, SoundSourceConfig  # noqa: E402
from simulate import PEOPLE, build_frame  # noqa: E402 — 复用假人脸场景, 不重复写


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="/dev/ttyUSB0", help="J7034G4 串口号")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--offset-deg", type=float, default=0.0,
                        help="声源 0° 跟机器人正前方对不上时的角度校准")
    parser.add_argument("--invert", action="store_true", help="方向反了就加这个")
    parser.add_argument("--stale-s", type=float, default=2.0,
                        help="超过这么久没收到新读数, 就当作没有有效声音")
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--min-gaze-s", type=float, default=1.5)
    parser.add_argument("--max-gaze-s", type=float, default=6.0)
    parser.add_argument("--jitter-frac", type=float, default=0.25)
    args = parser.parse_args()

    try:
        import serial  # noqa: F401
    except ImportError:
        print("✗ 没装 pyserial, 先跑: pip install pyserial")
        return 1

    print(f">> 打开麦克风阵列串口 {args.port} @ {args.baud} ...")
    reader = J7034DoaReader(args.port, args.baud)
    reader.start()
    time.sleep(0.3)
    if reader.last_error() is not None:
        print(f"✗ 打不开串口: {reader.last_error()}")
        return 1

    sound_cfg = SoundSourceConfig(offset_deg=args.offset_deg, invert=args.invert,
                                  stale_s=args.stale_s)
    registry = PersonRegistry(stale_timeout_s=5.0)
    scheduler = GazeScheduler(min_gaze_s=args.min_gaze_s, max_gaze_s=args.max_gaze_s,
                              jitter_frac=args.jitter_frac, rng=random.Random(7))
    sig_params = SignalParams()
    weights = WeightConfig()
    chat_flag: dict = {}

    print(">> 开始, Ctrl+C 停止. 对着假人所在方位说话/拍手, 看注意力转不转过去.")
    print("   假人方位: " + ", ".join(f"{v['label']}={v['yaw']:+.0f}°" for v in PEOPLE.values()))

    dt = 1.0 / args.rate_hz
    sim_t = 0.0
    try:
        while True:
            build_frame(sim_t, registry, chat_flag)  # 只要它的人脸副作用, 声音换成真实的
            sound = reader.latest_sound_context(sound_cfg)
            decision = scheduler.tick(registry, sound=sound, sig_params=sig_params,
                                      weights=weights, now=sim_t)
            target = registry.find_by_id(decision.person_id) if decision.person_id else None
            label = "(idle)" if target is None else PEOPLE[target.track_id]["label"]
            doa_str = f"{sound.doa_deg:+6.1f}" if sound.doa_deg is not None else "  --  "
            bd = decision.breakdown
            sound_w = f"{bd.sound:.2f}" if bd is not None else "--"
            print(f"\rt={sim_t:6.1f}s  声源yaw={doa_str}  sound权重={sound_w}  看={label:<28}",
                  end="", flush=True)

            time.sleep(dt)
            sim_t += dt
    except KeyboardInterrupt:
        pass
    finally:
        print("\n>> 停止")
        reader.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
