import time

from gaze_arbiter.input.sound_source import (
    J7034DoaReader,
    SoundSourceConfig,
    context_from_raw,
    doa_to_body_yaw,
    extract_doa,
)


def test_doa_to_body_yaw_center_is_zero():
    assert doa_to_body_yaw(90.0) == 0.0


def test_doa_to_body_yaw_extremes():
    assert doa_to_body_yaw(0.0) == -90.0
    assert doa_to_body_yaw(180.0) == 90.0


def test_doa_to_body_yaw_offset_shifts_center():
    # 阵列跟摄像头没对齐, 用 offset_deg 校准: 实际的"正前方"对应 doa=100° 而不是 90°
    assert doa_to_body_yaw(100.0, offset_deg=-10.0) == 0.0


def test_doa_to_body_yaw_invert_flips_sign():
    assert doa_to_body_yaw(0.0, invert=True) == 90.0
    assert doa_to_body_yaw(180.0, invert=True) == -90.0


def test_doa_to_body_yaw_clamped_within_bounds():
    # offset 把结果推出 [-90,90] 范围, 应该被夹住而不是原样返回
    assert doa_to_body_yaw(180.0, offset_deg=50.0) == 90.0
    assert doa_to_body_yaw(0.0, offset_deg=-50.0) == -90.0


def test_extract_doa_plain_line():
    assert extract_doa("doa_angle = 123") == 123.0


def test_extract_doa_strips_ansi_escape():
    line = "\x1b[32mdoa_angle=45\x1b[0m"
    assert extract_doa(line) == 45.0


def test_extract_doa_no_match_returns_none():
    assert extract_doa("garbage boot text 1234") is None


def test_extract_doa_negative_value():
    assert extract_doa("doa_angle=-5") == -5.0


def test_context_from_raw_fresh_returns_context():
    cfg = SoundSourceConfig(stale_s=2.0, confidence=0.9)
    ctx = context_from_raw(90.0, t=10.0, now=10.5, cfg=cfg)
    assert ctx.doa_deg == 0.0
    assert ctx.confidence == 0.9


def test_context_from_raw_stale_returns_none_doa():
    cfg = SoundSourceConfig(stale_s=2.0)
    ctx = context_from_raw(90.0, t=10.0, now=13.0, cfg=cfg)
    assert ctx.doa_deg is None


def test_context_from_raw_no_reading_yet():
    cfg = SoundSourceConfig()
    ctx = context_from_raw(None, t=0.0, now=5.0, cfg=cfg)
    assert ctx.doa_deg is None


def test_reader_surfaces_error_for_nonexistent_port():
    # 不需要真实硬件: 指向一个明显不存在的串口路径, pyserial 会立刻报错,
    # 这里验证错误被记录下来、不是让后台线程静默崩溃(排查起来会很难查).
    reader = J7034DoaReader("/dev/nonexistent_port_for_test_xyz")
    reader.start()
    try:
        deadline = time.monotonic() + 2.0
        while reader.last_error() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert reader.last_error() is not None
        assert reader.latest_raw() == (None, 0.0)
    finally:
        reader.stop()
