import random

import pytest

from gaze_arbiter.registry import PersonRegistry
from gaze_arbiter.scheduler import GazeScheduler
from gaze_arbiter.weights import WeightConfig


def test_idle_when_no_one_present():
    reg = PersonRegistry()
    sched = GazeScheduler(idle_hold_s=2.0)
    decision = sched.tick(reg, now=0.0)
    assert decision.person_id is None
    assert decision.reason == "idle"
    assert decision.duration_s == 2.0


def test_single_candidate_stays_locked_across_fixations():
    reg = PersonRegistry(stale_timeout_s=1000.0)
    reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    sched = GazeScheduler(min_gaze_s=1.0, max_gaze_s=2.0, jitter_frac=0.0,
                          rng=random.Random(0))
    now = 0.0
    for _ in range(5):
        d = sched.tick(reg, now=now)
        assert d.person_id == "P1"
        now += d.duration_s + 0.01  # 跳到 fixation 结束之后


def test_holds_target_within_fixation_window():
    reg = PersonRegistry(stale_timeout_s=1000.0)
    reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    reg.observe(track_id=2, yaw_deg=90.0, now=0.0)
    sched = GazeScheduler(min_gaze_s=2.0, max_gaze_s=4.0, jitter_frac=0.0,
                          rng=random.Random(1))
    first = sched.tick(reg, now=0.0)
    assert first.is_new is True
    still = sched.tick(reg, now=0.5)  # 远没到 fixation 结束
    assert still.is_new is False
    assert still.reason == "hold"
    assert still.person_id == first.person_id


@pytest.mark.parametrize("seed", range(5))
def test_duration_respects_min_bound_and_jitter_ceiling(seed):
    """min_gaze_s 是硬下界; 上界不再硬封顶在 max_gaze_s —— jitter 可以把时长
    推过它, 但最多推到 max_gaze_s*(1+jitter_frac)(公式锚点乘以抖动幅度)。"""
    reg = PersonRegistry(stale_timeout_s=1000.0)
    reg.observe(track_id=1, yaw_deg=0.0, face_area_frac=0.5, is_speaking=True, now=0.0)
    reg.observe(track_id=2, yaw_deg=45.0, face_area_frac=0.1, now=0.0)
    reg.observe(track_id=3, yaw_deg=-45.0, facing_score=1.0, now=0.0)
    sched = GazeScheduler(min_gaze_s=1.5, max_gaze_s=6.0, jitter_frac=0.4,
                          rng=random.Random(seed))
    now = 0.0
    for _ in range(20):
        d = sched.tick(reg, now=now)
        assert 1.5 <= d.duration_s <= 6.0 * 1.4
        now += d.duration_s


def test_current_target_leaving_triggers_immediate_reselect_not_wait_for_fixation_end():
    reg = PersonRegistry(stale_timeout_s=1.0)
    reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    sched = GazeScheduler(min_gaze_s=5.0, max_gaze_s=10.0, jitter_frac=0.0,
                          rng=random.Random(3))
    first = sched.tick(reg, now=0.0)
    assert first.person_id == "P1"
    assert first.duration_s >= 5.0  # fixation 本来该锁 5~10 秒

    # 人早就不在场了(没有新的 observe 刷新 last_seen_at), 但 stale_timeout_s=1.0
    # 早就过了, fixation 却还没到期 —— 应该立刻发现人没了, 不硬等 fixation 结束.
    d = sched.tick(reg, now=2.0)
    assert d.person_id is None
    assert d.reason == "idle"


def test_high_score_preempts_fixation_early():
    """规则2: 分数明显反超且持续够久, 应该提前打断fixation, 不用死等到期."""
    reg = PersonRegistry(stale_timeout_s=1000.0)
    pA = reg.observe(track_id=1, yaw_deg=0.0, face_area_frac=0.2, facing_score=0.5, now=0.0)
    pB = reg.observe(track_id=2, yaw_deg=60.0, face_area_frac=0.1, facing_score=0.2, now=0.0)
    sched = GazeScheduler(min_gaze_s=1.5, max_gaze_s=6.0, jitter_frac=0.0,
                          rng=random.Random(1))
    first = sched.tick(reg, now=0.0)
    assert first.person_id == pA.person_id

    # B 中途脸变大+朝向拉满+开口说话, 分数大幅反超(远超 preempt_margin).
    reg.observe(track_id=2, yaw_deg=60.0, face_area_frac=0.5, facing_score=1.0,
               is_speaking=True, now=0.2)
    weights = WeightConfig()
    now = 0.2
    for _ in range(150):
        d = sched.tick(reg, now=now, weights=weights)
        if d.person_id == pB.person_id:
            assert d.reason == "preempt"
            assert now - 0.2 < first.duration_s  # 明显早于死等fixation到期
            return
        now += 0.04
    raise AssertionError("应该在fixation到期前就被抢占切换到B")


def test_noise_level_score_gap_does_not_trigger_preempt():
    """分数只是噪声级的小幅波动(不到 preempt_margin), 不该触发抢占——
    否则又变回"稍微风吹草动就换人"的老问题。"""
    reg = PersonRegistry(stale_timeout_s=1000.0)
    reg.observe(track_id=1, yaw_deg=0.0, face_area_frac=0.3, facing_score=1.0, now=0.0)
    reg.observe(track_id=2, yaw_deg=60.0, face_area_frac=0.32, facing_score=0.9, now=0.0)
    sched = GazeScheduler(min_gaze_s=3.0, max_gaze_s=3.0, jitter_frac=0.0,
                          rng=random.Random(2))
    first = sched.tick(reg, now=0.0)
    now = 0.1
    while now < first.duration_s - 0.05:
        d = sched.tick(reg, now=now)
        assert d.reason == "hold"
        now += 0.05
