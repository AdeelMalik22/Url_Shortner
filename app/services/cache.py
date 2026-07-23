"""Redis-backed cache for resolved short URLs."""

from dataclasses import dataclass
import logging
from threading import Lock
from time import monotonic

from redis.exceptions import RedisError

from app.config import get_settings
from app.utils.redis_client import get_cache_redis_client


logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "url-shortener:url:"
_MISSING_VALUE = "!snaplink:not-found:v1!"
_REDIS_OPERATION_ERRORS = (RedisError, OSError, ValueError)
_FAILURE_COOLDOWN_SECONDS = 5.0
_WARNING_INTERVAL_SECONDS = 60.0
_state_lock = Lock()
_unavailable_until = 0.0
_last_warning_at = float("-inf")


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """A positive hit, negative hit, or cache miss/unavailable result."""

    hit: bool
    original_url: str | None


def _cache_key(code: str) -> str:
    return f"{_CACHE_KEY_PREFIX}{code}"


def _cache_enabled() -> bool:
    return int(get_settings().redis_cache_ttl) > 0


def _attempt_allowed() -> bool:
    with _state_lock:
        return monotonic() >= _unavailable_until


def _record_cache_success() -> None:
    global _unavailable_until

    with _state_lock:
        _unavailable_until = 0.0


def _record_cache_failure(operation: str) -> None:
    """Open a short circuit and rate-limit credential-free warning logs."""

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
        logger.warning(
            "Redis URL cache unavailable during %s; bypassing cache briefly",
            operation,
        )


def get_cached_url(code: str) -> CacheLookup:
    """Return a positive/negative hit, or a miss when Redis is unavailable."""

    if not _cache_enabled() or not _attempt_allowed():
        return CacheLookup(hit=False, original_url=None)

    try:
        value = get_cache_redis_client().get(_cache_key(code))
        _record_cache_success()
        if value == _MISSING_VALUE:
            return CacheLookup(hit=True, original_url=None)
        if isinstance(value, str):
            return CacheLookup(hit=True, original_url=value)
        return CacheLookup(hit=False, original_url=None)
    except _REDIS_OPERATION_ERRORS:
        _record_cache_failure("read")
        return CacheLookup(hit=False, original_url=None)


def cache_url(code: str, original_url: str) -> bool:
    """Cache a URL mapping for the configured TTL.

    The boolean result reports whether Redis accepted the value. In optional
    Redis mode an unavailable cache returns ``False`` instead of interrupting
    the request.
    """

    settings = get_settings()
    if int(settings.redis_cache_ttl) <= 0 or not _attempt_allowed():
        return False

    try:
        result = get_cache_redis_client().set(
            _cache_key(code),
            original_url,
            ex=int(settings.redis_cache_ttl),
        )
        _record_cache_success()
        return bool(result)
    except _REDIS_OPERATION_ERRORS:
        _record_cache_failure("write")
        return False


def cache_missing_url(code: str) -> bool:
    """Briefly cache a not-found result to absorb repeated invalid lookups."""

    settings = get_settings()
    ttl = int(settings.redis_negative_cache_ttl)
    if (
        int(settings.redis_cache_ttl) <= 0
        or ttl <= 0
        or not _attempt_allowed()
    ):
        return False

    try:
        result = get_cache_redis_client().set(
            _cache_key(code),
            _MISSING_VALUE,
            ex=ttl,
        )
        _record_cache_success()
        return bool(result)
    except _REDIS_OPERATION_ERRORS:
        _record_cache_failure("negative write")
        return False


def invalidate_cached_url(code: str) -> bool:
    """Remove a cached mapping, returning whether a key was deleted."""

    if not _cache_enabled() or not _attempt_allowed():
        return False

    try:
        result = bool(get_cache_redis_client().delete(_cache_key(code)))
        _record_cache_success()
        return result
    except _REDIS_OPERATION_ERRORS:
        _record_cache_failure("invalidation")
        return False


def ping_cache() -> bool:
    """Report whether Redis is reachable without exposing connection details."""

    try:
        result = bool(get_cache_redis_client().ping())
        if result:
            _record_cache_success()
        return result
    except _REDIS_OPERATION_ERRORS:
        _record_cache_failure("ping")
        return False


def reset_cache_resilience_state() -> None:
    """Reset the process-local circuit; intended for lifecycle/tests."""

    global _last_warning_at, _unavailable_until
    with _state_lock:
        _unavailable_until = 0.0
        _last_warning_at = float("-inf")
