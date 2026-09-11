from uuid import UUID

from app.core.exceptions import AppError
from app.db.models.workspace import Workspace, WorkspaceMember
from app.db.repositories.workspace import WorkspaceRepository


class WorkspaceService:
    def __init__(self, workspaces: WorkspaceRepository) -> None:
        self.workspaces = workspaces

    async def get_default(self, *, user_id: UUID) -> tuple[Workspace, WorkspaceMember]:
        result = await self.workspaces.get_default_for_user(user_id)
        if result is None:
            raise AppError(
                status_code=404,
                code="workspace_not_found",
                message="The default workspace was not found",
            )
        return result
