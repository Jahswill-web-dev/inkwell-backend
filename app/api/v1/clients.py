from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.db.repositories.client import ClientRepository
from app.db.repositories.workspace import WorkspaceRepository
from app.db.session import get_db_session
from app.schemas.client import ClientCreate, ClientListResponse, ClientResponse, ClientUpdate
from app.schemas.common import ErrorResponse
from app.services.client_service import ClientService

router = APIRouter(prefix="/clients", tags=["clients"])


def _service(session: AsyncSession) -> ClientService:
    return ClientService(ClientRepository(session), WorkspaceRepository(session))


@router.get("", response_model=ClientListResponse)
async def list_clients(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ClientListResponse:
    items, total = await _service(session).list(user_id=current_user.id, offset=offset, limit=limit)
    return ClientListResponse(
        items=[ClientResponse.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def create_client(
    payload: ClientCreate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ClientResponse:
    client = await _service(session).create(user_id=current_user.id, payload=payload)
    return ClientResponse.model_validate(client)


@router.get(
    "/{client_id}",
    response_model=ClientResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def get_client(
    client_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ClientResponse:
    client = await _service(session).get(client_id=client_id, user_id=current_user.id)
    return ClientResponse.model_validate(client)


@router.patch(
    "/{client_id}",
    response_model=ClientResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def update_client(
    client_id: UUID,
    payload: ClientUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ClientResponse:
    client = await _service(session).update(
        client_id=client_id, user_id=current_user.id, payload=payload
    )
    return ClientResponse.model_validate(client)
