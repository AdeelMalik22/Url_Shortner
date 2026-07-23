import pytest
from sqlalchemy.exc import IntegrityError

from app.api.v1.url_shortner.models import URL
from app.services import url_shortner
from app.utils.common import SHORT_CODE_ALPHABET, generate_code
from app.utils.db_connection import SessionLocal


def test_generate_code_uses_configured_length_and_alphabet():
    code = generate_code(16)

    assert len(code) == 16
    assert set(code) <= set(SHORT_CODE_ALPHABET)


def test_generate_code_rejects_invalid_length():
    with pytest.raises(ValueError):
        generate_code(0)


def test_create_short_url_retries_unique_code_collisions(monkeypatch):
    db = SessionLocal()
    duplicate = "A" * 10
    replacement = "B" * 10
    codes = iter((duplicate, replacement))

    db.add(URL(short_code=duplicate, original_url="https://existing.test"))
    db.commit()
    monkeypatch.setattr(
        url_shortner,
        "generate_code",
        lambda _: next(codes),
    )

    created = url_shortner.create_short_url(db, "https://example.com")

    assert created.short_code == replacement
    assert db.query(URL).count() == 2
    db.close()


def test_non_collision_integrity_errors_are_not_retried(monkeypatch):
    db = SessionLocal()
    monkeypatch.setattr(
        url_shortner,
        "generate_code",
        lambda _: "C" * 10,
    )

    with pytest.raises(IntegrityError):
        url_shortner.create_short_url(db, None)

    db.close()
