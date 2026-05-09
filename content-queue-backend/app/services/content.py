"""
ContentIngestionService — domain logic for saving a new URL to the library.

Owns the ingestion seam: URL normalization, duplicate detection, ContentItem
creation, and Celery task dispatch. Both the web API and MCP tool delegate here.

Public API:
    ingest_url()          — normalize, dedup, create, dispatch
    normalize_url()       — strip tracking params, canonical form
    DuplicateContentError — raised when the URL already exists in the library
"""

from __future__ import annotations

from urllib.parse import urlparse, parse_qs, urlencode
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.user import User

# Tracking/analytics params that never change which article a URL points to.
_STRIP_PARAMS = {
    # UTM
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_source_platform",
    "utm_creative_format",
    "utm_marketing_tactic",
    # Facebook
    "fbclid",
    "fb_action_ids",
    "fb_action_types",
    "fb_source",
    "fb_ref",
    # Google / DoubleClick
    "gclid",
    "gclsrc",
    "dclid",
    # Twitter / X
    "twclid",
    # Microsoft
    "msclkid",
    # HubSpot
    "hsa_acc",
    "hsa_cam",
    "hsa_grp",
    "hsa_ad",
    "hsa_src",
    "hsa_tgt",
    "hsa_kw",
    "hsa_mt",
    "hsa_net",
    "hsa_ver",
    # Mailchimp / email
    "mc_cid",
    "mc_eid",
    # Generic click-tracking
    "ref",
    "source",
    "campaign",
    "medium",
}


class DuplicateContentError(Exception):
    """Raised by ingest_url() when the URL already exists in the user's library."""

    def __init__(self, existing_id: str, is_archived: bool) -> None:
        self.existing_id = existing_id
        self.is_archived = is_archived
        super().__init__(f"URL already in library: {existing_id}")


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Lowercase scheme + host
    - Strip trailing slash from path
    - Remove tracking/analytics query params (UTM, fbclid, gclid, etc.)
    - Preserve all other query params (sorted for determinism)
    - Drop fragment
    """
    url = url.strip()
    parsed = urlparse(url)
    clean_params = {
        k: v
        for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
        if k.lower() not in _STRIP_PARAMS
    }
    clean_path = parsed.path.rstrip("/")
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=clean_path,
        query=urlencode(sorted(clean_params.items()), doseq=True),
        fragment="",
    )
    return normalized.geturl()


def find_existing_active_item(
    *,
    db: Session,
    user_id: UUID,
    normalized_url: str,
) -> ContentItem | None:
    """Find an active duplicate by normalized URL.

    Primary lookup uses the indexed exact match on `original_url`.
    Fallback scans active items and normalizes each stored URL to catch legacy
    rows created before URL normalization was consistently applied.
    """
    existing = (
        db.query(ContentItem)
        .filter(
            ContentItem.user_id == user_id,
            ContentItem.original_url == normalized_url,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )
    if existing:
        return existing

    active_items = (
        db.query(ContentItem)
        .filter(
            ContentItem.user_id == user_id,
            ContentItem.deleted_at.is_(None),
        )
        .all()
    )
    for item in active_items:
        if normalize_url(item.original_url) == normalized_url:
            return item
    return None


def ingest_url(
    *,
    url: str,
    user: User,
    db: Session,
    submitted_via: str = "web",
    dispatch_extraction: bool = True,
) -> ContentItem:
    """
    Normalize a URL, check for duplicates, create a ContentItem, and optionally
    queue extraction.

    Args:
        url: Raw URL from the caller.
        user: Authenticated user.
        db: Database session.
        submitted_via: Source identifier stored on the item ('web', 'mcp', 'extension').
        dispatch_extraction: Queue extract_metadata task after creation (default True).
            Pass False when the caller will handle dispatch itself (e.g. extension path
            that sets pre-extracted HTML and dispatches embedding + metadata separately).

    Returns:
        Newly created ContentItem with processing_status='pending'.

    Raises:
        DuplicateContentError: If an active (non-deleted) item with the same
            normalized URL already exists in the user's library.
    """
    normalized = normalize_url(url)

    existing = find_existing_active_item(
        db=db, user_id=user.id, normalized_url=normalized
    )
    if existing:
        raise DuplicateContentError(
            existing_id=str(existing.id),
            is_archived=existing.is_archived,
        )

    item = ContentItem(
        user_id=user.id,
        original_url=normalized,
        submitted_via=submitted_via,
        processing_status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    if dispatch_extraction:
        from app.tasks.extraction import extract_metadata

        extract_metadata.delay(str(item.id))

    return item
