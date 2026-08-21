from __future__ import annotations

from tara_web.api.middleware.rate_limit import RateLimiter


def test_category_global_reset_and_bounded_memory() -> None:
    now = [0.0]
    limiter = RateLimiter(
        limits={"global": 2, "polling": 5, "invalid_secret": 1},
        window_seconds=10,
        maximum_keys=3,
        clock=lambda: now[0],
    )
    assert limiter.allow("polling", "one") is None
    assert limiter.allow("invalid_secret", "one") is None
    assert limiter.allow("polling", "one") == 10
    now[0] = 10
    assert limiter.allow("polling", "one") is None
    for identity in ("two", "three", "four"):
        limiter.allow("polling", identity)
    assert len(limiter._items) <= 3


def test_category_can_use_a_dedicated_global_bucket() -> None:
    limiter = RateLimiter(
        limits={
            "global": 1,
            "polling": 5,
            "upload_chunk": 3,
            "upload_chunk_global": 4,
        },
        window_seconds=60,
    )
    assert limiter.allow("polling", "browser") is None
    assert limiter.allow("polling", "browser") == 60
    assert limiter.allow("upload_chunk", "browser") is None
    assert limiter.allow("upload_chunk", "browser") is None
    assert limiter.allow("upload_chunk", "browser") is None
    assert limiter.allow("upload_chunk", "browser") == 60
