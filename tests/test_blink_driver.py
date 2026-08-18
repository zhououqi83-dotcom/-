import random

from gaze_arbiter.output.blink_driver import BlinkDriver, BlinkDriverConfig


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


def test_reset_forces_eyes_open():
    client = FakeHeadClient()
    driver = BlinkDriver(client, BlinkDriverConfig(), rng=random.Random(0))
    driver.reset()
    assert client.last_positions["left_blink"] == 0.44
    assert client.last_positions["right_blink"] == 0.44


def test_head_moving_suppresses_new_blinks():
    cfg = BlinkDriverConfig(min_interval_s=0.0, max_interval_s=0.0)
    client = FakeHeadClient()
    driver = BlinkDriver(client, cfg, rng=random.Random(0))
    driver.reset()
    calls_before = client.call_count
    for _ in range(50):
        driver.update(dt=0.05, head_moving=True)  # 头一直在动, 不该触发眨眼
    assert client.call_count == calls_before


def test_idle_head_triggers_one_blink_that_settles_back_open():
    # 先等 min_interval_s=1.0s 才会触发第一次眨眼, 单次眨眼总耗时
    # close+hold+open = 0.25s, 跑 1.5s: 够等到第一次并眨完, 又不够长到
    # 触发第二次(下一次要再等满 1.0s)。
    cfg = BlinkDriverConfig(min_interval_s=1.0, max_interval_s=1.0, double_blink_prob=0.0)
    client = FakeHeadClient()
    driver = BlinkDriver(client, cfg, rng=random.Random(0))
    driver.reset()

    saw_closed = False
    for _ in range(75):  # 75 * 0.02s = 1.5s
        driver.update(dt=0.02, head_moving=False)
        if client.last_positions["left_blink"] >= cfg.closed_frac - 1e-9:
            saw_closed = True

    assert saw_closed, "应该在某一帧闭到底"
    assert abs(client.last_positions["left_blink"] - cfg.open_frac) < 1e-9
    assert abs(client.last_positions["right_blink"] - cfg.open_frac) < 1e-9


def test_in_progress_blink_is_not_interrupted_by_head_moving():
    cfg = BlinkDriverConfig(min_interval_s=0.0, max_interval_s=0.0, double_blink_prob=0.0)
    client = FakeHeadClient()
    driver = BlinkDriver(client, cfg, rng=random.Random(0))
    driver.reset()

    driver.update(dt=0.02, head_moving=False)  # 第一步只是从 IDLE 切到 CLOSING
    driver.update(dt=0.02, head_moving=False)  # 第二步才真正开始往闭眼方向发指令
    assert client.last_positions["left_blink"] > cfg.open_frac

    # 头从这里开始一直在动, 但这一次已经在进行中的眨眼应该继续眨完, 不会卡在
    # 半闭状态——中途应该能看到它回到过睁眼位置。
    saw_open_again = False
    for _ in range(20):
        driver.update(dt=0.02, head_moving=True)
        if abs(client.last_positions["left_blink"] - cfg.open_frac) < 1e-9:
            saw_open_again = True
    assert saw_open_again


def test_left_and_right_always_sent_together():
    cfg = BlinkDriverConfig(min_interval_s=0.0, max_interval_s=0.0, double_blink_prob=0.0)
    client = FakeHeadClient()
    driver = BlinkDriver(client, cfg, rng=random.Random(0))
    driver.reset()
    for _ in range(30):
        driver.update(dt=0.02, head_moving=False)
        assert client.last_positions["left_blink"] == client.last_positions["right_blink"]
