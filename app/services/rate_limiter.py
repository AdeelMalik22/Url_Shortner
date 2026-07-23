"""Atomic, distributed rate limiting backed by Redis."""

from dataclasses import dataclass
import hashlib
import logging
from threading import Lock
from time import monotonic
from typing import Any

from redis.exceptions import RedisError

from app.config import get_settings
from app.utils.redis_client import get_rate_limit_redis_client


logger = logging.getLogger(__name__)

_RATE_LIMIT_KEY_PREFIX = "url-shortener:rate-limit:"
_REDIS_OPERATION_ERRORS = (RedisError, OSError, ValueError)
_FAILURE_COOLDOWN_SECONDS = 5.0
_WARNING_INTERVAL_SECONDS = 60.0
_state_lock = Lock()
_unavailable_until = 0.0
_last_warning_at = float("-inf")

# Increment, initial expiry, and decision happen as one Redis transaction. The
# TTL repair protects against a key left without expiry by an interrupted or
# manually altered Redis state.
_FIXED_WINDOW_LUA = """
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local count = redis.call("INCR", KEYS[1])

if count == 1 then
    redis.call("EXPIRE", KEYS[1], window)
end

local ttl = redis.call("TTL", KEYS[1])
if ttl < 0 then
    redis.call("EXPIRE", KEYS[1], window)
    ttl = window
end

local allowed = 0
local remaining = 0
local retry_after = 0

if count <= limit then
    allowed = 1
    remaining = limit - count
else
    retry_after = math.max(ttl, 1)
end

return {allowed, remaining, retry_after}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """The result needed to enforce and describe a rate limit."""

    allowed: bool
    remaining: int
    retry_after: int
    available: bool = True
    enabled: bool = True


def _rate_limit_key(client_id: str, endpoint: str) -> str:
    # Hash identifiers to keep keys bounded and avoid placing client details in
    # Redis key listings or operational logs.
    identity = f"{client_id}\0{endpoint}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    return f"{_RATE_LIMIT_KEY_PREFIX}{digest}"


def _parse_decision(raw: Any) -> RateLimitDecision:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError("Unexpected Redis rate-limit response")

    return RateLimitDecision(
        allowed=bool(int(raw[0])),
        remaining=max(0, int(raw[1])),
        retry_after=max(0, int(raw[2])),
    )


def _attempt_allowed() -> bool:
    with _state_lock:
        return monotonic() >= _unavailable_until


def _record_success() -> None:
    global _unavailable_until
    with _state_lock:
        _unavailable_until = 0.0


def _record_failure(fail_open: bool) -> None:
    global _last_warning_at, _unavailable_until

    now = monotonic()
    with _state_lock:
        _unavailable_until = max(
            _unavailable_until,
            now + _FAILURE_COOLDOWN_SECONDS,
        )
        should_log = now - _last_warning_at >= _WARNING_INTERVAL_SECONDS
        if should_log:
            _last_warning_at = now

    if should_log:
        policy = "allowing" if fail_open else "rejecting"
        logger.warning(
            "Redis rate limiter unavailable; %s requests per configured policy",
            policy,
        )


def _unavailable_decision(
    *,
    fail_open: bool,
    limit: int,
    window: int,
) -> RateLimitDecision:
    return RateLimitDecision(
        allowed=fail_open,
        remaining=limit if fail_open else 0,
        retry_after=0 if fail_open else window,
        available=False,
    )


def check_rate_limit(
    client_id: str,
    endpoint: str,
    *,
    requests: int | None = None,
    window_seconds: int | None = None,
) -> RateLimitDecision:
    """Atomically consume one request from a client's endpoint-specific limit."""

    settings = get_settings()
    limit = requests if requests is not None else int(settings.rate_limit_requests)
    window = (
        window_seconds
        if window_seconds is not None
        else int(settings.rate_limit_window_seconds)
    )

    if not settings.rate_limit_enabled:
        return RateLimitDecision(
            allowed=True,
            remaining=limit,
            retry_after=0,
            enabled=False,
        )

    if not _attempt_allowed():
        return _unavailable_decision(
            fail_open=settings.rate_limit_fail_open,
            limit=limit,
            window=window,
        )

    try:
        raw = get_rate_limit_redis_client().eval(
            _FIXED_WINDOW_LUA,
            1,
            _rate_limit_key(client_id, endpoint),
            limit,
            window,
        )
        decision = _parse_decision(raw)
        _record_success()
        return decision
    except _REDIS_OPERATION_ERRORS:
        _record_failure(settings.rate_limit_fail_open)
        return _unavailable_decision(
            fail_open=settings.rate_limit_fail_open,
            limit=limit,
            window=window,
        )


def reset_rate_limiter_resilience_state() -> None:
    """Reset the process-local circuit; intended for lifecycle/tests."""

    global _last_warning_at, _unavailable_until
    with _state_lock:
        _unavailable_until = 0.0
        _last_warning_at = float("-inf")
