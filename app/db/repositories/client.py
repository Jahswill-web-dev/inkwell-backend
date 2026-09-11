from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.client import Client
from app.db.queries import Repository


class ClientRepository(Repository[Client]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Client, session)

    async def get_in_workspace(self, client_id: UUID, workspace_id: UUID) -> Client | None:
        result = await self.session.scalars(
            select(Client)
            .options(selectinload(Client.brand_profile))
            .where(Client.id == client_id, Client.workspace_id == workspace_id)
        )
        return result.first()

    async def list_in_workspace(
        self, workspace_id: UUID, *, offset: int, limit: int
    ) -> Sequence[Client]:
        result = await self.session.scalars(
            select(Client)
            .options(selectinload(Client.brand_profile))
            .where(Client.workspace_id == workspace_id)
            .order_by(func.lower(Client.name), Client.id)
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    async def count_in_workspace(self, workspace_id: UUID) -> int:
        count = await self.session.scalar(
            select(func.count()).select_from(Client).where(Client.workspace_id == workspace_id)
        )
        return count or 0
