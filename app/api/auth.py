"""Local account and private dashboard endpoints."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.url_shortner.models import URL, User
from app.config import get_settings
from app.services.auth import hash_password, verify_password
from app.utils.db_connection import get_db

router = APIRouter(tags=["accounts"])
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not _EMAIL.fullmatch(email):
            raise ValueError("Enter a valid email address")
        return email


class UserResponse(BaseModel):
    id: int
    email: str


class DashboardLink(BaseModel):
    original_url: str
    short_url: str


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return db.get(User, user_id)


def get_current_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
    return user


def _response(user: User) -> UserResponse:
    return UserResponse(id=user.id, email=user.email)


@router.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(credentials: Credentials, request: Request, db: Session = Depends(get_db)) -> UserResponse:
    user = User(email=credentials.email, password_hash=hash_password(credentials.password))
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account already exists for this email") from exc
    request.session.clear()
    request.session["user_id"] = user.id
    return _response(user)


@router.post("/auth/login", response_model=UserResponse)
def login(credentials: Credentials, request: Request, db: Session = Depends(get_db)) -> UserResponse:
    user = db.scalar(select(User).where(User.email == credentials.email))
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    request.session.clear()
    request.session["user_id"] = user.id
    return _response(user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> None:
    request.session.clear()


@router.get("/auth/me", response_model=UserResponse)
def me(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return _response(user)


@router.get("/account/links", response_model=list[DashboardLink])
def account_links(
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> list[DashboardLink]:
    base_url = get_settings().public_base_url.rstrip("/")
    urls = db.scalars(
        select(URL).where(URL.user_id == user.id).order_by(URL.id.desc()).limit(100)
    )
    return [
        DashboardLink(original_url=url.original_url, short_url=f"{base_url}/{url.short_code}")
        for url in urls
    ]
