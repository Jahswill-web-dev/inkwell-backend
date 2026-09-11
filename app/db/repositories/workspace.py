from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workspace import Workspace, WorkspaceMember
from app.db.queries import Repository


class WorkspaceRepository(Repository[Workspace]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Workspace, session)

    async def get_default_for_user(self, user_id: UUID) -> tuple[Workspace, WorkspaceMember] | None:
        result = await self.session.execute(
            select(Workspace, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.is_default.is_(True),
            )
        )
        row = result.first()
        return (row[0], row[1]) if row is not None else None

    async def is_member(self, *, workspace_id: UUID, user_id: UUID) -> bool:
        membership = await self.session.scalar(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        return membership is not None
