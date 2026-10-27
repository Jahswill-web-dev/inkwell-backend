from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class RegisterRequest(BaseModel):
    experience: Literal["marketing", "consultant"] = "marketing"
    email: EmailStr
    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-z0-9_]+$")
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class PublicUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    username: str
    preferred_experience: Literal["marketing", "consultant"] = "marketing"

    @field_validator("preferred_experience", mode="before")
    @classmethod
    def default_experience(cls, value: object) -> object:
        return value or "marketing"

    created_at: datetime
    updated_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime
    token_type: str = "bearer"
    user: PublicUser


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=50, max_length=200)


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime
