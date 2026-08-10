from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


class Repository[ModelT: Base]:
    """Small reusable query foundation for future domain repositories."""

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get(self, object_id: UUID) -> ModelT | None:
        return await self.session.get(self.model, object_id)

    async def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[ModelT]:
        statement = select(self.model).offset(offset).limit(limit)
        result = await self.session.scalars(statement)
        return result.all()

    async def add(self, instance: ModelT) -> ModelT:
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self.session.delete(instance)
        await self.session.flush()
