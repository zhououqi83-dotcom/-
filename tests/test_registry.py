from gaze_arbiter.registry import PersonRegistry


def test_observe_assigns_stable_person_id_for_same_track():
    reg = PersonRegistry()
    p1 = reg.observe(track_id=7, yaw_deg=10.0, now=0.0)
    p2 = reg.observe(track_id=7, yaw_deg=12.0, now=1.0)
    assert p1.person_id == p2.person_id
    assert p2.yaw_deg == 12.0  # 状态被更新


def test_observe_different_tracks_get_different_ids():
    reg = PersonRegistry()
    p1 = reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    p2 = reg.observe(track_id=2, yaw_deg=0.0, now=0.0)
    assert p1.person_id != p2.person_id
    assert len(reg) == 2


def test_prune_stale_removes_and_reports_ids():
    reg = PersonRegistry(stale_timeout_s=2.0)
    p1 = reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    reg.observe(track_id=2, yaw_deg=0.0, now=5.0)
    removed = reg.prune_stale(now=5.0)
    assert removed == [p1.person_id]
    assert len(reg) == 1


def test_set_and_clear_chat_target():
    reg = PersonRegistry()
    p = reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    ok = reg.set_chat_target(p.person_id, duration_s=5.0, now=0.0)
    assert ok
    assert p.chat_target_active(now=3.0)
    assert not p.chat_target_active(now=10.0)
    reg.set_chat_target(p.person_id, duration_s=5.0, now=0.0)
    reg.clear_chat_target(p.person_id)
    assert not p.chat_target_active(now=1.0)


def test_set_chat_target_unknown_person_returns_false():
    reg = PersonRegistry()
    assert reg.set_chat_target("nonexistent", duration_s=5.0) is False


def test_mark_glanced_updates_timestamp():
    reg = PersonRegistry()
    p = reg.observe(track_id=1, yaw_deg=0.0, now=0.0)
    assert p.last_glanced_at == 0.0
    reg.mark_glanced(p.person_id, now=42.0)
    assert p.last_glanced_at == 42.0
