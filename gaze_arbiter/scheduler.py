"""scheduler.py — GazeScheduler: 决定"接下来看谁、看多久".

三个用户明确要的参数都在这里落地:
    min_gaze_s / max_gaze_s   — 最短/最长注视时间(硬边界, 最终时长一定落在这个区间内)
    jitter_frac               — 注视随机区间(在权重算出的基础时长上再叠加一层随机抖动,
                                 避免每次时长都精确等于公式算出来的值, 显得呆板)

时长公式: 候选人越"该被看"(总权重在候选集合里占比越高), 基础时长越接近
max_gaze_s; 权重占比低的人被抽中时, 基础时长接近 min_gaze_s(抽中了但没那么
"该看", 扫一眼就走)。这跟 droidcore-temp 的 InterestGazeStrategy 用的
T = T_MIN + (T_MAX-T_MIN)*p_i 是同一个思路(见该项目 src/decision/interest/
strategy.py), 这里额外加了 jitter 这一层。

切换目标时的两条硬规则(避免"高频率扭来扭去"):
    1. fixation 时间没到之前绝不重新选目标 —— 哪怕这一帧算出别人权重更高,
       也要等当前这位的注视时间走完。这是防抖动的关键, 不是遗漏。
    2. 候选人多于 1 个时, 强制排除"刚看过的这位", 逼着看别的地方,
       避免两人权重非常接近时被同一个人反复抽中显得"锁死"。
       只剩 1 个人在场时不排除 —— 单人场景应该持续盯着他, 而不是没人可选。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .person import Person
from .registry import PersonRegistry
from .signals import SignalParams, SoundContext
from .weights import InterestBreakdown, WeightConfig, compute_interest


@dataclass
class GazeDecision:
    """一次 tick 的产出."""
    person_id: Optional[str]        # None = 场上没人, idle
    duration_s: float               # 这次注视(或 idle)预计维持多久
    is_new: bool                    # True=这一帧刚切换目标, False=延续上一帧的注视
    breakdown: Optional[InterestBreakdown]   # 被选中者的权重构成, 调试/可视化用
    reason: str                     # "hold" / "switch" / "idle"


class GazeScheduler:
    def __init__(
        self,
        *,
        min_gaze_s: float = 1.5,
        max_gaze_s: float = 6.0,
        jitter_frac: float = 0.2,
        idle_hold_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
    ) -> None:
        if min_gaze_s <= 0 or max_gaze_s < min_gaze_s:
            raise ValueError("需要 0 < min_gaze_s <= max_gaze_s")
        if not (0.0 <= jitter_frac <= 1.0):
            raise ValueError("jitter_frac 应在 [0,1] 之间(基础时长的百分比抖动)")

        self.min_gaze_s = min_gaze_s
        self.max_gaze_s = max_gaze_s
        self.jitter_frac = jitter_frac
        self.idle_hold_s = idle_hold_s
        self._clock = clock
        self._rng = rng if rng is not None else random.Random()

        self._current_person_id: Optional[str] = None
        self._fixation_end: float = 0.0
        self._last_breakdown: Optional[InterestBreakdown] = None

    @property
    def current_person_id(self) -> Optional[str]:
        return self._current_person_id

    def tick(
        self,
        registry: PersonRegistry,
        *,
        sound: Optional[SoundContext] = None,
        sig_params: Optional[SignalParams] = None,
        weights: Optional[WeightConfig] = None,
        now: Optional[float] = None,
    ) -> GazeDecision:
        now = now if now is not None else self._clock()
        sound = sound if sound is not None else SoundContext()
        sig_params = sig_params if sig_params is not None else SignalParams()
        weights = weights if weights is not None else WeightConfig()

        removed_ids = registry.prune_stale(now=now)
        if self._current_person_id in removed_ids:
            # 正看着的人从场上消失了(比如转身走掉), 不硬等 fixation 到期, 立刻重选.
            self._current_person_id = None
            self._fixation_end = now

        current = (registry.find_by_id(self._current_person_id)
                   if self._current_person_id else None)

        # 规则 1: fixation 没到期 → 保持, 不管这一帧别人算出来权重多高.
        if current is not None and now < self._fixation_end:
            return GazeDecision(
                person_id=self._current_person_id,
                duration_s=self._fixation_end - now,
                is_new=False,
                breakdown=self._last_breakdown,
                reason="hold",
            )

        # 到期或没有当前目标: 上一个目标标记为"刚看过"(novelty 清零), 再选下一个.
        if current is not None:
            registry.mark_glanced(self._current_person_id, now=now)

        candidates = registry.people
        if not candidates:
            self._current_person_id = None
            self._last_breakdown = None
            self._fixation_end = now + self.idle_hold_s
            return GazeDecision(
                person_id=None, duration_s=self.idle_hold_s,
                is_new=True, breakdown=None, reason="idle",
            )

        breakdowns: Dict[str, InterestBreakdown] = {
            p.person_id: compute_interest(
                p, now=now, sound=sound, sig_params=sig_params, weights=weights,
            )
            for p in candidates
        }

        # 规则 2: 多于 1 人时强制排除刚看过的那位; 只剩 1 人则允许持续锁定.
        pool: List[Person] = candidates
        if len(pool) > 1 and self._current_person_id is not None:
            filtered = [p for p in pool if p.person_id != self._current_person_id]
            if filtered:
                pool = filtered

        # 选谁: 只在 pool(排除刚看过那位后剩下的人)里按权重抽.
        weight_values = [breakdowns[p.person_id].total for p in pool]
        total_w = sum(weight_values)
        if total_w <= 0:
            chosen = self._rng.choice(pool)
        else:
            chosen = self._rng.choices(pool, weights=weight_values, k=1)[0]

        # 看多久: 用"这位在所有在场候选人里的权重占比"(不是在 pool 里的占比!).
        # 这两者在候选人只有 2 个时天差地别 —— 如果按 pool 占比算, pool 里
        # 强制排除后往往只剩 1 个人, 那个人占比会被算成 100%, 不管他本身权重
        # 多低, 每次被"轮到"都给满 max_gaze_s, 完全体现不出真实的权重差异。
        # 按全体候选人占比算, 低权重的人被轮到时只扫一眼(接近 min_gaze_s),
        # 高权重的人被轮到时才长时间停留, 这才是"权重高的人该多看"的本意。
        total_w_all = sum(b.total for b in breakdowns.values())
        p_i = breakdowns[chosen.person_id].total / total_w_all if total_w_all > 0 else 1.0 / len(candidates)

        base_duration = self.min_gaze_s + (self.max_gaze_s - self.min_gaze_s) * p_i
        jitter = base_duration * self._rng.uniform(-self.jitter_frac, self.jitter_frac)
        duration = min(self.max_gaze_s, max(self.min_gaze_s, base_duration + jitter))

        self._current_person_id = chosen.person_id
        self._last_breakdown = breakdowns[chosen.person_id]
        self._fixation_end = now + duration

        return GazeDecision(
            person_id=chosen.person_id,
            duration_s=duration,
            is_new=True,
            breakdown=self._last_breakdown,
            reason="switch",
        )


__all__ = ["GazeScheduler", "GazeDecision"]
