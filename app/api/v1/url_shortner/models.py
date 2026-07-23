from enum import Enum

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, func

from app.utils.db_connection import Base


class AccountPlan(str, Enum):
    FREE = "free"
    PREMIUM = "premium"


class URL(Base):
    __tablename__ = "urls"

    id = Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
    )

    short_code = Column(
        String,
        unique=True,
        index=True,
        nullable=False,
    )

    original_url = Column(
        String,
        nullable=False
    )

    user_id = Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True)
    first_name = Column(String(100), nullable=False, server_default="")
    last_name = Column(String(100), nullable=False, server_default="")
    username = Column(String(30), unique=True, index=True, nullable=False)
    email = Column(String(320), unique=True, index=True, nullable=False)
    avatar_filename = Column(String(64), nullable=True)
    password_hash = Column(String(256), nullable=False)
    plan = Column(
        String(16),
        nullable=False,
        default=AccountPlan.FREE.value,
        server_default=AccountPlan.FREE.value,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
