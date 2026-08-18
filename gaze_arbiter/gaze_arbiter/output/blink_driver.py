"""blink_driver.py — 不摇头的时候让机器人自然眨眼.

眨眼时机跟头部转动互斥: 头在动的时候(GazeScheduler 正在追踪目标切换/
声源定向搜索)不触发新的眨眼, 等头停下来再算下一次眨眼的倒计时——这样不会
跟转头动作叠在一起显得诡异。已经在进行中的一次眨眼动作不会被头突然开始
转动打断(眨眼总共一两百毫秒, 打断意义不大反而更假)。"头在不在动"由调用方
判断(比如比较 HeadDriver.sent_frac 前后两帧的差值), 这个模块不关心为什么
动, 只关心动没动。

眼睑舵机(left_blink/right_blink)取值约定见
servo_tuning/head-sdk-face/head-sdk/src/head_sdk/servo_mappings.yaml:
0.44 ≈ 睁眼(舵机上电时的初始位置), 1.0 = 完全闭眼, 中间线性过渡; 两个舵机
的物理接线方向相反, 但 servo_contrl.py 里的 `dir` 字段已经在更底层把这个
差异抹平了, 这里只管发同一个值给两只眼睛。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from .head_driver import HeadClient


@dataclass
class BlinkDriverConfig:
    open_frac: float = 0.44        # 睁眼位置, 对应舵机上电初始值
    closed_frac: float = 1.0       # 全闭位置
    close_duration_s: float = 0.09  # 闭眼动作耗时
    hold_duration_s: float = 0.04   # 闭眼状态停留耗时
    open_duration_s: float = 0.12   # 睁眼动作耗时(比闭眼略慢, 更像真人)
    min_interval_s: float = 2.0     # 两次眨眼之间最短间隔
    max_interval_s: float = 6.0     # 两次眨眼之间最长间隔(真人约 12~20 次/分钟)
    double_blink_prob: float = 0.1  # 每次眨完, 小概率紧接着很快再眨一次
    left_channel: str = "left_blink"
    right_channel: str = "right_blink"


class _Phase(Enum):
    IDLE = auto()
    CLOSING = auto()
    HOLDING = auto()
    OPENING = auto()


class BlinkDriver:
    """独立于头部转动的眨眼状态机. 每帧调用一次 update(dt, head_moving)."""

    def __init__(self, head_client: HeadClient, config: Optional[BlinkDriverConfig] = None,
                 rng: Optional[random.Random] = None) -> None:
        self._head = head_client
        self.cfg = config if config is not None else BlinkDriverConfig()
        self._rng = rng if rng is not None else random.Random()
        self._phase = _Phase.IDLE
        self._phase_t = 0.0
        self._next_blink_in = self._sample_interval()

    def _sample_interval(self) -> float:
        return self._rng.uniform(self.cfg.min_interval_s, self.cfg.max_interval_s)

    def _send(self, frac: float) -> None:
        self._head.set_servo_positions({
            self.cfg.left_channel: round(frac, 3),
            self.cfg.right_channel: round(frac, 3),
        })

    def reset(self) -> None:
        """强制回到"睁眼+空闲"状态, 连接/重连时调用一次, 避免上次异常退出
        遗留在半闭眼状态。"""
        self._phase = _Phase.IDLE
        self._phase_t = 0.0
        self._next_blink_in = self._sample_interval()
        self._send(self.cfg.open_frac)

    def update(self, dt: float, head_moving: bool) -> None:
        cfg = self.cfg

        if self._phase == _Phase.IDLE:
            if head_moving:
                return  # 头在动就不倒计时, 等停下来再算下一次眨眼什么时候到
            self._next_blink_in -= dt
            if self._next_blink_in <= 0.0:
                self._phase = _Phase.CLOSING
                self._phase_t = 0.0
            return

        # 已经在眨眼动作中途: 不管头这时候有没有开始动, 都让这一次眨完。
        self._phase_t += dt
        if self._phase == _Phase.CLOSING:
            t = min(1.0, self._phase_t / cfg.close_duration_s)
            self._send(cfg.open_frac + t * (cfg.closed_frac - cfg.open_frac))
            if t >= 1.0:
                self._phase = _Phase.HOLDING
                self._phase_t = 0.0
        elif self._phase == _Phase.HOLDING:
            if self._phase_t >= cfg.hold_duration_s:
                self._phase = _Phase.OPENING
                self._phase_t = 0.0
        elif self._phase == _Phase.OPENING:
            t = min(1.0, self._phase_t / cfg.open_duration_s)
            self._send(cfg.closed_frac + t * (cfg.open_frac - cfg.closed_frac))
            if t >= 1.0:
                self._phase = _Phase.IDLE
                if self._rng.random() < cfg.double_blink_prob:
                    self._next_blink_in = self._rng.uniform(0.12, 0.3)
                else:
                    self._next_blink_in = self._sample_interval()


__all__ = ["BlinkDriver", "BlinkDriverConfig"]
