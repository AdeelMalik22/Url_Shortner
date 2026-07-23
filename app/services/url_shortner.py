from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.url_shortner.models import URL
from app.config import get_settings
from app.utils.common import generate_code


class ShortCodeAllocationError(RuntimeError):
    """Raised when a unique short code cannot be allocated after retries."""


def create_short_url(
    db: Session,
    original_url: str,
    user_id: int | None = None,
) -> URL:
    settings = get_settings()
    last_collision: IntegrityError | None = None

    for _ in range(settings.short_code_max_attempts):
        code = generate_code(settings.short_code_length)
        url = URL(
            short_code=code,
            original_url=original_url,
            user_id=user_id,
        )

        try:
            db.add(url)
            db.commit()
            return url
        except IntegrityError as exc:
            db.rollback()

            collision = db.scalar(
                select(URL.id).where(URL.short_code == code)
            )
            if collision is None:
                raise

            last_collision = exc

    raise ShortCodeAllocationError(
        "Could not allocate a unique short code"
    ) from last_collision
