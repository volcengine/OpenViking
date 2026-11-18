"""循环活着但不推进时不可Ready；空闲和六小时补扫按各自周期判断。"""

from openviking.scheduling.progress import LoopProgress


def test_progress_distinguishes_idle_failure_stall_and_recovery():
    now = [100.0]
    progress = LoopProgress(15, lambda: now[0])
    progress.begin()
    progress.succeeded()
    now[0] += 20
    assert progress.snapshot()["healthy"]
    progress.failed()
    assert progress.snapshot()["failures"] == 1
    now[0] += 50
    assert not progress.snapshot()["healthy"]
    progress.succeeded()
    assert progress.snapshot()["healthy"]
    assert progress.snapshot()["consecutive_failures"] == 0


def test_compensation_idle_is_not_a_short_poll_stall():
    now = [0.0]
    progress = LoopProgress(6 * 3600, lambda: now[0])
    progress.succeeded()
    now[0] = 4 * 3600
    assert progress.snapshot()["healthy"]
    now[0] = 13 * 3600
    assert not progress.snapshot()["healthy"]
