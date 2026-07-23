from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.config import get_settings


settings = get_settings()
database_url = make_url(settings.database_url)

engine_options = {"pool_pre_ping": True}
health_engine_options = {"poolclass": NullPool}
if database_url.get_backend_name() == "sqlite":
    sqlite_connect_args = {"check_same_thread": False}
    engine_options["connect_args"] = sqlite_connect_args
    health_engine_options["connect_args"] = sqlite_connect_args
    if database_url.database in (None, "", ":memory:"):
        engine_options["poolclass"] = StaticPool
else:
    if database_url.get_backend_name() == "postgresql":
        engine_options["connect_args"] = {
            "connect_timeout": settings.db_connect_timeout
        }
        health_engine_options["connect_args"] = {
            "connect_timeout": settings.db_connect_timeout,
            "options": (
                "-c statement_timeout="
                f"{settings.db_health_statement_timeout_ms}"
            ),
        }
    engine_options.update(
        {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
            "pool_recycle": settings.db_pool_recycle,
        }
    )

engine = create_engine(settings.database_url, **engine_options)
health_engine = create_engine(
    settings.database_url,
    **health_engine_options,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
