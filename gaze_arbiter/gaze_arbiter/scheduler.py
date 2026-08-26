"""scheduler.py — GazeScheduler: 决定"接下来看谁、看多久".

三个用户明确要的参数都在这里落地:
    min_gaze_s                — 最短注视时间(硬下界, 最终时长一定不低于它)
    max_gaze_s                — 时长公式的上限锚点(不是硬封顶: jitter 可以把最终
                                 时长推过它, jitter_frac=0.25 时最多约 1.25 倍)
    jitter_frac               — 注视随机区间(在权重算出的基础时长上再叠加一层随机抖动,
                                 避免每次时长都精确等于公式算出来的值, 显得呆板)

时长公式: 候选人越"该被看"(总权重在候选集合里占比越高), 基础时长越接近
max_gaze_s; 权重占比低的人被抽中时, 基础时长接近 min_gaze_s(抽中了但没那么
"该看", 扫一眼就走)。这跟 droidcore-temp 的 InterestGazeStrategy 用的
T = T_MIN + (T_MAX-T_MIN)*p_i 是同一个思路(见该项目 src/decision/interest/
strategy.py), 这里额外加了 jitter 这一层。

切换目标时的规则(避免"高频率扭来扭去", 同时不让明显更该看的人干等):
    1. fixation 时间没到之前默认不重新选目标 —— 哪怕这一帧算出别人权重更高,
       也要等当前这位的注视时间走完。这是防抖动的关键, 不是遗漏。
    2. 例外(高分抢占): 如果有人的分数持续、明显超过当前正在看的人
       (总分 ≥ 当前人 × preempt_margin, 且这个优势连续保持了
       preempt_confirm_s 秒——单帧的分数波动不算数, 跟 head_driver.py
       眼动优先那套滞回思路一样, 防止噪声抖动触发抢占), 才提前打断
       fixation 立刻切换, 不用死等到期。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

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
    reason: str                     # "hold" / "switch" / "preempt" / "idle"


class GazeScheduler:
    def __init__(
        self,
        *,
        min_gaze_s: float = 1.5,
        max_gaze_s: float = 6.0,
        jitter_frac: float = 0.2,
        idle_hold_s: float = 2.0,
        preempt_margin: float = 1.3,
        preempt_confirm_s: float = 0.4,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
    ) -> None:
        if min_gaze_s <= 0 or max_gaze_s < min_gaze_s:
            raise ValueError("需要 0 < min_gaze_s <= max_gaze_s")
        if not (0.0 <= jitter_frac <= 1.0):
            raise ValueError("jitter_frac 应在 [0,1] 之间(基础时长的百分比抖动)")
        if preempt_margin < 1.0:
            raise ValueError("preempt_margin 应 >= 1.0(否则分数更低的人也能抢占, 违背本意)")
        if preempt_confirm_s < 0:
            raise ValueError("preempt_confirm_s 不能为负")

        self.min_gaze_s = min_gaze_s
        self.max_gaze_s = max_gaze_s
        self.jitter_frac = jitter_frac
        self.idle_hold_s = idle_hold_s
        self.preempt_margin = preempt_margin
        self.preempt_confirm_s = preempt_confirm_s
        self._clock = clock
        self._rng = rng if rng is not None else random.Random()

        self._current_person_id: Optional[str] = None
        self._fixation_end: float = 0.0
        self._last_breakdown: Optional[InterestBreakdown] = None
        self._preempt_candidate_id: Optional[str] = None
        self._preempt_since: Optional[float] = None

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
            self._preempt_candidate_id = None
            self._preempt_since = None

        current = (registry.find_by_id(self._current_person_id)
                   if self._current_person_id else None)

        candidates = registry.people
        if not candidates:
            self._current_person_id = None
            self._last_breakdown = None
            self._fixation_end = now + self.idle_hold_s
            self._preempt_candidate_id = None
            self._preempt_since = None
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

        fixation_active = current is not None and now < self._fixation_end
        preempted = False
        if fixation_active:
            preempted = self._check_preempt(current, candidates, breakdowns, now=now)
        else:
            self._preempt_candidate_id = None
            self._preempt_since = None

        # 规则 1: fixation 没到期 → 保持, 除非规则 2(高分抢占)判定要提前打断.
        if fixation_active and not preempted:
            return GazeDecision(
                person_id=self._current_person_id,
                duration_s=self._fixation_end - now,
                is_new=False,
                breakdown=self._last_breakdown,
                reason="hold",
            )

        # 到期/被抢占/没有当前目标: 上一个目标标记为"刚看过"(novelty 清零), 再选下一个.
        if current is not None:
            registry.mark_glanced(self._current_person_id, now=now)
        self._preempt_candidate_id = None
        self._preempt_since = None

        # 选谁: 在全体候选人里按权重抽(不再强制排除刚看过的那位).
        weight_values = [breakdowns[p.person_id].total for p in candidates]
        total_w = sum(weight_values)
        if total_w <= 0:
            chosen = self._rng.choice(candidates)
        else:
            chosen = self._rng.choices(candidates, weights=weight_values, k=1)[0]

        # 看多久: 用"这位在所有在场候选人里的权重占比".
        total_w_all = sum(b.total for b in breakdowns.values())
        p_i = breakdowns[chosen.person_id].total / total_w_all if total_w_all > 0 else 1.0 / len(candidates)

        base_duration = self.min_gaze_s + (self.max_gaze_s - self.min_gaze_s) * p_i
        jitter = base_duration * self._rng.uniform(-self.jitter_frac, self.jitter_frac)
        # 上限不再硬封顶: max_gaze_s 只当公式的锚点用, jitter 允许把时长推过它
        # (jitter_frac=0.25 时最多约 1.25×max_gaze_s), 免得权重最高的那位每次都
        # 精确卡在 max_gaze_s 上、显得机械。下限仍然保底 min_gaze_s。
        duration = max(self.min_gaze_s, base_duration + jitter)

        self._current_person_id = chosen.person_id
        self._last_breakdown = breakdowns[chosen.person_id]
        self._fixation_end = now + duration

        return GazeDecision(
            person_id=chosen.person_id,
            duration_s=duration,
            is_new=True,
            breakdown=self._last_breakdown,
            reason="preempt" if preempted else "switch",
        )

    def _check_preempt(self, current, candidates, breakdowns, *, now: float) -> bool:
        """fixation 没到期时判断要不要提前打断.

        找除当前人以外分数最高的挑战者, 分数要 >= 当前人 × preempt_margin
        才算"明显更该看"; 而且这个优势要连续保持 preempt_confirm_s 秒才真正
        触发抢占(挑战者中途换人或优势消失就清零重新计时)——防止单帧噪声
        (比如某人偶然侧脸被判定成一瞬间的高朝向分)就把头拽走。
        """
        others = [p for p in candidates if p.person_id != current.person_id]
        if not others:
            self._preempt_candidate_id = None
            self._preempt_since = None
            return False

        challenger = max(others, key=lambda p: breakdowns[p.person_id].total)
        cur_total = breakdowns[current.person_id].total
        if breakdowns[challenger.person_id].total < cur_total * self.preempt_margin:
            self._preempt_candidate_id = None
            self._preempt_since = None
            return False

        if self._preempt_candidate_id != challenger.person_id:
            self._preempt_candidate_id = challenger.person_id
            self._preempt_since = now
            return False

        return now - self._preempt_since >= self.preempt_confirm_s


__all__ = ["GazeScheduler", "GazeDecision"]
