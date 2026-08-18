from gaze_arbiter.person import Person
from gaze_arbiter.signals import (
    SignalParams,
    SoundContext,
    chat_target_score,
    face_size_score,
    facing_score,
    novelty_score,
    sound_direction_score,
    speaking_score,
)


def make_person(**kw) -> Person:
    base = dict(person_id="P1", track_id=1)
    base.update(kw)
    return Person(**base)


def test_face_size_score_saturates():
    params = SignalParams(size_saturation=0.4)
    assert face_size_score(make_person(face_area_frac=0.0), params) == 0.0
    assert face_size_score(make_person(face_area_frac=0.4), params) == 1.0
    assert face_size_score(make_person(face_area_frac=0.8), params) == 1.0  # 超过饱和值仍封顶
    assert face_size_score(make_person(face_area_frac=0.2), params) == 0.5


def test_novelty_score_never_glanced_is_max():
    params = SignalParams(novelty_saturation_s=10.0)
    p = make_person(last_glanced_at=0.0)
    assert novelty_score(p, now=100.0, params=params) == 1.0


def test_novelty_score_just_glanced_is_zero():
    params = SignalParams(novelty_saturation_s=10.0)
    p = make_person(last_glanced_at=50.0)
    assert novelty_score(p, now=50.0, params=params) == 0.0


def test_novelty_score_half_saturation():
    params = SignalParams(novelty_saturation_s=10.0)
    p = make_person(last_glanced_at=50.0)
    assert abs(novelty_score(p, now=55.0, params=params) - 0.5) < 1e-9


def test_sound_direction_score_no_sound_is_zero():
    params = SignalParams(sound_tolerance_deg=15.0)
    p = make_person(yaw_deg=0.0)
    assert sound_direction_score(p, SoundContext(doa_deg=None), params) == 0.0


def test_sound_direction_score_peaks_when_aligned():
    params = SignalParams(sound_tolerance_deg=15.0)
    p = make_person(yaw_deg=30.0)
    aligned = sound_direction_score(p, SoundContext(doa_deg=30.0, confidence=1.0), params)
    off = sound_direction_score(p, SoundContext(doa_deg=-30.0, confidence=1.0), params)
    assert aligned == 1.0
    assert 0.0 <= off < aligned


def test_sound_direction_score_scaled_by_confidence():
    params = SignalParams(sound_tolerance_deg=15.0)
    p = make_person(yaw_deg=0.0)
    full = sound_direction_score(p, SoundContext(doa_deg=0.0, confidence=1.0), params)
    half = sound_direction_score(p, SoundContext(doa_deg=0.0, confidence=0.5), params)
    assert abs(half - full * 0.5) < 1e-9


def test_sound_direction_score_wraps_around_180():
    params = SignalParams(sound_tolerance_deg=15.0)
    p = make_person(yaw_deg=179.0)
    # -179 和 179 只差 2 度(环绕), 不应该被当成接近 360 度的夹角
    score = sound_direction_score(p, SoundContext(doa_deg=-179.0, confidence=1.0), params)
    assert score > 0.9


def test_facing_score_clamped():
    assert facing_score(make_person(facing_score=1.5)) == 1.0
    assert facing_score(make_person(facing_score=-0.5)) == 0.0
    assert facing_score(make_person(facing_score=0.7)) == 0.7


def test_speaking_score():
    assert speaking_score(make_person(is_speaking=True)) == 1.0
    assert speaking_score(make_person(is_speaking=False)) == 0.0


def test_chat_target_score_respects_expiry():
    p = make_person(is_chat_target=True, chat_target_until=100.0)
    assert chat_target_score(p, now=50.0) == 1.0
    assert chat_target_score(p, now=150.0) == 0.0


def test_chat_target_score_no_expiry_means_indefinite():
    p = make_person(is_chat_target=True, chat_target_until=0.0)
    assert chat_target_score(p, now=1_000_000.0) == 1.0
