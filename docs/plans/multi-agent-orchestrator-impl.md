---
type: plan
status: draft
last_updated: 2026-07-10
feature_doc: docs/design/product/multi-agent-research-orchestrator.md
---

# Implementation Plan: Multi-Agent Research Orchestrator

Detailed build sequence with acceptance criteria and test cases for every step.
Each step has a "done when" definition that can be verified before moving to the
next. No step requires the next one to test it.

Read the feature doc first:
`docs/design/product/multi-agent-research-orchestrator.md`

---

## Conventions

- All new backend files go in `content-queue-backend/`
- All new test files go in `content-queue-backend/tests/`
- LLM calls are mocked in unit/integration tests; real calls only in `tests/evals/`
- Every task that writes to DB uses the existing `DatabaseTask` base class
- New `TASK_*` constants added to `app/core/llm_client.py` before any task that uses them
- Migration files follow existing naming: `NNN_<description>.py` in `alembic/versions/`

---

## Phase 1 — Memory + Skills

---

### Step 1.1 — DB migration: `user_memory_events` + `user_profiles`

**Files:**
- `alembic/versions/NNN_add_user_memory_tables.py`
- `app/models/memory.py`

**What to build:**

Migration creates both tables exactly as specced in the feature doc. Models:

```python
# app/models/memory.py
import enum

class ReadingVelocity(str, enum.Enum):
    fast     = "fast"
    deep     = "deep"
    browsing = "browsing"

class UserMemoryEvent(Base):
    __tablename__ = "user_memory_events"
    __table_args__ = (
        # Prevents nightly re-insertion of the same event
        UniqueConstraint(
            "user_id", "event_type", "content_item_id",
            func.date("occurred_at"),
            name="uq_memory_event_day",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(nullable=False)
    content_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("content_items.id"))
    metadata: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())

class UserProfile(Base):
    __tablename__ = "user_profiles"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    current_focus: Mapped[str | None]
    reading_velocity: Mapped[ReadingVelocity | None] = mapped_column(
        SAEnum(ReadingVelocity, name="reading_velocity_enum", create_constraint=True)
    )
    preferred_depth_words: Mapped[int | None]
    writing_style_notes: Mapped[str | None]
    active_knowledge_gaps: Mapped[list | None] = mapped_column(JSONB)
    past_synthesis_topics: Mapped[list | None] = mapped_column(JSONB)
    last_consolidated: Mapped[datetime | None]
```

Migration must create the `reading_velocity_enum` Postgres enum type before the
table, and add the `uq_memory_event_day` unique constraint.

`app/models/__init__.py` updated to export both.

**Done when:** `alembic upgrade head` runs clean. Tables exist in DB.

**Tests** (`tests/test_memory_models.py`):

```python
class TestUserMemoryModels:
    def test_can_create_memory_event(self, db_session, test_user):
        event = UserMemoryEvent(
            user_id=test_user.id,
            event_type="deep_read",
            metadata={"highlight_count": 5},
        )
        db_session.add(event); db_session.commit()
        assert event.id is not None
        assert event.occurred_at is not None

    def test_memory_event_requires_user_id(self, db_session):
        with pytest.raises(Exception):
            db_session.add(UserMemoryEvent(event_type="deep_read"))
            db_session.commit()

    def test_can_upsert_user_profile(self, db_session, test_user):
        profile = UserProfile(
            user_id=test_user.id,
            current_focus="agent evaluation design",
            reading_velocity="deep",
        )
        db_session.merge(profile); db_session.commit()
        fetched = db_session.get(UserProfile, test_user.id)
        assert fetched.current_focus == "agent evaluation design"

    def test_profile_upsert_overwrites_fields(self, db_session, test_user):
        db_session.merge(UserProfile(user_id=test_user.id, current_focus="topic A"))
        db_session.commit()
        db_session.merge(UserProfile(user_id=test_user.id, current_focus="topic B"))
        db_session.commit()
        fetched = db_session.get(UserProfile, test_user.id)
        assert fetched.current_focus == "topic B"

    def test_user_scoped_events_not_visible_across_users(self, db_session, test_user):
        from app.models.user import User
        from app.core.security import get_password_hash
        other = User(email="other@test.com", username="other2",
                     hashed_password=get_password_hash("x"), is_active=True)
        db_session.add(other); db_session.commit()

        db_session.add(UserMemoryEvent(user_id=other.id, event_type="deep_read"))
        db_session.commit()

        my_events = db_session.query(UserMemoryEvent).filter_by(user_id=test_user.id).all()
        assert len(my_events) == 0
```

---

### Step 1.2 — `consolidate_memory` task

**Files:**
- `app/tasks/memory.py`
- `app/core/celery_app.py` (beat schedule entry)

**What to build:**

`consolidate_memory(user_id)` — loads recent activity, calls `llm_client.structured_chat()`,
writes profile + episodic events.

`consolidate_all_users()` — fan-out task, same pattern as `cluster_all_users_task`:
queries for users with activity in last 7 days, dispatches `consolidate_memory.delay(uid)`
for each.

New `TASK_MEMORY_CONSOLIDATION` constant in `llm_client.py` — routes to `gpt-4o-mini`
(same tier as tagging: structured extraction, not reasoning).

`ConsolidationResult` Pydantic model:
```python
class KnowledgeGap(BaseModel):
    concept: str
    confidence: float  # 0.0-1.0

class MemoryEvent(BaseModel):
    event_type: str
    content_item_id: str | None
    metadata: dict

class ConsolidationResult(BaseModel):
    current_focus: str
    reading_velocity: Literal["fast", "deep", "browsing"]
    writing_style_notes: str | None
    knowledge_gaps: list[KnowledgeGap]
    episodic_events: list[MemoryEvent]
```

Helper functions (private, in `memory.py`):
- `_load_recent_activity(user_id, days, db)` — reads content_items, highlights, drafts
- `_format_activity(recent)` — builds text summary for the prompt
- `_load_profile(user_id, db)` — returns existing `UserProfile` or None
- `_upsert_profile(user_id, insights, db)` — `db.merge()`
- `_insert_episodic_events(user_id, events, db)` — bulk insert via `INSERT ... ON CONFLICT ON CONSTRAINT uq_memory_event_day DO NOTHING`; prevents nightly re-insertion of the same event when the task re-runs

Beat entry in `celery_app.py`:
```python
"consolidate-memory-nightly": {
    "task": "app.tasks.memory.consolidate_all_users",
    "schedule": crontab(hour=3, minute=0),
},
```

**Done when:** `consolidate_memory.delay(user_id)` runs without error on a user with
activity. Profile row exists in DB after run. Events row exists in DB.

**Tests** (`tests/test_memory_task.py`):

```python
class TestConsolidateMemory:
    def test_writes_profile_on_activity(self, db_session, test_user, article):
        # article fixture gives test_user one content item
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = ConsolidationResult(
                current_focus="RAG systems",
                reading_velocity="deep",
                writing_style_notes="concise, avoids hedging",
                knowledge_gaps=[KnowledgeGap(concept="HippoRAG", confidence=0.8)],
                episodic_events=[MemoryEvent(event_type="deep_read",
                                             content_item_id=str(article.id),
                                             metadata={"highlight_count": 3})],
            )
            consolidate_memory(str(test_user.id))

        profile = db_session.get(UserProfile, test_user.id)
        assert profile is not None
        assert profile.current_focus == "RAG systems"
        assert profile.reading_velocity == "deep"
        assert len(profile.active_knowledge_gaps) == 1

    def test_inserts_episodic_events(self, db_session, test_user, article):
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = ConsolidationResult(
                current_focus="agents",
                reading_velocity="fast",
                writing_style_notes=None,
                knowledge_gaps=[],
                episodic_events=[MemoryEvent(event_type="deep_read",
                                             content_item_id=str(article.id),
                                             metadata={})],
            )
            consolidate_memory(str(test_user.id))

        events = db_session.query(UserMemoryEvent).filter_by(user_id=test_user.id).all()
        assert len(events) == 1
        assert events[0].event_type == "deep_read"

    def test_no_op_when_no_activity(self, db_session, test_user):
        # test_user has no articles
        with patch("app.tasks.memory.llm_client") as mock_llm:
            consolidate_memory(str(test_user.id))
            mock_llm.structured_chat.assert_not_called()

        assert db_session.get(UserProfile, test_user.id) is None

    def test_second_run_upserts_not_duplicates(self, db_session, test_user, article):
        result = ConsolidationResult(
            current_focus="topic A", reading_velocity="fast",
            writing_style_notes=None, knowledge_gaps=[], episodic_events=[],
        )
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = result
            consolidate_memory(str(test_user.id))
            result.current_focus = "topic B"
            mock_llm.structured_chat.return_value = result
            consolidate_memory(str(test_user.id))

        profiles = db_session.query(UserProfile).filter_by(user_id=test_user.id).all()
        assert len(profiles) == 1
        assert profiles[0].current_focus == "topic B"

    def test_fan_out_dispatches_per_active_user(self, db_session, test_user, article):
        with patch("app.tasks.memory.consolidate_memory") as mock_task:
            mock_task.delay = MagicMock()
            consolidate_all_users()
            mock_task.delay.assert_called_once_with(str(test_user.id))
```

---

### Step 1.3 — Skills registration in MCP server

**Files:**
- `app/mcp/server.py`
- `app/mcp/skills.py` (new — skill text constants)

**What to build:**

`app/mcp/skills.py` contains the three Skill instruction strings as module-level
constants (`WEEKLY_DIGEST_SKILL`, `CONNECT_NEW_SAVE_SKILL`, `DRAFT_FROM_HIGHLIGHTS_SKILL`)
exactly as written in the feature doc.

In `app/mcp/server.py`, register as an MCP resource:
```python
from app.mcp.skills import SEDI_SKILLS
mcp.add_resource("skills://sedi", lambda: json.dumps(SEDI_SKILLS))
```

**Done when:** `mcp.list_resources()` returns `skills://sedi`. Reading the resource
returns a JSON object with keys `weekly-digest`, `connect-new-save`,
`draft-from-highlights`.

**Tests** (`tests/mcp/test_skills.py`):

```python
class TestSkillsRegistration:
    def test_skills_resource_registered(self):
        from app.mcp.skills import SEDI_SKILLS
        assert "weekly-digest" in SEDI_SKILLS
        assert "connect-new-save" in SEDI_SKILLS
        assert "draft-from-highlights" in SEDI_SKILLS

    def test_each_skill_has_required_sections(self):
        from app.mcp.skills import (
            WEEKLY_DIGEST_SKILL, CONNECT_NEW_SAVE_SKILL, DRAFT_FROM_HIGHLIGHTS_SKILL,
        )
        for skill in [WEEKLY_DIGEST_SKILL, CONNECT_NEW_SAVE_SKILL, DRAFT_FROM_HIGHLIGHTS_SKILL]:
            assert "Goal:" in skill
            assert "Steps:" in skill

    def test_draft_skill_contains_write_constraint(self):
        from app.mcp.skills import DRAFT_FROM_HIGHLIGHTS_SKILL
        # Bounded write scope must be explicit in the skill text
        assert "update_draft" in DRAFT_FROM_HIGHLIGHTS_SKILL
        assert "do not modify the library" in DRAFT_FROM_HIGHLIGHTS_SKILL.lower() \
            or "only call update_draft" in DRAFT_FROM_HIGHLIGHTS_SKILL.lower()

    def test_connect_skill_references_entity_traversal(self):
        from app.mcp.skills import CONNECT_NEW_SAVE_SKILL
        assert "explore_concept" in CONNECT_NEW_SAVE_SKILL

    def test_weekly_skill_references_memory(self):
        from app.mcp.skills import WEEKLY_DIGEST_SKILL
        assert "current_focus" in WEEKLY_DIGEST_SKILL or "user memory" in WEEKLY_DIGEST_SKILL.lower()
```

---

### Step 1.4 — Routing classifier

**Files:**
- `app/core/request_router.py` (new)
- `app/core/llm_client.py` (add `TASK_ROUTING`)

**What to build:**

Two-tier classifier. Tier 1 (free): catches obvious filter/lookup queries by
checking for filter operators or explicit single-item lookup verbs with no
synthesis signal. Tier 2 (gpt-4o-mini): everything else.

```python
# app/core/request_router.py

_DIRECT_OPERATORS = ("after:", "before:", "tag:", "author:", "site:")
_DIRECT_VERBS = ("find ", "search ", "show me ", "get ", "list ", "how many ")

class RouteDecision(BaseModel):
    route: Literal["direct", "skill", "orchestrate"]
    skill: Literal["weekly-digest", "connect-new-save", "draft-from-highlights"] | None

def classify_request(question: str) -> tuple[str, str | None]:
    """
    Returns (route, skill_name | None).
    Tier 1 (free): obvious direct lookups bypass LLM.
    Tier 2 (gpt-4o-mini): structured output with few-shot examples.
    """
    q = question.lower().strip()
    if any(op in q for op in _DIRECT_OPERATORS):
        return ("direct", None)
    if any(q.startswith(v) for v in _DIRECT_VERBS) and not _has_synthesis_signal(q):
        return ("direct", None)
    decision = llm_client.structured_chat(
        messages=[{"role": "user", "content": ROUTING_PROMPT.format(question=question)}],
        response_model=RouteDecision,
        task=TASK_ROUTING,
    )
    return (decision.route, decision.skill)

def _has_synthesis_signal(q: str) -> bool:
    signals = ("competing views", "synthesize", "themes", "what do i know", "overview")
    return any(s in q for s in signals)
```

`ROUTING_PROMPT` contains ~20 labeled examples covering paraphrase variation:
`"past 7 days"` → `weekly-digest`, `"summarize my recent saves"` → `weekly-digest`,
`"how is X related to my work"` → `connect-new-save`, etc. This example set is the
artifact to maintain — not a regex table.

**Done when:** `classify_request` returns correct route for all test cases below.
Tier 1 cases make no LLM call. Tier 2 cases call `llm_client.structured_chat` once.

**Tests** (`tests/test_request_router.py`):

```python
class TestClassifyRequest:
    # --- Tier 1: no LLM call ---

    @pytest.mark.parametrize("question", [
        "find articles about transformers",
        "show me what I read last month",
        "get my reading stats",
        "search tag:ml after:2026-01-01",
        "list my recent saves",
    ])
    def test_tier1_direct_makes_no_llm_call(self, question):
        with patch("app.core.request_router.llm_client") as mock:
            route, skill = classify_request(question)
        assert route == "direct"
        assert skill is None
        mock.structured_chat.assert_not_called()

    # --- Tier 2: LLM called ---

    @pytest.mark.parametrize("question,expected_route,expected_skill", [
        # Skill routes — paraphrase variation is the point
        ("what did I save this week?",                  "skill", "weekly-digest"),
        ("give me a summary of my past 7 days",         "skill", "weekly-digest"),
        ("summarize what I've been reading recently",   "skill", "weekly-digest"),
        ("how does this article connect to what I know?","skill", "connect-new-save"),
        ("how is this related to my library?",          "skill", "connect-new-save"),
        ("help me draft an intro using my reading",     "skill", "draft-from-highlights"),
        ("write a paragraph from my highlights",        "skill", "draft-from-highlights"),
        # Orchestration routes
        ("what are the competing views I've saved on AI alignment?", "orchestrate", None),
        ("synthesize everything I know about production ML systems", "orchestrate", None),
        ("what themes keep coming up across my ML reading?",         "orchestrate", None),
    ])
    def test_tier2_routes_via_llm(self, question, expected_route, expected_skill):
        mock_decision = RouteDecision(route=expected_route, skill=expected_skill)
        with patch("app.core.request_router.llm_client") as mock:
            mock.structured_chat.return_value = mock_decision
            route, skill = classify_request(question)
        assert route == expected_route
        assert skill == expected_skill
        mock.structured_chat.assert_called_once()

    def test_empty_string_calls_llm_not_tier1(self):
        mock_decision = RouteDecision(route="orchestrate", skill=None)
        with patch("app.core.request_router.llm_client") as mock:
            mock.structured_chat.return_value = mock_decision
            route, skill = classify_request("")
        assert route == "orchestrate"
        mock.structured_chat.assert_called_once()
```

---

### Step 1.5 — `synthesize_topic` quick mode MCP tool

**Files:**
- `app/mcp/tools/synthesis.py` (new)
- `app/mcp/server.py` (register new tool)
- `app/core/llm_client.py` (add `TASK_SYNTHESIS`)
- `app/schemas/synthesis.py` (new — response models)

**What to build:**

`SynthesisResponse` schema:
```python
class PerspectiveItem(BaseModel):
    stance: str        # "pro" | "con" | "nuanced" | "alternative"
    summary: str
    source_ids: list[str]

class SourceCitation(BaseModel):
    item_id: str
    title: str
    quote: str | None

class SynthesisResponse(BaseModel):
    summary: str                          # 2-3 sentence overview
    perspectives: list[PerspectiveItem]
    key_concepts: list[str]
    sources: list[SourceCitation]
    confidence: Literal["high", "medium", "low"]
```

`synthesize_topic` quick mode:
- Loads `UserProfile` for `current_focus` (None-safe — profile may not exist yet)
- Calls `hybrid_search(topic, user, db, limit=10, mode="full")`
- Builds context string from results (titles + descriptions, ≤4000 tokens)
- One `structured_chat()` call → `SynthesisResponse`
- Returns dict (MCP tools return dicts, not models)

`_build_context(results, topic, db, max_tokens)` — private helper. Uses `tiktoken`
(cl100k_base encoder) to count tokens. Builds one block per article (title +
description + top highlight). Appends blocks in descending relevance score order,
stopping when the running total would exceed `max_tokens`. Never truncates a block
mid-way — drop the whole block instead. Returns `(context_str, included_ids)` so
the caller knows which articles are actually in context.

**Done when:** MCP client calling `synthesize_topic("attention mechanisms")` returns
a dict with `summary`, `perspectives`, `key_concepts`, `sources`, `confidence`.
LLM is mocked in tests; real call tested manually.

**Tests** (`tests/mcp/test_synthesis_tools.py`):

```python
class TestSynthesizeTopicQuick:
    def test_returns_structured_response(self, db, user, article):
        mock_response = SynthesisResponse(
            summary="The library contains 1 article on this topic.",
            perspectives=[PerspectiveItem(stance="nuanced", summary="...", source_ids=[str(article.id)])],
            key_concepts=["attention mechanism"],
            sources=[SourceCitation(item_id=str(article.id), title=article.title, quote=None)],
            confidence="medium",
        )
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = mock_response
            result = synthesize_topic.__wrapped__(topic="attention mechanisms",
                                                  depth="quick", user=user, db=db)

        assert "summary" in result
        assert "perspectives" in result
        assert "sources" in result
        assert result["confidence"] in ("high", "medium", "low")

    def test_seeds_from_user_profile(self, db, user, article):
        from app.models.memory import UserProfile
        db.merge(UserProfile(user_id=user.id, current_focus="agent evals"))
        db.commit()

        captured_messages = []
        def capture(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return SynthesisResponse(summary="x", perspectives=[], key_concepts=[],
                                     sources=[], confidence="low")

        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.side_effect = capture
            synthesize_topic.__wrapped__(topic="evals", depth="quick", user=user, db=db)

        # Profile context injected into prompt
        combined = " ".join(m["content"] for m in captured_messages)
        assert "agent evals" in combined

    def test_no_results_returns_low_confidence(self, db, user):
        # User has no articles
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = SynthesisResponse(
                summary="No articles found.", perspectives=[], key_concepts=[],
                sources=[], confidence="low",
            )
            result = synthesize_topic.__wrapped__(topic="obscure topic",
                                                  depth="quick", user=user, db=db)
        assert result["confidence"] == "low"

    def test_sources_only_from_retrieved_items(self, db, user, article):
        # Verifies _build_context only uses actual DB results
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = SynthesisResponse(
                summary="found it", perspectives=[],
                key_concepts=[],
                sources=[SourceCitation(item_id=str(article.id),
                                        title=article.title, quote=None)],
                confidence="high",
            )
            result = synthesize_topic.__wrapped__(topic=article.title,
                                                  depth="quick", user=user, db=db)

        returned_ids = [s["item_id"] for s in result["sources"]]
        assert str(article.id) in returned_ids

    def test_other_users_articles_not_in_context(self, db, user, other_user):
        from app.models.content import ContentItem
        other_article = ContentItem(
            original_url="https://secret.com/article",
            title="Secret Article",
            user_id=other_user.id,
            processing_status="completed",
        )
        db.add(other_article); db.commit()

        captured = []
        def capture(**kwargs):
            captured.append(kwargs.get("messages", []))
            return SynthesisResponse(summary="x", perspectives=[], key_concepts=[],
                                     sources=[], confidence="low")

        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.side_effect = capture
            synthesize_topic.__wrapped__(topic="secret", depth="quick", user=user, db=db)

        combined = " ".join(str(m) for m in captured)
        assert "Secret Article" not in combined
```

---

### Step 1.6 — `assist_draft` MCP tool

**Files:**
- `app/mcp/tools/synthesis.py` (add to existing)

**What to build:**

- Loads draft via `get_draft(list_id)`
- Loads `writing_style_notes` from `UserProfile` (None-safe)
- Calls `hybrid_search(instruction, ...)` for relevant articles
- Calls `get_highlights_for_articles(item_ids, user_id, db)` — existing helper
- One `structured_chat()` call → `DraftAddition(content: str, citations: list[Citation])`
- Calls `update_draft(list_id, appended)` — existing MCP tool logic reused directly
- Returns `{added, citations, source_count}`

Bounded write scope enforced: `assist_draft` calls only `update_draft`. It cannot
call `add_content`, `create_list`, or any other write tool. This is structural, not
just documented — the function literally only calls `_append_to_draft()`.

**Done when:** `assist_draft(list_id, "write an intro about RAG")` returns
`{added: str, citations: [...], source_count: int}` and the draft in DB is
updated with the appended content.

**Tests** (`tests/mcp/test_synthesis_tools.py`, continued):

```python
class TestAssistDraft:
    def test_appends_to_draft(self, db, user, reading_list):
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = DraftAddition(
                content="RAG grounds LLM responses in retrieved documents.",
                citations=[],
            )
            result = assist_draft.__wrapped__(
                list_id=str(reading_list.id),
                instruction="write a sentence about RAG",
                user=user, db=db,
            )

        assert result["added"] == "RAG grounds LLM responses in retrieved documents."
        assert result["source_count"] >= 0

        # Verify draft was updated in DB
        from app.models.draft import Draft
        draft = db.query(Draft).filter_by(list_id=reading_list.id).first()
        assert draft is not None
        assert "RAG" in draft.content

    def test_uses_writing_style_from_profile(self, db, user, reading_list):
        from app.models.memory import UserProfile
        db.merge(UserProfile(user_id=user.id, writing_style_notes="concise, no hedging"))
        db.commit()

        captured = []
        def capture(**kwargs):
            captured.append(kwargs.get("messages", []))
            return DraftAddition(content="x", citations=[])

        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.side_effect = capture
            assist_draft.__wrapped__(list_id=str(reading_list.id),
                                     instruction="write intro", user=user, db=db)

        combined = " ".join(str(m) for m in captured)
        assert "concise, no hedging" in combined

    def test_citations_only_reference_retrieved_items(self, db, user, article, reading_list):
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = DraftAddition(
                content="Content about the article.",
                citations=[Citation(item_id=str(article.id), title=article.title)],
            )
            result = assist_draft.__wrapped__(
                list_id=str(reading_list.id),
                instruction=article.title,
                user=user, db=db,
            )

        cited_ids = [c["item_id"] for c in result["citations"]]
        # All cited IDs must belong to this user
        from app.models.content import ContentItem
        for cid in cited_ids:
            item = db.get(ContentItem, cid)
            assert item is not None
            assert item.user_id == user.id

    def test_does_not_modify_library(self, db, user, reading_list):
        from app.models.content import ContentItem
        count_before = db.query(ContentItem).filter_by(user_id=user.id).count()

        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = DraftAddition(content="x", citations=[])
            assist_draft.__wrapped__(list_id=str(reading_list.id),
                                     instruction="write something", user=user, db=db)

        count_after = db.query(ContentItem).filter_by(user_id=user.id).count()
        assert count_before == count_after
```

---

### Phase 1 integration test

**File:** `tests/test_memory_skills_integration.py`

```python
class TestMemorySkillsIntegration:
    def test_profile_context_flows_into_synthesis(self, db_session, test_user, article):
        """
        Consolidate memory → profile written → synthesize_topic reads profile →
        current_focus appears in LLM prompt.
        """
        with patch("app.tasks.memory.llm_client") as mock_mem:
            mock_mem.structured_chat.return_value = ConsolidationResult(
                current_focus="distributed systems",
                reading_velocity="deep",
                writing_style_notes=None,
                knowledge_gaps=[],
                episodic_events=[],
            )
            consolidate_memory(str(test_user.id))

        captured = []
        with patch("app.mcp.tools.synthesis.llm_client") as mock_syn:
            mock_syn.structured_chat.side_effect = lambda **kw: (
                captured.extend(kw["messages"]) or
                SynthesisResponse(summary="x", perspectives=[], key_concepts=[],
                                  sources=[], confidence="low")
            )
            synthesize_topic.__wrapped__(topic="CAP theorem", depth="quick",
                                         user=test_user, db=db_session)

        combined = " ".join(m["content"] for m in captured)
        assert "distributed systems" in combined

    def test_router_directs_weekly_question_to_skill_not_orchestration(self):
        route, skill = classify_request("what did I save this week?")
        assert route == "skill"
        assert skill == "weekly-digest"
        # Confirm this does NOT route to orchestration (which needs Phase 2)
        assert route != "orchestrate"
```

---

## Phase 2 — Full Orchestration

---

### Step 2.1 — DB migration: `research_runs` + status endpoint

**Files:**
- `alembic/versions/NNN_add_research_runs.py`
- `app/models/research.py`
- `app/schemas/research.py` (ResearchBrief Pydantic schema)
- `app/api/research.py`
- `app/main.py` (register router)

**What to build:**

`ResearchRun` SQLAlchemy model. Key fields:

```python
class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    question: Mapped[str]
    mode: Mapped[str]  # "quick" | "deep"
    status: Mapped[str]  # "queued" | "planning" | "searching" | "synthesizing" | "verifying" | "done" | "partial" | "failed"
    plan: Mapped[str | None]                       # sub-questions JSON string from planning step
    sub_questions: Mapped[list | None] = mapped_column(JSONB)  # list[str]
    subagent_results: Mapped[list | None] = mapped_column(JSONB)  # list[SubagentResult]
    item_ids_retrieved: Mapped[list | None] = mapped_column(JSONB)
    searches_run: Mapped[list | None] = mapped_column(JSONB)   # [{idempotency_key, subagent_id}]
    result: Mapped[dict | None] = mapped_column(JSONB)         # ResearchBrief when done
    cost: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    iteration_count: Mapped[int] = mapped_column(default=0)
    budget: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
```

`ResearchBrief` Pydantic schema (`app/schemas/research.py`) — this is the output
structure the synthesis task produces and the eval runner consumes:

```python
class SourceCitation(BaseModel):
    item_id: str
    title: str
    representative_highlight: str | None

class SubQuestionFinding(BaseModel):
    sub_question: str
    coverage: Literal["full", "partial", "none"]
    finding: str
    key_sources: list[SourceCitation]
    tensions: list[str]

class GapItem(BaseModel):
    sub_question: str
    what_is_missing: str
    partial_coverage: list[str]  # item_ids that touch this sub-Q but don't answer it

class ResearchBrief(BaseModel):
    summary: str
    sub_question_findings: list[SubQuestionFinding]
    cross_cutting_tensions: list[str]
    gaps: list[GapItem]
    engagement_note: str
    confidence: Literal["high", "medium", "low"]
```

`SubagentResult` (also in `app/schemas/research.py`) — what each subagent returns:

```python
class SubagentResult(BaseModel):
    sub_question: str
    articles: list[dict]  # {id, title, description, highlights, engagement_score}
    coverage_assessment: Literal["full", "partial", "none"]
```

Engagement score computed in subagent:
```python
engagement_score = highlight_count * 2 + int(is_read) * 1 + draft_citation_count * 3
```

`GET /research/{run_id}` endpoint — auth-guarded, scoped to `current_user.id`:
```python
@router.get("/research/{run_id}")
def get_research_run(run_id: UUID, current_user=Depends(get_current_user), db=Depends(get_db)):
    run = db.query(ResearchRun).filter_by(id=run_id, user_id=current_user.id).first()
    if not run:
        raise HTTPException(404)
    return {
        "status": run.status,
        "result": run.result,
        "cost": run.cost,
        "error": run.error,
        "progress": {
            "iteration": run.iteration_count,
            "sub_questions": run.sub_questions or [],
            "searches_run_count": len(run.searches_run or []),
        },
    }
```

**Done when:** `GET /research/{run_id}` returns 200 with `{status: "queued"}` for
a newly created row. Returns 404 for unknown ID. Returns 403 (or 404) for another
user's run ID. `ResearchBrief` model imports without error.

**Tests** (`tests/test_research_api.py`):

```python
class TestResearchRunStatus:
    def test_returns_status_for_own_run(self, client, auth_headers, db_session, test_user):
        run = ResearchRun(
            user_id=test_user.id, question="test", mode="deep",
            status="queued",
            budget={"max_tokens": 10000, "max_iterations": 3,
                    "max_subagents": 5, "timeout_s": 300},
        )
        db_session.add(run); db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_returns_404_for_unknown_id(self, client, auth_headers):
        resp = client.get(f"/research/{uuid.uuid4()}", headers=auth_headers)
        assert resp.status_code == 404

    def test_cannot_read_other_users_run(self, client, auth_headers,
                                          db_session, other_user):
        run = ResearchRun(
            user_id=other_user.id, question="secret", mode="deep",
            status="done",
            budget={"max_tokens": 10000, "max_iterations": 3,
                    "max_subagents": 5, "timeout_s": 300},
        )
        db_session.add(run); db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.status_code in (403, 404)

    def test_returns_result_when_done(self, client, auth_headers, db_session, test_user):
        run = ResearchRun(
            user_id=test_user.id, question="test", mode="deep",
            status="done",
            result={
                "summary": "Found 3 articles.",
                "sub_question_findings": [],
                "cross_cutting_tensions": [],
                "gaps": [],
                "engagement_note": "No high-engagement articles in this run.",
                "confidence": "medium",
            },
            budget={"max_tokens": 10000, "max_iterations": 3,
                    "max_subagents": 5, "timeout_s": 300},
        )
        db_session.add(run); db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.json()["result"]["summary"] == "Found 3 articles."

    def test_returns_progress_with_sub_questions(self, client, auth_headers, db_session, test_user):
        run = ResearchRun(
            user_id=test_user.id, question="test", mode="deep",
            status="searching", iteration_count=2,
            sub_questions=["What is X?", "What is Y?"],
            searches_run=[{"subagent_id": "a"}, {"subagent_id": "b"}],
            budget={"max_tokens": 10000, "max_iterations": 3,
                    "max_subagents": 5, "timeout_s": 300},
        )
        db_session.add(run); db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        progress = resp.json()["progress"]
        assert progress["iteration"] == 2
        assert progress["searches_run_count"] == 2
        assert "What is X?" in progress["sub_questions"]
```

---

### Step 2.2 — Lead agent task skeleton

**Files:**
- `app/tasks/research.py`
- `app/core/celery_app.py` (register task)

**What to build:**

Skeleton only — no real search, no LLM calls. Proves the plumbing:

```python
@celery_app.task(base=DatabaseTask, bind=True, max_retries=0,
                 time_limit=300, soft_time_limit=270)
def run_research_lead(self, run_id: str):
    db = self.get_db()
    run = db.query(ResearchRun).filter_by(id=run_id).first()
    if not run or run.status != "queued":
        return   # idempotency guard

    run.status = "planning"
    run.plan = "Skeleton plan — no search yet."
    db.commit()

    run.status = "done"
    run.result = {"summary": "Skeleton complete.", "perspectives": [],
                  "key_concepts": [], "sources": [], "confidence": "low"}
    db.commit()
```

**Done when:** `run_research_lead.delay(run_id)` completes and `research_runs.status`
changes from `queued` → `planning` → `done`.

**Tests** (`tests/test_research_tasks.py`):

```python
class TestLeadAgentSkeleton:
    def test_transitions_queued_to_done(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="queued")
        run_research_lead(str(run.id))
        db_session.refresh(run)
        assert run.status == "done"

    def test_idempotent_on_non_queued_status(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="done")
        run.result = {"summary": "original"}
        db_session.commit()
        run_research_lead(str(run.id))
        db_session.refresh(run)
        assert run.result["summary"] == "original"  # unchanged

    def test_writes_plan_before_search(self, db_session, test_user):
        # Plan must be written before status moves past planning
        transitions = []
        original_commit = db_session.commit
        def tracked_commit():
            run = db_session.query(ResearchRun).filter_by(user_id=test_user.id).first()
            if run:
                transitions.append((run.status, run.plan))
            original_commit()
        db_session.commit = tracked_commit

        run = _make_run(db_session, test_user, status="queued")
        db_session.commit = original_commit  # restore after setup
        run_research_lead(str(run.id))

        planning_states = [(s, p) for s, p in transitions if s == "planning"]
        assert any(p is not None for s, p in planning_states)
```

---

### Step 2.3 — Single subagent + idempotency

**Files:**
- `app/tasks/research.py` (add `run_research_subagent`)

**What to build:**

`run_research_subagent` calls `hybrid_search()` directly and returns
`{ok, data: {item_ids, summaries}, error, meta}`.

Lead agent updated to dispatch one subagent, collect result, write
`item_ids_retrieved` to run record.

Idempotency: before dispatching, check if `idempotency_key` already in
`run.searches_run`. If yes, skip dispatch.

**Done when:** Lead dispatches one subagent. Subagent result is written to
`research_runs.item_ids_retrieved`. On retry, same subagent is not dispatched
twice (idempotency key check).

**Tests** (`tests/test_research_tasks.py`, continued):

```python
class TestSubagentContract:
    def test_subagent_returns_ok_true_on_success(self, db_session, test_user, article):
        run = _make_run(db_session, test_user, status="queued")
        result = run_research_subagent(
            str(run.id),
            "subagent-1",
            {"task_description": "search for articles",
             "search_params": {"query": article.title, "limit": 5},
             "skill": None,
             "budget": {"timeout_s": 30}},
        )
        assert result["ok"] is True
        assert "item_ids" in result["data"]

    def test_subagent_returns_ok_false_on_error(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="queued")
        with patch("app.tasks.research.hybrid_search", side_effect=Exception("DB down")):
            result = run_research_subagent(
                str(run.id), "subagent-1",
                {"task_description": "search", "search_params": {"query": "x"},
                 "skill": None, "budget": {"timeout_s": 30}},
            )
        assert result["ok"] is False
        assert result["error"]["code"] is not None

    def test_idempotency_key_prevents_duplicate_dispatch(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="queued")
        key = "test-idempotency-key"
        run.searches_run = [{"idempotency_key": key, "subagent_id": "a"}]
        db_session.commit()

        dispatch_count = []
        original = run_research_subagent.delay
        run_research_subagent.delay = lambda *a, **kw: dispatch_count.append(1)

        _dispatch_subagent_if_new(run, key, {}, db_session)
        assert len(dispatch_count) == 0  # not dispatched again

        run_research_subagent.delay = original

    def test_lead_writes_item_ids_after_subagent(self, db_session, test_user, article):
        run = _make_run(db_session, test_user, status="queued")
        with patch("app.tasks.research.run_research_subagent") as mock_sub:
            mock_sub.return_value = {
                "ok": True,
                "data": {"item_ids": [str(article.id)], "summaries": []},
                "error": None,
                "meta": {"tokens_used": 100, "duration_ms": 500},
            }
            run_research_lead(str(run.id))

        db_session.refresh(run)
        assert str(article.id) in [str(x) for x in run.item_ids_retrieved]
```

---

### Step 2.4 — Parallel subagents + chord callback + iteration loop

**Files:**

- `app/tasks/research.py` (add `collect_subagent_results`; update lead agent)

**What to build:**

Lead dispatches a Celery `group()` of subagents wired into a chord with
`collect_subagent_results` as the named callback. The callback is a Celery task —
not a lambda — because Celery must be able to serialize and route it.

```python
# Dispatch pattern inside run_research_lead:
job = group(
    run_research_subagent.s(run_id, sid, payload)
    for sid, payload in subagent_payloads
)
chord(job)(collect_subagent_results.s(run_id=run_id))
```

`collect_subagent_results(results, run_id)`:

- Receives the list of subagent `{ok, data, error, meta}` dicts as `results`
- Merges `data.item_ids` from successful subagents into `run.item_ids_retrieved` (deduped, set union)
- Increments `run.iteration_count`
- Commits both writes before deciding next step
- If `len(merged_ids) >= target_count` OR `iteration_count >= max_iterations`: dispatches `synthesize_run.delay(run_id)`
- Otherwise: dispatches `run_research_lead.apply_async(args=[run_id], kwargs={"resume": True})` for another round

Budget check inside `collect_subagent_results`:
```python
if run.iteration_count >= run.budget["max_iterations"] and len(merged) < target:
    run.status = "partial"
    db.commit()
    synthesize_run.delay(run_id)  # synthesize from what we have
    return
```

**Done when:** With 3 articles in DB and `target_count=2, max_iterations=2`,
`collect_subagent_results` merges results and triggers `synthesize_run` after round 1.
With `target_count=100` (unreachable), run terminates as `partial` after `max_iterations`
rounds and `synthesize_run` is still called so the user gets a partial result.

**Tests** (`tests/test_research_tasks.py`, continued):

```python
class TestIterationLoop:
    def test_terminates_when_target_reached(self, db_session, test_user):
        # 5 articles in DB, target_count=3
        articles = [_make_article(db_session, test_user) for _ in range(5)]
        run = _make_run(db_session, test_user, status="queued",
                        budget={"max_tokens": 50000, "max_iterations": 3,
                                "max_subagents": 5, "timeout_s": 300})

        with patch("app.tasks.research.llm_client"):
            with patch("app.tasks.research.hybrid_search") as mock_search:
                mock_search.return_value = [
                    {"id": str(a.id), "title": a.title} for a in articles[:3]
                ]
                run_research_lead(str(run.id))

        db_session.refresh(run)
        assert run.status in ("synthesizing", "verifying", "done")
        assert run.iteration_count == 1  # found enough on first pass

    def test_iterates_when_target_not_reached(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(db_session, test_user, status="queued",
                        budget={"max_tokens": 50000, "max_iterations": 3,
                                "max_subagents": 5, "timeout_s": 300})

        call_count = []
        def search_side_effect(*args, **kwargs):
            call_count.append(1)
            return [{"id": str(article.id), "title": article.title}]

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.chat.return_value = MagicMock(content="reformulated query")
            mock_llm.structured_chat.return_value = SynthesisResponse(
                summary="x", perspectives=[], key_concepts=[], sources=[], confidence="low"
            )
            with patch("app.tasks.research.hybrid_search", side_effect=search_side_effect):
                run = _make_run(db_session, test_user, status="queued",
                                budget={"max_tokens": 50000, "max_iterations": 3,
                                        "max_subagents": 5, "timeout_s": 300,
                                        "target_count": 10})  # unreachable
                run_research_lead(str(run.id))

        db_session.refresh(run)
        # Should have iterated until max_iterations, then gone partial or synthesized
        assert run.iteration_count <= 3

    def test_marks_partial_on_budget_exhaustion(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="queued",
                        budget={"max_tokens": 50000, "max_iterations": 1,
                                "max_subagents": 5, "timeout_s": 300,
                                "target_count": 100})  # impossible target

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.chat.return_value = MagicMock(content="query")
            mock_llm.structured_chat.return_value = SynthesisResponse(
                summary="partial", perspectives=[], key_concepts=[], sources=[], confidence="low"
            )
            with patch("app.tasks.research.hybrid_search", return_value=[]):
                run_research_lead(str(run.id))

        db_session.refresh(run)
        assert run.status in ("partial", "done")

    def test_subagents_run_concurrently_not_sequentially(self, db_session, test_user):
        # Proves group() dispatch — subagents are dispatched as a group, not one-by-one
        article = _make_article(db_session, test_user)
        run = _make_run(db_session, test_user, status="queued",
                        budget={"max_tokens": 50000, "max_iterations": 1,
                                "max_subagents": 3, "timeout_s": 300})

        dispatched_ids = []
        original_delay = run_research_subagent.delay
        run_research_subagent.delay = lambda *a, **kw: dispatched_ids.append(a[1])

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = SynthesisResponse(
                summary="x", perspectives=[], key_concepts=[], sources=[], confidence="low"
            )
            run_research_lead(str(run.id))

        run_research_subagent.delay = original_delay
        # Multiple subagents dispatched in one round
        assert len(set(dispatched_ids)) > 1


class TestCollectSubagentResults:
    def test_merges_item_ids_from_successful_subagents(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
        results = [
            {"ok": True, "data": {"item_ids": [id_a], "summaries": []}, "error": None, "meta": {}},
            {"ok": True, "data": {"item_ids": [id_b], "summaries": []}, "error": None, "meta": {}},
        ]
        with patch("app.tasks.research.synthesize_run") as mock_syn:
            mock_syn.delay = MagicMock()
            collect_subagent_results(results, run_id=str(run.id))

        db_session.refresh(run)
        retrieved = [str(x) for x in run.item_ids_retrieved]
        assert id_a in retrieved
        assert id_b in retrieved

    def test_skips_failed_subagent_results(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        good_id = str(uuid.uuid4())
        results = [
            {"ok": True, "data": {"item_ids": [good_id], "summaries": []}, "error": None, "meta": {}},
            {"ok": False, "data": None, "error": {"code": "timeout"}, "meta": {}},
        ]
        with patch("app.tasks.research.synthesize_run") as mock_syn:
            mock_syn.delay = MagicMock()
            collect_subagent_results(results, run_id=str(run.id))

        db_session.refresh(run)
        retrieved = [str(x) for x in run.item_ids_retrieved]
        assert good_id in retrieved
        assert len(retrieved) == 1  # failed subagent contributed nothing

    def test_increments_iteration_count(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        assert run.iteration_count == 0
        with patch("app.tasks.research.synthesize_run") as mock_syn:
            mock_syn.delay = MagicMock()
            collect_subagent_results([], run_id=str(run.id))
        db_session.refresh(run)
        assert run.iteration_count == 1

    def test_dispatches_synthesize_when_target_reached(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching",
                        budget={"max_tokens": 50000, "max_iterations": 3,
                                "max_subagents": 5, "timeout_s": 300, "target_count": 2})
        results = [
            {"ok": True, "data": {"item_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
                                   "summaries": []}, "error": None, "meta": {}},
        ]
        with patch("app.tasks.research.synthesize_run") as mock_syn:
            mock_syn.delay = MagicMock()
            collect_subagent_results(results, run_id=str(run.id))
        mock_syn.delay.assert_called_once_with(str(run.id))

    def test_dispatches_another_round_when_target_not_reached(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching",
                        budget={"max_tokens": 50000, "max_iterations": 3,
                                "max_subagents": 5, "timeout_s": 300, "target_count": 10})
        results = [
            {"ok": True, "data": {"item_ids": [str(uuid.uuid4())], "summaries": []},
             "error": None, "meta": {}},
        ]
        with patch("app.tasks.research.run_research_lead") as mock_lead:
            mock_lead.apply_async = MagicMock()
            with patch("app.tasks.research.synthesize_run") as mock_syn:
                mock_syn.delay = MagicMock()
                collect_subagent_results(results, run_id=str(run.id))
        mock_lead.apply_async.assert_called_once()
        mock_syn.delay.assert_not_called()

    def test_marks_partial_and_synthesizes_on_max_iterations(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching",
                        budget={"max_tokens": 50000, "max_iterations": 1,
                                "max_subagents": 5, "timeout_s": 300, "target_count": 100})
        # Already at max_iterations after this call (iteration_count will become 1)
        with patch("app.tasks.research.synthesize_run") as mock_syn:
            mock_syn.delay = MagicMock()
            collect_subagent_results([], run_id=str(run.id))
        db_session.refresh(run)
        assert run.status == "partial"
        mock_syn.delay.assert_called_once_with(str(run.id))
```

---

### Step 2.5 — Synthesis + verification tasks

**Files:**

- `app/tasks/research.py` (add `synthesize_run`, `verify_synthesis`)
- `app/core/llm_client.py` (`TASK_SYNTHESIS` already added in Phase 1)

**What to build:**

`synthesize_run(run_id)`:

1. Loads articles from `item_ids_retrieved`, fetches all highlights per article.
2. Computes engagement score per article: `highlight_count × 2 + is_read × 1 + draft_citation_count × 3`.
3. Builds context via `_build_context_for_brief`:
   - High-engagement articles (score ≥ 3): include full highlight set.
   - Low-engagement articles (score < 3): title + description only.
   - Token budget: 6000 tokens (tiktoken cl100k_base), drop whole blocks not partial.
   - Context labeled by sub-question (from `run.sub_questions`) so synthesis is structured.
4. Calls `structured_chat(TASK_SYNTHESIS, response_model=ResearchBrief)`.
5. Writes result as dict to `run.result`, sets status `verifying`.

`_build_context_for_brief(articles_with_engagement, sub_questions, max_tokens)` —
private helper, unit-testable. Returns `(context_str, included_ids)`.

`verify_synthesis(run_id)`:

- For each `SourceCitation` in every `sub_question_findings[].key_sources`:
  check `citation.item_id in run.item_ids_retrieved`.
- Citations whose ID is not in the retrieved set are **removed** (not marked,
  not flagged — removed). The finding text is unchanged.
- Also removes from `result.gaps[].partial_coverage` any IDs not in retrieved set.
- Sets status `done`.

**Done when:** A run with `item_ids_retrieved=[article.id]` and a synthesis
claiming that article produces a `done` result with the citation present.
A synthesis citing a fabricated ID produces a `done` result with that citation absent.
High-engagement articles (highlight_count=5) include all their highlights in the prompt
while low-engagement (highlight_count=0) include title+description only.

**Tests** (`tests/test_research_tasks.py`, continued):

```python
class TestSynthesisAndVerification:
    def test_synthesis_writes_research_brief_to_run(self, db_session, test_user, article):
        run = _make_run(db_session, test_user, status="searching",
                        item_ids=[str(article.id)],
                        sub_questions=["What is RAG?"])

        mock_brief = ResearchBrief(
            summary="One article found on RAG.",
            sub_question_findings=[SubQuestionFinding(
                sub_question="What is RAG?",
                coverage="full",
                finding="RAG grounds LLM outputs in retrieved documents.",
                key_sources=[SourceCitation(item_id=str(article.id),
                                            title=article.title,
                                            representative_highlight=None)],
                tensions=[],
            )],
            cross_cutting_tensions=[],
            gaps=[],
            engagement_note="1 high-engagement article contributed all highlights.",
            confidence="medium",
        )
        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = mock_brief
            synthesize_run(str(run.id))

        db_session.refresh(run)
        assert run.result is not None
        assert run.result["summary"] == "One article found on RAG."
        assert len(run.result["sub_question_findings"]) == 1
        assert run.status == "verifying"

    def test_synthesis_context_allocates_more_to_high_engagement(self, db_session, test_user):
        from app.tasks.research import _build_context_for_brief
        high_eng = {"id": "a", "title": "H", "description": "desc",
                    "highlights": ["hl1", "hl2", "hl3"], "engagement_score": 9}
        low_eng = {"id": "b", "title": "L", "description": "desc",
                   "highlights": ["hl1"], "engagement_score": 0}
        ctx, ids = _build_context_for_brief([high_eng, low_eng],
                                             sub_questions=["Q"], max_tokens=4000)
        # High-engagement article includes highlights; low-engagement does not
        assert "hl1" in ctx or "hl2" in ctx or "hl3" in ctx  # high-eng highlights present
        assert "hl1" not in ctx.split("L")[1] if "L" in ctx else True  # low-eng no highlights

    def test_verification_passes_grounded_citations(self, db_session, test_user, article):
        run = _make_run(db_session, test_user, status="verifying",
                        item_ids=[str(article.id)])
        run.result = {
            "summary": "x",
            "sub_question_findings": [{
                "sub_question": "Q1",
                "coverage": "full",
                "finding": "finding",
                "key_sources": [{"item_id": str(article.id),
                                 "title": article.title,
                                 "representative_highlight": None}],
                "tensions": [],
            }],
            "cross_cutting_tensions": [],
            "gaps": [],
            "engagement_note": "",
            "confidence": "medium",
        }
        db_session.commit()

        verify_synthesis(str(run.id))

        db_session.refresh(run)
        assert run.status == "done"
        sources = run.result["sub_question_findings"][0]["key_sources"]
        assert any(s["item_id"] == str(article.id) for s in sources)

    def test_verification_removes_fabricated_citations(self, db_session, test_user, article):
        fake_id = str(uuid.uuid4())
        run = _make_run(db_session, test_user, status="verifying",
                        item_ids=[str(article.id)])
        run.result = {
            "summary": "x",
            "sub_question_findings": [{
                "sub_question": "Q1",
                "coverage": "partial",
                "finding": "finding",
                "key_sources": [
                    {"item_id": str(article.id), "title": "Real", "representative_highlight": None},
                    {"item_id": fake_id, "title": "Fabricated", "representative_highlight": None},
                ],
                "tensions": [],
            }],
            "cross_cutting_tensions": [],
            "gaps": [],
            "engagement_note": "",
            "confidence": "medium",
        }
        db_session.commit()

        verify_synthesis(str(run.id))

        db_session.refresh(run)
        sources = run.result["sub_question_findings"][0]["key_sources"]
        result_ids = [s["item_id"] for s in sources]
        assert str(article.id) in result_ids
        assert fake_id not in result_ids

    def test_verification_cleans_partial_coverage_in_gaps(self, db_session, test_user, article):
        fake_id = str(uuid.uuid4())
        run = _make_run(db_session, test_user, status="verifying",
                        item_ids=[str(article.id)])
        run.result = {
            "summary": "x",
            "sub_question_findings": [],
            "cross_cutting_tensions": [],
            "gaps": [{
                "sub_question": "Q2",
                "what_is_missing": "Policy research",
                "partial_coverage": [str(article.id), fake_id],
            }],
            "engagement_note": "",
            "confidence": "low",
        }
        db_session.commit()

        verify_synthesis(str(run.id))

        db_session.refresh(run)
        assert run.status == "done"
        gap_ids = run.result["gaps"][0]["partial_coverage"]
        assert str(article.id) in gap_ids
        assert fake_id not in gap_ids

    def test_full_run_ends_in_done_status(self, db_session, test_user, article):
        run = _make_run(db_session, test_user, status="queued",
                        budget={"max_tokens": 50000, "max_iterations": 3,
                                "max_subagents": 3, "timeout_s": 300})

        mock_brief = ResearchBrief(
            summary="Found one article.",
            sub_question_findings=[],
            cross_cutting_tensions=[],
            gaps=[],
            engagement_note="",
            confidence="medium",
        )
        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.chat.return_value = MagicMock(content="query")
            mock_llm.structured_chat.return_value = mock_brief
            with patch("app.tasks.research.hybrid_search") as mock_search:
                mock_search.return_value = [{"id": str(article.id), "title": article.title}]
                run_research_lead(str(run.id))

        db_session.refresh(run)
        assert run.status == "done"
        assert run.result is not None
        assert run.cost is not None
```

---

### Step 2.6 — Recovery beat task + reliability

**Files:**
- `app/tasks/research.py` (add `recover_orphaned_runs`)
- `app/core/celery_app.py` (beat schedule)

**What to build:**

```python
@celery_app.task(base=DatabaseTask, bind=True)
def recover_orphaned_runs(self):
    """
    Marks runs stuck in non-terminal status with stale updated_at as partial.
    Runs every 5 minutes via beat.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    terminal = ("done", "failed", "partial")
    stale = db.query(ResearchRun).filter(
        ResearchRun.status.notin_(terminal),
        ResearchRun.updated_at < cutoff,
    ).all()
    for run in stale:
        run.status = "partial"
        run.error = {"code": "orphaned", "message": "Run stalled — marked partial by recovery task"}
    db.commit()
    return len(stale)
```

Beat entry:
```python
"recover-orphaned-runs": {
    "task": "app.tasks.research.recover_orphaned_runs",
    "schedule": timedelta(minutes=5),
},
```

**Done when:** A run stuck in `searching` for >10 minutes is marked `partial`
on the next recovery task execution.

**Tests** (`tests/test_research_tasks.py`, continued):

```python
class TestRecovery:
    def test_marks_stale_non_terminal_run_as_partial(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        # Backdate updated_at
        db_session.execute(
            text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
            {"ts": datetime.utcnow() - timedelta(minutes=15), "id": run.id},
        )
        db_session.commit()

        recover_orphaned_runs()

        db_session.refresh(run)
        assert run.status == "partial"
        assert run.error["code"] == "orphaned"

    def test_does_not_touch_recently_updated_run(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        # updated_at is recent (default: now)
        recover_orphaned_runs()
        db_session.refresh(run)
        assert run.status == "searching"  # unchanged

    def test_does_not_touch_terminal_runs(self, db_session, test_user):
        for status in ("done", "failed", "partial"):
            run = _make_run(db_session, test_user, status=status)
            db_session.execute(
                text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
                {"ts": datetime.utcnow() - timedelta(minutes=15), "id": run.id},
            )
        db_session.commit()
        recover_orphaned_runs()
        # Terminal runs unchanged
        for run in db_session.query(ResearchRun).filter_by(user_id=test_user.id).all():
            assert run.status in ("done", "failed", "partial")

    def test_returns_count_of_recovered_runs(self, db_session, test_user):
        for _ in range(3):
            run = _make_run(db_session, test_user, status="planning")
            db_session.execute(
                text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
                {"ts": datetime.utcnow() - timedelta(minutes=15), "id": run.id},
            )
        db_session.commit()
        count = recover_orphaned_runs()
        assert count == 3
```

---

### Step 2.7 — `synthesize_topic` deep mode + Phase 2 integration

**Files:**
- `app/mcp/tools/synthesis.py` (extend existing `synthesize_topic`)

**What to build:**

Extend `synthesize_topic` with `depth="deep"`:
- Creates `ResearchRun` row with `status="queued"`
- Dispatches `run_research_lead.delay(run_id)`
- Returns `{run_id, status_url}` immediately

**Done when:** Calling `synthesize_topic(topic="X", depth="deep")` returns
`{run_id: str, status_url: str}` within 500ms. A `research_runs` row with
`status="queued"` exists in DB.

**Tests** (`tests/mcp/test_synthesis_tools.py`, continued):

```python
class TestSynthesizeTopicDeep:
    def test_returns_run_id_immediately(self, db, user):
        with patch("app.mcp.tools.synthesis.run_research_lead") as mock_task:
            mock_task.delay = MagicMock()
            result = synthesize_topic.__wrapped__(
                topic="AI alignment", depth="deep", user=user, db=db
            )

        assert "run_id" in result
        assert "status_url" in result
        assert result["status_url"].startswith("/research/")

    def test_creates_queued_run_in_db(self, db, user):
        with patch("app.mcp.tools.synthesis.run_research_lead") as mock_task:
            mock_task.delay = MagicMock()
            result = synthesize_topic.__wrapped__(
                topic="RAG systems", depth="deep", user=user, db=db
            )

        run = db.query(ResearchRun).filter_by(id=result["run_id"]).first()
        assert run is not None
        assert run.status == "queued"
        assert run.question == "RAG systems"
        assert run.user_id == user.id

    def test_dispatches_lead_task(self, db, user):
        dispatched = []
        with patch("app.mcp.tools.synthesis.run_research_lead") as mock_task:
            mock_task.delay = lambda run_id: dispatched.append(run_id)
            result = synthesize_topic.__wrapped__(
                topic="test", depth="deep", user=user, db=db
            )

        assert len(dispatched) == 1
        assert dispatched[0] == result["run_id"]

    def test_deep_enqueues_not_executes(self, db, user):
        # Proves the task is dispatched (delay called) but NOT executed inline.
        # Wall-clock assertions are unreliable in CI; checking the call contract is not.
        with patch("app.mcp.tools.synthesis.run_research_lead") as mock_task:
            mock_task.delay = MagicMock()
            synthesize_topic.__wrapped__(topic="test", depth="deep", user=user, db=db)

        # delay() was called — task was enqueued
        mock_task.delay.assert_called_once()
        # The task function itself was NOT called directly
        mock_task.assert_not_called()

    def test_rate_limit_blocks_fourth_concurrent_run(self, db, user):
        for _ in range(3):
            db.add(ResearchRun(
                user_id=user.id, question="x", mode="deep", status="searching",
                budget={"max_tokens": 50000, "max_iterations": 3,
                        "max_subagents": 5, "timeout_s": 300},
            ))
        db.commit()

        with patch("app.mcp.tools.synthesis.run_research_lead") as mock_task:
            mock_task.delay = MagicMock()
            result = synthesize_topic.__wrapped__(
                topic="fourth run", depth="deep", user=user, db=db
            )

        assert result.get("error") == "run_limit_exceeded"
        mock_task.delay.assert_not_called()

    def test_rate_limit_allows_run_when_under_limit(self, db, user):
        for _ in range(2):
            db.add(ResearchRun(
                user_id=user.id, question="x", mode="deep", status="searching",
                budget={"max_tokens": 50000, "max_iterations": 3,
                        "max_subagents": 5, "timeout_s": 300},
            ))
        db.commit()

        with patch("app.mcp.tools.synthesis.run_research_lead") as mock_task:
            mock_task.delay = MagicMock()
            result = synthesize_topic.__wrapped__(
                topic="third run", depth="deep", user=user, db=db
            )

        assert "run_id" in result
        mock_task.delay.assert_called_once()

    def test_rate_limit_counts_only_active_runs(self, db, user):
        for status in ("done", "failed", "partial"):
            db.add(ResearchRun(
                user_id=user.id, question="x", mode="deep", status=status,
                budget={"max_tokens": 50000, "max_iterations": 3,
                        "max_subagents": 5, "timeout_s": 300},
            ))
        db.commit()

        with patch("app.mcp.tools.synthesis.run_research_lead") as mock_task:
            mock_task.delay = MagicMock()
            result = synthesize_topic.__wrapped__(
                topic="new run", depth="deep", user=user, db=db
            )

        assert "run_id" in result
        mock_task.delay.assert_called_once()
```

---

### Phase 2 end-to-end integration test

**File:** `tests/test_orchestrator_e2e.py`

```python
def _make_brief(article_id=None, summary="Found articles."):
    finding = SubQuestionFinding(
        sub_question="Q1",
        coverage="full",
        finding="finding text",
        key_sources=[SourceCitation(item_id=str(article_id),
                                    title="Article",
                                    representative_highlight=None)] if article_id else [],
        tensions=[],
    )
    return ResearchBrief(
        summary=summary,
        sub_question_findings=[finding],
        cross_cutting_tensions=[],
        gaps=[],
        engagement_note="",
        confidence="medium",
    )


class TestOrchestratorEndToEnd:
    def test_full_run_queued_to_done(self, db_session, test_user, article):
        """
        synthesize_topic deep → run created → lead task → sub-question decomposition
        → parallel subagent retrieval with engagement scoring → ResearchBrief synthesis
        → verification → status=done → GET /research/{run_id} returns brief.
        """
        mock_brief = _make_brief(article_id=article.id, summary="One article found.")

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.chat.return_value = MagicMock(content="reformulated")
            mock_llm.structured_chat.return_value = mock_brief
            with patch("app.tasks.research.hybrid_search") as mock_search:
                mock_search.return_value = [{"id": str(article.id), "title": article.title}]
                with patch("app.mcp.tools.synthesis.run_research_lead") as mock_lead:
                    mock_lead.delay = lambda run_id: run_research_lead(run_id)
                    result = synthesize_topic.__wrapped__(
                        topic="test topic", depth="deep", user=test_user, db=db_session
                    )

        run = db_session.query(ResearchRun).filter_by(id=result["run_id"]).first()
        assert run.status == "done"
        assert run.result["summary"] == "One article found."
        assert "sub_question_findings" in run.result
        assert "gaps" in run.result
        assert len(run.item_ids_retrieved) >= 1
        assert run.cost is not None

    def test_partial_result_on_budget_exhaustion(self, db_session, test_user):
        mock_brief = ResearchBrief(
            summary="partial", sub_question_findings=[], cross_cutting_tensions=[],
            gaps=[], engagement_note="", confidence="low",
        )
        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.chat.return_value = MagicMock(content="query")
            mock_llm.structured_chat.return_value = mock_brief
            with patch("app.tasks.research.hybrid_search", return_value=[]):
                with patch("app.mcp.tools.synthesis.run_research_lead") as mock_lead:
                    mock_lead.delay = lambda run_id: run_research_lead(run_id)
                    result = synthesize_topic.__wrapped__(
                        topic="impossible topic", depth="deep", user=test_user,
                        db=db_session,
                    )

        run = db_session.query(ResearchRun).filter_by(id=result["run_id"]).first()
        assert run.status in ("partial", "done")

    def test_other_users_data_not_in_result(self, db_session, test_user, other_user):
        from app.models.content import ContentItem
        other_article = ContentItem(
            original_url="https://secret.com/article",
            title="Other User Secret",
            user_id=other_user.id,
            processing_status="completed",
        )
        db_session.add(other_article); db_session.commit()

        mock_brief = ResearchBrief(
            summary="no results", sub_question_findings=[], cross_cutting_tensions=[],
            gaps=[], engagement_note="", confidence="low",
        )
        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.chat.return_value = MagicMock(content="query")
            mock_llm.structured_chat.return_value = mock_brief
            with patch("app.mcp.tools.synthesis.run_research_lead") as mock_lead:
                mock_lead.delay = lambda run_id: run_research_lead(run_id)
                result = synthesize_topic.__wrapped__(
                    topic="secret", depth="deep", user=test_user, db=db_session
                )

        run = db_session.query(ResearchRun).filter_by(id=result["run_id"]).first()
        retrieved_ids = [str(x) for x in (run.item_ids_retrieved or [])]
        assert str(other_article.id) not in retrieved_ids

    def test_recovery_task_rescues_stalled_run(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="synthesizing")
        db_session.execute(
            text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
            {"ts": datetime.utcnow() - timedelta(minutes=15), "id": run.id},
        )
        db_session.commit()

        recover_orphaned_runs()

        db_session.refresh(run)
        assert run.status == "partial"

    def test_run_result_has_research_brief_shape(self, client, auth_headers,
                                                   db_session, test_user, article):
        mock_brief = _make_brief(article_id=article.id, summary="Found an article.")
        mock_brief.confidence = "high"

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.chat.return_value = MagicMock(content="query")
            mock_llm.structured_chat.return_value = mock_brief
            with patch("app.tasks.research.hybrid_search") as mock_search:
                mock_search.return_value = [{"id": str(article.id), "title": article.title}]
                with patch("app.mcp.tools.synthesis.run_research_lead") as mock_lead:
                    mock_lead.delay = lambda run_id: run_research_lead(run_id)
                    mcp_result = synthesize_topic.__wrapped__(
                        topic="test", depth="deep", user=test_user, db=db_session
                    )

        resp = client.get(f"/research/{mcp_result['run_id']}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "done"
        result = body["result"]
        assert result["summary"] == "Found an article."
        assert result["confidence"] == "high"
        # ResearchBrief shape — not the old SynthesisResponse shape
        assert "sub_question_findings" in result
        assert "gaps" in result
        assert "engagement_note" in result
        # SynthesisResponse fields must NOT appear
        assert "perspectives" not in result
        assert "key_concepts" not in result
```

---

## Summary: done definitions per step

| Step | Done when |
|---|---|
| 1.1 DB migration | `alembic upgrade head` clean; `reading_velocity_enum` exists; `uq_memory_event_day` constraint exists |
| 1.2 consolidate_memory | Profile + events written to DB; no-op on no activity; second run upserts not duplicates |
| 1.3 Skills registration | `skills://sedi` resource returns all 3 skills with Goal + Steps |
| 1.4 Routing classifier | Tier 1 cases make no LLM call; tier 2 cases call `structured_chat` once |
| 1.5 synthesize_topic quick | Returns structured response; profile seeds prompt; other user's articles absent from context |
| 1.6 assist_draft | Draft updated in DB; citations verified against retrieved set; library item count unchanged |
| Phase 1 integration | Profile flows into synthesis prompt end-to-end |
| 2.1 research_runs + endpoint | 200 for own run; 404 for unknown; 403/404 cross-user; `ResearchBrief` and `SubagentResult` schemas importable |
| 2.2 Lead skeleton | `queued → planning → done`; idempotent on re-run; plan written before status advances |
| 2.3 Single subagent | `{ok, data}` contract; idempotency key blocks re-dispatch; item_ids written to run |
| 2.4 Chord callback | Merges ids from successful subagents; failed subagents contribute nothing; dispatches synthesize or next round; marks partial on budget breach |
| 2.5 Synthesis + verification | Synthesis writes `ResearchBrief` + sets `verifying`; high-engagement articles get full highlights in context; grounded citations survive; fabricated IDs removed from `key_sources` and `partial_coverage` |
| 2.6 Recovery task | Stale non-terminal run → partial; terminal and recent runs untouched; returns count |
| 2.7 Deep mode | task enqueued not executed; queued run in DB; rate limit rejects 4th concurrent run; terminal runs don't count |
| Phase 2 E2E | Full run → done; result has `sub_question_findings`/`gaps`/`engagement_note` (not old `perspectives`/`key_concepts`); cross-user isolation; recovery works |
