from sqlalchemy import BigInteger, Column, Integer, String

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
