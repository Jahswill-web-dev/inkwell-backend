from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.security import decode_access_token
from app.db.models.user import User
from app.db.repositories.user import UserRepository
from app.db.session import get_db_session

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="Authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials, request.app.state.settings)
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise _invalid_token()

    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise _invalid_token() from exc

    user = await UserRepository(session).get(user_id)
    if user is None:
        raise _invalid_token()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _invalid_token() -> AppError:
    return AppError(
        status_code=401,
        code="invalid_token",
        message="The access token is invalid or expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
