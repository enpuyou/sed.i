"""
Multi-agent research brief tasks.

run_research_lead(run_id)           — lead agent: planning → dispatch subagents → synthesize
run_research_subagent(run_id, ...)  — per-sub-question: search + relevance filter + chunk retrieval
collect_subagent_results(results, run_id) — chord callback: merge results, iterate or synthesize
synthesize_run(run_id)              — produce ResearchBrief from retrieved articles
verify_synthesis(run_id)            — remove citations not in retrieved set
recover_orphaned_runs()             — beat task: mark stale non-terminal runs partial

Dispatch: run_research_lead.delay(run_id)
Direct call (tests): run_research_lead(run_id)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from celery import group, chord
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.hybrid_search import hybrid_search
from app.core.llm_client import (
    llm_client,
    braintrust_span,
    TASK_RESEARCH_PLANNING,
    TASK_RESEARCH_EXPANSION,
    TASK_RESEARCH_FILTER,
    TASK_RESEARCH_SUMMARY,
    TASK_RESEARCH_SYNTHESIS,
)
from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.models.research import ResearchRun
from app.models.user import User
from app.schemas.research import ResearchBrief
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = {
    "max_tokens": 50000,
    "max_iterations": 3,
    "max_subagents": 6,
    "timeout_s": 300,
    "target_count": 8,
}

_SYNTHESIS_MAX_TOKENS = 6000
_CHUNKS_PER_ARTICLE = 4  # top-k chunks fetched per article per sub-question
_RELEVANCE_BATCH = 15  # max candidates sent to relevance filter LLM

# ---------------------------------------------------------------------------
# Prompts — each is a (system, user_template) tuple.
# system: static instructions (stable prefix — hits OpenAI prompt cache across calls).
# user_template: dynamic content only, formatted at call time.
# ---------------------------------------------------------------------------

_PLANNING_PROMPT = (
    # system — stable across all planning calls
    """\
You are the planning step of a library research agent. Your job is to decompose a research question into the minimum set of sub-questions needed to give a complete answer.

Rules — read all before generating:
1. Sub-questions must derive entirely from what the QUESTION requires — not from what articles appear in the library. Do NOT generate sub-questions because a library article looks related.
2. Use the library titles ONLY to calibrate how many sub-questions to generate:
   - Fewer than 3 relevant titles → 2-3 focused sub-questions.
   - 3-6 relevant titles → 3-4 sub-questions.
   - 6+ relevant titles → 4-5 sub-questions.
   Titles do NOT determine WHAT the sub-questions are — only how many.
3. For simple, focused questions: 2-3 sub-questions. Do not pad.
4. For questions requiring multiple angles (competing views, tensions): 3-5 sub-questions.
5. Use specific, search-friendly language — not abstract category names.
6. Do NOT ask meta questions about the library (e.g. "What does the library contain about X?").
7. Do NOT generate more than 6 sub-questions.

Past research context (if provided in the user message):
- Use it to avoid re-generating sub-questions for topics the library has already answered well.
- If a past topic had "no coverage", try a different vocabulary or narrower framing — do not re-ask the identical sub-question verbatim.
- Past context is informational only — it does not constrain which sub-questions you generate.

Output format — respond with a JSON object exactly matching this schema:
{"sub_questions": ["<question 1>", "<question 2>", ...]}\
""",
    # user template — dynamic content only
    """\
Library articles (titles only):
{library_titles}

{prior_context}Research question: {question}\
""",
)

_QUERY_EXPANSION_PROMPT = (
    # system
    """\
You are helping a retrieval agent search a personal reading library.

Personal reading libraries store content under the vocabulary the user happened to encounter — not under academic or canonical terms. Your job is to generate 2 alternative search queries using different but semantically equivalent vocabulary to maximise recall.

Rules:
- Each query must be a short phrase (5-10 words), not a full sentence.
- Use synonyms, related concepts, and practitioner vocabulary.
- Do NOT restate the sub-question verbatim.
- Example: for "does AI reduce cognitive load?" good alternatives are "AI mental effort workload reduction" and "artificial intelligence attention burden automation".

Output format:
{"queries": ["<alternative 1>", "<alternative 2>"]}\
""",
    # user template
    "Sub-question: {sub_question}",
)

_RELEVANCE_FILTER_PROMPT = (
    # system
    """\
You are deciding which articles from a personal reading library are relevant to a specific research sub-question.

Before selecting IDs, reason through these steps:
1. Identify the core claim or question the sub-question is asking about — strip it down to its essential meaning.
2. For each candidate, ask: would this article contribute a specific claim, data point, or perspective that helps answer that core meaning — even if it uses different vocabulary?
3. Bridge vocabulary gaps: "context engineering" or "attention management" may directly address "cognitive load"; "automation" may address "job displacement". Judge substance, not words.
4. An article is NOT relevant just because it shares a keyword without contributing a substantive claim.
5. Err on the side of inclusion when uncertain — a false positive is less harmful than missing a relevant source.

Output format — return only the IDs of relevant articles:
{"relevant_ids": ["<id>", ...]}\
""",
    # user template
    """\
Sub-question: {sub_question}

Candidate articles (id | title | description):
{candidates}\
""",
)

_ARTICLE_SUMMARY_PROMPT = (
    # system
    """\
You are summarizing one article's contribution to a specific research sub-question.

Rules:
- Write 2-3 sentences that directly answer what this article contributes to the sub-question.
- Be specific: name claims, data points, or arguments — not just topics.
- Your answer must be based ONLY on the excerpts and highlights provided below. Do not use any knowledge not present in the provided text.
- If the excerpts do not address the sub-question, say so in one sentence.
- Do not pad. No preamble like "This article...".\
""",
    # user template
    """\
Sub-question: {sub_question}

Article: {title}
Description: {description}

Key excerpts:
{chunks}

User highlights:
{highlights}\
""",
)

_SYNTHESIS_PROMPT = (
    # system
    """\
You are synthesizing a structured research brief from a user's personal reading library.

Grounding rules (strictly enforced):
- Only cite article IDs shown in the context provided.
- Base every claim on a specific excerpt or article summary shown — do not draw on general knowledge.
- If a sub-question has no relevant articles, the finding must say so plainly in one sentence. Do not synthesize from tangentially related articles.

Synthesis quality rules:
- Each finding must state what the library collectively says — not "Article X says... Article Y says...". Connect sources: do they converge, contradict, or address different facets?
- Bad: "Article A argues X. Article B discusses Y."
- Good: "The library converges on X (A, B), though C complicates this by showing Z under condition W."
- Tensions must be named explicitly. Do not imply contradictions — state them: "Article X argues Y while Article Z argues the opposite." If no genuine tension exists within a sub-question, leave tensions empty.

Output format — produce a ResearchBrief JSON object:
- summary: 2-4 sentences on what the library collectively says. If coverage is thin, say so directly.
- sub_question_findings: one entry per sub-question. The `tensions` field must name the specific articles on each side of any disagreement — not just note that "mixed views exist".
- cross_cutting_tensions: contradictions that span multiple sub-questions, with the specific articles named on each side.
- gaps: include an entry ONLY for sub-questions whose coverage is "none". Each entry must name the sub-question and describe specifically what kind of source, data, or perspective would fill it — not just "more research needed".
- engagement_note: note any articles the user has highlighted or read multiple times.
- confidence: "high" | "medium" | "low" based on how well the library covers the question.\
""",
    # user template
    """\
Research question: {question}

Per-sub-question results:
{per_sq_context}\
""",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_run(run_id: str, db: Session) -> ResearchRun | None:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        return None
    return db.query(ResearchRun).filter(ResearchRun.id == rid).first()


def _get_user(user_id, db: Session) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def _fetch_highlights_for_article(article_id, user_id, db: Session) -> list[str]:
    rows = (
        db.query(Highlight.text)
        .filter(Highlight.content_item_id == article_id, Highlight.user_id == user_id)
        .order_by(Highlight.created_at)
        .all()
    )
    return [r.text for r in rows if r.text]


def _fetch_top_chunks(
    article_id: uuid.UUID,
    query_embedding: list[float],
    db: Session,
    k: int = _CHUNKS_PER_ARTICLE,
) -> list[str]:
    """
    Return the k chunk texts from article_id whose embeddings are closest to query_embedding.
    Falls back to empty list if no chunks exist.
    """
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
    rows = db.execute(
        text(
            """
            SELECT text
            FROM content_chunks
            WHERE content_item_id = :item_id
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :k
        """
        ),
        {"item_id": article_id, "q": embedding_str, "k": k},
    ).fetchall()
    return [r.text for r in rows if r.text]


def _count_tokens_approx(text_: str) -> int:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text_))
    except Exception:
        return len(text_) // 4


def _merge_item_ids(existing: list | None, new_ids: list[str]) -> list[str]:
    seen = set(existing or [])
    result = list(existing or [])
    for iid in new_ids:
        if iid not in seen:
            seen.add(iid)
            result.append(iid)
    return result


def _build_per_sq_context(
    subagent_results: list[dict],
    sub_questions: list[str],
    max_tokens: int = _SYNTHESIS_MAX_TOKENS,
) -> str:
    """
    Build synthesis context organized by sub-question.

    Each sub-question block lists its confirmed-relevant articles with:
      - title
      - top chunks (specific excerpts)
      - user highlights (if any) — as metadata, not weighted for space
      - engagement note (read/highlight count) — metadata line only

    Token budget applied across all blocks; drops whole article entries (never truncates mid-chunk).
    """
    # Index subagent results by sub-question text
    sq_data: dict[str, dict] = {}
    for sr in subagent_results:
        sq = sr.get("sub_question", "")
        if sq not in sq_data:
            sq_data[sq] = sr

    parts: list[str] = []
    used_tokens = 0

    for i, sq in enumerate(sub_questions):
        sr = sq_data.get(sq, {})
        coverage = sr.get("coverage_assessment", "none")
        articles = sr.get("articles", [])

        sq_header = f"--- Sub-question {i+1}: {sq}\nCoverage: {coverage}"
        parts.append(sq_header)
        used_tokens += _count_tokens_approx(sq_header)

        if not articles:
            parts.append("  (no relevant articles found)")
            continue

        for art in articles:
            art_id = art.get("id", "")
            title = art.get("title", "")
            highlights = art.get("highlights", [])
            engagement = art.get("engagement_score", 0)
            chunks = art.get("chunks", [])
            article_summary = art.get("article_summary", "")

            # Article block: summary first (most useful for synthesis), then supporting evidence
            lines = [f"  [{art_id}] {title}"]

            if article_summary:
                lines.append(f"  Summary: {article_summary}")
            elif not chunks:
                desc = art.get("description", "") or ""
                if desc:
                    lines.append(f"  Description: {desc[:200]}")

            if chunks:
                lines.append("  Supporting excerpts:")
                for chunk in chunks:
                    lines.append(f"    › {chunk[:300]}")

            if highlights:
                lines.append(f"  User highlights ({len(highlights)}):")
                for h in highlights[:3]:
                    lines.append(f"    ★ {h[:150]}")

            if engagement > 0:
                lines.append(f"  [engagement: {engagement}]")

            block = "\n".join(lines)
            block_tokens = _count_tokens_approx(block)
            if used_tokens + block_tokens > max_tokens:
                # Token budget exhausted — skip this article entirely
                continue
            parts.append(block)
            used_tokens += block_tokens

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Research memory helpers — cross-run context for the planner
# ---------------------------------------------------------------------------


def _fetch_research_memory(
    user_id,
    question_embedding: list[float],
    db: Session,
    k: int | None = None,
    max_age_days: int | None = None,
) -> list:
    """
    Return the top-k ResearchMemory rows for this user whose topic_embedding is
    most similar (cosine) to question_embedding, filtered by age.
    Returns [] on any error so callers degrade gracefully.
    """
    from app.core.config import settings

    if k is None:
        k = settings.RESEARCH_MEMORY_K
    if max_age_days is None:
        max_age_days = settings.RESEARCH_MEMORY_MAX_AGE_DAYS

    if not question_embedding:
        return []

    try:
        embedding_str = "[" + ",".join(map(str, question_embedding)) + "]"
        cutoff = f"now() - interval '{max_age_days} days'"
        rows = db.execute(
            text(
                f"""
                SELECT id, sub_question, coverage, topic_summary, gap_description,
                       1 - (topic_embedding <=> CAST(:q AS vector)) AS similarity
                FROM research_memory
                WHERE user_id = :uid
                  AND created_at > {cutoff}
                  AND topic_embedding IS NOT NULL
                ORDER BY topic_embedding <=> CAST(:q AS vector)
                LIMIT :k
            """
            ),
            {"uid": user_id, "q": embedding_str, "k": k},
        ).fetchall()
        return [r for r in rows if r.similarity >= 0.75]
    except Exception as e:
        logger.warning("_fetch_research_memory: query failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return []


def _format_memory_context(entries: list) -> str:
    """
    Format retrieved ResearchMemory rows into a compact planner injection block.
    Returns empty string when entries is empty.
    """
    if not entries:
        return ""

    lines = ["Past research context (from your library, previous sessions):"]
    for e in entries:
        if e.coverage in ("full", "partial"):
            detail = e.topic_summary or "(no summary)"
            lines.append(f'- "{e.sub_question}" → {e.coverage} coverage. {detail}')
        else:
            detail = e.gap_description or "library has no articles on this angle"
            lines.append(f'- "{e.sub_question}" → no coverage. Gap: {detail}')

    return "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------
# Step 2.2 — Lead agent
# ---------------------------------------------------------------------------


def run_research_lead(run_id: str, db: Session | None = None, resume: bool = False):
    """
    Lead agent: plan sub-questions → dispatch parallel subagents → trigger synthesis.
    """
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        _own_db = True
    else:
        _own_db = False

    try:
        run = _get_run(run_id, db)
        if not run:
            logger.warning("run_research_lead: run %s not found", run_id)
            return

        if not resume and run.status != "queued":
            logger.info(
                "run_research_lead: skipping run %s (status=%s)", run_id, run.status
            )
            return

        if resume and run.status not in ("queued", "searching"):
            logger.info(
                "run_research_lead: resume skipping run %s (status=%s)",
                run_id,
                run.status,
            )
            return

        budget = run.budget or _DEFAULT_BUDGET
        user = _get_user(run.user_id, db)
        if not user:
            run.status = "failed"
            run.error = {"code": "user_not_found", "message": "User not found"}
            db.commit()
            return

        # --- Planning: broad library scan first, then decompose ---
        run.status = "planning"

        # Quick broad search to surface what the library actually contains
        broad_results = hybrid_search(
            query=run.question, user=user, db=db, limit=15, mode="full"
        )
        library_titles = (
            "\n".join(
                f"  - {r.get('title', '')}" for r in broad_results if r.get("title")
            )
            or "  (library appears empty or has no relevant articles)"
        )

        # On resume: tell the planner which sub-questions had no coverage so it
        # can reformulate them instead of re-running identical queries.
        prior_context = ""
        if resume and run.subagent_results:
            none_sqs = [
                sr.get("sub_question", "")
                for sr in run.subagent_results
                if sr.get("coverage_assessment") == "none" and sr.get("sub_question")
            ]
            if none_sqs:
                prior_context = (
                    "Previous iteration found no relevant articles for these sub-questions. "
                    "Reformulate them with different vocabulary or narrower scope:\n"
                    + "\n".join(f"  - {q}" for q in none_sqs)
                    + "\n\n"
                )
        elif not resume:
            # First iteration only: inject cross-run memory from past research sessions.
            try:
                question_embedding = llm_client.embed(run.question).embeddings[0]
                memory_entries = _fetch_research_memory(
                    run.user_id, question_embedding, db
                )
                prior_context = _format_memory_context(memory_entries)
                if memory_entries:
                    logger.info(
                        "run_research_lead: injecting %d memory entries for run=%s",
                        len(memory_entries),
                        run_id,
                    )
            except Exception as e:
                logger.warning(
                    "run_research_lead: memory fetch failed, continuing without: %s", e
                )
                prior_context = ""

        class SubQuestionPlan(BaseModel):
            sub_questions: list[str]

        planning_system, planning_user_tmpl = _PLANNING_PROMPT
        memory_count = prior_context.count("\n- ") if prior_context else 0
        with braintrust_span(
            "planning",
            input={
                "question": run.question,
                "run_id": run_id,
                "resume": resume,
                "memory_entries_injected": memory_count,
            },
        ):
            planning_response = llm_client.structured_chat(
                messages=[
                    {"role": "system", "content": planning_system},
                    {
                        "role": "user",
                        "content": planning_user_tmpl.format(
                            question=run.question,
                            library_titles=library_titles,
                            prior_context=prior_context,
                        ),
                    },
                ],
                response_model=SubQuestionPlan,
                task=TASK_RESEARCH_PLANNING,
                max_tokens=512,
            )
        sub_questions = planning_response.sub_questions[:6]
        run.plan = "\n".join(f"{i+1}. {q}" for i, q in enumerate(sub_questions))
        run.sub_questions = sub_questions
        db.commit()

        # --- Dispatch subagents ---
        run.status = "searching"
        db.commit()

        payloads = []
        for i, sq in enumerate(sub_questions):
            ikey = f"{run_id}:sq{i}:iter{run.iteration_count}"
            payload = {
                "sub_question": sq,
                "search_params": {"query": sq, "limit": _RELEVANCE_BATCH},
                "budget": {"timeout_s": budget.get("timeout_s", 300)},
            }
            existing_keys = {s.get("idempotency_key") for s in (run.searches_run or [])}
            if ikey not in existing_keys:
                payloads.append((ikey, payload))

        if not payloads:
            return

        searches = list(run.searches_run or [])
        for ikey, payload in payloads:
            searches.append(
                {
                    "idempotency_key": ikey,
                    "subagent_id": ikey,
                    "sub_question": payload["sub_question"],
                }
            )
        run.searches_run = searches
        db.commit()

        job = group(
            run_research_subagent.s(run_id, ikey, payload) for ikey, payload in payloads
        )
        chord(job)(collect_subagent_results.s(run_id=run_id))

    finally:
        if _own_db:
            db.close()


@celery_app.task(
    base=DatabaseTask, bind=True, max_retries=0, time_limit=300, soft_time_limit=270
)
def run_research_lead_task(self, run_id: str, resume: bool = False):
    run_research_lead(run_id, db=self.db, resume=resume)


# ---------------------------------------------------------------------------
# Step 2.3 — Subagent: search → relevance filter → chunk retrieval
# ---------------------------------------------------------------------------


def run_research_subagent(
    run_id: str,
    subagent_id: str,
    payload: dict,
    db: Session | None = None,
) -> dict:
    """
    Per-sub-question retrieval agent.

    1. hybrid_search → candidate articles
    2. LLM relevance filter → keep only genuinely relevant articles
    3. Chunk retrieval → top-k chunks per article (by embedding sim to sub-question)
    4. Per-article summary → 2-3 sentences scoped to the sub-question, generated from chunks + highlights
    5. Return honest coverage based on what passed the filter, not raw result count
    """
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        _own_db = True
    else:
        _own_db = False

    try:
        run = _get_run(run_id, db)
        if not run:
            return {
                "ok": False,
                "data": None,
                "error": {"code": "run_not_found"},
                "meta": {},
            }

        user = _get_user(run.user_id, db)
        if not user:
            return {
                "ok": False,
                "data": None,
                "error": {"code": "user_not_found"},
                "meta": {},
            }

        sub_question = payload.get(
            "sub_question", payload.get("search_params", {}).get("query", "")
        )
        limit = payload.get("search_params", {}).get("limit", _RELEVANCE_BATCH)

        import time

        t0 = time.monotonic()

        # Step 1: multi-query retrieval — expand sub-question into alternative phrasings
        # to bridge vocabulary gaps (e.g. "cognitive load" → "context engineering").
        class QueryExpansion(BaseModel):
            queries: list[str]

        expansion_system, expansion_user_tmpl = _QUERY_EXPANSION_PROMPT
        try:
            with braintrust_span(
                "query_expansion",
                input={"sub_question": sub_question, "run_id": run_id},
            ):
                expansion = llm_client.structured_chat(
                    messages=[
                        {"role": "system", "content": expansion_system},
                        {
                            "role": "user",
                            "content": expansion_user_tmpl.format(
                                sub_question=sub_question
                            ),
                        },
                    ],
                    response_model=QueryExpansion,
                    task=TASK_RESEARCH_EXPANSION,
                    max_tokens=128,
                )
            extra_queries = [q.strip() for q in expansion.queries if q.strip()][:2]
        except Exception:
            extra_queries = []

        all_queries = [sub_question] + extra_queries
        seen_ids: set[str] = set()
        candidates: list[dict] = []
        per_query_limit = max(10, limit // len(all_queries))
        for q in all_queries:
            for r in hybrid_search(
                query=q, user=user, db=db, limit=per_query_limit, mode="full"
            ):
                rid = str(r.get("id") or r.get("item_id") or "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    candidates.append(r)
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break

        if not candidates:
            return {
                "ok": True,
                "data": {
                    "sub_question": sub_question,
                    "item_ids": [],
                    "articles": [],
                    "coverage_assessment": "none",
                },
                "error": None,
                "meta": {"duration_ms": int((time.monotonic() - t0) * 1000)},
            }

        # Step 2: LLM relevance filter
        # For articles with no/thin description, pull one chunk so the filter
        # has actual content to reason about (18% of the library has empty descriptions).
        _THIN_DESC_THRESHOLD = 60  # chars — below this, description is not useful

        def _snippet_for_candidate(r: dict) -> str:
            desc = (r.get("description") or "").strip()
            if len(desc) >= _THIN_DESC_THRESHOLD:
                return desc[:120]
            art_id_raw = r.get("id") or r.get("item_id")
            if art_id_raw:
                try:
                    row = db.execute(
                        text(
                            "SELECT text FROM content_chunks "
                            "WHERE content_item_id = :id AND embedding IS NOT NULL "
                            "ORDER BY created_at LIMIT 1"
                        ),
                        {"id": uuid.UUID(str(art_id_raw))},
                    ).fetchone()
                    if row and row.text:
                        return row.text[:120]
                except Exception:
                    pass
            return desc[:120] if desc else "(no description)"

        candidate_lines = "\n".join(
            f"{r.get('id', r.get('item_id', ''))} | {r.get('title', '')} | {_snippet_for_candidate(r)}"
            for r in candidates
        )

        class RelevanceResult(BaseModel):
            relevant_ids: list[str]

        filter_system, filter_user_tmpl = _RELEVANCE_FILTER_PROMPT
        with braintrust_span(
            "relevance_filter",
            input={
                "sub_question": sub_question,
                "candidate_count": len(candidates),
                "run_id": run_id,
            },
        ):
            filter_response = llm_client.structured_chat(
                messages=[
                    {"role": "system", "content": filter_system},
                    {
                        "role": "user",
                        "content": filter_user_tmpl.format(
                            sub_question=sub_question,
                            candidates=candidate_lines,
                        ),
                    },
                ],
                response_model=RelevanceResult,
                task=TASK_RESEARCH_FILTER,
                max_tokens=256,
            )
        relevant_ids = set(str(x).strip() for x in filter_response.relevant_ids)

        # Step 3: chunk retrieval for relevant articles
        # Get query embedding once for all chunk lookups
        from app.core.embedding_cache import call_embed

        try:
            query_embedding = call_embed(sub_question)
        except Exception:
            query_embedding = None

        articles = []
        item_ids = []

        for r in candidates:
            art_id_raw = r.get("id") or r.get("item_id")
            if not art_id_raw:
                continue
            art_id_str = str(art_id_raw)

            # Only keep articles that passed the relevance filter
            if art_id_str not in relevant_ids:
                continue

            try:
                art_uuid = uuid.UUID(art_id_str)
            except ValueError:
                continue

            highlights = _fetch_highlights_for_article(art_uuid, user.id, db)

            # Engagement score — metadata only, not used for context allocation
            hl_count = len(highlights)
            item = db.query(ContentItem).filter(ContentItem.id == art_uuid).first()
            is_read = 1 if (item and item.is_read) else 0
            engagement_score = hl_count * 2 + is_read

            # Fetch top chunks by embedding similarity to the sub-question
            chunks: list[str] = []
            if query_embedding:
                try:
                    chunks = _fetch_top_chunks(
                        art_uuid, query_embedding, db, k=_CHUNKS_PER_ARTICLE
                    )
                except Exception:
                    pass

            # Generate focused per-article summary scoped to the sub-question
            article_summary = ""
            try:
                chunks_text = (
                    "\n".join(f"  › {c[:400]}" for c in chunks)
                    if chunks
                    else "  (no excerpts available)"
                )
                highlights_text = (
                    "\n".join(f"  › {h[:200]}" for h in highlights[:5])
                    if highlights
                    else "  (none)"
                )
                summary_system, summary_user_tmpl = _ARTICLE_SUMMARY_PROMPT
                with braintrust_span(
                    "article_summary",
                    input={
                        "sub_question": sub_question,
                        "title": r.get("title", ""),
                        "run_id": run_id,
                    },
                ):
                    summary_response = llm_client.chat(
                        messages=[
                            {"role": "system", "content": summary_system},
                            {
                                "role": "user",
                                "content": summary_user_tmpl.format(
                                    sub_question=sub_question,
                                    title=r.get("title", ""),
                                    description=(r.get("description") or "")[:300],
                                    chunks=chunks_text,
                                    highlights=highlights_text,
                                ),
                            },
                        ],
                        task=TASK_RESEARCH_SUMMARY,
                        max_tokens=150,
                    )
                article_summary = summary_response.content.strip()
            except Exception:
                pass

            articles.append(
                {
                    "id": art_id_str,
                    "title": r.get("title", ""),
                    "description": r.get("description", "") or "",
                    "highlights": highlights,
                    "engagement_score": engagement_score,
                    "chunks": chunks,
                    "article_summary": article_summary,
                }
            )
            item_ids.append(art_id_str)

        # Coverage based on what actually passed the filter
        if len(articles) >= 3:
            coverage = "full"
        elif len(articles) >= 1:
            coverage = "partial"
        else:
            coverage = "none"

        return {
            "ok": True,
            "data": {
                "sub_question": sub_question,
                "item_ids": item_ids,
                "articles": articles,
                "coverage_assessment": coverage,
            },
            "error": None,
            "meta": {"duration_ms": int((time.monotonic() - t0) * 1000)},
        }

    except Exception as exc:
        logger.exception("run_research_subagent error for run %s: %s", run_id, exc)
        return {
            "ok": False,
            "data": None,
            "error": {"code": "subagent_error", "message": str(exc)},
            "meta": {},
        }
    finally:
        if _own_db:
            db.close()


@celery_app.task(
    base=DatabaseTask, bind=True, max_retries=1, time_limit=120, soft_time_limit=100
)
def run_research_subagent_task(
    self, run_id: str, subagent_id: str, payload: dict
) -> dict:
    return run_research_subagent(run_id, subagent_id, payload, db=self.db)


run_research_subagent.s = run_research_subagent_task.s
run_research_subagent.delay = run_research_subagent_task.delay


# ---------------------------------------------------------------------------
# Step 2.4 — Chord callback: collect + iterate
# ---------------------------------------------------------------------------


def collect_subagent_results(
    results: list[dict],
    run_id: str,
    db: Session | None = None,
) -> None:
    """
    Chord callback. Receives list of subagent {ok, data, error, meta} dicts.
    Merges retrieved IDs, increments iteration, decides: iterate or synthesize.

    Synthesize when:
    - All sub-questions have non-none coverage (no point iterating), OR
    - Enough unique articles collected (target_count reached), OR
    - Max iterations hit
    Iterate when:
    - Some sub-questions have coverage "none" AND iterations remain AND new articles were found this round.
    """
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        _own_db = True
    else:
        _own_db = False

    try:
        run = _get_run(run_id, db)
        if not run:
            return

        budget = run.budget or _DEFAULT_BUDGET
        target_count = budget.get("target_count", 8)
        max_iterations = budget.get("max_iterations", 3)

        new_ids: list[str] = []
        subagent_results = list(run.subagent_results or [])
        none_coverage_sqs: list[str] = []

        for r in results:
            if r.get("ok") and r.get("data"):
                data = r["data"]
                new_ids.extend(data.get("item_ids", []))
                subagent_results.append(data)
                if data.get("coverage_assessment") == "none":
                    none_coverage_sqs.append(data.get("sub_question", ""))

        merged_ids = _merge_item_ids(run.item_ids_retrieved, new_ids)
        run.item_ids_retrieved = merged_ids
        run.subagent_results = subagent_results
        run.iteration_count = (run.iteration_count or 0) + 1
        db.commit()

        all_covered = len(none_coverage_sqs) == 0
        hit_target = len(merged_ids) >= target_count
        hit_max = run.iteration_count >= max_iterations
        found_new = len(new_ids) > 0

        if all_covered or hit_target or hit_max or not found_new:
            if hit_max and not hit_target:
                run.status = "partial"
                db.commit()
            synthesize_run(run_id, db=db)
        else:
            run_research_lead(run_id, db=db, resume=True)

    finally:
        if _own_db:
            db.close()


@celery_app.task(base=DatabaseTask, bind=True)
def collect_subagent_results_task(self, results: list[dict], run_id: str):
    collect_subagent_results(results, run_id=run_id, db=self.db)


collect_subagent_results.s = collect_subagent_results_task.s
collect_subagent_results.delay = collect_subagent_results_task.delay


# ---------------------------------------------------------------------------
# Step 2.5 — Synthesis + verification
# ---------------------------------------------------------------------------


def synthesize_run(run_id: str, db: Session | None = None) -> None:
    """Produce ResearchBrief from retrieved articles, write to run.result."""
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        _own_db = True
    else:
        _own_db = False

    try:
        run = _get_run(run_id, db)
        if not run:
            return

        run.status = "synthesizing"
        db.commit()

        sub_questions = run.sub_questions or [run.question]

        per_sq_context = _build_per_sq_context(
            subagent_results=run.subagent_results or [],
            sub_questions=sub_questions,
        )

        coverage_counts = {
            "full": sum(
                1
                for sr in (run.subagent_results or [])
                if sr.get("coverage_assessment") == "full"
            ),
            "partial": sum(
                1
                for sr in (run.subagent_results or [])
                if sr.get("coverage_assessment") == "partial"
            ),
            "none": sum(
                1
                for sr in (run.subagent_results or [])
                if sr.get("coverage_assessment") == "none"
            ),
        }
        synthesis_system, synthesis_user_tmpl = _SYNTHESIS_PROMPT
        with braintrust_span(
            "synthesis",
            input={
                "run_id": run_id,
                "question": run.question,
                "sub_question_count": len(sub_questions),
                "articles_retrieved": len(run.item_ids_retrieved or []),
                "coverage": coverage_counts,
            },
        ):
            brief: ResearchBrief = llm_client.structured_chat(
                messages=[
                    {"role": "system", "content": synthesis_system},
                    {
                        "role": "user",
                        "content": synthesis_user_tmpl.format(
                            question=run.question,
                            per_sq_context=per_sq_context,
                        ),
                    },
                ],
                response_model=ResearchBrief,
                task=TASK_RESEARCH_SYNTHESIS,
                max_tokens=3000,
            )

        run.result = brief.model_dump()
        run.status = "verifying"
        db.commit()

        verify_synthesis(run_id, db=db)

    finally:
        if _own_db:
            db.close()


@celery_app.task(base=DatabaseTask, bind=True)
def synthesize_run_task(self, run_id: str):
    synthesize_run(run_id, db=self.db)


synthesize_run.delay = synthesize_run_task.delay


def verify_synthesis(run_id: str, db: Session | None = None) -> None:
    """
    Remove citations not in retrieved set.
    Cleans key_sources in sub_question_findings and partial_coverage in gaps.
    """
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        _own_db = True
    else:
        _own_db = False

    try:
        run = _get_run(run_id, db)
        if not run or not run.result:
            return

        retrieved = set(str(x) for x in (run.item_ids_retrieved or []))
        result = dict(run.result)

        citations_removed = 0
        cleaned_findings = []
        for finding in result.get("sub_question_findings", []):
            finding = dict(finding)
            before = len(finding.get("key_sources", []))
            finding["key_sources"] = [
                s
                for s in finding.get("key_sources", [])
                if str(s.get("item_id", "")) in retrieved
            ]
            citations_removed += before - len(finding["key_sources"])
            cleaned_findings.append(finding)
        result["sub_question_findings"] = cleaned_findings

        cleaned_gaps = []
        for gap in result.get("gaps", []):
            gap = dict(gap)
            gap["partial_coverage"] = [
                iid for iid in gap.get("partial_coverage", []) if str(iid) in retrieved
            ]
            cleaned_gaps.append(gap)

        # Strip fabricated gaps: any gap whose sub-question has coverage "full" or "partial"
        # in subagent_results was addressed by the library — gaps on it are hallucinated.
        # Only sub-questions with coverage "none" are true gaps.
        covered_sqs = {
            sr.get("sub_question", "").strip()
            for sr in (run.subagent_results or [])
            if sr.get("coverage_assessment") in ("full", "partial")
        }
        gaps_before_strip = len(cleaned_gaps)
        cleaned_gaps = [
            g
            for g in cleaned_gaps
            if g.get("sub_question", "").strip() not in covered_sqs
        ]
        gaps_stripped = gaps_before_strip - len(cleaned_gaps)

        # Inject missing gap entries for sub-questions the agent failed to report.
        # A sub-question with coverage "none" in subagent results MUST appear in gaps.
        existing_gap_sqs = {g.get("sub_question", "").strip() for g in cleaned_gaps}
        gaps_injected = 0
        for sr in run.subagent_results or []:
            if sr.get("coverage_assessment") == "none":
                sq = sr.get("sub_question", "").strip()
                if sq and sq not in existing_gap_sqs:
                    cleaned_gaps.append(
                        {
                            "sub_question": sq,
                            "what_is_missing": (
                                f"No articles in the library directly address: {sq}. "
                                "A source specifically covering this angle would be needed to answer it."
                            ),
                            "partial_coverage": [],
                        }
                    )
                    existing_gap_sqs.add(sq)
                    gaps_injected += 1

        result["gaps"] = cleaned_gaps

        verification_meta = {
            "citations_removed": citations_removed,
            "gaps_stripped": gaps_stripped,
            "gaps_injected": gaps_injected,
            "final_gap_count": len(cleaned_gaps),
        }
        logger.info("verify_synthesis run=%s %s", run_id, verification_meta)
        with braintrust_span("verification", input={"run_id": run_id}) as span:
            if span is not None:
                try:
                    span.log(output=verification_meta)
                except Exception:
                    pass

        run.result = result
        run.status = "done"
        db.commit()

        # Fire-and-forget: extract memory entries for future planning context.
        # Import here to avoid a circular import (research_memory imports research models).
        try:
            from app.tasks.research_memory import extract_research_memory

            extract_research_memory.delay(run_id)
        except Exception as e:
            logger.warning(
                "verify_synthesis: could not fire extract_research_memory: %s", e
            )

    finally:
        if _own_db:
            db.close()


@celery_app.task(base=DatabaseTask, bind=True)
def verify_synthesis_task(self, run_id: str):
    verify_synthesis(run_id, db=self.db)


verify_synthesis.delay = verify_synthesis_task.delay


# ---------------------------------------------------------------------------
# Step 2.6 — Recovery beat task
# ---------------------------------------------------------------------------


def recover_orphaned_runs(db: Session | None = None) -> int:
    """Mark runs stuck in non-terminal status with stale updated_at as partial."""
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        _own_db = True
    else:
        _own_db = False

    try:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
        terminal = ("done", "failed", "partial")
        stale = (
            db.query(ResearchRun)
            .filter(
                ResearchRun.status.notin_(terminal),
                ResearchRun.updated_at < cutoff,
            )
            .all()
        )
        for run in stale:
            run.status = "partial"
            run.error = {
                "code": "orphaned",
                "message": "Run stalled — marked partial by recovery task",
            }
        db.commit()
        return len(stale)
    finally:
        if _own_db:
            db.close()


@celery_app.task(base=DatabaseTask, bind=True)
def recover_orphaned_runs_task(self):
    return recover_orphaned_runs(db=self.db)


recover_orphaned_runs.delay = recover_orphaned_runs_task.delay
