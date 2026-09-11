from collections.abc import Sequence
from uuid import UUID

from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.workspace import Workspace
from app.db.repositories.article import ArticleRepository
from app.db.repositories.client import ClientRepository
from app.db.repositories.workspace import WorkspaceRepository
from app.schemas.article import ArticleCreate, ArticleUpdate


class ArticleService:
    def __init__(
        self,
        articles: ArticleRepository,
        clients: ClientRepository,
        workspaces: WorkspaceRepository,
    ) -> None:
        self.articles = articles
        self.clients = clients
        self.workspaces = workspaces

    async def create(self, *, user_id: UUID, payload: ArticleCreate) -> Article:
        workspace = await self._default_workspace(user_id)
        client_id = payload.client_id
        assignee_id = payload.assignee_id or user_id
        await self._validate_client(client_id, workspace.id)
        await self._validate_assignee(assignee_id, workspace.id)
        values = payload.model_dump(
            exclude={"client_id", "assignee_id"},
            mode="python",
        )
        article = Article(
            user_id=user_id,
            workspace_id=workspace.id,
            client_id=client_id,
            assignee_id=assignee_id,
            status="setup",
            draft_readiness=False,
            published_at=None,
            **values,
        )
        await self.articles.add(article)
        return await self.get(article_id=article.id, user_id=user_id)

    async def list(
        self, *, user_id: UUID, offset: int, limit: int
    ) -> tuple[Sequence[Article], int]:
        items = await self.articles.list_owned(user_id, offset=offset, limit=limit)
        total = await self.articles.count_owned(user_id)
        return items, total

    async def get(self, *, article_id: UUID, user_id: UUID) -> Article:
        article = await self.articles.get_owned(article_id, user_id)
        if article is None:
            raise _article_not_found()
        return article

    async def update(self, *, article_id: UUID, user_id: UUID, payload: ArticleUpdate) -> Article:
        article = await self.get(article_id=article_id, user_id=user_id)
        values = payload.model_dump(exclude_unset=True, mode="python")
        if "client_id" in values:
            await self._validate_client(values["client_id"], article.workspace_id)
        if "assignee_id" in values and values["assignee_id"] is not None:
            await self._validate_assignee(values["assignee_id"], article.workspace_id)
        next_status = values.get("status", article.status)
        next_published_at = values.get("published_at", article.published_at)
        if (next_status == "published") != (next_published_at is not None):
            raise AppError(
                status_code=422,
                code="invalid_article_workflow",
                message="Published articles require published_at; other statuses must clear it",
            )
        for field, value in values.items():
            setattr(article, field, value)
        await self.articles.session.flush()
        return await self.get(article_id=article.id, user_id=user_id)

    async def delete(self, *, article_id: UUID, user_id: UUID) -> None:
        article = await self.get(article_id=article_id, user_id=user_id)
        await self.articles.delete(article)

    async def _default_workspace(self, user_id: UUID) -> Workspace:
        result = await self.workspaces.get_default_for_user(user_id)
        if result is None:
            raise AppError(
                status_code=404,
                code="workspace_not_found",
                message="The default workspace was not found",
            )
        return result[0]

    async def _validate_client(self, client_id: UUID | None, workspace_id: UUID) -> None:
        if (
            client_id is not None
            and await self.clients.get_in_workspace(client_id, workspace_id) is None
        ):
            raise AppError(
                status_code=422,
                code="invalid_article_client",
                message="The selected client does not belong to this workspace",
            )

    async def _validate_assignee(self, assignee_id: UUID, workspace_id: UUID) -> None:
        if not await self.workspaces.is_member(workspace_id=workspace_id, user_id=assignee_id):
            raise AppError(
                status_code=422,
                code="invalid_article_assignee",
                message="The selected assignee does not belong to this workspace",
            )


def _article_not_found() -> AppError:
    return AppError(
        status_code=404,
        code="article_not_found",
        message="The article was not found",
    )
