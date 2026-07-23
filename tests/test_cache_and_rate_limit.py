from types import SimpleNamespace

from redis.exceptions import RedisError

from app.services import cache, rate_limiter


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.eval_result = [1, 99, 0]
        self.eval_args = None

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex):
        self.values[key] = value
        return ex > 0

    def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    def ping(self):
        return True

    def eval(self, *args):
        self.eval_args = args
        return self.eval_result


class FailingRedis:
    def __getattr__(self, _):
        raise RedisError("unavailable")


def setup_function():
    cache.reset_cache_resilience_state()
    rate_limiter.reset_rate_limiter_resilience_state()


def test_url_cache_round_trip(monkeypatch):
    redis = FakeRedis()
    settings = SimpleNamespace(redis_cache_ttl=60, redis_required=False)
    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    monkeypatch.setattr(cache, "get_cache_redis_client", lambda: redis)

    assert cache.cache_url("abc123", "https://example.com")
    lookup = cache.get_cached_url("abc123")
    assert lookup.hit
    assert lookup.original_url == "https://example.com"
    assert cache.invalidate_cached_url("abc123")
    assert not cache.get_cached_url("abc123").hit


def test_negative_url_cache(monkeypatch):
    redis = FakeRedis()
    settings = SimpleNamespace(
        redis_cache_ttl=60,
        redis_negative_cache_ttl=10,
    )
    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    monkeypatch.setattr(cache, "get_cache_redis_client", lambda: redis)

    assert cache.cache_missing_url("missing1")
    lookup = cache.get_cached_url("missing1")
    assert lookup.hit
    assert lookup.original_url is None


def test_cache_failure_is_non_fatal(monkeypatch):
    settings = SimpleNamespace(redis_cache_ttl=60, redis_required=True)
    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cache,
        "get_cache_redis_client",
        lambda: FailingRedis(),
    )

    assert not cache.get_cached_url("abc123").hit
    assert cache.cache_url("abc123", "https://example.com") is False
    assert cache.ping_cache() is False


def test_distributed_rate_limit_decision_and_hashed_key(monkeypatch):
    redis = FakeRedis()
    settings = SimpleNamespace(
        rate_limit_enabled=True,
        rate_limit_requests=100,
        rate_limit_window_seconds=60,
        rate_limit_fail_open=False,
    )
    monkeypatch.setattr(rate_limiter, "get_settings", lambda: settings)
    monkeypatch.setattr(
        rate_limiter,
        "get_rate_limit_redis_client",
        lambda: redis,
    )

    decision = rate_limiter.check_rate_limit("203.0.113.8", "shorten")

    assert decision.allowed
    assert decision.remaining == 99
    assert redis.eval_args[2].startswith("url-shortener:rate-limit:")
    assert "203.0.113.8" not in redis.eval_args[2]


def test_rate_limiter_failure_policy(monkeypatch):
    settings = SimpleNamespace(
        rate_limit_enabled=True,
        rate_limit_requests=100,
        rate_limit_window_seconds=60,
        rate_limit_fail_open=True,
    )
    monkeypatch.setattr(rate_limiter, "get_settings", lambda: settings)
    monkeypatch.setattr(
        rate_limiter,
        "get_rate_limit_redis_client",
        lambda: FailingRedis(),
    )

    assert rate_limiter.check_rate_limit("client", "shorten").allowed

    settings.rate_limit_fail_open = False
    decision = rate_limiter.check_rate_limit("client", "shorten")
    assert not decision.allowed
    assert decision.retry_after == 60
    assert not decision.available


def test_disabled_rate_limiter_is_marked_disabled(monkeypatch):
    settings = SimpleNamespace(
        rate_limit_enabled=False,
        rate_limit_requests=100,
        rate_limit_window_seconds=60,
        rate_limit_fail_open=True,
    )
    monkeypatch.setattr(rate_limiter, "get_settings", lambda: settings)

    decision = rate_limiter.check_rate_limit("client", "shorten")

    assert decision.allowed
    assert not decision.enabled
