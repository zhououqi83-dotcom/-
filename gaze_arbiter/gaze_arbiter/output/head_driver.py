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


def frac_to_yaw(frac: float, *, fov_deg: float, invert: bool,
                min_frac: float, max_frac: float) -> float:
    """`yaw_to_frac` 的逆运算: 舵机归一化值 -> 本体系 yaw_deg.

    眼动优先那套逻辑要知道"头现在实际朝向哪个角度", 才能算出目标相对头还偏了
    多少(residual)。头的当前位置只有 `_sent_frac` 这个归一化值, 所以需要反推。
    """
    if fov_deg <= 0:
        raise ValueError("fov_deg 必须 > 0")
    span = max_frac - min_frac
    raw = (frac - min_frac) / span if abs(span) > 1e-9 else 0.5
    raw = _clamp01(raw)
    if invert:
        raw = 1.0 - raw
    return raw * fov_deg - fov_deg / 2.0


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

    # ── 眼动优先(小幅度只转眼珠, 大幅度眼珠回中再转头) ──────────────────
    # 模仿人的注视方式: 目标只偏一点点时人不会扭脖子, 只瞟一眼; 偏得多了才
    # 转头, 同时眼珠回到正中(不然会变成"斗鸡眼盯着看"的怪表情)。
    eye_first: bool = True
    eye_channels: tuple = ("left_eye_level", "right_eye_level")
    # 两只眼的 level 通道共用同一套约定(见 servo_tuning/config/ULA_new.yaml:
    # 0=最右, 0.5=正中, 1=最左), 所以左右眼直接给同一个值就是共轭注视, 不用分开算。
    eye_only_deg: float = 12.0    # 残差小于它 -> 只动眼珠, 头不动
    head_engage_deg: float = 20.0  # 残差大于它 -> 眼珠回中, 头转过去
    # 两个阈值之间是滞回带: 维持当前模式不变, 避免残差在阈值附近抖动时
    # "转头/转眼"来回切换(跟 max_speed 限速是两回事, 那个防的是速度突变)。
    eye_fov_deg: float = 40.0     # 眼珠满行程对应的视角范围(±20°, 正好等于 head_engage_deg)
    eye_min_frac: float = 0.2     # 同样别设到 0/1, 避免撞眼珠舵机的硬限位
    eye_max_frac: float = 0.8
    eye_smoothing: float = 0.5    # 眼珠比头灵活, 平滑可以更弱(响应更快)
    eye_max_speed: float = 2.0    # 眼珠转速上限, 比头快得多(归一化值/秒)


class HeadDriver:
    """管一路舵机通道的平滑跟随. 不管连接/断线这些生命周期以外的事."""

    def __init__(self, head_client: HeadClient, config: Optional[HeadDriverConfig] = None) -> None:
        self._head = head_client
        self.cfg = config if config is not None else HeadDriverConfig()
        self._ema_frac = 0.5
        self._sent_frac = 0.5
        self._recentering = False
        self._recenter_speed = 0.0
        self._eye_frac = 0.5
        self._eye_ema = 0.5
        self._mode = "eye"   # "eye"=只动眼珠 / "head"=眼珠回中+转头

    @property
    def sent_frac(self) -> float:
        return self._sent_frac

    @property
    def eye_frac(self) -> float:
        return self._eye_frac

    @property
    def mode(self) -> str:
        """当前是"只动眼珠"还是"转头", 方便调用方在页面/日志上显示."""
        return self._mode

    @property
    def head_yaw_deg(self) -> float:
        """头(颈部舵机)当前实际朝向的本体系角度(把 sent_frac 换算回度数,
        是 `update()` 里算 residual 用的 head_yaw_now 那套换算的公开版本)。

        只是头这一节的角度——**摄像头实际在看哪不能只用这个**, 因为摄像头
        是装在眼球机构里的, 眼球转动会带着摄像头一起转(不是单纯装饰性的假
        眼珠)。要拿"摄像头现在到底朝哪"用 `camera_yaw_deg`。
        """
        return frac_to_yaw(
            self._sent_frac, fov_deg=self.cfg.fov_deg, invert=self.cfg.invert,
            min_frac=self.cfg.min_frac, max_frac=self.cfg.max_frac,
        )

    @property
    def eye_yaw_deg(self) -> float:
        """眼珠相对头的本体系角度偏移(把 eye_frac 换算回度数)。
        eye_first 关掉时 eye_frac 恒为 0.5(中), 这里自然算出 0°。"""
        return frac_to_yaw(
            self._eye_frac, fov_deg=self.cfg.eye_fov_deg, invert=self.cfg.invert,
            min_frac=self.cfg.eye_min_frac, max_frac=self.cfg.eye_max_frac,
        )

    @property
    def camera_yaw_deg(self) -> float:
        """摄像头当前实际朝向的本体系角度 = 头角度 + 眼相对头的角度。

        给上游(face_source.py::observe_face_track)用: 人脸框位置换算出来的
        角度是"相对当前摄像头光轴"的——摄像头没偏(头正、眼也正)时光轴=正前方
        =0°没问题; 但头转开或者眼动优先接管让眼珠转开之后, 光轴跟着偏了,
        画面里其他人的方位角不加上这个总偏移量就会算错。
        """
        return self.head_yaw_deg + self.eye_yaw_deg

    def _eye_positions(self) -> dict:
        """眼珠两个通道的指令. eye_first 关掉时返回空字典(完全不碰眼珠舵机)."""
        if not self.cfg.eye_first:
            return {}
        return {ch: round(self._eye_frac, 3) for ch in self.cfg.eye_channels}

    def recenter_eyes(self) -> None:
        """把眼珠单独强制拉回正中, 不动头. 运行中把 eye_first 从开切到关之前调一次——
        `_eye_positions()` 关掉后就不会再发眼珠指令了, 不主动收一次的话, 眼珠会停留在
        切换那一刻偏着的位置, 看起来像"斗鸡眼"卡住。"""
        self._eye_frac = 0.5
        self._eye_ema = 0.5
        if self.cfg.eye_channels:
            self._head.set_servo_positions({ch: 0.5 for ch in self.cfg.eye_channels})

    def center(self) -> None:
        """回中, 通常连接后调(瞬间跳, 断开/退出前请用 recenter() 慢速回中)."""
        self._ema_frac = 0.5
        self._sent_frac = 0.5
        self._recentering = False
        self._eye_frac = 0.5
        self._eye_ema = 0.5
        self._mode = "eye"
        positions = {self.cfg.channel: 0.5}
        positions.update(self._eye_positions())   # 眼珠也一起回正, 别留在偏着的位置
        self._head.set_servo_positions(positions)

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

        if not self.cfg.eye_first:
            # 原来的行为: 只转头, 不碰眼珠.
            self._ema_frac = self.cfg.smoothing * target_frac + (1 - self.cfg.smoothing) * self._ema_frac
            self._sent_frac = _slew_limit(self._sent_frac, self._ema_frac, self.cfg.max_speed * dt)
            self._head.set_servo_positions({self.cfg.channel: round(self._sent_frac, 3)})
            return self._sent_frac

        # 目标相对"头当前实际朝向"还偏多少度 —— 眼珠该不该接管看的是这个残差,
        # 而不是目标的绝对角度(头已经转过去了的话, 残差自然就小了)。
        head_yaw_now = frac_to_yaw(
            self._sent_frac, fov_deg=self.cfg.fov_deg, invert=self.cfg.invert,
            min_frac=self.cfg.min_frac, max_frac=self.cfg.max_frac,
        )
        residual = yaw_deg - head_yaw_now

        # 滞回: 只有明确越过某一侧的阈值才换模式, 中间带保持原样.
        if abs(residual) >= self.cfg.head_engage_deg:
            self._mode = "head"
        elif abs(residual) <= self.cfg.eye_only_deg:
            self._mode = "eye"

        eye_step = self.cfg.eye_max_speed * dt
        if self._mode == "eye":
            # 头原地不动(_ema 也跟着钉在当前位置, 不然模式切回来时会先"补"上
            # 这段时间里 EMA 偷偷累积的位移, 表现成头突然自己动一下)。
            self._ema_frac = self._sent_frac
            eye_target = yaw_to_frac(
                residual, fov_deg=self.cfg.eye_fov_deg, invert=self.cfg.invert,
                min_frac=self.cfg.eye_min_frac, max_frac=self.cfg.eye_max_frac,
            )
        else:
            # 转头模式: 头去追目标, 眼珠同时慢慢回到正中.
            self._ema_frac = self.cfg.smoothing * target_frac + (1 - self.cfg.smoothing) * self._ema_frac
            self._sent_frac = _slew_limit(self._sent_frac, self._ema_frac, self.cfg.max_speed * dt)
            eye_target = 0.5

        self._eye_ema = self.cfg.eye_smoothing * eye_target + (1 - self.cfg.eye_smoothing) * self._eye_ema
        self._eye_frac = _slew_limit(self._eye_frac, self._eye_ema, eye_step)

        # 头和眼珠合成一条指令一起发: 舵机板那边是 9600 baud, 每多发一帧就多
        # 占一次带宽(2026-08-20 排查抽搐时确认过发太密会写失败), 能合并就合并。
        positions = {self.cfg.channel: round(self._sent_frac, 3)}
        positions.update(self._eye_positions())
        self._head.set_servo_positions(positions)
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
        # 眼珠跟着一起回中, 而且走得比头快(退出时先把眼神收回来更自然)
        self._eye_frac = _slew_limit(self._eye_frac, 0.5, self.cfg.eye_max_speed * dt)
        self._eye_ema = self._eye_frac
        positions = {self.cfg.channel: self._sent_frac}
        positions.update(self._eye_positions())
        self._head.set_servo_positions(positions)
        if abs(0.5 - self._sent_frac) < 1e-3:
            self._sent_frac = 0.5
            self._ema_frac = 0.5
            self._eye_frac = 0.5
            self._eye_ema = 0.5
            self._mode = "eye"
            self._recentering = False
            positions = {self.cfg.channel: 0.5}
            positions.update(self._eye_positions())
            self._head.set_servo_positions(positions)
            return True
        return False


__all__ = ["HeadDriver", "HeadDriverConfig", "HeadClient", "yaw_to_frac", "frac_to_yaw"]
