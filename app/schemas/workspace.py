from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: WorkspaceRole
    created_at: datetime
    updated_at: datetime
