"""registry.py — PersonRegistry: 维护"当前场上有哪些人"这张表.

只做身份管理和状态更新, 不做任何权重计算(那是 weights.py 的事)、
不做任何注视决策(那是 scheduler.py 的事)。职责单一, 方便单测。

track_id -> person_id 的映射策略是本原型里最简化的一版: 同一个 track_id
只要没有超过 stale_timeout_s 没更新, 就一直对应同一个 person_id。
真实场景接入人脸识别/ReID 后, 可以在这层加"人脸特征比对复用旧 person_id"
的逻辑, 上层(weights/scheduler)不需要跟着改。
"""
from __future__ import annotations

import time
from typing import Callable, Dict, Iterable, List, Optional

from .person import Person


class PersonRegistry:
    """track_id -> Person 的活跃列表, 带过期清理."""

    def __init__(self, *, stale_timeout_s: float = 3.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.stale_timeout_s = stale_timeout_s
        self._clock = clock
        self._by_track: Dict[int, Person] = {}
        self._next_seq = 1

    # ── 写入: 每帧人脸检测结果调 ────────────────────────────────
    def observe(
        self,
        *,
        track_id: int,
        yaw_deg: float,
        pitch_deg: float = 0.0,
        face_area_frac: float = 0.0,
        facing_score: float = 0.0,
        is_speaking: bool = False,
        now: Optional[float] = None,
    ) -> Person:
        now = now if now is not None else self._clock()
        p = self._by_track.get(track_id)
        if p is None:
            p = Person(
                person_id=f"P{self._next_seq}",
                track_id=track_id,
                created_at=now,
            )
            self._next_seq += 1
            self._by_track[track_id] = p

        p.yaw_deg = yaw_deg
        p.pitch_deg = pitch_deg
        p.face_area_frac = face_area_frac
        p.facing_score = facing_score
        p.is_speaking = is_speaking
        p.touch(now=now)
        return p

    # ── 外部语义信号写入 ────────────────────────────────────────
    def set_chat_target(self, person_id: str, *, duration_s: float,
                         now: Optional[float] = None) -> bool:
        """语义判定模块认为"当前在跟这个人聊天", 给一段时间的高权重.

        duration_s 到期后 is_chat_target 自动失效(靠 Person.chat_target_active
        在读取时判断, 这里不用定时器)。
        """
        now = now if now is not None else self._clock()
        p = self.find_by_id(person_id)
        if p is None:
            return False
        p.is_chat_target = True
        p.chat_target_until = now + duration_s
        return True

    def clear_chat_target(self, person_id: str) -> None:
        p = self.find_by_id(person_id)
        if p is not None:
            p.is_chat_target = False
            p.chat_target_until = 0.0

    def mark_glanced(self, person_id: str, *, now: Optional[float] = None) -> None:
        p = self.find_by_id(person_id)
        if p is not None:
            p.mark_glanced(now=now if now is not None else self._clock())

    # ── 清理 ────────────────────────────────────────────────────
    def prune_stale(self, *, now: Optional[float] = None) -> List[str]:
        """移除超过 stale_timeout_s 没更新的人. 返回被移除的 person_id 列表."""
        now = now if now is not None else self._clock()
        gone = [tid for tid, p in self._by_track.items()
                if now - p.last_seen_at > self.stale_timeout_s]
        removed_ids = [self._by_track[tid].person_id for tid in gone]
        for tid in gone:
            del self._by_track[tid]
        return removed_ids

    # ── 只读访问 ────────────────────────────────────────────────
    @property
    def people(self) -> List[Person]:
        return list(self._by_track.values())

    def __len__(self) -> int:
        return len(self._by_track)

    def __iter__(self) -> Iterable[Person]:
        return iter(self._by_track.values())

    def find_by_id(self, person_id: str) -> Optional[Person]:
        for p in self._by_track.values():
            if p.person_id == person_id:
                return p
        return None

    def find_by_track(self, track_id: int) -> Optional[Person]:
        return self._by_track.get(track_id)


__all__ = ["PersonRegistry"]
