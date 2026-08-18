"""head_driver.py — 把 GazeScheduler 选出的目标, 转成头部舵机指令.

刻意不 import head_sdk / HeadSDK —— 这样 gaze_arbiter 这个包本身仍然是
"独立算法原型", 不依赖 servo_tuning 那套需要 conda 环境(face_servo) + 真实
硬件(USB 头部舵机板 + head_grpc_server.py)才能跑起来的东西。真正的 HeadSDK
实例由调用方(examples/run_with_head.py)创建, 以 `head_client` 参数注入进来
—— 只要求这个对象有 `set_servo_positions(dict)` 方法, 方便测试时用假对象替换。

平滑/限速这套逻辑照抄 servo_tuning/sound_track_head.py 的经验(那边已经
在真实场景里验证过 EMA + 限速两层组合能有效防止"声源方向突变时头猛地甩过去"),
这里只是把输入从"单一 doa_angle(0~180°, 麦克风连线夹角)"换成
"任意本体系 yaw_deg(GazeScheduler 选中的人的位置)", 数学上是同一件事。

断开/退出时用 recenter()/recenter_step() 慢速回中, 避免瞬间甩回伤舵机。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _slew_limit(current: float, target: float, max_step: float) -> float:
    """限制每次最多能往 target 靠近 max_step, 防止目标突变时头猛地甩过去."""
    delta = target - current
    if delta > max_step:
        delta = max_step
    elif delta < -max_step:
        delta = -max_step
    return current + delta


def yaw_to_frac(yaw_deg: float, *, fov_deg: float, invert: bool,
                min_frac: float, max_frac: float) -> float:
    """本体系 yaw_deg(0=正前, 左负右正) -> 舵机归一化值(min_frac~max_frac).

    超出 [-fov_deg/2, +fov_deg/2] 范围的角度会被夹到两端, 而不是报错——
    人脸检测给出的角度理论上就该在摄像头视场角内, 万一有噪声抖出界外,
    夹到极限位置比乱转更安全。
    """
    if fov_deg <= 0:
        raise ValueError("fov_deg 必须 > 0")
    raw = (yaw_deg + fov_deg / 2.0) / fov_deg   # 0=最左, 1=最右
    raw = _clamp01(raw)
    if invert:
        raw = 1.0 - raw
    return min_frac + raw * (max_frac - min_frac)


class HeadClient(Protocol):
    """HeadDriver 需要的最小接口, HeadSDK 满足这个协议. 测试时可以传假对象."""
    def release_control(self) -> None: ...
    def set_servo_positions(self, positions: dict) -> None: ...
    def disconnect(self) -> None: ...


@dataclass
class HeadDriverConfig:
    fov_deg: float = 90.0        # 摄像头水平视场角, 决定 yaw_deg 怎么换算成舵机行程
    invert: bool = False         # 头转的方向跟目标反了就设 True
    min_frac: float = 0.15       # 别设到 0, 避免撞硬限位
    max_frac: float = 0.85       # 别设到 1, 避免撞硬限位
    smoothing: float = 0.25      # EMA 系数, 越小越平滑但越滞后
    max_speed: float = 0.4       # 最高转动速度, 归一化值/秒(保护舵机, 2026-08-14 从 0.6 调低)
    channel: str = "head_yao"    # 舵机通道, 见 head-sdk-face 的 servoConfig_25DV3_Ula.yaml


class HeadDriver:
    """管一路舵机通道的平滑跟随. 不管连接/断线这些生命周期以外的事."""

    def __init__(self, head_client: HeadClient, config: Optional[HeadDriverConfig] = None) -> None:
        self._head = head_client
        self.cfg = config if config is not None else HeadDriverConfig()
        self._ema_frac = 0.5
        self._sent_frac = 0.5
        self._recentering = False
        self._recenter_speed = 0.0

    @property
    def sent_frac(self) -> float:
        return self._sent_frac

    def center(self) -> None:
        """回中, 通常连接后调(瞬间跳, 断开/退出前请用 recenter() 慢速回中)."""
        self._ema_frac = 0.5
        self._sent_frac = 0.5
        self._recentering = False
        self._head.set_servo_positions({self.cfg.channel: 0.5})

    def update(self, yaw_deg: Optional[float], dt: float) -> float:
        """推进一步. yaw_deg=None 表示"没有有效目标"(比如 GazeScheduler 处于
        idle), 这时候不发新指令、头停在原地不动, 跟
        sound_track_head.py 里"等待声音输入"时不发指令是同一个道理。

        返回当前(可能刚更新, 也可能没变)的 sent_frac, 方便调用方打印/画图。
        """
        if yaw_deg is None:
            return self._sent_frac

        target_frac = yaw_to_frac(
            yaw_deg, fov_deg=self.cfg.fov_deg, invert=self.cfg.invert,
            min_frac=self.cfg.min_frac, max_frac=self.cfg.max_frac,
        )
        self._ema_frac = self.cfg.smoothing * target_frac + (1 - self.cfg.smoothing) * self._ema_frac
        max_step = self.cfg.max_speed * dt
        self._sent_frac = _slew_limit(self._sent_frac, self._ema_frac, max_step)
        self._head.set_servo_positions({self.cfg.channel: round(self._sent_frac, 3)})
        return self._sent_frac

    def recenter(self, speed_scale: float = 0.4) -> None:
        """断开连接/退出前调用: 从头当前位置**慢速**回中, 而不是瞬间跳回.

        回中速度 = max_speed × speed_scale(默认 0.4, 比追踪更慢更稳);
        之后每帧调 `recenter_step(dt)` 推进, 返回是否已完成。
        """
        self._recentering = True
        self._recenter_speed = self.cfg.max_speed * speed_scale

    def recenter_step(self, dt: float) -> bool:
        """推进一次慢速回中, 返回是否已回到中间(0.5)."""
        if not self._recentering:
            return True
        delta = 0.5 - self._sent_frac
        step = max(-self._recenter_speed * dt, min(self._recenter_speed * dt, delta))
        self._sent_frac = round(self._sent_frac + step, 3)
        self._ema_frac = self._sent_frac
        self._head.set_servo_positions({self.cfg.channel: self._sent_frac})
        if abs(0.5 - self._sent_frac) < 1e-3:
            self._sent_frac = 0.5
            self._ema_frac = 0.5
            self._recentering = False
            self._head.set_servo_positions({self.cfg.channel: 0.5})
            return True
        return False


__all__ = ["HeadDriver", "HeadDriverConfig", "HeadClient", "yaw_to_frac"]
