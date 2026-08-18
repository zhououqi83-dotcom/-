from types import SimpleNamespace

from gaze_arbiter.input.face_source import (
    FaceSourceConfig,
    bbox_area_frac,
    bbox_center_yaw_deg,
    facing_score_from_pose,
    is_speaking_from_score,
    observe_face_track,
)
from gaze_arbiter.registry import PersonRegistry


def test_bbox_area_frac_basic():
    # 100x100 的框, 1000x1000 的画面 -> 1%
    assert abs(bbox_area_frac((0, 0, 100, 100), 1000, 1000) - 0.01) < 1e-9


def test_bbox_area_frac_zero_frame_is_zero():
    assert bbox_area_frac((0, 0, 10, 10), 0, 0) == 0.0


def test_bbox_center_yaw_deg_center_is_zero():
    # 画面正中的框(比如 400~600, 画面宽1000) -> yaw=0
    yaw = bbox_center_yaw_deg((400, 0, 600, 100), frame_w=1000, fov_deg=90.0)
    assert abs(yaw) < 1e-9


def test_bbox_center_yaw_deg_left_is_negative_right_is_positive():
    left = bbox_center_yaw_deg((0, 0, 100, 100), frame_w=1000, fov_deg=90.0)
    right = bbox_center_yaw_deg((900, 0, 1000, 100), frame_w=1000, fov_deg=90.0)
    assert left < 0
    assert right > 0
    assert abs(left + right) < 1.0  # 对称位置大致对称


def test_bbox_center_yaw_deg_invert_flips_sign():
    normal = bbox_center_yaw_deg((0, 0, 100, 100), frame_w=1000, fov_deg=90.0, invert=False)
    inverted = bbox_center_yaw_deg((0, 0, 100, 100), frame_w=1000, fov_deg=90.0, invert=True)
    assert abs(normal + inverted) < 1e-9


def test_facing_score_perfectly_facing_is_one():
    assert facing_score_from_pose(0.0, 0.0) == 1.0


def test_facing_score_at_threshold_boundary_is_zero():
    # 正好卡在阈值边界上(沿一个轴), 应该约等于 0 —— 跟 live_demo.py 的二值判定
    # "等于阈值算不算 facing"的边界行为对齐(那边用 <=, 这里到边界分数为 0
    # 而不是负数, 差异只在"压线算不算", 不影响后续按权重排序的实际效果)。
    assert abs(facing_score_from_pose(30.0, 0.0, yaw_th=30.0, pitch_th=20.0)) < 1e-9
    assert abs(facing_score_from_pose(0.0, 20.0, yaw_th=30.0, pitch_th=20.0)) < 1e-9


def test_facing_score_beyond_threshold_clamped_to_zero():
    assert facing_score_from_pose(90.0, 90.0, yaw_th=30.0, pitch_th=20.0) == 0.0


def test_facing_score_invalid_thresholds_raise():
    import pytest
    with pytest.raises(ValueError):
        facing_score_from_pose(0.0, 0.0, yaw_th=0.0)


def test_is_speaking_from_score():
    assert is_speaking_from_score(0.5) is True
    assert is_speaking_from_score(-0.1) is False
    assert is_speaking_from_score(0.0) is False  # 边界: 严格大于 0 才算


def test_observe_face_track_writes_all_fields():
    """用一个 duck-typed 假 track(不需要真的 import FaceTrack/mediapipe/torch)
    验证跟 light_asd_test/Light-ASD/live_demo.py::FaceTrack 的字段约定接得上."""
    registry = PersonRegistry()
    fake_track = SimpleNamespace(
        id=7, bbox=(0, 0, 100, 100), yaw=5.0, pitch=-3.0, last_score=0.8,
    )
    cfg = FaceSourceConfig(fov_deg=90.0)
    p = observe_face_track(registry, fake_track, frame_w=1000, frame_h=1000, cfg=cfg, now=1.0)

    assert p.track_id == 7
    assert p.is_speaking is True
    assert 0.0 < p.face_area_frac < 1.0
    assert p.facing_score > 0.5  # yaw=5/pitch=-3 离阈值(30/20)还很远, 应该接近正对
    assert p.yaw_deg < 0  # bbox 靠左(0~100, 画面宽1000) -> 方位角应为负


def test_observe_face_track_persists_person_id_across_frames():
    registry = PersonRegistry()
    cfg = FaceSourceConfig()
    t1 = SimpleNamespace(id=3, bbox=(0, 0, 50, 50), yaw=0.0, pitch=0.0, last_score=0.0)
    t2 = SimpleNamespace(id=3, bbox=(10, 10, 60, 60), yaw=0.0, pitch=0.0, last_score=1.0)

    p1 = observe_face_track(registry, t1, frame_w=1000, frame_h=1000, cfg=cfg, now=0.0)
    p2 = observe_face_track(registry, t2, frame_w=1000, frame_h=1000, cfg=cfg, now=1.0)

    assert p1.person_id == p2.person_id
    assert p2.is_speaking is True
