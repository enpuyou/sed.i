---
layout: layouts/chapter.njk
title: Memory consolidation
part: 3
order: 3
summary: How sed.i condenses a user's recent reading into a short stored profile that other features read, and how the prompt for it was chosen.
verified: 2026-10-25
permalink: /llm/memory-consolidation/
---

## In one minute

Language models start every conversation knowing nothing about the person. sed.i keeps one small row per user, `user_profiles`, holding a one-line current focus, a reading style (`fast`, `deep` or `browsing`), a short paragraph of notes, and a text field of recurring gaps. A nightly job collects what the user saved, read, highlighted and put in lists since the last run, turns it into a text summary, and asks a model to merge it into the existing profile. The first run for a user builds the profile from scratch; later runs only see the new activity and patch what is stored. The synthesis feature reads the focus and the notes and puts them in its prompt, so answers lean toward what the person is working on.

The wording of the prompt was chosen by comparing three versions on ten made-up reading histories. That comparison is useful but small, and this chapter says how small.

## The problem

**How can an assistant "remember" a user across sessions without storing and re-reading their whole history?**

The obvious design is to put the history in the prompt each time: every title, highlight and list. That fails in three ways. It does not fit (a library of hundreds of articles and thousands of highlights is far larger than a sensible prompt), it costs tokens on every call, and it shows the model facts, not conclusions. "Saves distributed-systems papers, reads them to the end, highlights the proofs" is more useful to a model than forty titles.

The opposite design is a fixed set of fields filled by rules (most common tag, average read percentage). That is cheap and predictable but cannot express trajectory ("preparing for interviews") or contrast ("deep on consensus protocols, skims news").

Consolidation sits between the two: a model compresses a bounded slice of activity into a few sentences, once per night per active user, and the stored result is cheap to read many times.

## Mental model

Think of a note a good bookseller keeps about a regular customer: "Studying for interviews, working through consensus algorithms in order. Buys baking and sleep books and never opens them." Each visit the bookseller adds what changed and crosses out what no longer holds. The note stays short because it is rewritten, not appended to.

| Term | Meaning here |
|---|---|
| Profile | The single `user_profiles` row for a user. |
| Bootstrap | The first consolidation, when no profile exists: built from scratch. |
| Delta | Every later consolidation: the stored profile plus only the activity since `last_consolidated`. |
| Activity string | A plain-text summary of recent saves, reads, highlights, lists and themes, built by `_format_activity`. |
| Fan-out | A scheduled job that does no work itself but dispatches one task per user. |
| Overlap lock | A Redis key that stops the next scheduled run starting while the previous run is still dispatching. |
| Reading velocity | A label for how the user reads: `fast`, `deep` or `browsing`, defined in the prompt. |

{% fig "The nightly path" %}
```text
beat (every 24 h)
  |
  v
consolidate_all_users_task
  | take overlap lock (20 h)
  | SQL: users with >= 3 qualifying items since last run
  v  one task per user
consolidate_memory_task(user)
  | profile exists?  no -> bootstrap prompt, since = earliest activity
  |                  yes -> delta prompt,    since = last_consolidated
  | load activity, count items; < 3 -> skip
  | format activity string + research gaps
  v
structured_chat(ConsolidationResult)   via the LLM gateway
  v
user_profiles row upserted, last_consolidated = now
  |
  +--> GET /memory/profile
  +--> MCP synthesis prompt (focus + notes)
```
{% endfig %}

## Walkthrough: a synthetic user, bootstrap then delta

All of this ran against a scratch Postgres database holding made-up rows for one made-up user: six saved articles, four highlights, one list, one reading theme. The real functions built the activity string and the prompts and ran the whole task. The model did not run: its replies were hand-written and served by a stand-in for the HTTP layer, so every model output below is illustrative. The model request itself (model, limits, prompt) is captured.

### Step 1: which window of activity

The user has no profile, so this is a bootstrap. `_bootstrap_since` finds the earliest save or highlight and compares it with a 30-day cutoff:

<!--src content-queue-backend/app/tasks/memory.py#208-217-->
```python
candidates = [t for t in (earliest_save, earliest_highlight) if t is not None]
if not candidates:
    return cap

earliest = min(candidates)
if earliest.tzinfo is None:
    earliest = earliest.replace(tzinfo=timezone.utc)

# Use the earlier of (actual first activity) and the cap
return min(earliest, cap)
```

The earliest item here was saved about 70 hours ago, so the window started 30.0 days back (captured). I then moved that item's date to 90 days ago and the window started **90.0 days** back (captured). The docstrings say the window is "up to 30 days"; the code takes the earlier of the two dates, so 30 days is a floor and the window can be longer. The queries below cap how many rows are read, which bounds the size of what the model sees.

### Step 2: load the activity

`_load_recent_activity` runs five queries and counted (captured):

```text
{'saved': 6, 'read': 3, 'highlights': 4, 'lists': 1, 'clusters': 1}
```

- **saved**: articles created since the cutoff, newest first, at most 50.
- **read**: articles whose `updated_at` is after the cutoff and `read_position` above 0.1, at most 30. `read_position` runs from 0 to 1 and is saved by the reader. There is no separate "opened" event, so "read" means "updated recently and more than 10% scrolled".
- **highlights**: at most 100. **lists**: at most 20.
- **clusters**: the user's reading themes (see [Tagging and article analysis](/llm/tagging-and-analysis/)), kept only if they contain an active article.

### Step 3: format it for the model

The formatter builds sections in priority order and shows relative times. Here is the helper that makes "2d ago":

<!--src content-queue-backend/app/tasks/memory.py#294-307-->
```python
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
```

The activity string for the synthetic user (captured; titles and highlights are made up):

```text
Read deeply (with highlights, in reading order):
  [distributed systems, consensus protocols, leader election] Designing a Raft log  (3200w, read 95%, 2d ago)
    > "Leader election needs a term number so stale leaders can be detected."
    > "The log is the source of truth, not the state machine."
  [distributed systems, consensus protocols] Paxos made less painful  (2800w, read 80%, 1d ago)
    > "Paxos separates choosing a value from learning it."

Read without highlights (in reading order):
  [distributed systems, conflict-free replication] A short history of CRDTs  (1900w, read 30%, 20h ago)

Saved but never opened (3 articles — backlog signal):
  [home baking, dough hydration] Sourdough hydration basics  (900w, saved 1d ago)
  [sleep science, circadian rhythm] Why sleep debt compounds  (1500w, saved 20h ago)
  [coffee brewing, grind size effects] Espresso grind size, explained  (1100w, saved 5h ago)

Highlights on older articles (1):
  > "Recovery sleep does not fully repay a week of short nights."

Reading lists (1):
  - "Distributed systems interview prep"

Topic clusters active this window:
  - consensus protocols (2 articles)
```

The design is in the ordering and the labels. Reads are sorted oldest first so the model can see sequence (Raft, then Paxos, then CRDTs). Highlights are inlined under their article, three per article at most and 150 characters each, as the strongest sign of engagement. "Backlog signal" is written into the text so the model does not have to infer that unopened saves matter. Caps keep the string bounded: 15 deep reads, 10 shallow reads, 15 unopened saves, 8 orphan highlights.

Two quirks show in this output. The sleep highlight is listed under "older articles" although the article was saved 20 hours ago: that section means "highlights on articles not in the read set", and a never-opened article is not in it. And "3 articles" is correct but "1 articles" appears for a single item in the delta below, because the label is not pluralised.

### Step 4: the bootstrap prompt

The activity is inserted into `_BOOTSTRAP_PROMPT`. The sent prompt (captured) was 499 words, 727 tokens by the `o200k_base` tokenizer (an approximation for gpt-4o-mini). It starts:

```text
You are building a first-time memory profile for a personal reading assistant.
This is a BOOTSTRAP — derive the profile from scratch based on all available reading activity.

Reading activity:
[the activity string above]

Produce a profile that captures WHO this reader is and WHAT they are working toward —
not just what topics appear, but the patterns, depth, and trajectory.
```

It then gives rules for each field. `current_focus` must name a sub-domain, with a bad and a good example ("artificial intelligence" against "AI engineering and agent systems — context engineering, LLM evals, forward-deployed roles"). `reading_velocity` is defined from behaviour: fast is high read percentage with few highlights, deep is high read percentage and several highlights, browsing is many saves and low read percentage. `memory_text` must cover four things (trajectory, depth asymmetry, behavioural pattern, unread backlog) in 3 to 6 sentences. And the prompt ends with a rule against inventing coherence:

```text
If the activity shows no dominant focus — diverse domains, all shallow reads, no highlights, no
reading lists — say so explicitly in current_focus and memory_text. Do not construct a coherent
focus where the data shows none.
```

The request used `gpt-4o-mini`, `max_tokens` 1024, a forced tool call named `ConsolidationResult`, and no temperature setting. If research gaps exist for the user (sub-questions the research agent could not answer, from the `research_memory` table), a section listing them is appended to the bootstrap prompt, but the bootstrap prompt has no rule about what to do with them.

### Step 5: the reply and the stored row

The output class has four fields:

<!--src content-queue-backend/app/tasks/memory.py#137-141-->
```python
class ConsolidationResult(BaseModel):
    current_focus: str  # specific sub-domain, one line
    reading_velocity: Literal["fast", "deep", "browsing"]
    memory_text: str  # free-form prose, LLM-managed
    persistent_gaps: str | None = None  # recurring research gaps, null if none
```

The hand-written reply (illustrative):

```json
{"current_focus": "Distributed systems fundamentals - Raft and Paxos consensus, CRDTs, preparing for interviews",
 "reading_velocity": "deep",
 "memory_text": "The reader is preparing for distributed-systems interviews and is working through consensus protocols in order: Raft, then Paxos, then CRDTs. ... Outside that thread they save baking, sleep and coffee articles and open none of them, which looks like backlog rather than interest.",
 "persistent_gaps": null}
```

The hybrid schema is a design point: `current_focus` and `reading_velocity` are typed (an enum), so code can filter or branch on them. `memory_text` is free prose, because the useful observations do not fit fixed fields. The result is written by `_upsert_profile`:

<!--src content-queue-backend/app/tasks/memory.py#438-458-->
```python
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
                persistent_gaps=result.persistent_gaps,
                last_consolidated=datetime.now(tz=timezone.utc),
            )
        )
    else:
        existing.current_focus = result.current_focus
        existing.reading_velocity = ReadingVelocity(result.reading_velocity)
        existing.memory_text = result.memory_text
        if result.persistent_gaps is not None:
            existing.persistent_gaps = result.persistent_gaps
        existing.last_consolidated = datetime.now(tz=timezone.utc)
```

Three fields are overwritten whole each time; `persistent_gaps` is overwritten only when the model returns a value, so a gap, once stored, is never cleared by a `null` reply.

### Step 6: the delta

I ran the task again immediately. It returned (captured):

```text
{'status': 'skipped', 'reason': 'insufficient_activity'}
```

The cutoff is now `last_consolidated`, and nothing new existed, so fewer than 3 items were found and the model was not called:

<!--src content-queue-backend/app/tasks/memory.py#497-507-->
```python
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
```

Skipping does not move `last_consolidated`, so quiet days accumulate until the threshold is reached. I then moved `last_consolidated` back two hours and added two articles and a highlight from the last hour. The run counted four items, chose the delta prompt, and sent (captured, the part before the rules):

```text
You are maintaining a persistent memory profile for a personal reading assistant.
UPDATE the existing profile by merging in new reading activity.
Carry forward what is already known. Only update what the new activity changes or adds.

Existing profile:
current_focus: Distributed systems fundamentals - Raft and Paxos consensus, CRDTs, ...
reading_velocity: deep

The reader is preparing for distributed-systems interviews and is working through ...

New activity since last update:

Read without highlights (in reading order):
  [distributed systems, consensus protocols] Raft membership changes  (2500w, read 60%, 1h ago)

Saved but never opened (1 articles — backlog signal):
  [distributed systems, database testing] Jepsen on etcd  (4000w, saved 1h ago)

Highlights on older articles (1):
  > "Merge must be commutative, associative and idempotent."
```

The rest is rules: update `current_focus` only on a clear shift, infer velocity from the new window only, rewrite `memory_text` as a complete paragraph (not a diff) while keeping accurate facts and softening contradicted ones, and update `persistent_gaps` from research gaps or carry the old value forward. The delta prompt was 363 words, 497 tokens in this case (captured). The model is the only thing doing the merge; the code does not diff anything.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/tasks/memory.py` | Prompts, activity loading and formatting, `consolidate_memory`, the Celery tasks |
| `content-queue-backend/app/models/memory.py` | `UserProfile` (and an event table, `UserMemoryEvent`, that this task does not use) |
| `content-queue-backend/app/api/memory.py` | `GET /memory/profile`, `POST /memory/consolidate` |
| `content-queue-backend/app/core/task_locks.py` | `acquire_run_lock`, the Redis overlap lock |
| `content-queue-backend/app/mcp/tools/synthesis.py` | Reads the profile into the synthesis prompt |
| `evals/memory-consolidation-prompt/results/report.md` | The evaluation that chose the prompt |

### The profile row

<!--src content-queue-backend/app/models/memory.py#63-80-->
```python
class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_focus = Column(Text, nullable=True)
    reading_velocity = Column(
        SAEnum(ReadingVelocity, name="reading_velocity_enum", create_constraint=True),
        nullable=True,
    )
    memory_text = Column(Text, nullable=True)  # free-form prose, LLM-managed
    persistent_gaps = Column(
        Text, nullable=True
    )  # recurring research gaps, LLM-managed
    last_consolidated = Column(DateTime(timezone=True), nullable=True)
```

One row per user (the user id is the primary key) and removed with the user (`ON DELETE CASCADE`). `last_consolidated` null means "bootstrap pending".

### The nightly fan-out

<!--src content-queue-backend/app/tasks/memory.py#614-643-->
```python
rows = self.db.execute(
    text(
        """
    SELECT user_id FROM (
        SELECT ci.user_id, ci.id
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
        UNION ALL
        SELECT h.user_id, h.id
        FROM highlights h
        LEFT JOIN user_profiles up ON up.user_id = h.user_id
        WHERE h.created_at > COALESCE(up.last_consolidated, :bootstrap_cutoff)
    ) AS qualifying_activity
    GROUP BY user_id
    HAVING COUNT(*) >= :min_activity_items
"""
    ),
    {
        "bootstrap_cutoff": bootstrap_cutoff,
        "min_activity_items": _MIN_ACTIVITY_ITEMS,
    },
).fetchall()
```

One query counts, per user, the items that would qualify (new or recently read articles plus new highlights, each counted once) and keeps users with at least 3. Users below the threshold get no task at all. The rule is a copy of the one inside `consolidate_memory`, kept in sync by comment, not by sharing code. The per-user tasks use `max_retries=2` (see "How it fails").

The beat schedule is a plain 24-hour interval (`60 * 60 * 24` seconds), so the run time is set by when the beat process started, not by a clock time. A code comment says "3 AM UTC"; the schedule does not do that.

### The overlap lock

<!--src content-queue-backend/app/tasks/memory.py#602-608-->
```python
lock_name = "consolidate_all_users_task"
# TTL covers most of the 24h gap between nightly firings minus a safety
# margin — if a run is somehow still going after 20h, let the next one
# through rather than risk a permanently stuck lock from a crashed run.
if not acquire_run_lock(lock_name, ttl_seconds=60 * 60 * 20):
    logger.info(f"{lock_name}: previous run still in progress, skipping")
    return {"dispatched": 0, "skipped": "lock_held"}
```

`acquire_run_lock` runs `SET key 1 NX EX 72000` in Redis: set only if absent, expire after 20 hours. The first caller gets the key and proceeds; a second caller within 20 hours gets nothing and skips. The lock is deliberately not released when the dispatch loop ends, because the dispatched per-user tasks run later and the dispatcher cannot see them finish. In effect the lock also makes the fan-out run at most once per 20 hours. If Redis is unreachable, `acquire_run_lock` returns true (fail open), like the budget check in the [LLM gateway](/llm/gateway/).

### Who reads the profile

`GET /memory/profile` returns the focus, velocity, notes and `last_consolidated` (404 if no profile yet). `POST /memory/consolidate` queues a run for the calling user and returns 202. I found no frontend code that calls either endpoint. The only consumer in the product is the quick mode of topic synthesis: `synthesize_topic` builds a memory block from `current_focus` and `memory_text` and puts it in the prompt above the topic. The `persistent_gaps` field is stored but neither the API response nor the synthesis prompt includes it; see the discrepancies list.

## What the evaluation showed

The prompt wording was chosen with a small evaluation (`evals/memory-consolidation-prompt/`). Three prompts were compared:

- **A**, the old single prompt with extra output fields and a fixed 7-day window.
- **B**, the current design: separate bootstrap and delta prompts and an explicit four-point checklist.
- **C**, a "write a briefing note from one assistant to another" framing without a checklist.

Ten made-up reading histories were written by hand, each with human-written ideal labels: for example a heavy saver who never reads, a deep reader on one topic, a topic shift, an eclectic browser with no theme, and a brand-new user with two articles. For each history and each prompt, the model produced a profile, and a second model call (a gpt-4o judge) scored it from 1 to 5 on five dimensions, weighted: trajectory 0.30, specificity 0.25, depth asymmetry 0.20, behavioural pattern 0.15, faithfulness 0.10. A case passes at a weighted score of 0.70 or more with no faithfulness failure.

The clean re-run of 2026-09-25 (measured by the evaluation harness, one run) gave:

| Prompt | Mean weighted score | Pass rate |
|---|---|---|
| A, old | 0.6663 | 50% |
| B, shipped | 0.8412 | 90% |
| C, briefing | 0.7250 | 50% |

B is ahead by 0.175 over A. The largest per-dimension gain in the first run was trajectory (+1.3 points on the 1-to-5 scale), which also carries the highest weight.

What the numbers can and cannot support:

- **Ten cases, one run.** The report itself says differences under about 0.10 are directional only, and that A's own score moved by more than that between the July and September runs of the same prompt. The ranking B above A is consistent across both runs; the exact margin is not stable.
- **Synthetic histories.** The inputs are strings written by the author, not output from `_format_activity` on real data, and no real user's history was tested.
- **Labels not approved.** The report notes the ideal labels had not been signed off by the user.
- **A model judges a model.** The generator is gpt-4o-mini and the judge is gpt-4o, a related model family; a judge can prefer text that resembles its own style. No human scored the outputs.
- **The score rewards the checklist.** The judge dimensions (trajectory, depth asymmetry) are the same four points the B prompt tells the model to cover. B was designed to satisfy the rubric, so it is expected to win it. Whether profiles written to this rubric make synthesis answers better was not measured.
- **Bootstrap only.** All ten cases used the bootstrap prompt. The delta prompt, which runs on every night after the first, was not evaluated, and neither was the quality of merges over many nights.
- **The targeted fix did not work.** The "resist false coherence" rule was added after the first run because the eclectic-browser case failed in every variant. The re-run shows it still fails for B (weighted 0.475): the model still invents a focus. The report calls it a residual failure.
- **Harness bug caught.** The first re-run silently scored 21 of 150 judge answers as 1 because of rate limits (B appeared to fall from 0.860 to 0.589); a retry was added and the clean run had 0 corrupted scores. The episode is a reminder that an evaluation harness is code that can fail quietly.

Overall: good evidence that an explicit checklist beats the original prompt on this kind of input, weak evidence for the exact numbers, and none for the delta path or for downstream benefit.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Consolidate nightly in batch | Update on every event; compute at read time | One cheap call per active user per day, and reads cost nothing. Freshness is up to a day. |
| Bootstrap and delta prompts | Always recompute from a fixed window | The delta keeps history that has scrolled out of the window and costs fewer tokens. Risk: errors persist and compound, since each night's profile is the next night's input. |
| Typed fields plus free prose | All typed; all prose | Typed fields can drive code; prose carries what does not fit. |
| Minimum 3 items | Run for any activity | Avoids paying for a profile from almost no evidence; the sparse-user eval case is the guard. |
| Count in SQL before dispatching | Dispatch for everyone and let the task skip | Saves a task and a round-trip per idle user per night. The cost is a duplicated rule. |
| Redis overlap lock held for its TTL | Release on completion | The dispatcher cannot see when its children finish. |
| Prompt picked by evaluation | Pick by feel | An evaluation, even a small one, exposed that the old prompt scored 0.667 and produced wrong velocity labels in several cases (see the report's per-case table). |

## How it fails

| Failure | What happens | Notes |
|---|---|---|
| Too little activity | Skipped, `last_consolidated` unchanged | Measured above |
| Daily budget reached | Task returns `failed` (not `budget_exceeded`) and logs an error | Inferred: `consolidate_memory` has a single broad `except Exception`, unlike the analysis task |
| Model or API error | Same: the function catches it and returns `failed`; the Celery wrapper never sees an exception, so its `max_retries=2` is never used | The same failure-semantics trap described in [Tagging and article analysis](/llm/tagging-and-analysis/). The next night's run retries naturally, because `last_consolidated` did not move |
| Model returns a bad `reading_velocity` | Validation fails and instructor re-asks | The field is an enum in the schema |
| Worker dies mid-run | No row change (the commit never happened); the next night covers the same window | Inferred from the code |
| Activity during the model call | `last_consolidated` is set to the time after the call, so items created during the call fall before the cutoff and are never seen | Inferred from `_upsert_profile`; the window is seconds long |
| Overlapping fan-outs | The second one skips | The lock, run above for the analogous gateway key; the lock itself not exercised here |

## What is weak or missing

Ranked by how likely each is to matter.

1. **Privacy of what is stored and sent.** The activity string contains article titles, tags and up to 150 characters of each highlight, and it is sent to OpenAI. The result is stored as plain text in Postgres and is inserted into other prompts. The user can read it through `GET /memory/profile`, but there is no endpoint, in `app/api/memory.py`, to edit or delete it; it goes away only when the account is deleted. Highlights can contain anything the user selected, including personal text. A product that stores a model-written dossier on each user should let the user view, correct and delete it, and should say what is sent to the model provider.
2. **Stored text from web pages reaches later prompts.** Titles and highlights come from pages the user saved, and the profile is built from them and later injected into the synthesis prompt. Text on a page could therefore influence a stored profile, which influences later answers. The model is told to be grounded, which is not a security control. A fix is to treat profile text as data in the later prompt (clear delimiters and an instruction not to follow it) and to cap its length.
3. **Retries are inert** for this task, as in the table above; the nightly cadence is the only retry.
4. **Errors compound.** In the delta design, a wrong claim in one night's `memory_text` becomes input to the next. The prompt says to soften contradicted claims, which the evaluation did not test.
5. **The bootstrap window is not capped as documented**, and reads are approximated by `updated_at` plus scroll position. Any later change to an article row (for example a re-tagging) moves `updated_at`, so a long-read article that is updated again can re-enter the "read" list (inferred from the query).
6. **`persistent_gaps` is half connected.** It is written, but nothing visible reads it, and a stored gap cannot be cleared by a null reply.
7. **The dev data shows drift.** The single profile row in the development database has a focus but no notes at all, which suggests an older generation of the prompt wrote it. Rows are not versioned or migrated when the prompt changes.
8. **No sampling control.** The call carries no temperature, so two runs on the same input can differ.

## Cost per user per night

Illustrative, using the price table in `llm_client.py` for gpt-4o-mini ($0.15 per million prompt tokens, $0.60 per million completion tokens) and the token counts from this chapter's walkthrough:

| Case | Prompt tokens | Completion tokens | Cost |
|---|---|---|---|
| The small bootstrap above | 727 (measured) | 160 (assumed) | about $0.0002 |
| A busy delta at the caps in `_format_activity` | about 6,600 (estimated from a synthetic worst-case string) | 1,024 (the limit) | about $0.0016 |
| 1,000 users, small prompt each | 1,000 each | 160 | about $0.25 per night |

The model is cheap and the cost is bounded by the caps, so cost is not the constraint. A re-ask after a validation error doubles a user's cost for the night. The dollars are charged to each user's own budget through `user_id`.

{% say %}
Once a night, for each user with at least three new saves, reads or highlights, a job collects what they did since the last run, formats it as a short text with the strongest signals first, and asks gpt-4o-mini to merge it into a stored profile: a one-line focus, a reading style, and a paragraph of notes. The first run builds the profile from scratch with one prompt and later runs patch it with another. Synthesis reads the focus and the notes to bias its answers. The prompt was picked by scoring three versions on ten made-up histories with a model judge: the checklist version scored 0.84 against 0.67 for the old one, but ten synthetic cases and a judge that shares the checklist's criteria make that directional, the delta path was never evaluated, and one case still fails. The biggest gaps are privacy, since the user cannot edit or delete the stored profile, and the retry setting that never fires.
{% endsay %}

## Check yourself

{% qa "Why does a quiet day not advance last_consolidated, and what follows from that?" %}
The task returns early when fewer than 3 items are found, before any model call or write. Activity therefore accumulates across quiet days until the threshold is met, and the next run covers all of it.
{% endqa %}

{% qa "What does the overlap lock protect against, and why is it not released when the dispatch loop finishes?" %}
It stops a second nightly firing from dispatching the same users again while a previous fan-out may still be running. The dispatcher only queues per-user tasks and cannot see them finish, so releasing at the end of the loop would protect nothing; it holds the key for its 20-hour expiry.
{% endqa %}

{% qa "The evaluation shows 0.8412 against 0.6663. List two reasons not to treat that as proof that profiles improve the product." %}
Ten synthetic cases in one run, where the report itself calls differences under about 0.10 directional; and the judge scores the same four points the winning prompt tells the model to cover. Also, a model judges a related model, nobody checked the delta prompt, and no one measured whether synthesis answers got better.
{% endqa %}

{% qa "consolidate_memory_task has max_retries=2. Will an OpenAI timeout trigger a retry?" %}
No. `consolidate_memory` catches every exception and returns a `failed` dictionary, so Celery sees a normal return and never retries. The next nightly run covers the same window because `last_consolidated` did not move.
{% endqa %}

{% qa "What would you add so a user can trust the stored profile?" %}
A way to view it with its sources, edit or delete it, and a statement of what is sent to the model provider. In the prompt that later reads it, treat the profile as data, not instructions, and cap its length.
{% endqa %}
