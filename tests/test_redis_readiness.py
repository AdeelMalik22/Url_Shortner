from redis.exceptions import RedisError

from app.utils import redis_client


class CapabilityRedis:
    def __init__(self, result=1):
        self.result = result
        self.arguments = None

    def eval(self, *arguments):
        self.arguments = arguments
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_redis_readiness_exercises_required_commands():
    client = CapabilityRedis()

    assert redis_client.redis_capabilities_ready(client)
    assert "SET" in client.arguments[0]
    assert "INCR" in client.arguments[0]
    assert client.arguments[1] == 2


def test_redis_readiness_rejects_command_failure():
    client = CapabilityRedis(RedisError("read only"))

    assert not redis_client.redis_capabilities_ready(client)


def test_matching_role_urls_share_one_connection_pool(monkeypatch):
    shared_client = object()
    settings = type(
        "Settings",
        (),
        {
            "redis_cache_url": "redis://localhost/0",
            "redis_rate_limit_url": "redis://localhost/0",
        },
    )()
    redis_client.get_cache_redis_client.cache_clear()
    redis_client.get_rate_limit_redis_client.cache_clear()
    monkeypatch.setattr(redis_client, "get_settings", lambda: settings)
    monkeypatch.setattr(
        redis_client,
        "_build_redis_client",
        lambda _: shared_client,
    )

    try:
        assert redis_client.get_cache_redis_client() is shared_client
        assert redis_client.get_rate_limit_redis_client() is shared_client
    finally:
        redis_client.get_cache_redis_client.cache_clear()
        redis_client.get_rate_limit_redis_client.cache_clear()
