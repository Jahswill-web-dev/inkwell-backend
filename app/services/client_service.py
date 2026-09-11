from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppError
from app.db.models.client import Client, ClientBrandProfile
from app.db.repositories.client import ClientRepository
from app.db.repositories.workspace import WorkspaceRepository
from app.schemas.client import ClientCreate, ClientUpdate


class ClientService:
    def __init__(
        self,
        clients: ClientRepository,
        workspaces: WorkspaceRepository,
    ) -> None:
        self.clients = clients
        self.workspaces = workspaces

    async def create(self, *, user_id: UUID, payload: ClientCreate) -> Client:
        workspace_id = await self._workspace_id(user_id)
        values = payload.model_dump(exclude={"brand_profile"}, mode="json")
        client = Client(workspace_id=workspace_id, **values)
        if payload.brand_profile is not None:
            client.brand_profile = ClientBrandProfile(
                **payload.brand_profile.model_dump(mode="json")
            )
        try:
            await self.clients.add(client)
        except IntegrityError as exc:
            await self.clients.session.rollback()
            raise _client_name_conflict() from exc
        persisted = await self.clients.get_in_workspace(client.id, workspace_id)
        assert persisted is not None
        return persisted

    async def list(self, *, user_id: UUID, offset: int, limit: int) -> tuple[Sequence[Client], int]:
        workspace_id = await self._workspace_id(user_id)
        items = await self.clients.list_in_workspace(workspace_id, offset=offset, limit=limit)
        total = await self.clients.count_in_workspace(workspace_id)
        return items, total

    async def get(self, *, client_id: UUID, user_id: UUID) -> Client:
        workspace_id = await self._workspace_id(user_id)
        client = await self.clients.get_in_workspace(client_id, workspace_id)
        if client is None:
            raise _client_not_found()
        return client

    async def update(self, *, client_id: UUID, user_id: UUID, payload: ClientUpdate) -> Client:
        client = await self.get(client_id=client_id, user_id=user_id)
        values = payload.model_dump(exclude_unset=True, exclude={"brand_profile"}, mode="json")
        for field, value in values.items():
            setattr(client, field, value)
        if "brand_profile" in payload.model_fields_set:
            if payload.brand_profile is None:
                client.brand_profile = None
            elif client.brand_profile is None:
                client.brand_profile = ClientBrandProfile(
                    **payload.brand_profile.model_dump(mode="json")
                )
            else:
                for field, value in payload.brand_profile.model_dump(mode="json").items():
                    setattr(client.brand_profile, field, value)
        try:
            await self.clients.session.flush()
        except IntegrityError as exc:
            await self.clients.session.rollback()
            raise _client_name_conflict() from exc
        return await self.get(client_id=client_id, user_id=user_id)

    async def _workspace_id(self, user_id: UUID) -> UUID:
        result = await self.workspaces.get_default_for_user(user_id)
        if result is None:
            raise AppError(
                status_code=404,
                code="workspace_not_found",
                message="The default workspace was not found",
            )
        return result[0].id


def _client_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="client_not_found",
        message="The client was not found",
    )


def _client_name_conflict() -> AppError:
    return AppError(
        status_code=409,
        code="client_name_taken",
        message="A client with this name already exists in the workspace",
    )
