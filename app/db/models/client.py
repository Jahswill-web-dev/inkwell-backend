from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.article import Article


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (
        Index("ix_clients_workspace_id", "workspace_id"),
        Index(
            "uq_clients_workspace_name_ci",
            "workspace_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)

    brand_profile: Mapped[ClientBrandProfile | None] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )
    articles: Mapped[list[Article]] = relationship(back_populates="client")


class ClientBrandProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_brand_profiles"
    __table_args__ = (Index("uq_client_brand_profiles_client_id", "client_id", unique=True),)

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    default_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_terminology: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), nullable=False, default=list
    )
    avoided_terminology: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), nullable=False, default=list
    )
    default_calls_to_action: Mapped[list[str]] = mapped_column(
        ARRAY(String(500)), nullable=False, default=list
    )

    client: Mapped[Client] = relationship(back_populates="brand_profile")
