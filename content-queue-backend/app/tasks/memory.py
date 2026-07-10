"""
Celery tasks for persistent user memory.

consolidate_memory(user_id)  — nightly per-user: merges new activity (since last
                                run) onto the existing profile snapshot. First run
                                bootstraps from the earliest recorded activity (up
                                to 30 days back). Subsequent runs only process the
                                delta since last_consolidated.
consolidate_all_users()      — beat fan-out: dispatches consolidate_memory for each
                                user with activity since their last consolidation.

Dispatch: consolidate_memory.delay(user_id)
Direct call (tests): consolidate_memory(user_id, db=session)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.llm_client import llm_client, TASK_MEMORY_CONSOLIDATION
from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.models.list import List
from app.models.memory import UserProfile, ReadingVelocity
from app.models.reading_cluster import ReadingCluster
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)

# Bootstrap: how far back to look for a user's first consolidation.
# Uses the earliest actual activity date (up to this many days ago),
# so a user who installed 2 weeks ago gets 2 weeks, not a truncated 7.
_BOOTSTRAP_MAX_DAYS = 30

# Minimum signal threshold — don't call the LLM if there's almost nothing to work with.
_MIN_ACTIVITY_ITEMS = 3

_BOOTSTRAP_PROMPT = """\
You are building a first-time memory profile for a personal reading assistant.
This is a BOOTSTRAP — derive the profile from scratch based on all available reading activity.

Reading activity:
{activity}

Produce a profile that captures WHO this reader is and WHAT they are working toward —
not just what topics appear, but the patterns, depth, and trajectory.

Rules for current_focus:
- Be specific about sub-domain, not just the parent field.
  BAD:  "artificial intelligence"
  GOOD: "AI engineering and agent systems — context engineering, LLM evals, forward-deployed roles"
- If multiple distinct threads exist, name the primary one and note the secondary.

Rules for reading_velocity:
- Infer from behavioral signals (read_position %, highlight count), not topics.
- fast = consistently high read% but few highlights (skimming for coverage)
- deep = high read% AND multiple highlights per article (sustained engagement)
- browsing = saved many articles but low read% overall (collecting, not consuming)
- Note: a user can be "deep" on technical content but "fast" on news — pick the dominant pattern.

Rules for memory_text (free-form prose, 3-6 sentences):
Write as if briefing a new assistant who needs to know this person quickly.
Cover all four of:
1. Trajectory — what are they building toward or preparing for?
2. Depth asymmetry — which topics do they engage with deeply vs. skim?
3. Behavioral pattern — heavy saver vs. active reader, highlight-heavy vs. passive?
4. Unread backlog signal — topics they save repeatedly but never open deeply (anxiety-saving or unresolved interest)

Be specific and grounded in the evidence. Do not generalize beyond what the activity shows.
Avoid filler like "the user is interested in". State what the data shows.

If the activity shows no dominant focus — diverse domains, all shallow reads, no highlights, no
reading lists — say so explicitly in current_focus and memory_text. Do not construct a coherent
focus where the data shows none. "No dominant focus this window — ambient browsing across unrelated
domains" is a valid and useful profile.
"""

_DELTA_PROMPT = """\
You are maintaining a persistent memory profile for a personal reading assistant.
UPDATE the existing profile by merging in new reading activity.
Carry forward what is already known. Only update what the new activity changes or adds.

Existing profile:
{current_profile}

New activity since last update:
{activity}

Rules for current_focus:
- Update ONLY if new activity shows a clear shift in direction.
- Minor topic variation does not warrant a change.
- Be specific (sub-domain, not parent field).

Rules for reading_velocity:
- Infer from behavioral signals in the NEW activity only.
- fast / deep / browsing — same definitions as before.
- Only update if the new window shows a clearly different pattern than what is stored.

Rules for memory_text:
- Rewrite as a complete, self-contained paragraph — not a diff.
- Preserve accurate facts from the existing profile.
- Update trajectory if new activity reveals a shift.
- Add new depth asymmetries or behavioral patterns observed.
- Remove or soften claims that the new activity contradicts.
- Keep 3-6 sentences. Be specific. No filler.
"""


# ---------------------------------------------------------------------------
# Pydantic schema — hybrid output
# ---------------------------------------------------------------------------


class ConsolidationResult(BaseModel):
    current_focus: str  # specific sub-domain, one line
    reading_velocity: Literal["fast", "deep", "browsing"]
    memory_text: str  # free-form prose, LLM-managed


# ---------------------------------------------------------------------------
# Activity loading
# ---------------------------------------------------------------------------


def _bootstrap_since(user_id: str, db: Session) -> datetime:
    """
    For first-run users: find the earliest actual activity date and go back
    to that (capped at BOOTSTRAP_MAX_DAYS). This ensures a user who installed
    3 weeks ago gets all 3 weeks of data, not an arbitrary fixed window.
    """
    uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    cap = datetime.now(tz=timezone.utc) - timedelta(days=_BOOTSTRAP_MAX_DAYS)

    earliest_save = (
        db.query(ContentItem.created_at)
        .filter(ContentItem.user_id == uid, ContentItem.deleted_at.is_(None))
        .order_by(ContentItem.created_at.asc())
        .limit(1)
        .scalar()
    )
    earliest_highlight = (
        db.query(Highlight.created_at)
        .filter(Highlight.user_id == uid)
        .order_by(Highlight.created_at.asc())
        .limit(1)
        .scalar()
    )

    candidates = [t for t in (earliest_save, earliest_highlight) if t is not None]
    if not candidates:
        return cap

    earliest = min(candidates)
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)

    # Use the earlier of (actual first activity) and the cap
    return min(earliest, cap)


def _load_recent_activity(user_id: str, since: datetime, db: Session) -> dict:
    """
    Load all trackable activity since the given cutoff.

    saved      — articles newly added (intent signal: what topics are they collecting?)
    read       — articles opened in reader, updated_at >= since AND read_position > 0.1
    highlights — text selections with their article context
    lists      — reading lists (explicit curation intent)
    clusters   — topic clusters overlapping with active articles
    """
    uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id

    saved = (
        db.query(ContentItem)
        .filter(
            ContentItem.user_id == uid,
            ContentItem.deleted_at.is_(None),
            ContentItem.created_at >= since,
        )
        .order_by(ContentItem.created_at.desc())
        .limit(50)
        .all()
    )

    # updated_at changes when read_position is set by the reader
    read = (
        db.query(ContentItem)
        .filter(
            ContentItem.user_id == uid,
            ContentItem.deleted_at.is_(None),
            ContentItem.updated_at >= since,
            ContentItem.read_position > 0.1,
        )
        .order_by(ContentItem.updated_at.desc())
        .limit(30)
        .all()
    )

    highlights = (
        db.query(Highlight)
        .filter(Highlight.user_id == uid, Highlight.created_at >= since)
        .order_by(Highlight.created_at.desc())
        .limit(100)
        .all()
    )

    lists = (
        db.query(List)
        .filter(List.owner_id == uid, List.created_at >= since)
        .order_by(List.created_at.desc())
        .limit(20)
        .all()
    )

    # Clusters have no created_at — include only those overlapping active articles
    active_ids = {a.id for a in saved} | {a.id for a in read}
    clusters = [
        c
        for c in db.query(ReadingCluster)
        .filter(ReadingCluster.user_id == uid)
        .limit(20)
        .all()
        if any(aid in active_ids for aid in (c.article_ids or []))
    ]

    return {
        "saved": saved,
        "read": read,
        "highlights": highlights,
        "lists": lists,
        "clusters": clusters,
    }


def _rel_time(ts: datetime | None) -> str:
    """Return a human-readable relative timestamp like '2h ago' or '5d ago'."""
    if ts is None:
        return "?"
    now = datetime.now(tz=timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _format_activity(recent: dict) -> str:
    """
    Format activity for the LLM prompt. Signals in priority order:

    1. Read deeply — articles opened with highlights inline (highest signal)
    2. Read without highlights — engagement without annotation
    3. Saved but never opened — intent without engagement (backlog signal)
    4. Reading lists — explicit curation intent
    5. Topic clusters — inferred groupings

    Read articles are shown in chronological order (oldest first) so the LLM
    can observe sequencing — e.g. a user who read A → B → C on the same topic
    in one day is different from random bouncing across a week.
    """
    saved = recent.get("saved", [])
    read = recent.get("read", [])
    highlights = recent.get("highlights", [])
    lists = recent.get("lists", [])
    clusters = recent.get("clusters", [])

    # Index highlights by article id for inline display
    hl_by_article: dict[str, list[str]] = {}
    for h in highlights:
        key = str(h.content_item_id)
        hl_by_article.setdefault(key, []).append(h.text)

    read_ids = {str(a.id) for a in read}

    # Sort reads chronologically so sequencing is visible to the LLM
    read_chrono = sorted(
        read, key=lambda a: a.updated_at or datetime.min.replace(tzinfo=timezone.utc)
    )

    parts = []

    # --- Section 1: Read articles, split by depth, in reading order ---
    if read_chrono:
        with_highlights = [a for a in read_chrono if hl_by_article.get(str(a.id))]
        without_highlights = [
            a for a in read_chrono if not hl_by_article.get(str(a.id))
        ]

        if with_highlights:
            parts.append("Read deeply (with highlights, in reading order):")
            for a in with_highlights[:15]:
                pos = round((a.read_position or 0) * 100)
                tags = ", ".join((a.tags or [])[:4])
                length = f"{a.word_count}w" if a.word_count else "?"
                when = _rel_time(a.updated_at)
                parts.append(
                    f"  [{tags}] {a.title or 'Untitled'}  ({length}, read {pos}%, {when})"
                )
                for hl in hl_by_article[str(a.id)][:3]:
                    parts.append(f'    > "{hl[:150]}"')

        if without_highlights:
            parts.append("\nRead without highlights (in reading order):")
            for a in without_highlights[:10]:
                pos = round((a.read_position or 0) * 100)
                tags = ", ".join((a.tags or [])[:4])
                length = f"{a.word_count}w" if a.word_count else "?"
                when = _rel_time(a.updated_at)
                parts.append(
                    f"  [{tags}] {a.title or 'Untitled'}  ({length}, read {pos}%, {when})"
                )

    # --- Section 2: Saved but never opened (chronological, oldest first) ---
    never_opened = [
        a for a in saved if str(a.id) not in read_ids and (a.read_position or 0) < 0.05
    ]
    never_opened_chrono = sorted(
        never_opened,
        key=lambda a: a.created_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    if never_opened_chrono:
        parts.append(
            f"\nSaved but never opened ({len(never_opened_chrono)} articles — backlog signal):"
        )
        for a in never_opened_chrono[:15]:
            tags = ", ".join((a.tags or [])[:4])
            length = f"{a.word_count}w" if a.word_count else "?"
            when = _rel_time(a.created_at)
            parts.append(
                f"  [{tags}] {a.title or 'Untitled'}  ({length}, saved {when})"
            )

    # --- Section 3: Highlights on articles outside the read window ---
    orphan_highlights = [
        h for h in highlights if str(h.content_item_id) not in read_ids
    ]
    if orphan_highlights:
        parts.append(f"\nHighlights on older articles ({len(orphan_highlights)}):")
        for h in orphan_highlights[:8]:
            parts.append(f'  > "{h.text[:150]}"')

    # --- Section 4: Reading lists ---
    if lists:
        parts.append(f"\nReading lists ({len(lists)}):")
        for lst in lists:
            parts.append(f'  - "{lst.name}"')

    # --- Section 5: Topic clusters ---
    if clusters:
        parts.append("\nTopic clusters active this window:")
        for c in clusters:
            parts.append(f"  - {c.label} ({len(c.article_ids or [])} articles)")

    return "\n".join(parts) if parts else "(no activity)"


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------


def _load_profile(user_id: str, db: Session) -> UserProfile | None:
    uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    return db.query(UserProfile).filter(UserProfile.user_id == uid).first()


def _format_profile(profile: UserProfile) -> str:
    return (
        f"current_focus: {profile.current_focus or 'unknown'}\n"
        f"reading_velocity: {profile.reading_velocity.value if profile.reading_velocity else 'unknown'}\n"
        f"\n{profile.memory_text or '(no prior notes)'}"
    )


def _upsert_profile(user_id: str, result: ConsolidationResult, db: Session) -> None:
    uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    existing = db.query(UserProfile).filter(UserProfile.user_id == uid).first()
    if existing is None:
        db.add(
            UserProfile(
                user_id=uid,
                current_focus=result.current_focus,
                reading_velocity=ReadingVelocity(result.reading_velocity),
                memory_text=result.memory_text,
                last_consolidated=datetime.now(tz=timezone.utc),
            )
        )
    else:
        existing.current_focus = result.current_focus
        existing.reading_velocity = ReadingVelocity(result.reading_velocity)
        existing.memory_text = result.memory_text
        existing.last_consolidated = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Core logic (directly callable in tests)
# ---------------------------------------------------------------------------


def consolidate_memory(user_id: str, db: Session | None = None) -> dict:
    """
    Merge new reading activity onto the existing profile snapshot.

    Cutoff logic:
      - No profile yet:  bootstrap from earliest actual activity (up to 30 days)
      - Profile exists:  delta from last_consolidated only

    Bootstrap uses a different prompt (derive from scratch).
    Delta uses a merge prompt (patch existing profile).
    """
    own_session = db is None
    if own_session:
        from app.core.database import SessionLocal

        db = SessionLocal()

    try:
        current_profile = _load_profile(user_id, db)
        is_bootstrap = (
            current_profile is None or current_profile.last_consolidated is None
        )

        if is_bootstrap:
            since = _bootstrap_since(user_id, db)
            prompt_template = _BOOTSTRAP_PROMPT
        else:
            since = current_profile.last_consolidated
            prompt_template = _DELTA_PROMPT

        recent = _load_recent_activity(user_id, since=since, db=db)
        total_items = sum(len(recent[k]) for k in ("saved", "read", "highlights"))
        if total_items < _MIN_ACTIVITY_ITEMS:
            logger.info(
                f"consolidate_memory: insufficient activity for {user_id} "
                f"(found {total_items}, need {_MIN_ACTIVITY_ITEMS}), skipping"
            )
            return {
                "user_id": user_id,
                "status": "skipped",
                "reason": "insufficient_activity",
            }

        activity_str = _format_activity(recent)

        if is_bootstrap:
            prompt = prompt_template.format(activity=activity_str)
        else:
            prompt = prompt_template.format(
                current_profile=_format_profile(current_profile),
                activity=activity_str,
            )

        result: ConsolidationResult = llm_client.structured_chat(
            messages=[{"role": "user", "content": prompt}],
            response_model=ConsolidationResult,
            task=TASK_MEMORY_CONSOLIDATION,
            max_tokens=1024,
        )

        _upsert_profile(user_id, result, db)
        db.commit()

        logger.info(
            f"consolidate_memory: {'bootstrapped' if is_bootstrap else 'updated'} "
            f"profile for {user_id}"
        )
        return {"user_id": user_id, "status": "completed", "bootstrap": is_bootstrap}

    except Exception as e:
        logger.error(f"consolidate_memory failed for {user_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"user_id": user_id, "status": "failed", "error": str(e)}
    finally:
        if own_session:
            db.close()


# ---------------------------------------------------------------------------
# Celery task wrappers
# ---------------------------------------------------------------------------


@celery_app.task(base=DatabaseTask, bind=True, max_retries=2)
def consolidate_memory_task(self, user_id: str):
    return consolidate_memory(user_id, db=self.db)


@celery_app.task(base=DatabaseTask, bind=True)
def consolidate_all_users_task(self):
    """Nightly beat fan-out: dispatch consolidation only for users with new activity."""
    from sqlalchemy import text

    bootstrap_cutoff = datetime.now(tz=timezone.utc) - timedelta(
        days=_BOOTSTRAP_MAX_DAYS
    )

    rows = self.db.execute(
        text(
            """
        SELECT DISTINCT ci.user_id
        FROM content_items ci
        LEFT JOIN user_profiles up ON up.user_id = ci.user_id
        WHERE ci.deleted_at IS NULL
          AND (
            ci.created_at > COALESCE(up.last_consolidated, :bootstrap_cutoff)
            OR (
              ci.updated_at > COALESCE(up.last_consolidated, :bootstrap_cutoff)
              AND ci.read_position > 0.1
            )
          )
        UNION
        SELECT DISTINCT h.user_id
        FROM highlights h
        LEFT JOIN user_profiles up ON up.user_id = h.user_id
        WHERE h.created_at > COALESCE(up.last_consolidated, :bootstrap_cutoff)
    """
        ),
        {"bootstrap_cutoff": bootstrap_cutoff},
    ).fetchall()

    count = 0
    for (uid,) in rows:
        consolidate_memory_task.delay(str(uid))
        count += 1
    logger.info(f"consolidate_all_users: dispatched {count} tasks")
    return {"dispatched": count}
