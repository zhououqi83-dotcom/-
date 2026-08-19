"""sound_track_head.py — 让机器人头随 J7034G4 麦克风阵列测出的声源方向转动.

数据源: sound_localization/pc_tool/j7034g4_monitor.py 同一块 J7034G4 麦克风阵列板,
        板载 DSP 直接从串口吐 "doa_angle = X" 文本(0~180°, 单线阵天生分不清前后,
        细节见 sound_localization/README.md 和 fusion_360.py 开头注释)。
执行端: servo_tuning 那套头部舵机链路——head_grpc_server.py(终端1, USB 串口
        直连头部舵机控制板, gRPC :2543)+ head_sdk.HeadSDK 客户端。
        用的舵机通道是 head_yao("摇头" = 左右摆动 = yaw), 不是 head_dian(点头/俯仰)
        或 head_bai(摆头/侧倾), 具体 id/量程见
        head-sdk-face/head-server/src/servoConfig_25DV3_Ula.yaml。

前提(跟 servo_tuning/使用说明.md 一致):
  终端1 已经在跑 head_grpc_server.py(USB 连好头部, gRPC :2543 可连)。
  J7034G4 板子已经用 USB 转串口接到这台电脑上, 知道是哪个 COM 口。

用法:
  conda activate face_servo
  python sound_track_head.py --mic-port COM7

  # 如果发现头转的方向反了(比如声音在左边, 头却转向右边), 加 --invert
  python sound_track_head.py --mic-port COM7 --invert

  # 如果 0.5 中心跟头实际"正对前方"对不上, 用 --offset-deg 微调
  python sound_track_head.py --mic-port COM7 --offset-deg 15

  # 嫌头转得太猛/太快, 调小 --max-speed(默认 0.6, 单位见参数说明)
  python sound_track_head.py --mic-port COM7 --max-speed 0.3

Ctrl+C 退出, 退出前把头回中。
"""
from __future__ import annotations

import argparse
import re
import threading
import time

import serial
from head_sdk import HeadSDK

DOA_PATTERN = re.compile(r"doa_angle\s*=\s*(-?\d+)")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

HEAD_YAO_CHANNEL = "head_yao"  # 左右摇头 (yaw), 见 servoConfig_25DV3_Ula.yaml id=202


class DoaReader:
    """后台线程读 J7034G4 串口, 只保留最新一个 doa_angle 和收到时间."""

    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = baud
        self._lock = threading.Lock()
        self._angle: float | None = None
        self._t = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="doa-reader", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def latest(self) -> tuple[float | None, float]:
        with self._lock:
            return self._angle, self._t

    def _run(self) -> None:
        ser = serial.Serial(self.port, self.baud, timeout=1)
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
                    text = ANSI_ESCAPE.sub("", line.decode("utf-8", errors="ignore"))
                    m = DOA_PATTERN.search(text)
                    if m:
                        with self._lock:
                            self._angle = float(m.group(1))
                            self._t = time.monotonic()
        finally:
            ser.close()


def angle_to_frac(angle: float, *, invert: bool, offset_deg: float,
                   min_frac: float, max_frac: float) -> float:
    """doa_angle(0~180) -> head_yao 归一化值(min_frac~max_frac), 0.5 附近=正对前方."""
    a = (angle + offset_deg) % 180.0
    frac = a / 180.0
    if invert:
        frac = 1.0 - frac
    return min_frac + frac * (max_frac - min_frac)


def frac_to_angle(frac: float, *, invert: bool, offset_deg: float,
                   min_frac: float, max_frac: float) -> float:
    """angle_to_frac 的反函数: head_yao 归一化值 -> 等效的 doa 角度(0~180),
    只为了在仪表盘上把"头现在转到哪"和"声音在哪"画在同一把尺子上, 方便肉眼对比."""
    span = max_frac - min_frac
    x = (frac - min_frac) / span if span else 0.5
    x = min(1.0, max(0.0, x))
    if invert:
        x = 1.0 - x
    return (x * 180.0 - offset_deg) % 180.0


def slew_limit(current: float, target: float, max_step: float) -> float:
    """限制每次最多能往 target 靠近 max_step, 防止声源方向突变时头猛地甩过去.
    跟 --smoothing 的区别: smoothing 是滤掉读数里的抖动噪声(信号层面),
    这个是限制舵机实际移动速度的硬上限(执行层面), 两者互不替代。"""
    delta = target - current
    if delta > max_step:
        delta = max_step
    elif delta < -max_step:
        delta = -max_step
    return current + delta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mic-port", default="COM7", help="J7034G4 串口号")
    parser.add_argument("--mic-baud", type=int, default=115200)
    parser.add_argument("--host", default="127.0.0.1",
                        help="head_grpc_server.py 所在地址, 本机 USB 直连场景默认 127.0.0.1")
    parser.add_argument("--sdk-port", type=int, default=2543)
    parser.add_argument("--invert", action="store_true",
                        help="头转的方向跟声源反了就加这个")
    parser.add_argument("--offset-deg", type=float, default=0.0,
                        help="0° 跟'正对前方'对不上时的角度微调")
    parser.add_argument("--min-frac", type=float, default=0.15,
                        help="head_yao 归一化下限, 别设到 0 避免撞硬限位")
    parser.add_argument("--max-frac", type=float, default=0.85,
                        help="head_yao 归一化上限, 别设到 1 避免撞硬限位")
    parser.add_argument("--rate-hz", type=float, default=12.0, help="发指令频率")
    parser.add_argument("--smoothing", type=float, default=0.25,
                        help="指数平滑系数 alpha (0~1), 越小越平滑但越滞后")
    parser.add_argument("--max-speed", type=float, default=0.6,
                        help="头部转动最高速度, 单位: head_yao 归一化值/秒 "
                             "(min_frac~max_frac 区间宽度一般是 0.7, 默认 0.6/秒 "
                             "意味着从一头转到另一头大约需要 1.2 秒), 调小转得更慢更稳")
    parser.add_argument("--stale-s", type=float, default=2.0,
                        help="超过这么久没收到新 doa_angle, 就停止发指令(头停在原地)")
    parser.add_argument("--gui", action="store_true",
                        help="打开图形化仪表盘(声音方向 vs 头部朝向), 而不是纯文字刷屏")
    args = parser.parse_args()

    print(f">> 连接头部舵机服务 {args.host}:{args.sdk_port} ...")
    head = HeadSDK(args.host, sdk_port=args.sdk_port)
    if not head.is_connected():
        print("✗ 连不上头部舵机服务, 检查终端1的 head_grpc_server.py 是不是还在跑")
        return 1
    head.release_control()
    print("✓ 已连接, 头回中...")
    head.set_servo_positions({HEAD_YAO_CHANNEL: 0.5})
    time.sleep(0.5)

    print(f">> 打开麦克风阵列串口 {args.mic_port} @ {args.mic_baud} ...")
    doa = DoaReader(args.mic_port, args.mic_baud)
    doa.start()

    print(">> 开始跟踪声源方向, Ctrl+C 停止" if not args.gui else ">> 打开图形化仪表盘, 关闭窗口或 Ctrl+C 停止")
    try:
        if args.gui:
            run_gui_loop(head, doa, args)
        else:
            run_text_loop(head, doa, args)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n>> 停止, 头回中...")
        try:
            head.set_servo_positions({HEAD_YAO_CHANNEL: 0.5})
            time.sleep(0.3)
        except Exception:  # noqa: BLE001
            pass
        doa.stop()
        head.disconnect()
    return 0


def run_text_loop(head: HeadSDK, doa: DoaReader, args) -> None:
    ema_frac = 0.5
    sent_frac = 0.5
    dt = 1.0 / args.rate_hz
    max_step = args.max_speed * dt
    while True:
        angle, t = doa.latest()
        fresh = angle is not None and (time.monotonic() - t) < args.stale_s
        if fresh:
            target_frac = angle_to_frac(
                angle, invert=args.invert, offset_deg=args.offset_deg,
                min_frac=args.min_frac, max_frac=args.max_frac,
            )
            ema_frac = args.smoothing * target_frac + (1 - args.smoothing) * ema_frac
            sent_frac = slew_limit(sent_frac, ema_frac, max_step)
            head.set_servo_positions({HEAD_YAO_CHANNEL: round(sent_frac, 3)})
            print(f"\rdoa={angle:6.1f}°  head_yao={sent_frac:.2f}        ", end="", flush=True)
        else:
            print("\r等待声音输入...                          ", end="", flush=True)
        time.sleep(dt)


def run_gui_loop(head: HeadSDK, doa: DoaReader, args) -> None:
    """图形化仪表盘: 极坐标图上同时画'声音方向'(doa_angle 原始读数)和
    '头部朝向'(head_yao 当前实际位置, 换算回同一把角度尺子), 方便肉眼确认
    头是不是真的转到了声音那个方向。跟 sound_localization/pc_tool/j7034g4_monitor.py
    是同一个极坐标画法, 只是多叠了一根"头部朝向"指针。"""
    import numpy as np
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation

    matplotlib.rcParams["font.sans-serif"] = [
        "PingFang SC", "Heiti SC", "Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False

    ema_frac = {"v": 0.5}
    sent_frac = {"v": 0.5}
    dt_ms = int(1000.0 / args.rate_hz)
    max_step = args.max_speed * (dt_ms / 1000.0)

    fig = plt.figure(figsize=(6.5, 6.5))
    fig.suptitle("声源方向 vs 头部朝向 (J7034G4 + head_yao)")
    ax = fig.add_subplot(1, 1, 1, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 1)
    ax.set_yticklabels([])

    doa_pointer, = ax.plot([0, 0], [0, 1], linewidth=3, color="tab:red", label="声音方向")
    head_pointer, = ax.plot([0, 0], [0, 0.8], linewidth=3, color="tab:blue", label="头部朝向")
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.1), fontsize=9)
    status_text = ax.text(
        0.5, -0.15, "等待数据...", transform=ax.transAxes, ha="center", fontsize=12,
    )

    def update(_frame):
        angle, t = doa.latest()
        fresh = angle is not None and (time.monotonic() - t) < args.stale_s

        if fresh:
            target_frac = angle_to_frac(
                angle, invert=args.invert, offset_deg=args.offset_deg,
                min_frac=args.min_frac, max_frac=args.max_frac,
            )
            ema_frac["v"] = args.smoothing * target_frac + (1 - args.smoothing) * ema_frac["v"]
            sent_frac["v"] = slew_limit(sent_frac["v"], ema_frac["v"], max_step)
            head.set_servo_positions({HEAD_YAO_CHANNEL: round(sent_frac["v"], 3)})

            theta_doa = np.deg2rad(angle)
            doa_pointer.set_data([theta_doa, theta_doa], [0, 1])

        # 头部朝向指针: 用 HeadSDK 后台同步线程拿到的真实当前位置(不是我们
        # 发出去的目标值), 这样如果舵机没跟上/被限位卡住, 指针也能如实反映。
        state = head.state
        head_frac = state.parameters.get(HEAD_YAO_CHANNEL, 0.5) if state else 0.5
        head_angle = frac_to_angle(
            head_frac, invert=args.invert, offset_deg=args.offset_deg,
            min_frac=args.min_frac, max_frac=args.max_frac,
        )
        theta_head = np.deg2rad(head_angle)
        head_pointer.set_data([theta_head, theta_head], [0, 0.8])

        if fresh:
            status_text.set_text(f"声音 doa={angle:.0f}°   头部 head_yao={head_frac:.2f}")
        else:
            status_text.set_text(f"等待声音输入...   头部 head_yao={head_frac:.2f}")

        return doa_pointer, head_pointer, status_text

    ani = animation.FuncAnimation(fig, update, interval=dt_ms, cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    raise SystemExit(main())
