"""
Minimal in-memory rate limiter — no Redis/external service needed, which
matters for a zero-cost single-process deployment. Good enough to blunt
brute-force login attempts; not meant for multi-process/multi-region scale.
"""

import time
from collections import defaultdict
from threading import Lock

_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def is_rate_limited(key: str, max_attempts: int = 8, window_seconds: int = 300) -> bool:
    now = time.time()
    with _lock:
        attempts = [t for t in _attempts[key] if now - t < window_seconds]
        _attempts[key] = attempts
        if len(attempts) >= max_attempts:
            return True
        attempts.append(now)
        return False
