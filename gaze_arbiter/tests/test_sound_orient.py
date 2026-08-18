from gaze_arbiter.sound_orient import SoundOrientConfig, SoundOrientState


def test_no_sound_never_activates():
    state = SoundOrientState()
    for _ in range(5):
        result = state.tick(doa_deg=None, confidence=0.0, idle=True, now=0.0)
    assert result is None
    assert state.active is False


def test_new_sound_activates_when_idle():
    state = SoundOrientState()
    result = state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=0.0)
    assert result == 30.0
    assert state.active is True


def test_sound_does_not_interrupt_when_not_idle():
    """场上已经有人被锁定(idle=False)时, 声音再新也不该打断, 交给
    GazeScheduler 自己的防抖规则处理。"""
    state = SoundOrientState()
    result = state.tick(doa_deg=-40.0, confidence=0.9, idle=False, now=0.0)
    assert result is None
    assert state.active is False


def test_low_confidence_sound_ignored_even_when_idle():
    cfg = SoundOrientConfig(confidence_threshold=0.5)
    state = SoundOrientState(cfg)
    result = state.tick(doa_deg=30.0, confidence=0.2, idle=True, now=0.0)
    assert result is None
    assert state.active is False


def test_same_direction_does_not_reset_search_timeout():
    """同一个方向持续有声音(比如人还在那说话), 不该每帧都重置搜索计时器,
    不然只要一直有声音就永远不会超时放弃。"""
    cfg = SoundOrientConfig(search_timeout_s=2.0)
    state = SoundOrientState(cfg)
    state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=0.0)
    state.tick(doa_deg=30.2, confidence=0.9, idle=True, now=1.0)  # 几乎同方向
    result = state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=2.5)  # 超过 2.0s
    assert result is None
    assert state.active is False


def test_person_appears_hands_control_back_immediately():
    """定向中场上出现了人(idle 变 False), 不管搜索超时还没到, 立刻交还。"""
    state = SoundOrientState(SoundOrientConfig(search_timeout_s=10.0))
    state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=0.0)
    assert state.active is True
    result = state.tick(doa_deg=30.0, confidence=0.9, idle=False, now=0.1)
    assert result is None
    assert state.active is False


def test_search_timeout_gives_up_and_hands_control_back():
    cfg = SoundOrientConfig(search_timeout_s=2.5)
    state = SoundOrientState(cfg)
    state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=0.0)
    still_active = state.tick(doa_deg=None, confidence=0.0, idle=True, now=2.0)
    assert still_active == 30.0  # 还没超时, 继续指向声源
    timed_out = state.tick(doa_deg=None, confidence=0.0, idle=True, now=2.6)
    assert timed_out is None
    assert state.active is False


def test_new_direction_while_active_redirects_and_resets_timeout():
    cfg = SoundOrientConfig(search_timeout_s=2.0, redirect_threshold_deg=15.0)
    state = SoundOrientState(cfg)
    state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=0.0)
    # 1.5s 后换了个明显不同的方向 -> 应该重新定向、重置计时器
    result = state.tick(doa_deg=-20.0, confidence=0.9, idle=True, now=1.5)
    assert result == -20.0
    # 原计时器本该在 2.0s 到期, 但因为重新定向, 3.0s 时(相对新方向只过了 1.5s)不该超时
    still_active = state.tick(doa_deg=None, confidence=0.0, idle=True, now=3.0)
    assert still_active == -20.0


def test_small_drift_within_redirect_threshold_does_not_reset_timeout():
    cfg = SoundOrientConfig(search_timeout_s=2.0, redirect_threshold_deg=15.0)
    state = SoundOrientState(cfg)
    state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=0.0)
    state.tick(doa_deg=35.0, confidence=0.9, idle=True, now=1.9)  # 5° 内, 不算换方向
    timed_out = state.tick(doa_deg=None, confidence=0.0, idle=True, now=2.1)
    assert timed_out is None
    assert state.active is False


def test_becoming_idle_while_sound_continues_can_still_trigger():
    """场上原本有人(idle=False), 人走了变 idle=True, 这时如果还有声音,
    应该正常触发定向(不需要"声音必须是全新事件"这种额外条件)。"""
    state = SoundOrientState()
    no_trigger = state.tick(doa_deg=30.0, confidence=0.9, idle=False, now=0.0)
    assert no_trigger is None
    triggered = state.tick(doa_deg=30.0, confidence=0.9, idle=True, now=0.5)
    assert triggered == 30.0
    assert state.active is True
