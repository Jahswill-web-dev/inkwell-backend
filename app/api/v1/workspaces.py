from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.db.repositories.workspace import WorkspaceRepository
from app.db.session import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.workspace import WorkspaceResponse
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get(
    "/current",
    response_model=WorkspaceResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def current_workspace(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WorkspaceResponse:
    workspace, membership = await WorkspaceService(WorkspaceRepository(session)).get_default(
        user_id=current_user.id
    )
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        role=membership.role,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )
