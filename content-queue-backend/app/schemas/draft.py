from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID


class DraftCreate(BaseModel):
    content: str = Field(default="", description="Raw markdown content")
    title: Optional[str] = Field(
        None, description="Optional title (derived from first heading if absent)"
    )
    word_count: int = Field(default=0, ge=0)


class DraftUpdate(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None
    word_count: Optional[int] = Field(None, ge=0)


class DraftResponse(BaseModel):
    id: UUID
    list_id: UUID
    user_id: UUID
    title: Optional[str]
    content: str
    word_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
