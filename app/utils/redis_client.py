"""Construction of the application's synchronous Redis client."""

from functools import lru_cache
import secrets

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings


_SOCKET_CONNECT_TIMEOUT_SECONDS = 0.5
_SOCKET_TIMEOUT_SECONDS = 0.5
_READINESS_KEY_PREFIX = "url-shortener:health:"
_REDIS_OPERATION_ERRORS = (RedisError, OSError, ValueError)
_READINESS_LUA = """
redis.call("SET", KEYS[1], "ready", "EX", 5)
local value = redis.call("GET", KEYS[1])
local count = redis.call("INCR", KEYS[2])
redis.call("EXPIRE", KEYS[2], 5)
local ttl = redis.call("TTL", KEYS[2])
redis.call("DEL", KEYS[1], KEYS[2])

if value == "ready" and count == 1 and ttl > 0 then
    return 1
end
return 0
"""


def _build_redis_client(url: str) -> Redis:
    settings = get_settings()
    return Redis.from_url(
        url,
        decode_responses=True,
        encoding="utf-8",
        socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        retry_on_timeout=False,
        health_check_interval=30,
        max_connections=settings.redis_max_connections,
    )


@lru_cache(maxsize=1)
def get_cache_redis_client() -> Redis:
    """Return the process-local URL-cache Redis client."""

    return _build_redis_client(get_settings().redis_cache_url)


@lru_cache(maxsize=1)
def get_rate_limit_redis_client() -> Redis:
    """Return the process-local distributed-limiter Redis client.

    ``redis-py`` clients are safe to share between threads. The client opens
    connections lazily, so importing this module does not make Redis a startup
    dependency.
    """

    settings = get_settings()
    if settings.redis_rate_limit_url == settings.redis_cache_url:
        return get_cache_redis_client()
    return _build_redis_client(settings.redis_rate_limit_url)


def redis_capabilities_ready(client: Redis) -> bool:
    """Verify the commands required by caching and distributed limiting.

    ``PING`` alone can succeed against a read-only or ACL-restricted server.
    This short-lived probe checks EVAL, SET, GET, INCR, EXPIRE, TTL, and DEL.
    """

    token = secrets.token_hex(12)
    # The hash tag keeps both probe keys in one slot on cluster-compatible
    # Redis deployments.
    key_prefix = f"{_READINESS_KEY_PREFIX}{{{token}}}"
    try:
        return bool(
            client.eval(
                _READINESS_LUA,
                2,
                f"{key_prefix}:value",
                f"{key_prefix}:counter",
            )
        )
    except _REDIS_OPERATION_ERRORS:
        return False


def clear_redis_client_cache() -> None:
    """Drop cached clients, closing their connection pools first.

    This is useful during application shutdown and in tests that replace
    settings between cases.
    """

    clients = []
    for factory in (get_cache_redis_client, get_rate_limit_redis_client):
        if factory.cache_info().currsize:
            client = factory()
            if all(client is not existing for existing in clients):
                clients.append(client)
    for client in clients:
        client.close()
    for factory in (get_cache_redis_client, get_rate_limit_redis_client):
        factory.cache_clear()
