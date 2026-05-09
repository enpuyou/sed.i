from typing import Optional, Any
from pydantic import BaseModel, computed_field, ConfigDict
from datetime import datetime
from uuid import UUID


class EphemeralHighlight(BaseModel):
    """A highlight captured in the ephemeral reader before the article is saved."""

    text: str
    note: Optional[str] = None
    start_offset: int
    end_offset: int
    color: str = "yellow"


class ContentItemCreate(BaseModel):
    """Data needed to save a new link"""

    url: str  # The URL to save
    list_ids: list[UUID] | None = None  # Optional: add to specific lists
    # Extension fields: pre-extracted content from the browser (bypasses fetch/trafilatura pipeline)
    pre_extracted_html: Optional[str] = None
    pre_extracted_title: Optional[str] = None
    pre_extracted_author: Optional[str] = None
    pre_extracted_description: Optional[str] = None
    pre_extracted_thumbnail: Optional[str] = None
    pre_extracted_published_date: Optional[str] = None
    pre_extracted_access_restricted: bool = (
        False  # Extension signals paywall/access gate
    )
    # Ephemeral reader: highlights captured before saving
    initial_highlights: list[EphemeralHighlight] | None = None


class ContentItemResponse(BaseModel):
    """What we return to the client"""

    id: UUID
    user_id: UUID
    original_url: str
    title: str | None
    description: str | None
    thumbnail_url: str | None
    content_type: str | None
    summary: str | None
    tags: list[str] | None = []
    auto_tags: list[str] | None = []  # AI-suggested tags
    status: str | None = None  # Reading status (read/unread/in_progress/archived)

    # Full content fields — full_text intentionally excluded from list responses
    # (see ContentItemDetail for the reader path)
    word_count: int | None
    reading_time_minutes: int | None

    # Reading progress
    read_position: Optional[float] = 0.0

    author: str | None = None
    published_date: datetime | None = None

    content_vertical: str | None = "general"
    vertical_metadata: dict[str, Any] | None = None

    is_read: bool
    is_archived: bool
    is_public: bool
    processing_status: str
    processing_error: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore
    @property
    def reading_status(self) -> str:
        """
        Compute reading status from is_read, read_position, and is_archived.

        Priority:
        1. Archived items are always "archived"
        2. Items explicitly marked as read stay "read" (don't change based on position)
        3. Items with position >= 0.9 are "read" (auto-mark as complete)
        4. Items with position > 0 are "in_progress"
        5. Otherwise "unread"
        """
        if self.is_archived:
            return "archived"

        # If explicitly marked as read, keep it read regardless of position
        if self.is_read:
            return "read"

        # Check read_position (handle None case explicitly)
        if self.read_position is not None:
            # Auto-mark as read if scrolled to near the end
            if self.read_position >= 0.9:
                return "read"
            # Show as in-progress if started but not finished
            if self.read_position > 0:
                return "in_progress"

        return "unread"

    model_config = ConfigDict(from_attributes=True)


class ContentItemDetail(ContentItemResponse):
    """Single-item response for the reader — includes full_text."""

    full_text: str | None


class ContentItemUpdate(BaseModel):
    """Fields that can be updated"""

    title: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    content_type: str | None = "unknown"
    author: str | None = None
    published_date: datetime | None = None
    content_vertical: str | None = "general"
    vertical_metadata: dict[str, Any] | None = None

    # Interactions

    is_read: bool | None = None
    is_archived: bool | None = None
    is_public: bool | None = None
    read_position: float | None = None
    tags: list[str] | None = None
    auto_tags: list[str] | None = None
    full_text: str | None = None  # Allow updating full text (e.g. for persistence)


class ContentItemList(BaseModel):
    """Paginated list response"""

    items: list[ContentItemResponse]
    total: int
    skip: int
    limit: int
