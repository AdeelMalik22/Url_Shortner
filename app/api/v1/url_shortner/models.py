from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, func

from app.utils.db_connection import Base


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
    email = Column(String(320), unique=True, index=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
