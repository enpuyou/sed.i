from pydantic import BaseModel, EmailStr, ConfigDict, field_validator
from datetime import datetime
from uuid import UUID


class UserCreate(BaseModel):
    """Data needed to register a new user"""

    email: EmailStr  # Must be valid email format
    password: str
    username: str
    full_name: str | None = None  # Optional


class UserLogin(BaseModel):
    """Data needed to log in"""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """What we return to the client (no password!)"""

    id: UUID
    email: str
    username: str | None
    full_name: str | None
    is_active: bool
    is_public: bool
    is_queue_public: bool
    is_crates_public: bool
    is_verified: bool
    created_at: datetime

    @field_validator(
        "is_public", "is_queue_public", "is_crates_public", "is_verified", mode="before"
    )
    @classmethod
    def set_default_false(cls, v):
        return False if v is None else v

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    """JWT token response"""

    access_token: str
    token_type: str
    refresh_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class TokenData(BaseModel):
    """Data stored inside the JWT token"""

    email: str | None = None


class UserUpdate(BaseModel):
    """Fields that can be updated on a user profile"""

    full_name: str | None = None
    username: str | None = None
    is_public: bool | None = None
    is_queue_public: bool | None = None
    is_crates_public: bool | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class GenericMessage(BaseModel):
    message: str


class DeleteAccountRequest(BaseModel):
    password: str
