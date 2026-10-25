"""normalize_stored_content_urls

Rewrites every active content item's original_url to its normalized form (the
form ingest_url stores today), so duplicate detection is one indexed lookup and
the O(library) fallback scan in find_existing_active_item can be removed.

Rows saved before normalization was consistent (trailing slash, #fragment,
tracking params, mixed-case host) are rewritten in place. If two active rows of
one user normalize to the same URL, one wins and the others are soft-deleted
(deleted_at set, recoverable) before the rewrite so the partial unique index
uq_content_items_user_url_active is never violated. The winner is the row that
has highlights, then the already-normalized one, then the most recently created
(5534fbac2811 also kept the newest). The winner inherits the losers' list
memberships; a loser's highlights stay attached to its soft-deleted row and are
logged by id (rows are never hard-deleted).

The normalization below is a frozen copy of app.services.content.normalize_url
as of this revision: a migration must not change behaviour when that function
is edited later.

Not reversible: the original spellings are not kept; downgrade is a no-op.

Revision ID: a7d3e9c15b42
Revises: f4c8b6e21a3d
Create Date: 2026-10-25 15:30:00.000000

"""

import logging
from collections import defaultdict
from typing import Sequence, Union
from urllib.parse import parse_qs, urlencode, urlparse

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e9c15b42"
down_revision: Union[str, Sequence[str], None] = "f4c8b6e21a3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_BATCH = 500

_STRIP_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_source_platform",
    "utm_creative_format",
    "utm_marketing_tactic",
    "fbclid",
    "fb_action_ids",
    "fb_action_types",
    "fb_source",
    "fb_ref",
    "gclid",
    "gclsrc",
    "dclid",
    "twclid",
    "msclkid",
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
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "campaign",
    "medium",
}


def _normalize_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    clean_params = {
        k: v
        for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
        if k.lower() not in _STRIP_PARAMS
    }
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/"),
        query=urlencode(sorted(clean_params.items()), doseq=True),
        fragment="",
    )
    return normalized.geturl()


def normalize_active_urls(conn) -> dict:
    """Normalize active rows' original_url; return counts. Idempotent."""
    rows = conn.execute(
        sa.text(
            "SELECT id, user_id, original_url, created_at "
            "FROM content_items WHERE deleted_at IS NULL"
        )
    ).fetchall()

    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[(row.user_id, _normalize_url(row.original_url))].append(row)

    colliding_ids = [
        m.id for members in groups.values() if len(members) > 1 for m in members
    ]
    highlight_counts: dict = {}
    if colliding_ids:
        highlight_counts = dict(
            conn.execute(
                sa.text(
                    "SELECT content_item_id, count(*) FROM highlights "
                    "WHERE content_item_id = ANY(:ids) GROUP BY content_item_id"
                ),
                {"ids": colliding_ids},
            ).fetchall()
        )

    rewrites: list[dict] = []
    losers: list = []
    carry_memberships: list[dict] = []
    for (_, normalized), members in groups.items():
        winner = max(
            members,
            key=lambda m: (
                highlight_counts.get(m.id, 0) > 0,
                m.original_url == normalized,
                m.created_at,
            ),
        )
        for member in members:
            if member.id == winner.id:
                continue
            losers.append(member.id)
            carry_memberships.append({"loser": member.id, "winner": winner.id})
            if highlight_counts.get(member.id):
                logger.warning(
                    f"normalize_stored_content_urls: soft-deleted item {member.id} "
                    f"has {highlight_counts[member.id]} highlight(s); kept item is "
                    f"{winner.id}"
                )
        if winner.original_url != normalized:
            rewrites.append({"id": winner.id, "url": normalized})

    for i in range(0, len(carry_memberships), _BATCH):
        conn.execute(
            sa.text(
                "INSERT INTO content_list_membership "
                "(content_item_id, list_id, added_at, added_by) "
                "SELECT :winner, list_id, added_at, added_by "
                "FROM content_list_membership WHERE content_item_id = :loser "
                "ON CONFLICT DO NOTHING"
            ),
            carry_memberships[i : i + _BATCH],
        )
    for i in range(0, len(losers), _BATCH):
        conn.execute(
            sa.text("UPDATE content_items SET deleted_at = now() WHERE id = ANY(:ids)"),
            {"ids": losers[i : i + _BATCH]},
        )
    for i in range(0, len(rewrites), _BATCH):
        conn.execute(
            sa.text("UPDATE content_items SET original_url = :url WHERE id = :id"),
            rewrites[i : i + _BATCH],
        )

    counts = {
        "scanned": len(rows),
        "rewritten": len(rewrites),
        "duplicates_soft_deleted": len(losers),
    }
    logger.info(f"normalize_stored_content_urls: {counts}")
    return counts


def upgrade() -> None:
    conn = op.get_bind()
    # Block concurrent writes for the few seconds this takes: a row inserted
    # between the snapshot SELECT and the UPDATEs could otherwise collide with a
    # rewrite on uq_content_items_user_url_active and abort the deploy. Reads are
    # unaffected (SHARE ROW EXCLUSIVE does not block SELECT).
    conn.execute(sa.text("LOCK TABLE content_items IN SHARE ROW EXCLUSIVE MODE"))
    normalize_active_urls(conn)


def downgrade() -> None:
    # Irreversible data normalization; nothing to undo.
    pass
