"""weights.py — 把 signals.py 里各个独立打分, 按可调系数合成一个总兴趣值.

合成方式: 加权和 / 权重总和(结果始终落在 [0,1]), 而不是加权乘积——乘积会导致
任何一个信号是 0 就把总分乘没了(比如没有声音时 sound_direction_score=0,
乘法会让"没在说话但脸很大又在等着聊天"的人也变成 0 分, 不合理)。加权和只是
拉低那部分贡献, 更符合"这些是互相独立的加分项"的直觉。

MIN_INTEREST_FLOOR 保证只要人还在场上, 总分不会精确为 0, 避免
weighted_sample 在"所有人权重都被压到 0"时抽不出人来。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import signals
from .person import Person
from .signals import SignalParams, SoundContext

MIN_INTEREST_FLOOR = 0.03


@dataclass
class WeightConfig:
    """各信号在总分里占多重. 数值是相对大小, 不要求加起来等于 1(内部会归一化)."""
    w_size: float = 1.0          # 脸大
    w_novelty: float = 1.5       # 没注视过
    w_sound: float = 2.0         # 声源方向匹配
    w_facing: float = 1.2        # 面朝机器人
    w_speaking: float = 1.0      # 正在说话(辅助, 常跟 sound 联动)
    w_chat_target: float = 3.0   # 语义判定出的聊天对象(给最高权重, 对应"要聊天的对象权重高")

    def total_weight(self) -> float:
        return (self.w_size + self.w_novelty + self.w_sound
                + self.w_facing + self.w_speaking + self.w_chat_target)


@dataclass
class InterestBreakdown:
    """调试/可视化用: 记录这个人的总分是怎么凑出来的."""
    person_id: str
    total: float
    size: float
    novelty: float
    sound: float
    facing: float
    speaking: float
    chat_target: float


def compute_interest(
    p: Person,
    *,
    now: float,
    sound: SoundContext,
    sig_params: SignalParams,
    weights: WeightConfig,
) -> InterestBreakdown:
    s_size = signals.face_size_score(p, sig_params)
    s_novel = signals.novelty_score(p, now, sig_params)
    s_sound = signals.sound_direction_score(p, sound, sig_params)
    s_facing = signals.facing_score(p)
    s_speak = signals.speaking_score(p)
    s_chat = signals.chat_target_score(p, now)

    total_w = weights.total_weight()
    if total_w <= 0:
        combined = 0.0
    else:
        combined = (
            weights.w_size * s_size
            + weights.w_novelty * s_novel
            + weights.w_sound * s_sound
            + weights.w_facing * s_facing
            + weights.w_speaking * s_speak
            + weights.w_chat_target * s_chat
        ) / total_w

    total = max(MIN_INTEREST_FLOOR, min(1.0, combined))
    return InterestBreakdown(
        person_id=p.person_id,
        total=total,
        size=s_size,
        novelty=s_novel,
        sound=s_sound,
        facing=s_facing,
        speaking=s_speak,
        chat_target=s_chat,
    )


__all__ = ["WeightConfig", "InterestBreakdown", "compute_interest", "MIN_INTEREST_FLOOR"]
