"""gaze_arbiter — 多人场景下"接下来该看谁"的注视目标仲裁算法原型.

独立算法模块, 不依赖摄像头/麦克风/NLU 模型等真实硬件或已训练模型,
用假数据(examples/simulate.py)验证逻辑本身对不对。设计上跟
droidcore-temp/src/decision/interest 的 InterestPointList 思路一致
(权重加权抽样 + fixation 时长按权重插值 + 看过降权), 但补上了
droidcore-temp 目前还没有的几个信号: 脸部大小、声源方向匹配、
朝向机器人程度。

后续如果要接入真实系统, 大概率是把这里的 Person/PersonRegistry 换成
真实检测器喂数据、把 SoundContext 换成 sound_localization 的实时输出,
核心的 signals/weights/scheduler 三层不需要动。`PersonRegistry.set_chat_target`
这套语义判定的写入接口还留着, 但已经不参与打分, 以后要接 nlu_intent
的话除了喂数据, 还得先把它重新接回 weights.py 的打分公式里。
"""
from .person import Person
from .registry import PersonRegistry
from .scheduler import GazeDecision, GazeScheduler
from .signals import SignalParams, SoundContext
from .sound_orient import SoundOrientConfig, SoundOrientState
from .weights import InterestBreakdown, WeightConfig, compute_interest

__all__ = [
    "Person",
    "PersonRegistry",
    "GazeScheduler",
    "GazeDecision",
    "SignalParams",
    "SoundContext",
    "SoundOrientConfig",
    "SoundOrientState",
    "WeightConfig",
    "InterestBreakdown",
    "compute_interest",
]
