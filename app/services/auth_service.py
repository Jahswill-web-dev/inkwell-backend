from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    verify_and_update_password,
)
from app.db.models.user import User
from app.db.repositories.user import UserRepository
from app.services.login_rate_limiter import LoginRateLimiter


@dataclass(frozen=True, slots=True)
class AuthResult:
    user: User
    access_token: str


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        settings: Settings,
        login_rate_limiter: LoginRateLimiter,
    ) -> None:
        self.users = users
        self.settings = settings
        self.login_rate_limiter = login_rate_limiter

    async def register(self, *, email: str, username: str, password: str) -> AuthResult:
        if await self.users.get_by_email(email) is not None:
            raise _email_conflict()
        if await self.users.get_by_username(username) is not None:
            raise _username_conflict()

        password_hash = await run_in_threadpool(hash_password, password)
        user = User(email=email, username=username, password_hash=password_hash)
        try:
            await self.users.add(user)
        except IntegrityError as exc:
            await self.users.session.rollback()
            constraint_name = _constraint_name(exc)
            if constraint_name == "uq_users_email":
                raise _email_conflict() from exc
            if constraint_name == "uq_users_username":
                raise _username_conflict() from exc
            raise

        return self._create_auth_result(user)

    async def login(self, *, email: str, password: str, client_ip: str) -> AuthResult:
        await self.login_rate_limiter.ensure_allowed(email=email, client_ip=client_ip)

        user = await self.users.get_by_email(email)
        stored_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        valid, updated_hash = await run_in_threadpool(
            verify_and_update_password, password, stored_hash
        )

        if user is None or not valid:
            await self.login_rate_limiter.record_failure(email=email, client_ip=client_ip)
            raise AppError(
                status_code=401,
                code="invalid_credentials",
                message="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        await self.login_rate_limiter.clear_email(email=email)
        if updated_hash is not None:
            user.password_hash = updated_hash
            await self.users.session.flush()
            await self.users.session.refresh(user)

        return self._create_auth_result(user)

    def _create_auth_result(self, user: User) -> AuthResult:
        token = create_access_token(str(user.id), self.settings)
        return AuthResult(user=user, access_token=token)


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None


def _email_conflict() -> AppError:
    return AppError(
        status_code=409,
        code="email_already_registered",
        message="An account with this email already exists",
    )


def _username_conflict() -> AppError:
    return AppError(
        status_code=409,
        code="username_taken",
        message="This username is already taken",
    )
