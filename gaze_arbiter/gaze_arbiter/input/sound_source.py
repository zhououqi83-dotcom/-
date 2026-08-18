"""sound_source.py — 从 J7034G4 麦克风阵列板读实时声源方向, 转成 SoundContext.

复用 servo_tuning/sound_track_head.py 里 `DoaReader` 的思路(后台线程读串口,
只保留最新一条 doa_angle 读数, 那份代码已经在真实硬件上跑通), 换了个消费方:
不直接转舵机, 而是转成 `SoundContext` 喂给 `GazeScheduler`。

**只接了 J7034G4 单阵列**(0~180°, 前后镜像), 没接 sound_localization/
fusion_360.py 的双阵列 360° 融合方案 —— 这是故意简化, 不是漏掉:
gaze_arbiter 只需要在"已经出现在摄像头画面里的人"中间挑一个声音方向匹配的,
摄像头视野本身就只覆盖前方, 前后歧义在这个场景里不构成实际问题(不会有人
站在机器人身后还同时出现在摄像头画面里)。如果以后真的需要区分正前方 vs
正后方的声音(比如画面里没人但背后有人喊), 再接 fusion_360.py 那套。

跟 `head_driver.py` 一样的原则: 不在模块顶层 `import serial`, 只有真的启动
读串口线程时才 import —— 这样没装 pyserial 时, `doa_to_body_yaw` 这些纯函数
依然能被单独 import 和测试。
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from ..signals import SoundContext

DOA_PATTERN = re.compile(r"doa_angle\s*=\s*(-?\d+)")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def doa_to_body_yaw(doa_deg: float, *, offset_deg: float = 0.0, invert: bool = False) -> float:
    """J7034G4 的 doa_angle(0~180°, 90°=阵列连线正中) -> 机器人本体系 yaw_deg(0=正前).

    跟 sound_track_head.py 的 `--offset-deg` / `--invert` 是同一件事: 阵列
    摆放跟摄像头朝向没法做到分毫不差对齐, 用这两个参数实测校准。结果夹在
    [-90, 90] 内, 跟 Person.yaw_deg 的约定("左负右正")保持一致。
    """
    yaw = doa_deg - 90.0 + offset_deg
    if invert:
        yaw = -yaw
    return max(-90.0, min(90.0, yaw))


def extract_doa(line: str) -> Optional[float]:
    """从一行串口文本里提取 doa_angle 数值, 提取不到返回 None(比如开机乱码行)."""
    text = ANSI_ESCAPE.sub("", line)
    m = DOA_PATTERN.search(text)
    return float(m.group(1)) if m else None


@dataclass
class SoundSourceConfig:
    offset_deg: float = 0.0
    invert: bool = False
    stale_s: float = 2.0          # 超过这么久没收到新读数, 认为当前没有有效声音
    confidence: float = 0.9       # 固定置信度(J7034G4 板子本身不吐置信度/响度数值, 只有角度)


def context_from_raw(angle: Optional[float], t: float, *, now: float,
                     cfg: SoundSourceConfig) -> SoundContext:
    """纯函数版本的"最新读数 -> SoundContext", 跟真实的串口/线程解耦, 方便单测.

    `J7034DoaReader.latest_sound_context()` 只是拿当前时间调这个函数。
    """
    fresh = angle is not None and (now - t) < cfg.stale_s
    if not fresh:
        return SoundContext(doa_deg=None)
    yaw = doa_to_body_yaw(angle, offset_deg=cfg.offset_deg, invert=cfg.invert)
    return SoundContext(doa_deg=yaw, confidence=cfg.confidence)


class J7034DoaReader:
    """后台线程读 J7034G4 串口, 只保留最新一个 doa_angle 和收到时间.

    跟 `sound_track_head.py::DoaReader` 几乎一样(那份代码已经在真实硬件上
    跑通), 这里独立复制一份而不是导入 servo_tuning 目录下的脚本, 原因跟
    `head_driver.py` 不 import head_sdk 一样: 不想让 gaze_arbiter 产生对
    其他项目目录的路径依赖。
    """

    def __init__(self, port: str, baud: int = 115200) -> None:
        self.port = port
        self.baud = baud
        self._lock = threading.Lock()
        self._angle: Optional[float] = None
        self._t = 0.0
        self._error: Optional[str] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="j7034-doa-reader", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def latest_raw(self) -> tuple:
        with self._lock:
            return self._angle, self._t

    def latest_sound_context(self, cfg: SoundSourceConfig) -> SoundContext:
        angle, t = self.latest_raw()
        return context_from_raw(angle, t, now=time.monotonic(), cfg=cfg)

    def last_error(self) -> Optional[str]:
        """串口打不开/读取途中断线时的错误信息. None = 目前没出错(不代表连上了,
        没出错也可能只是还没收到过任何一行数据)。

        原版 sound_track_head.py::DoaReader 没有这个 —— 串口打不开时那个
        后台线程直接崩溃退出, 主循环只会一直显示"等待声音输入...", 排查起来
        容易以为是"没声音", 实际是"串口根本没打开"。这里补上, 让调用方能
        主动检查、给出明确提示, 而不是安静地卡住。
        """
        with self._lock:
            return self._error

    def _run(self) -> None:
        import serial  # 延迟 import: 没装 pyserial 时, 模块其余部分仍可用/可测
        try:
            ser = serial.Serial(self.port, self.baud, timeout=1)
        except Exception as e:  # noqa: BLE001 — 串口打不开, 记录下来让主线程能查
            with self._lock:
                self._error = str(e)
            return

        buf = b""
        try:
            while not self._stop.is_set():
                chunk = ser.read(256)
                if not chunk:
                    continue
                buf += chunk
                while b"\r\n" in buf or b"\r" in buf:
                    sep = b"\r\n" if b"\r\n" in buf else b"\r"
                    line, buf = buf.split(sep, 1)
                    angle = extract_doa(line.decode("utf-8", errors="ignore"))
                    if angle is not None:
                        with self._lock:
                            self._angle = angle
                            self._t = time.monotonic()
        except Exception as e:  # noqa: BLE001 — 读取途中断线(比如拔线)
            with self._lock:
                self._error = str(e)
        finally:
            ser.close()


__all__ = [
    "SoundSourceConfig",
    "J7034DoaReader",
    "doa_to_body_yaw",
    "extract_doa",
    "context_from_raw",
]
