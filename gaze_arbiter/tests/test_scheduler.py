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


def test_forced_switch_away_from_previous_when_multiple_candidates():
    reg = PersonRegistry(stale_timeout_s=1000.0)
    reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    reg.observe(track_id=2, yaw_deg=90.0, now=0.0)
    sched = GazeScheduler(min_gaze_s=1.0, max_gaze_s=2.0, jitter_frac=0.0,
                          rng=random.Random(2))
    first = sched.tick(reg, now=0.0)
    second = sched.tick(reg, now=first.duration_s + 0.01)
    # 规则: 只要还有别人在场, 绝不会连续两次抽中同一个人, 防止"扭来扭去"式的抖动锁死.
    assert second.person_id != first.person_id
    assert second.reason == "switch"


@pytest.mark.parametrize("seed", range(5))
def test_duration_always_within_bounds(seed):
    reg = PersonRegistry(stale_timeout_s=1000.0)
    reg.observe(track_id=1, yaw_deg=0.0, face_area_frac=0.5, is_speaking=True, now=0.0)
    reg.observe(track_id=2, yaw_deg=45.0, face_area_frac=0.1, now=0.0)
    reg.observe(track_id=3, yaw_deg=-45.0, facing_score=1.0, now=0.0)
    sched = GazeScheduler(min_gaze_s=1.5, max_gaze_s=6.0, jitter_frac=0.4,
                          rng=random.Random(seed))
    now = 0.0
    for _ in range(20):
        d = sched.tick(reg, now=now)
        assert 1.5 <= d.duration_s <= 6.0
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


def test_chat_target_dominates_dwell_time_not_raw_pick_count():
    """场上正好 2 个人时, "强制排除刚看过那位"这条规则决定了选人必然是
    100% 交替(轮流排除对方), 这是故意的防抖设计, 不代表权重没生效。
    权重的效果应该体现在"停留多久"上: 聊天对象每次轮到该他时长时间停留
    (接近 max_gaze_s), 另一人每次轮到时只是扫一眼(接近 min_gaze_s)。
    """
    reg = PersonRegistry(stale_timeout_s=1000.0)
    p1 = reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    p2 = reg.observe(track_id=2, yaw_deg=90.0, now=0.0)
    reg.set_chat_target(p2.person_id, duration_s=100.0, now=0.0)

    # min/max 拉开到 1~6 秒(而不是 1~2), 这样"权重占比 3% vs 97%"才有足够
    # 的时长区间体现出来 —— min/max 太接近的话, 不管权重差多少, 时长上限
    # 摆动幅度本身就很小, 会掩盖掉这里想验证的效果。
    weights = WeightConfig(w_size=0.1, w_novelty=0.1, w_sound=0.1,
                           w_facing=0.1, w_speaking=0.1, w_chat_target=50.0)
    sched = GazeScheduler(min_gaze_s=1.0, max_gaze_s=6.0, jitter_frac=0.0,
                          rng=random.Random(4))

    durations = {p1.person_id: 0.0, p2.person_id: 0.0}
    now = 0.0
    for _ in range(10):
        d = sched.tick(reg, now=now, weights=weights)
        durations[d.person_id] += d.duration_s
        now += d.duration_s + 0.01

    # 总停留时长应该被聊天对象权重严重拉偏, 而不是两人各占一半.
    assert durations[p2.person_id] > durations[p1.person_id] * 3
