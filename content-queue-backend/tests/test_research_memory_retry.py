"""
Tests for extract_research_memory's retry-on-not-ready behavior
(app/tasks/research_memory.py).

verify_synthesis fires extract_research_memory_task via
apply_async(countdown=5) as a heuristic head start for the ResearchRun's
"done" commit to become visible — not a guarantee. Previously, if the run
still wasn't visible as "done" when the task fired, extract_research_memory
logged and silently returned, permanently losing that run's memory
extraction even though the task declares max_retries=2. This suite covers
the fix: a not-ready run now raises _RunNotReadyError, which the task
wrapper catches and retries via Celery's own retry mechanism.
"""

import uuid
from unittest.mock import patch

import pytest

from app.models.research import ResearchRun
from app.tasks.research_memory import (
    _RunNotReadyError,
    extract_research_memory,
    extract_research_memory_task,
)

DEFAULT_BUDGET = {
    "max_tokens": 50000,
    "max_llm_calls": 30,
    "timeout_s": 300,
}


def _make_run(db, user, *, status="queued"):
    run = ResearchRun(
        user_id=user.id,
        question="What are the competing views on AI and labor?",
        mode="deep",
        status=status,
        budget=DEFAULT_BUDGET,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


class TestExtractResearchMemoryNotReady:
    def test_raises_run_not_ready_when_status_not_done(self, db_session, test_user):
        """A run still 'synthesizing' (the commit-visibility race window)
        must raise _RunNotReadyError, not silently return."""
        run = _make_run(db_session, test_user, status="synthesizing")

        with pytest.raises(_RunNotReadyError):
            extract_research_memory(str(run.id), db=db_session)

    def test_missing_run_does_not_raise_run_not_ready(self, db_session):
        """A genuinely nonexistent run_id (bad data, not a visibility race)
        should not trigger a retry — retrying won't make it exist."""
        fake_id = str(uuid.uuid4())
        # Must not raise at all — silent no-op for a truly missing run.
        extract_research_memory(fake_id, db=db_session)

    def test_done_run_does_not_raise(self, db_session, test_user):
        """A run already visible as 'done' with no subagent_results is a
        normal early-return, not a not-ready condition."""
        run = _make_run(db_session, test_user, status="done")
        # subagent_results is None -> early return, no LLM calls, no raise.
        extract_research_memory(str(run.id), db=db_session)


class TestExtractResearchMemoryTaskRetry:
    def test_task_retries_via_celery_when_run_not_ready(self, db_session, test_user):
        """The Celery task wrapper must call self.retry() on
        _RunNotReadyError so the existing max_retries=2/default_retry_delay=30
        actually engages — verified by observing the retry actually happens
        (raise self.retry(...) is called), rather than the exception being
        silently swallowed as it was before this fix (a bare `return`)."""
        run = _make_run(db_session, test_user, status="searching")

        with patch(
            "app.tasks.research_memory.DatabaseTask.db",
            new_callable=lambda: db_session,
        ), patch.object(
            extract_research_memory_task, "retry", side_effect=lambda exc: exc
        ) as mock_retry:
            # apply() runs the task eagerly in-process; retry() is patched to
            # return its exc instead of raising Celery's Retry control-flow
            # exception, so we can assert it was actually invoked.
            extract_research_memory_task.apply(args=(str(run.id),))

        mock_retry.assert_called_once()
        assert isinstance(mock_retry.call_args.kwargs["exc"], _RunNotReadyError)
