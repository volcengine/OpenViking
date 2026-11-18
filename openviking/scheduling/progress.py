"""原生扫描循环的进度观测；只保存进程计数，不管理业务调度状态。"""

import time


class LoopProgress:
    def __init__(self, interval, clock=time.monotonic):
        self.interval = interval
        self.clock = clock
        self.started_at = clock()
        self.last_success = None
        self.failures = 0
        self.consecutive_failures = 0
        self.scanning = False
        self.oldest_due_age_seconds = 0

    def begin(self):
        self.scanning = True

    def succeeded(self):
        self.last_success = self.clock()
        self.consecutive_failures = 0
        self.scanning = False

    def failed(self):
        self.failures += 1
        self.consecutive_failures += 1
        self.scanning = False

    def snapshot(self):
        age = self.clock() - (
            self.last_success if self.last_success is not None else self.started_at
        )
        return {
            "last_success_age_seconds": max(0, age),
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "scanning": self.scanning,
            "oldest_due_age_at_scan_seconds": self.oldest_due_age_seconds,
            "healthy": self.consecutive_failures == 0 and age <= self.interval * 2 + 30,
        }
