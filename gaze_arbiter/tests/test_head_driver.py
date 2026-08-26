from gaze_arbiter.output.head_driver import HeadDriver, HeadDriverConfig, yaw_to_frac


class FakeHeadClient:
    """假的 HeadSDK, 只记录最后一次收到的舵机指令, 不连真实硬件."""
    def __init__(self) -> None:
        self.last_positions: dict = {}
        self.call_count = 0

    def release_control(self) -> None:
        pass

    def set_servo_positions(self, positions: dict) -> None:
        self.last_positions.update(positions)
        self.call_count += 1

    def disconnect(self) -> None:
        pass


def test_yaw_to_frac_center_is_midpoint():
    frac = yaw_to_frac(0.0, fov_deg=90.0, invert=False, min_frac=0.15, max_frac=0.85)
    assert abs(frac - 0.5) < 1e-9


def test_yaw_to_frac_extremes_hit_bounds():
    left = yaw_to_frac(-45.0, fov_deg=90.0, invert=False, min_frac=0.15, max_frac=0.85)
    right = yaw_to_frac(45.0, fov_deg=90.0, invert=False, min_frac=0.15, max_frac=0.85)
    assert abs(left - 0.15) < 1e-9
    assert abs(right - 0.85) < 1e-9


def test_yaw_to_frac_out_of_fov_clamped_not_error():
    beyond = yaw_to_frac(200.0, fov_deg=90.0, invert=False, min_frac=0.15, max_frac=0.85)
    assert beyond == 0.85


def test_yaw_to_frac_invert_flips_sides():
    normal = yaw_to_frac(30.0, fov_deg=90.0, invert=False, min_frac=0.0, max_frac=1.0)
    inverted = yaw_to_frac(30.0, fov_deg=90.0, invert=True, min_frac=0.0, max_frac=1.0)
    assert abs((normal + inverted) - 1.0) < 1e-9


def test_head_driver_center_sends_midpoint():
    client = FakeHeadClient()
    driver = HeadDriver(client)
    driver.center()
    assert client.last_positions["head_yao"] == 0.5


def test_head_driver_idle_target_does_not_send_command():
    client = FakeHeadClient()
    driver = HeadDriver(client)
    driver.center()
    calls_before = client.call_count
    result = driver.update(None, dt=0.1)
    assert client.call_count == calls_before  # 没有目标, 不该多发一条指令
    assert result == 0.5


def test_head_driver_slew_limits_speed_towards_target():
    client = FakeHeadClient()
    cfg = HeadDriverConfig(smoothing=1.0, max_speed=0.5)  # smoothing=1 → 直接信任目标值, 只看限速
    driver = HeadDriver(client, cfg)
    driver.center()  # sent_frac = 0.5
    # 目标在最右(yaw=+45, fov=90 → frac=1.0), dt=0.1s, max_speed=0.5/s → 每步最多挪 0.05
    sent = driver.update(45.0, dt=0.1)
    assert abs(sent - 0.55) < 1e-9
    assert client.last_positions["head_yao"] == 0.55


def test_head_driver_converges_over_multiple_steps():
    client = FakeHeadClient()
    cfg = HeadDriverConfig(smoothing=1.0, max_speed=2.0)  # 速度足够快, 应该能在几步内跟上
    driver = HeadDriver(client, cfg)
    driver.center()
    last = 0.5
    for _ in range(20):
        last = driver.update(45.0, dt=0.1)
    assert abs(last - 0.85) < 1e-6  # 默认 min/max_frac 是 0.15~0.85, 45° 是右边极限


def test_head_driver_recenter_slowly_returns_to_center():
    """断开连接时的慢速回中: 不是瞬间跳回, 分多步回到 0.5, 完成后才报告结束."""
    client = FakeHeadClient()
    cfg = HeadDriverConfig(smoothing=1.0, max_speed=2.0)
    driver = HeadDriver(client, cfg)
    driver.center()
    for _ in range(20):
        driver.update(45.0, dt=0.1)  # 先转到最右(0.85)
    assert abs(driver.sent_frac - 0.85) < 1e-3

    driver.recenter()
    steps = 0
    while not driver.recenter_step(0.1):
        steps += 1
        assert steps < 200, "回中卡住了"
    assert steps >= 3  # 分多步, 不是瞬间跳回
    assert abs(driver.sent_frac - 0.5) < 1e-3
    assert client.last_positions["head_yao"] == 0.5


def test_head_driver_no_eye_first_ignores_small_residual():
    """eye_first 关掉时, 残差在死区(head_dead_zone_deg)以内应该完全不动头,
    不发指令——否则人脸检测的小幅抖动会被 100% 转成头部动作, 显得"过度扭头"。"""
    client = FakeHeadClient()
    cfg = HeadDriverConfig(eye_first=False, smoothing=1.0, max_speed=2.0,
                          head_dead_zone_deg=5.0)
    driver = HeadDriver(client, cfg)
    driver.center()
    client.call_count = 0  # center() 本身会发一次, 归零后只看 update() 的行为

    # fov=90° 时 4° 残差 < 5° 死区, 应该完全不发指令、位置不变
    sent = driver.update(4.0, dt=0.1)
    assert sent == 0.5
    assert client.call_count == 0


def test_head_driver_no_eye_first_still_tracks_beyond_dead_zone():
    """死区以外(残差 > head_dead_zone_deg)要正常追过去, 死区只挡小幅度,
    不是把头完全锁死。"""
    client = FakeHeadClient()
    cfg = HeadDriverConfig(eye_first=False, smoothing=1.0, max_speed=2.0,
                          head_dead_zone_deg=5.0)
    driver = HeadDriver(client, cfg)
    driver.center()

    sent = driver.update(20.0, dt=0.5)  # 残差 20° > 5° 死区
    assert sent > 0.5
    assert abs(client.last_positions["head_yao"] - sent) < 1e-3  # 发送值四舍五入到3位小数
