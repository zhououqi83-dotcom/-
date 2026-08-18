"""signals.py — 各个独立的打分函数, 每个都是"这一个因素单独看, 这个人该得多少分".

每个函数返回 [0, 1] 的分数, 互相之间不知道对方存在。怎么把这些分数合成一个
总权重, 是 weights.py 的事, 不在这里做, 方便单独测每个信号、单独调每个信号
的参数。

命名对应用户原始需求:
    face_size_score      — "脸大的权重高"
    novelty_score         — "没注视过的对象权重高"
    sound_direction_score — "声源方向权重高"
    facing_score          — "面朝机器人的人权重高"
    speaking_score        — 辅助信号: 正在说话(配合 sound_direction 一起判断谁在讲话)
    chat_target_score     — "要聊天的对象权重高"(语义判定的落地结果, 见 SemanticContext)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .person import Person


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _angular_diff_deg(a: float, b: float) -> float:
    """两个角度的最短夹角, 处理 ±180 环绕."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d)


@dataclass
class SoundContext:
    """当前的声源方向信息, 由 sound_localization 那一路(或 fusion_360)喂进来."""
    doa_deg: Optional[float] = None      # 当前估计的声源方向, None = 没有有效声音
    confidence: float = 1.0              # [0,1], 声源估计的置信度(比如响度/信噪比换算)


@dataclass
class SignalParams:
    """各信号自己的形状参数, 跟 weights.py 里的"各信号有多重要"的系数分开管理."""
    size_saturation: float = 0.35        # face_area_frac 到这个值就封顶给满分 1.0
    novelty_saturation_s: float = 20.0   # 距上次注视超过这么久, novelty 封顶 1.0
    sound_tolerance_deg: float = 20.0    # 声源方向高斯打分的"半宽", 越小越挑剔


def face_size_score(p: Person, params: SignalParams) -> float:
    """脸越大(越近/越靠画面中心显著), 分越高. 饱和曲线, 避免"贴脸"的人一家独大到无穷."""
    if params.size_saturation <= 0:
        return 0.0
    return _clamp01(p.face_area_frac / params.size_saturation)


def novelty_score(p: Person, now: float, params: SignalParams) -> float:
    """没被机器人看过的人分高; 刚被看完的人分数掉到 0(天然实现"看腻了就换人"的 IoR),
    不需要额外的"看过降权"乘子。"""
    if p.last_glanced_at <= 0.0:
        return 1.0
    if params.novelty_saturation_s <= 0:
        return 1.0
    dt = now - p.last_glanced_at
    return _clamp01(dt / params.novelty_saturation_s)


def sound_direction_score(p: Person, sound: SoundContext, params: SignalParams) -> float:
    """人的方向跟当前声源方向越接近, 分越高. 高斯衰减, 没有声音时整体为 0.

    注意: sound_localization 那套 2 麦克风方案角度是 0~180 度、前后镜像对称的
    (参见 sound_localization/README.md), 如果直接喂那套的 angle_from_axis 进来,
    需要先在外面转换成机器人本体系的 yaw(比如靠 fusion_360 的 360° 融合结果,
    或者干脆在能确定前后的场景下才启用这个信号)。这里只管"给定一个本体系角度,
    算匹配分", 不管上游怎么把麦克风角度转成这个角度。
    """
    if sound.doa_deg is None or sound.confidence <= 0:
        return 0.0
    diff = _angular_diff_deg(p.yaw_deg, sound.doa_deg)
    tol = max(1e-6, params.sound_tolerance_deg)
    raw = math.exp(-(diff * diff) / (2.0 * tol * tol))
    return _clamp01(raw * sound.confidence)


def facing_score(p: Person) -> float:
    """面朝机器人的人权重高. 直接用 Person.facing_score(由头部姿态估计模块
    写入, 比如 light_asd_test/6DRepNet360 的 yaw/pitch 换算成"正对摄像头"的
    程度), 这里只是做一次防御性 clamp。"""
    return _clamp01(p.facing_score)


def speaking_score(p: Person) -> float:
    """当前正在说话(接 active speaker detection, 比如 Light-ASD)."""
    return 1.0 if p.is_speaking else 0.0


def chat_target_score(p: Person, now: float) -> float:
    """"要聊天的对象权重高" —— 由外部语义/对话管理模块(nlu_intent 的分类结果
    + 对话状态机)判定"现在在跟谁说话", 写入 Person.is_chat_target。这里只读
    这个标记, 不做语义判断本身(语义模型的输入输出跟人脸完全是两个模态,
    强行揉进这个模块只会让两边都难测)。"""
    return 1.0 if p.chat_target_active(now=now) else 0.0


__all__ = [
    "SoundContext",
    "SignalParams",
    "face_size_score",
    "novelty_score",
    "sound_direction_score",
    "facing_score",
    "speaking_score",
    "chat_target_score",
]
