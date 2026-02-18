from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class RetryConfig:
    """
    attempts: total attempts INCLUDING the first try (>=1)
    """
    attempts: int = 3
    base_delay_s: float = 0.4
    max_delay_s: float = 3.0
    jitter: float = 0.15  # +/- 15%


def compute_backoff_delay_s(cfg: RetryConfig, n: int) -> float:
    """
    n=1 for first retry delay, n=2 for second, ...
    """
    delay = min(cfg.max_delay_s, cfg.base_delay_s * (2 ** (n - 1)))
    j = 1.0 + random.uniform(-cfg.jitter, cfg.jitter)
    return max(0.0, delay * j)


def backoff_sleep(cfg: RetryConfig, n: int) -> float:
    d = compute_backoff_delay_s(cfg, n)
    time.sleep(d)
    return d


def is_retryable_message(msg: str) -> bool:
    s = (msg or "").lower()
    return any(k in s for k in [
        "status\":429", "status\": 429", "status=429", " 429 ",
        "status\":500", "status\": 500", "status=500", " 500 ",
        "status\":502", "status\": 502", "status=502", " 502 ",
        "status\":503", "status\": 503", "status=503", " 503 ",
        "status\":504", "status\": 504", "status=504", " 504 ",
        "timeout", "timed out",
        "connection reset", "temporarily unavailable",
        "rate limit", "too many requests",
        "service unavailable", "bad gateway", "gateway timeout",
    ])


def call_with_retries(
    fn: Callable[..., Any],
    *,
    args: dict,
    cfg: RetryConfig,
    is_retryable: Callable[[Exception], bool],
    on_retry: Optional[Callable[[int, int, Exception, float], None]] = None,
) -> Any:
    last_err: Optional[Exception] = None

    attempts = max(1, int(cfg.attempts))
    for i in range(attempts):
        try:
            return fn(**args)
        except Exception as e:
            last_err = e
            if i >= (attempts - 1) or not is_retryable(e):
                raise

            # Calculate retry delay: first failure uses n=1 for exponential backoff
            sleep_s = backoff_sleep(cfg, n=i + 1)
            if on_retry:
                # Report failed attempt number (1-based indexing for user clarity)
                on_retry(i + 1, attempts, e, float(sleep_s))

    raise last_err  # pragma: no cover
