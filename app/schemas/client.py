from datetime import datetime
from typing import Annotated, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
CallToAction = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


def _unique(values: list[str]) -> list[str]:
    normalized = [value.casefold() for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Values must be unique")
    return values


def _validate_website(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    parsed = urlsplit(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Website must be an absolute HTTP or HTTPS URL")
    return stripped


class ClientBrandProfileFields(BaseModel):
    default_audience: str | None = Field(default=None, max_length=2_000)
    brand_voice: str | None = Field(default=None, max_length=2_000)
    preferred_terminology: list[Term] = Field(default_factory=list, max_length=20)
    avoided_terminology: list[Term] = Field(default_factory=list, max_length=20)
    default_calls_to_action: list[CallToAction] = Field(default_factory=list, max_length=20)

    @field_validator("default_audience", "brand_voice", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator(
        "preferred_terminology",
        "avoided_terminology",
        "default_calls_to_action",
    )
    @classmethod
    def require_unique_values(cls, value: list[str]) -> list[str]:
        return _unique(value)


class ClientBrandProfileResponse(ClientBrandProfileFields):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_id: UUID
    created_at: datetime
    updated_at: datetime


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    website: str | None = Field(default=None, max_length=2_048)
    industry: str | None = Field(default=None, max_length=120)
    brand_profile: ClientBrandProfileFields | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("website", mode="before")
    @classmethod
    def validate_website(cls, value: object) -> object:
        return _validate_website(value) if isinstance(value, str) or value is None else value

    @field_validator("industry", mode="before")
    @classmethod
    def strip_industry(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    website: str | None = Field(default=None, max_length=2_048)
    industry: str | None = Field(default=None, max_length=120)
    brand_profile: ClientBrandProfileFields | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("website", mode="before")
    @classmethod
    def validate_website(cls, value: object) -> object:
        return _validate_website(value) if isinstance(value, str) or value is None else value

    @field_validator("industry", mode="before")
    @classmethod
    def strip_industry(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def require_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one client field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Client name cannot be null")
        return self


class ClientSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class ClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    website: str | None
    industry: str | None
    brand_profile: ClientBrandProfileResponse | None
    created_at: datetime
    updated_at: datetime


class ClientListResponse(BaseModel):
    items: list[ClientResponse]
    total: int
    offset: int
    limit: int
