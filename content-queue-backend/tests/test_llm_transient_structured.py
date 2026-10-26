"""
Transient OpenAI errors must survive instructor's retry wrapper so Celery can
retry the task. Reproduced at the HTTP layer with a stub server that returns 429.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import PropertyMock, patch

import openai
import pytest
from celery.exceptions import Retry
from pydantic import BaseModel

from app.core.llm_client import (
    TASK_TAGGING,
    TRANSIENT_CHAT_ERRORS,
    LLMClient,
)


class _Out(BaseModel):
    tags: list[str]


class _Handler(BaseHTTPRequestHandler):
    status = 429
    hits = 0

    def do_POST(self):
        type(self).hits += 1
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.dumps({"error": {"message": "slow down", "type": "x"}}).encode()
        self.send_response(type(self).status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_client():
    _Handler.hits = 0
    _Handler.status = 429
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = LLMClient()
    c._provider = "openai"
    c._openai_client = openai.OpenAI(
        api_key="x", base_url=f"http://127.0.0.1:{srv.server_port}/v1", max_retries=0
    )
    yield c
    srv.shutdown()


def test_structured_chat_429_raises_original_rate_limit_error(stub_client):
    with pytest.raises(openai.RateLimitError):
        stub_client.structured_chat(
            [{"role": "user", "content": "hi"}],
            response_model=_Out,
            task=TASK_TAGGING,
        )


def test_structured_chat_429_with_user_id_raises_rate_limit_error(stub_client):
    with patch("app.core.llm_client.check_budget"):
        with pytest.raises(openai.RateLimitError):
            stub_client.structured_chat(
                [{"role": "user", "content": "hi"}],
                response_model=_Out,
                task=TASK_TAGGING,
                user_id="u",
            )


def test_structured_chat_auth_error_is_not_transient(stub_client):
    _Handler.status = 401
    with pytest.raises(Exception) as ei:
        stub_client.structured_chat(
            [{"role": "user", "content": "hi"}],
            response_model=_Out,
            task=TASK_TAGGING,
        )
    assert not isinstance(ei.value, TRANSIENT_CHAT_ERRORS)


def test_structured_chat_validation_failure_still_raises_instructor_error(
    stub_client,
):
    from instructor.core.exceptions import InstructorRetryException

    _Handler.status = 200

    def ok(self):
        body = json.dumps(
            {
                "id": "1",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"nope": 1}'},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    with patch.object(_Handler, "do_POST", ok):
        with pytest.raises(InstructorRetryException):
            stub_client.structured_chat(
                [{"role": "user", "content": "hi"}],
                response_model=_Out,
                task=TASK_TAGGING,
            )


def test_generate_tags_with_llm_raises_on_structured_429(stub_client):
    from app.tasks import tagging

    with patch.object(tagging, "llm_client", stub_client):
        with pytest.raises(openai.RateLimitError):
            tagging.generate_tags_with_llm("T", "d", "some text " * 50)


def test_analyze_article_task_raises_retry_on_structured_429(
    stub_client, db_session, test_content
):
    from app.tasks import article_analysis
    from app.tasks.base import DatabaseTask

    task = article_analysis.analyze_article_task
    with patch.object(article_analysis, "llm_client", stub_client), patch.object(
        DatabaseTask, "db", new_callable=PropertyMock, return_value=db_session
    ), patch.object(task, "retry", side_effect=Retry()) as retry:
        with pytest.raises(Retry):
            task.run(str(test_content.id))
    assert retry.called
    assert isinstance(retry.call_args.kwargs["exc"], openai.RateLimitError)
