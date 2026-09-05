from __future__ import annotations

import threading
import time
from collections import deque

from app.providers.base import ProviderRateLimitError


class SlidingWindowRateLimiter:
    def __init__(self, calls_per_minute: int, daily_budget: int) -> None:
        self.limit = max(1, calls_per_minute)
        self.daily_budget = max(1, daily_budget)
        self.calls: deque[float] = deque()
        self.day = time.strftime("%Y-%m-%d")
        self.daily_calls = 0
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            today = time.strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.daily_calls = today, 0
            while self.calls and now - self.calls[0] >= 60:
                self.calls.popleft()
            if self.daily_calls >= self.daily_budget:
                raise ProviderRateLimitError("已达到今日 API 调用预算")
            if len(self.calls) >= self.limit:
                raise ProviderRateLimitError("API 请求过于频繁，请稍后重试")
            self.calls.append(now)
            self.daily_calls += 1
