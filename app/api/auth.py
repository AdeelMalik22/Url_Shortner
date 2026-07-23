"""Local account and private dashboard endpoints."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Path as ApiPath, Query, Request, UploadFile, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.url_shortner.models import AccountPlan, URL, User
from app.api.v1.url_shortner.schema import URLCreate
from app.config import get_settings
from app.services.auth import hash_password, verify_password
from app.services.cache import invalidate_cached_url
from app.services.rate_limiter import check_rate_limit
from app.services.url_shortner import ShortCodeAllocationError, create_short_url
from app.utils.db_connection import get_db

router = APIRouter(tags=["accounts"])
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DUMMY_PASSWORD_HASH = hash_password("snaplink-not-a-real-password")
_AVATAR_DIRECTORY = Path(__file__).resolve().parents[2] / "static" / "uploads" / "avatars"
_MAX_AVATAR_BYTES = 2 * 1024 * 1024
_AVATAR_TYPES = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
}


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



class ProfileUpdate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=3, max_length=30)

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


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordChange":
        if self.new_password != self.confirm_password:
            raise ValueError("New passwords do not match")
        return self


class UserResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    username: str
    email: str
    plan: AccountPlan
    avatar_url: str | None


class DashboardLink(BaseModel):
    id: int
    short_code: str
    original_url: str
    short_url: str


class LinkPage(BaseModel):
    items: list[DashboardLink]
    page: int
    page_size: int
    total: int
    total_pages: int
    query: str


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
        avatar_url=_avatar_url(user),
    )


def _avatar_url(user: User) -> str | None:
    if not user.avatar_filename:
        return None
    base_url = get_settings().public_base_url.rstrip("/")
    return f"{base_url}/static/uploads/avatars/{user.avatar_filename}"


def _avatar_extension(content_type: str | None, content: bytes) -> str | None:
    if content_type in _AVATAR_TYPES:
        extension, signature = _AVATAR_TYPES[content_type]
        if content.startswith(signature):
            return extension
    if (
        content_type == "image/webp"
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP"
    ):
        return ".webp"
    return None


def _remove_avatar(filename: str | None) -> None:
    if not filename or Path(filename).name != filename:
        return
    try:
        (_AVATAR_DIRECTORY / filename).unlink(missing_ok=True)
    except OSError:
        # A stale local file should never make an account update fail.
        pass


def _client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _link_response(url: URL) -> DashboardLink:
    base_url = get_settings().public_base_url.rstrip("/")
    return DashboardLink(
        id=url.id,
        short_code=url.short_code,
        original_url=url.original_url,
        short_url=f"{base_url}/{url.short_code}",
    )


def _owned_link_or_404(db: Session, user: User, short_code: str) -> URL:
    url = db.scalar(
        select(URL).where(URL.short_code == short_code).where(URL.user_id == user.id)
    )
    if url is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    return url


def _short_code_from_public_url(value: str) -> str | None:
    """Return a short code when a user pastes one of this app's short URLs."""

    parsed = urlsplit(value)
    public_url = urlsplit(get_settings().public_base_url)
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.scheme.lower() != public_url.scheme.lower()
        or parsed.netloc.lower() != public_url.netloc.lower()
        or parsed.query
        or parsed.fragment
    ):
        return None

    candidate = parsed.path.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", candidate):
        return None
    return candidate


def _enforce_shortening_rate_limit(request: Request) -> None:
    decision = check_rate_limit(_client_id(request), "shorten")
    if not decision.available and not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate-limit service unavailable; please retry",
            headers={"Retry-After": str(decision.retry_after)},
        )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many shortening requests",
            headers={"Retry-After": str(decision.retry_after)},
        )


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


@router.get("/account/profile", response_model=UserResponse)
def profile(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return _response(user)


@router.patch("/account/profile", response_model=UserResponse)
def update_profile(
    data: ProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> UserResponse:
    username_owner = db.scalar(
        select(User.id).where(User.username == data.username).where(User.id != user.id)
    )
    if username_owner is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This username is already taken",
        )
    user.first_name = data.first_name
    user.last_name = data.last_name
    user.username = data.username
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This username is already taken",
        ) from exc
    return _response(user)


@router.post("/account/avatar", response_model=UserResponse)
async def upload_avatar(
    photo: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    content = await photo.read(_MAX_AVATAR_BYTES + 1)
    await photo.close()
    if len(content) > _MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Profile photo must be 2 MB or smaller",
        )
    extension = _avatar_extension(photo.content_type, content)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Use a PNG, JPEG, or WebP image for your profile photo",
        )

    _AVATAR_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    destination = _AVATAR_DIRECTORY / filename
    destination.write_bytes(content)
    previous_filename = user.avatar_filename
    try:
        user.avatar_filename = filename
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        _remove_avatar(filename)
        raise
    _remove_avatar(previous_filename)
    return _response(user)


@router.delete("/account/avatar", status_code=status.HTTP_204_NO_CONTENT)
def delete_avatar(
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> None:
    previous_filename = user.avatar_filename
    if previous_filename is None:
        return
    user.avatar_filename = None
    db.commit()
    _remove_avatar(previous_filename)


@router.post("/account/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    data: PasswordChange,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> None:
    _enforce_auth_rate_limit(request)
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    user.password_hash = hash_password(data.new_password)
    db.commit()


@router.post("/account/links", response_model=DashboardLink, status_code=status.HTTP_201_CREATED)
def create_account_link(
    data: URLCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> DashboardLink:
    _enforce_shortening_rate_limit(request)
    try:
        url = create_short_url(db, str(data.url), user_id=user.id)
    except ShortCodeAllocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not allocate a short URL; please retry",
        ) from exc
    invalidate_cached_url(url.short_code)
    return _link_response(url)


@router.get("/account/links", response_model=LinkPage)
def account_links(
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
) -> LinkPage:
    query = q.strip()
    filters = [URL.user_id == user.id]
    if query:
        match = f"%{query}%"
        match_conditions = [
            URL.short_code.ilike(match),
            URL.original_url.ilike(match),
        ]
        pasted_short_code = _short_code_from_public_url(query)
        if pasted_short_code:
            match_conditions.append(URL.short_code == pasted_short_code)
        filters.append(
            or_(*match_conditions)
        )
    total = int(
        db.scalar(select(func.count()).select_from(URL).where(*filters))
        or 0
    )
    urls = db.scalars(
        select(URL)
        .where(*filters)
        .order_by(URL.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return LinkPage(
        items=[_link_response(url) for url in urls],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, (total + page_size - 1) // page_size),
        query=query,
    )


@router.delete("/account/links/{short_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account_link(
    short_code: Annotated[
        str,
        ApiPath(min_length=6, max_length=32, pattern=r"^[A-Za-z0-9]+$"),
    ],
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> None:
    url = _owned_link_or_404(db, user, short_code)
    db.delete(url)
    db.commit()
    invalidate_cached_url(short_code)


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
