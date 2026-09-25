"""
Celery task for research memory extraction.

extract_research_memory(run_id)  — reads a completed ResearchRun, writes one
                                    ResearchMemory row per sub-question with
                                    topic embedding + coverage + summary/gap.

Triggered by verify_synthesis (fire-and-forget) after status is set to "done",
via apply_async(countdown=5) rather than .delay() — 5 seconds is a heuristic
head start for the commit to become visible, not a guarantee (see
verify_synthesis's own comment in app/tasks/research.py). If the run still
isn't visible as "done" when this fires, extract_research_memory raises
_RunNotReadyError instead of silently returning, so the task wrapper's
existing max_retries=2/default_retry_delay=30 actually engages — previously
it logged and returned, meaning a race outside the 5s window silently lost
the memory extraction for that run with no retry ever attempted.

Failure here does not affect the user-visible run result.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.llm_client import llm_client, braintrust_span, TASK_MEMORY_RESEARCH
from app.models.research import ResearchRun
from app.models.research_memory import ResearchMemory
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)


class _RunNotReadyError(Exception):
    """Raised when the target run isn't visible as status='done' yet —
    signals the task wrapper to retry rather than silently give up."""


_SUMMARY_PROMPT = (
    "You are extracting a memory entry from a research result.\n\n"
    "Write 1-2 sentences summarizing what the user's library says about the "
    "sub-question below. Be specific — name claims or perspectives, not topics. "
    "Base your answer only on the article summaries provided.\n\n"
    'Output format: {"summary": "<1-2 sentences>"}'
)


def _summarize_sub_question(
    sub_question: str, articles: list[dict], user_id: str
) -> str | None:
    if not articles:
        return None
    article_lines = []
    for a in articles[:5]:
        title = a.get("title", "")
        summary = a.get("article_summary", "")
        if title or summary:
            article_lines.append(f"- {title}: {summary}")
    if not article_lines:
        return None

    user_content = f"Sub-question: {sub_question}\n\nArticle summaries:\n" + "\n".join(
        article_lines
    )

    class SummaryResponse(BaseModel):
        summary: str

    try:
        with braintrust_span(
            "research_memory_summary",
            input={"sub_question": sub_question},
            metadata={"user_id": user_id},
        ):
            result = llm_client.structured_chat(
                messages=[
                    {"role": "system", "content": _SUMMARY_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_model=SummaryResponse,
                task=TASK_MEMORY_RESEARCH,
                max_tokens=150,
                user_id=user_id,
            )
        return result.summary
    except Exception as e:
        logger.warning("research_memory: summary LLM call failed: %s", e)
        return None


def extract_research_memory(run_id: str, db: Session | None = None) -> None:
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        _own_db = True
    else:
        _own_db = False

    try:
        try:
            rid = uuid.UUID(run_id)
        except ValueError:
            logger.warning("extract_research_memory: invalid run_id %s", run_id)
            return

        run = db.query(ResearchRun).filter(ResearchRun.id == rid).first()
        if not run:
            # Genuinely missing (bad run_id), not a visibility race — retrying
            # won't help, so this stays a silent no-op, not _RunNotReadyError.
            logger.warning("extract_research_memory: run %s not found", run_id)
            return
        if run.status != "done":
            logger.info(
                "extract_research_memory: run %s not ready (status=%s), "
                "requesting retry",
                run_id,
                run.status,
            )
            raise _RunNotReadyError(
                f"run {run_id} status={run.status!r}, expected 'done'"
            )

        subagent_results = run.subagent_results or []
        if not subagent_results:
            return

        rows_written = 0
        for sr in subagent_results:
            sub_question = sr.get("sub_question", "").strip()
            if not sub_question:
                continue

            coverage = sr.get("coverage_assessment", "none")
            articles = sr.get("articles", [])
            article_ids = [a.get("id") for a in articles if a.get("id")]

            # Embed the sub-question text
            try:
                with braintrust_span(
                    "research_memory_embed",
                    input={"sub_question": sub_question},
                    metadata={"user_id": str(run.user_id)},
                ):
                    embedding = llm_client.embed(
                        sub_question, user_id=str(run.user_id)
                    ).embeddings[0]
            except Exception as e:
                logger.warning(
                    "extract_research_memory: embed failed for sq=%r: %s",
                    sub_question,
                    e,
                )
                embedding = None

            topic_summary = None
            gap_description = None

            if coverage in ("full", "partial"):
                topic_summary = _summarize_sub_question(
                    sub_question, articles, str(run.user_id)
                )
            else:
                # coverage == "none"
                gap_description = (
                    f"No articles in library address: {sub_question}. "
                    "A source specifically covering this angle would be needed."
                )

            source_uuids: list[uuid.UUID] = []
            for iid in article_ids:
                try:
                    source_uuids.append(uuid.UUID(str(iid)))
                except (ValueError, AttributeError):
                    pass

            entry = ResearchMemory(
                user_id=run.user_id,
                run_id=run.id,
                sub_question=sub_question,
                topic_embedding=embedding,
                coverage=coverage,
                topic_summary=topic_summary,
                gap_description=gap_description,
                source_item_ids=source_uuids or None,
            )
            try:
                db.add(entry)
                db.flush()
                rows_written += 1
            except Exception as e:
                logger.warning(
                    "extract_research_memory: skipping entry sq=%r: %s",
                    sub_question,
                    e,
                )
                db.rollback()

        db.commit()
        logger.info(
            "extract_research_memory: run=%s wrote %d entries", run_id, rows_written
        )

    except _RunNotReadyError:
        # Not caught by the generic handler below — must propagate to the
        # task wrapper so it can retry, not be logged-and-swallowed as an
        # "unexpected error."
        try:
            db.rollback()
        except Exception:
            pass
        raise

    except Exception as e:
        logger.exception(
            "extract_research_memory: unexpected error for run=%s: %s", run_id, e
        )
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        if _own_db:
            db.close()


@celery_app.task(
    base=DatabaseTask,
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="app.tasks.research_memory.extract_research_memory",
)
def extract_research_memory_task(self, run_id: str):
    try:
        extract_research_memory(run_id, db=self.db)
    except _RunNotReadyError as e:
        raise self.retry(exc=e)


# Expose .delay on the direct-call function so call sites import one name.
extract_research_memory.delay = extract_research_memory_task.delay
