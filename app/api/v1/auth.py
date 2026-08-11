from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies.auth import CurrentUser
from app.db.repositories.user import UserRepository
from app.db.session import get_db_session
from app.schemas.auth import AuthResponse, LoginRequest, PublicUser, RegisterRequest
from app.schemas.common import ErrorResponse
from app.services.auth_service import AuthService
from app.services.login_rate_limiter import LoginRateLimiter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthResponse:
    service = _auth_service(request, session)
    result = await service.register(
        email=str(payload.email),
        username=payload.username,
        password=payload.password.get_secret_value(),
    )
    return AuthResponse(
        access_token=result.access_token,
        user=PublicUser.model_validate(result.user),
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthResponse:
    service = _auth_service(request, session)
    client_ip = request.client.host if request.client is not None else "unknown"
    result = await service.login(
        email=str(payload.email),
        password=payload.password.get_secret_value(),
        client_ip=client_ip,
    )
    return AuthResponse(
        access_token=result.access_token,
        user=PublicUser.model_validate(result.user),
    )


def _auth_service(request: Request, session: AsyncSession) -> AuthService:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    limiter = LoginRateLimiter(session_factory, request.app.state.settings)
    return AuthService(UserRepository(session), request.app.state.settings, limiter)


@router.get(
    "/me",
    response_model=PublicUser,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
)
async def me(current_user: CurrentUser) -> PublicUser:
    return PublicUser.model_validate(current_user)
