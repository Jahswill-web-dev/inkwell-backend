from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.core.exceptions import AppError
from app.db.models.workspace import Workspace, WorkspaceMember
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
    experience: Literal["marketing", "consultant"] | None = None,
) -> WorkspaceResponse:
    if experience is None:
        workspace, membership = await WorkspaceService(WorkspaceRepository(session)).get_default(
            user_id=current_user.id
        )
    else:
        query = (
            select(Workspace, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == current_user.id, Workspace.experience == experience)
        )
        if experience == "consultant":
            query = query.where(Workspace.personal_owner_id == current_user.id)
        row = (
            await session.execute(
                query.order_by(WorkspaceMember.is_default.desc(), Workspace.created_at).limit(1)
            )
        ).first()
        if row is None:
            raise AppError(
                status_code=404, code="workspace_not_found", message="Enable this experience first."
            )
        workspace, membership = row
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        role=membership.role,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )
