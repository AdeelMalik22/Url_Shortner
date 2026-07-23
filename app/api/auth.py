"""Local account and private dashboard endpoints."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.url_shortner.models import AccountPlan, URL, User
from app.config import get_settings
from app.services.auth import hash_password, verify_password
from app.services.rate_limiter import check_rate_limit
from app.utils.db_connection import get_db

router = APIRouter(tags=["accounts"])
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DUMMY_PASSWORD_HASH = hash_password("snaplink-not-a-real-password")


class LoginCredentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not _EMAIL.fullmatch(email):
            raise ValueError("Enter a valid email address")
        return email


class RegistrationCredentials(LoginCredentials):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=3, max_length=30)
    confirm_password: str = Field(min_length=12, max_length=128)

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        name = " ".join(value.split())
        if not name or any(character.isdigit() for character in name):
            raise ValueError("Enter a valid name")
        return name

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = value.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,29}", username):
            raise ValueError(
                "Username must start with a letter and use only letters, numbers, or underscores"
            )
        return username

    @model_validator(mode="after")
    def passwords_match(self) -> "RegistrationCredentials":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class UserResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    username: str
    email: str
    plan: AccountPlan


class DashboardLink(BaseModel):
    original_url: str
    short_url: str


class AccountOverview(BaseModel):
    plan: AccountPlan
    saved_link_count: int
    features: list[str]


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
    return UserResponse(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        email=user.email,
        plan=AccountPlan(user.plan),
    )


def _client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_auth_rate_limit(request: Request) -> None:
    settings = get_settings()
    decision = check_rate_limit(
        _client_id(request),
        "auth",
        requests=settings.auth_rate_limit_requests,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    if not decision.available and not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable; please retry",
            headers={"Retry-After": str(decision.retry_after)},
        )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many account attempts; please retry later",
            headers={"Retry-After": str(decision.retry_after)},
        )


@router.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    credentials: RegistrationCredentials,
    request: Request,
    db: Session = Depends(get_db),
) -> UserResponse:
    _enforce_auth_rate_limit(request)
    if db.scalar(select(User.id).where(User.email == credentials.email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for this email",
        )
    if db.scalar(select(User.id).where(User.username == credentials.username)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This username is already taken",
        )

    user = User(
        first_name=credentials.first_name,
        last_name=credentials.last_name,
        username=credentials.username,
        email=credentials.email,
        password_hash=hash_password(credentials.password),
    )
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email address or username is already in use",
        ) from exc
    request.session.clear()
    request.session["user_id"] = user.id
    return _response(user)


@router.post("/auth/login", response_model=UserResponse)
def login(
    credentials: LoginCredentials,
    request: Request,
    db: Session = Depends(get_db),
) -> UserResponse:
    _enforce_auth_rate_limit(request)
    user = db.scalar(select(User).where(User.email == credentials.email))
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    if user is None or not verify_password(credentials.password, password_hash):
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


@router.get("/account/overview", response_model=AccountOverview)
def account_overview(
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> AccountOverview:
    saved_link_count = db.scalar(
        select(func.count()).select_from(URL).where(URL.user_id == user.id)
    )
    features = ["Private link dashboard"]
    if user.plan == AccountPlan.PREMIUM.value:
        features.extend(["Custom short links", "Detailed analytics", "API access"])
    return AccountOverview(
        plan=AccountPlan(user.plan),
        saved_link_count=int(saved_link_count or 0),
        features=features,
    )
