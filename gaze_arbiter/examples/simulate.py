#!/usr/bin/env python3
"""simulate.py — 用假数据跑通整条链路, 肉眼看输出是否符合直觉.

不接摄像头、不接麦克风、不接 NLU 模型 —— 这是"独立算法原型"阶段该有的样子:
先证明"人脸打标签 + 多因子权重 + 注视调度"这套逻辑本身对不对(会不会又变成
新的一种"扭来扭去"), 再考虑接哪个真实系统。

场景设定(4 个人, 固定方位角, 模拟一段时间的自然变化):
    P1  yaw=-40°  偶尔说话, 脸忽大忽小(模拟走近走远)
    P2  yaw=-10°  经常面朝机器人(专注看它), 脸中等大小, 基本不出声
    P3  yaw=+15°  一直没被特别关注, 用来看"没注视过权重高"能不能把它捞出来
    P4  yaw=+50°  跟 P3 类似, 全程没有额外信号加成

运行:
    python examples/simulate.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gaze_arbiter import (  # noqa: E402
    GazeScheduler,
    PersonRegistry,
    SignalParams,
    SoundContext,
    WeightConfig,
)

SIM_DURATION_S = 60.0
RNG = random.Random(42)

PEOPLE = {
    1: {"yaw": -40.0, "label": "P1(会时不时说话)"},
    2: {"yaw": -10.0, "label": "P2(一直盯着机器人)"},
    3: {"yaw": 15.0, "label": "P3(全程没人理)"},
    4: {"yaw": 50.0, "label": "P4(全程没人理)"},
}


def build_frame(now: float, registry: PersonRegistry) -> SoundContext:
    """模拟这一帧的感知输入: 人脸检测结果 + 当前声源方向.

    真实系统里这些字段分别来自:
      face_area_frac / facing_score → 摄像头人脸检测 + light_asd_test/6DRepNet360 头部姿态
      is_speaking                    → light_asd_test (Light-ASD active speaker detection)
      SoundContext.doa_deg            → sound_localization (需先转换到机器人本体系角度)
    """
    speaker_id = 1 if int(now / 8) % 3 == 0 else None  # P1 每隔一阵说一段话

    for track_id, meta in PEOPLE.items():
        is_speaking = (track_id == speaker_id)
        if track_id == 2:
            facing = 0.9 + 0.1 * RNG.random()
        else:
            facing = 0.2 * RNG.random()
        if track_id == 1:
            size = 0.15 + 0.15 * abs(RNG.gauss(0, 1)) * 0.3 + 0.1
        else:
            size = 0.1 + 0.05 * RNG.random()

        registry.observe(
            track_id=track_id,
            yaw_deg=meta["yaw"] + RNG.uniform(-1.5, 1.5),  # 模拟检测抖动
            face_area_frac=min(0.9, size),
            facing_score=facing,
            is_speaking=is_speaking,
            now=now,
        )

    sound = SoundContext(doa_deg=None)
    if speaker_id is not None:
        p = registry.find_by_track(speaker_id)
        if p is not None:
            sound = SoundContext(doa_deg=p.yaw_deg, confidence=0.9)
    return sound


def main() -> None:
    registry = PersonRegistry(stale_timeout_s=5.0)
    scheduler = GazeScheduler(min_gaze_s=1.5, max_gaze_s=5.0, jitter_frac=0.25,
                              rng=random.Random(7))
    sig_params = SignalParams()
    weights = WeightConfig()

    print(f"{'t(s)':>6} {'看谁':<28} {'时长':>5} {'原因':<8} 权重构成(size/novelty/sound/facing/speak)")
    print("-" * 100)

    now = 0.0
    while now < SIM_DURATION_S:
        sound = build_frame(now, registry)
        decision = scheduler.tick(registry, sound=sound, sig_params=sig_params,
                                  weights=weights, now=now)

        label = "( idle, 场上没人 )" if decision.person_id is None else \
            PEOPLE[registry.find_by_id(decision.person_id).track_id]["label"]
        bd = decision.breakdown
        bd_str = "" if bd is None else (
            f"{bd.size:.2f}/{bd.novelty:.2f}/{bd.sound:.2f}/"
            f"{bd.facing:.2f}/{bd.speaking:.2f} (total={bd.total:.2f})"
        )
        if decision.is_new:
            print(f"{now:6.1f} {label:<28} {decision.duration_s:5.1f} {decision.reason:<8} {bd_str}")

        now += decision.duration_s


if __name__ == "__main__":
    main()
