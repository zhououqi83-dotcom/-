"""sound_orient.py — SoundOrientState: 声源定向, 一套独立于 GazeScheduler 的
"该看谁"系统, 只管一件事: 场上没人的时候, 头该不该主动转去找声源。

设计上刻意跟 `GazeScheduler`(基于人脸的加权打分)完全独立、互不修改对方——
两套系统各自决定自己的"目标", 谁的目标真正拿去驱动 `HeadDriver` 是调用方
(examples/web_dashboard.py 的主循环)按"声源定向 active 就用它的, 否则用
GazeScheduler 的"这条简单规则来仲裁的, 不需要合并成一个信号/加权分。

行为(跟 signals.py/scheduler.py 一样是纯函数式的状态机, 不碰硬件):
    1. **只在没人的时候**(`idle=True`, 即 `GazeScheduler` 当前没锁定任何人)
       才会响应声音, 转头正对声源方向去找人——如果已经有人被锁定/正在看着,
       声音再新也不会打断, 交给 `GazeScheduler` 自己的两条防抖规则处理。
    2. 转过去之后, 在 `search_timeout_s` 这段时间里等场上出现人(`idle`
       变成 False, 说明 `GazeScheduler` 那边已经有候选人了):
       - 等到了(`idle=False`): 立刻交还控制权, 让 `GazeScheduler` 接手挑
         目标(它一直在后台正常运行, 没有被这套状态机改动或暂停过)。
       - 等超时还是没人: 也交还控制权, 回到"声音出现之前该在的地方"——
         这里同样是"不覆盖 GazeScheduler 的目标就完事", 不需要额外记忆
         "之前在看哪"。
       声源方向中途明显换了地方(超过 `redirect_threshold_deg`)会重新
       定向、重置这个等待计时器。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class SoundOrientConfig:
    confidence_threshold: float = 0.5   # 声源置信度低于这个不触发定向(噪声/误检)
    redirect_threshold_deg: float = 15.0  # 定向中新声源方向偏出这个角度才算"换了地方", 重新计时
    search_timeout_s: float = 2.5       # 转过去之后等人出现的最长时间, 超时放弃


class SoundOrientState:
    """一路声源定向状态机. 每帧调一次 `tick()`, 不管自己有没有在定向."""

    def __init__(self, config: Optional[SoundOrientConfig] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = config if config is not None else SoundOrientConfig()
        self._clock = clock
        self._active = False
        self._target_bearing: Optional[float] = None
        self._deadline = 0.0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def target_bearing(self) -> Optional[float]:
        """定向中的目标方位角(跟 `Person.yaw_deg`/`SoundContext.doa_deg` 同一套
        本体系角度约定), 不在定向状态时是 None。"""
        return self._target_bearing if self._active else None

    def tick(self, *, doa_deg: Optional[float], confidence: float,
             idle: bool, now: Optional[float] = None) -> Optional[float]:
        """推进一步.

        `idle`: 调用方算好的"GazeScheduler 当前有没有锁定任何人"(没人时才
        True)——只在这个前提下, 新声音才会触发/维持定向; 一旦不再 idle(场上
        出现人了), 不管定向进行到哪一步都立刻交还控制权。

        返回值就是 `target_bearing`: 非 None 表示"这一帧头该被声源定向接管,
        用这个角度喂给 HeadDriver, 别用 GazeScheduler 的决策"; None 表示
        "声源定向不插手, 用 GazeScheduler 的决策"。
        """
        now = now if now is not None else self._clock()

        has_sound = doa_deg is not None and confidence >= self.cfg.confidence_threshold
        if idle and has_sound:
            is_new = (not self._active or self._target_bearing is None
                      or abs(doa_deg - self._target_bearing) > self.cfg.redirect_threshold_deg)
            if is_new:
                self._active = True
                self._target_bearing = doa_deg
                self._deadline = now + self.cfg.search_timeout_s

        if self._active:
            if not idle:
                self._active = False
                self._target_bearing = None
            elif now >= self._deadline:
                self._active = False
                self._target_bearing = None

        return self.target_bearing


__all__ = ["SoundOrientConfig", "SoundOrientState"]
