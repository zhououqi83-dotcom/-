"""person.py — Person: 注视仲裁器眼里"场上一个人"的完整状态.

只存最新一帧的观测值 + 几个决策要用的时间戳, 不存历史轨迹(历史交给上游的
人脸跟踪器/track_id 管理)。person_id 是持久标签("给每个人脸打个标签"),
跟 track_id 解耦: 同一个人如果被跟踪器丢了又重新捕获到, track_id 会变,
但只要 Registry 判断是同一张脸(由外部传入同一个 track_id 或后续接入
人脸识别后用特征比对), person_id 保持不变。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Person:
    """场上一个人的最新状态快照."""

    # ── 身份 ────────────────────────────────────────────────────
    person_id: str                       # 持久标签, 如 "P1", "P2"
    track_id: int                        # 当前帧检测器给的 track id, 会churn

    # ── 空间(本体系, 度) ────────────────────────────────────────
    yaw_deg: float = 0.0                 # 相对机器人正前方, 0=正前, 左负右正
    pitch_deg: float = 0.0

    # ── 视觉信号 ────────────────────────────────────────────────
    face_area_frac: float = 0.0          # 人脸框面积 / 画面面积, [0,1]. "脸大"用这个.
    facing_score: float = 0.0            # 头部朝向机器人的程度, [0,1]. 1=正对镜头, 0=侧脸/背对.

    # ── 听觉/语义信号(由外部模块写入, 本模块不负责计算)────────────
    is_speaking: bool = False            # 当前是否在说话(接 active speaker detection)
    is_chat_target: bool = False         # 是否是当前对话对象(接语义/对话管理判定)
    chat_target_until: float = 0.0       # is_chat_target 的过期时间(monotonic), 0=未设置

    # ── 状态时间戳(monotonic 秒)────────────────────────────────
    created_at: float = field(default_factory=time.monotonic)
    last_seen_at: float = field(default_factory=time.monotonic)
    last_glanced_at: float = 0.0         # 0 = 从未被机器人注视过. "没注视过权重高"用这个.

    def touch(self, *, now: Optional[float] = None) -> None:
        self.last_seen_at = now if now is not None else time.monotonic()

    def mark_glanced(self, *, now: Optional[float] = None) -> None:
        self.last_glanced_at = now if now is not None else time.monotonic()

    def chat_target_active(self, *, now: Optional[float] = None) -> bool:
        n = now if now is not None else time.monotonic()
        return self.is_chat_target and (self.chat_target_until <= 0 or n < self.chat_target_until)

    def __repr__(self) -> str:  # 调试用
        return (f"<{self.person_id} track={self.track_id} yaw={self.yaw_deg:+.0f} "
                f"size={self.face_area_frac:.2f} facing={self.facing_score:.2f} "
                f"speak={int(self.is_speaking)} chat={int(self.is_chat_target)}>")


__all__ = ["Person"]
