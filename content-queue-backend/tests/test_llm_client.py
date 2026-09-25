"""
Direct unit tests for app.core.llm_client.LLMClient.

Unlike every other test file in this suite (which mocks llm_client.chat/embed/
structured_chat at the call-site boundary), these tests mock the OpenAI and
boto3 clients themselves — this is the one file that exercises LLMClient's
actual internals: provider dispatch, failover, and model resolution.

See docs/plans/llm-gateway-hardening.md Phase 0.
"""

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from app.core.llm_client import (
    BudgetExceededError,
    ChatResult,
    EmbedResult,
    LLMClient,
    braintrust_span,
    estimate_cost_usd,
)


def _httpx_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _httpx_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=_httpx_request())


def make_rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError(
        "rate limited", response=_httpx_response(429), body=None
    )


def make_auth_error() -> openai.AuthenticationError:
    return openai.AuthenticationError(
        "invalid api key", response=_httpx_response(401), body=None
    )


def make_timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_httpx_request())


def make_connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=_httpx_request())


def _fake_openai_chat_response(content: str = "hello", model: str = "gpt-4o-mini"):
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    response.model = model
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return response


def _fake_openai_embed_response(model: str = "text-embedding-3-small"):
    response = MagicMock()
    response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
    response.model = model
    response.usage = MagicMock(prompt_tokens=3)
    return response


@pytest.fixture
def client() -> LLMClient:
    """A fresh LLMClient with OpenAI/Bedrock clients replaced by mocks."""
    c = LLMClient()
    c._openai_client = MagicMock()
    c._bedrock_client = MagicMock()
    return c


class TestModelResolution:
    def test_resolves_openai_task_model_from_settings(self, client):
        from app.core.config import settings

        assert client._resolve_model("tagging", "openai") == (
            settings.LLM_MODEL_TAGGING_OPENAI
        )

    def test_resolves_bedrock_task_model_from_settings(self, client):
        from app.core.config import settings

        assert client._resolve_model("summary", "bedrock") == (
            settings.LLM_MODEL_SUMMARY_BEDROCK
        )


class TestEmbedDispatch:
    def test_embed_always_uses_openai_provider(self, client):
        """embed() ignores LLM_PROVIDER and always uses EMBED_PROVIDER (openai)."""
        client._provider = "bedrock"  # chat would route to bedrock; embed should not
        client._openai_client.embeddings.create.return_value = (
            _fake_openai_embed_response()
        )

        result = client.embed("some text")

        assert isinstance(result, EmbedResult)
        client._openai_client.embeddings.create.assert_called_once()
        client._bedrock_client.invoke_model.assert_not_called()

    def test_embed_wraps_single_string_in_list(self, client):
        client._openai_client.embeddings.create.return_value = (
            _fake_openai_embed_response()
        )

        client.embed("single string")

        call_kwargs = client._openai_client.embeddings.create.call_args.kwargs
        assert call_kwargs["input"] == ["single string"]

    def test_embed_has_no_fallback_on_failure(self, client):
        """Embed fails loudly rather than falling back to a different vector space."""
        client._openai_client.embeddings.create.side_effect = make_rate_limit_error()

        with pytest.raises(openai.RateLimitError):
            client.embed("text")

        client._bedrock_client.invoke_model.assert_not_called()


class TestChatProviderDispatch:
    def test_chat_uses_openai_when_provider_is_openai(self, client):
        client._provider = "openai"
        client._openai_client.chat.completions.create.return_value = (
            _fake_openai_chat_response()
        )

        result = client.chat(messages=[{"role": "user", "content": "hi"}])

        assert isinstance(result, ChatResult)
        assert result.content == "hello"
        client._openai_client.chat.completions.create.assert_called_once()

    def test_chat_task_resolves_model_via_settings(self, client):
        from app.core.config import settings

        client._provider = "openai"
        client._openai_client.chat.completions.create.return_value = (
            _fake_openai_chat_response()
        )

        client.chat(messages=[{"role": "user", "content": "hi"}], task="tagging")

        call_kwargs = client._openai_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == settings.LLM_MODEL_TAGGING_OPENAI

    def test_chat_explicit_model_overrides_task_routing(self, client):
        client._provider = "openai"
        client._openai_client.chat.completions.create.return_value = (
            _fake_openai_chat_response()
        )

        client.chat(
            messages=[{"role": "user", "content": "hi"}],
            task="tagging",
            model="gpt-4o-explicit",
        )

        call_kwargs = client._openai_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o-explicit"


class TestChatFailover:
    """chat()'s error handling: transient errors retry same-provider before
    falling back; permanent errors fall back immediately."""

    def test_permanent_error_triggers_immediate_fallback(self, client):
        client._provider = "openai"
        client._openai_client.chat.completions.create.side_effect = make_auth_error()
        client._bedrock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "fallback response"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }

        result = client.chat(
            messages=[{"role": "user", "content": "hi"}], task="summary"
        )

        assert result.content == "fallback response"
        assert client._openai_client.chat.completions.create.call_count == 1
        client._bedrock_client.converse.assert_called_once()

    def test_transient_error_retries_same_provider_before_success(self, client):
        client._provider = "openai"
        client._openai_client.chat.completions.create.side_effect = [
            make_rate_limit_error(),
            _fake_openai_chat_response("recovered"),
        ]

        with patch("app.core.llm_client.time.sleep"):
            result = client.chat(
                messages=[{"role": "user", "content": "hi"}], task="summary"
            )

        assert result.content == "recovered"
        assert client._openai_client.chat.completions.create.call_count == 2
        client._bedrock_client.converse.assert_not_called()

    def test_transient_error_falls_back_if_retry_also_fails(self, client):
        client._provider = "openai"
        client._openai_client.chat.completions.create.side_effect = make_timeout_error()
        client._bedrock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "fallback response"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }

        with patch("app.core.llm_client.time.sleep"):
            result = client.chat(
                messages=[{"role": "user", "content": "hi"}], task="summary"
            )

        assert result.content == "fallback response"
        assert client._openai_client.chat.completions.create.call_count == 2
        client._bedrock_client.converse.assert_called_once()

    def test_connection_error_is_treated_as_transient(self, client):
        client._provider = "openai"
        client._openai_client.chat.completions.create.side_effect = [
            make_connection_error(),
            _fake_openai_chat_response("recovered"),
        ]

        with patch("app.core.llm_client.time.sleep"):
            result = client.chat(
                messages=[{"role": "user", "content": "hi"}], task="summary"
            )

        assert result.content == "recovered"
        client._bedrock_client.converse.assert_not_called()


class TestStructuredChatDispatch:
    def test_structured_chat_uses_instructor_for_openai(self, client):
        from pydantic import BaseModel

        class DummyModel(BaseModel):
            value: str

        client._provider = "openai"
        fake_instance = DummyModel(value="ok")

        with patch("instructor.from_openai") as mock_from_openai:
            mock_instructor_client = MagicMock()
            mock_instructor_client.chat.completions.create.return_value = fake_instance
            mock_from_openai.return_value = mock_instructor_client

            result = client.structured_chat(
                messages=[{"role": "user", "content": "hi"}],
                response_model=DummyModel,
                task="tagging",
            )

        assert result is fake_instance
        mock_from_openai.assert_called_once_with(client._openai_client)


class TestBraintrustSpanMetadata:
    """braintrust_span() attaches metadata (e.g. user_id) to the span for
    Braintrust's cost-by-user / cost-by-task rollups (Phase 2)."""

    def test_no_op_when_braintrust_key_unset(self):
        with patch("app.core.llm_client.settings") as mock_settings:
            mock_settings.BRAINTRUST_API_KEY = ""
            with braintrust_span("test_task", metadata={"user_id": "u1"}) as span:
                assert span is None

    def test_passes_metadata_to_start_span(self):
        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("braintrust.start_span") as mock_start_span,
        ):
            mock_settings.BRAINTRUST_API_KEY = "fake-key"
            mock_span_cm = MagicMock()
            mock_start_span.return_value = mock_span_cm

            with braintrust_span(
                "test_task", input={"q": "hi"}, metadata={"user_id": "u1"}
            ):
                pass

            mock_start_span.assert_called_once_with(
                name="test_task", input={"q": "hi"}, metadata={"user_id": "u1"}
            )

    def test_defaults_metadata_to_empty_dict(self):
        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("braintrust.start_span") as mock_start_span,
        ):
            mock_settings.BRAINTRUST_API_KEY = "fake-key"
            mock_start_span.return_value = MagicMock()

            with braintrust_span("test_task"):
                pass

            call_kwargs = mock_start_span.call_args.kwargs
            assert call_kwargs["metadata"] == {}


class TestEstimateCostUsd:
    def test_known_model_computes_cost_from_tokens(self):
        cost = estimate_cost_usd(
            "gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000
        )
        assert cost == pytest.approx(0.15 + 0.60)

    def test_embedding_model_has_zero_completion_price(self):
        cost = estimate_cost_usd("text-embedding-3-small", prompt_tokens=1_000_000)
        assert cost == pytest.approx(0.02)

    def test_unknown_model_returns_zero_without_raising(self):
        assert estimate_cost_usd("some-unlisted-model", prompt_tokens=1000) == 0.0


class TestBedrockTracing:
    """Bedrock calls produce Braintrust spans with token/cost metrics, matching
    the OpenAI path's automatic instrumentation (Phase 4)."""

    def test_bedrock_chat_logs_cost_metrics_to_span(self, client):
        client._provider = "bedrock"
        client._bedrock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "hi"}]}},
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }

        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("braintrust.start_span") as mock_start_span,
        ):
            mock_settings.BRAINTRUST_API_KEY = "fake-key"
            mock_settings.LLM_MODEL_SUMMARY_BEDROCK = "amazon.nova-lite-v1:0"
            mock_span = MagicMock()
            mock_start_span.return_value.__enter__.return_value = mock_span

            client._chat_with(
                "bedrock",
                [{"role": "user", "content": "hi"}],
                model=None,
                task="summary",
                max_tokens=100,
                temperature=0.0,
                response_format=None,
            )

        mock_span.log.assert_called_once()
        metrics = mock_span.log.call_args.kwargs["metrics"]
        assert metrics["prompt_tokens"] == 100
        assert metrics["completion_tokens"] == 50
        assert metrics["estimated_cost"] > 0

    def test_bedrock_embed_logs_cost_metrics_to_span(self, client):
        client._bedrock_client.invoke_model.return_value = {
            "body": MagicMock(
                read=MagicMock(
                    return_value=b'{"embedding": [0.1, 0.2], "inputTextTokenCount": 5}'
                )
            )
        }

        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("braintrust.start_span") as mock_start_span,
        ):
            mock_settings.BRAINTRUST_API_KEY = "fake-key"
            mock_span = MagicMock()
            mock_start_span.return_value.__enter__.return_value = mock_span

            client._bedrock_embed(["text"], model="amazon.titan-embed-text-v1")

        mock_span.log.assert_called_once()
        metrics = mock_span.log.call_args.kwargs["metrics"]
        assert metrics["prompt_tokens"] == 5
        assert metrics["estimated_cost"] > 0

    def test_bedrock_chat_no_op_when_braintrust_unset(self, client):
        """Without BRAINTRUST_API_KEY, the span is a no-op and chat still works."""
        client._bedrock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "hi"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }

        result = client._bedrock_chat(
            [{"role": "user", "content": "hi"}],
            model="amazon.nova-lite-v1:0",
            max_tokens=100,
            temperature=0.0,
            response_format=None,
        )

        assert result.content == "hi"
        assert result.prompt_tokens == 10


class TestBudgetEnforcement:
    """Per-user daily spend ceiling: check_budget()/record_spend() gate LLM
    calls made with user_id=, using a Redis counter keyed by user + UTC date."""

    def _mock_redis(self, *, existing_spend: float | None = None):
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.get.return_value = (
            str(existing_spend).encode() if existing_spend is not None else None
        )
        return mock_redis

    def test_chat_raises_budget_exceeded_without_calling_provider(self, client):
        mock_redis = self._mock_redis(existing_spend=999.0)

        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("redis.from_url", return_value=mock_redis),
        ):
            mock_settings.LLM_DAILY_BUDGET_USD_PER_USER = 5.0
            mock_settings.REDIS_URL = "redis://fake"

            with pytest.raises(BudgetExceededError):
                client.chat(
                    messages=[{"role": "user", "content": "hi"}],
                    task="summary",
                    user_id="user-1",
                )

        client._openai_client.chat.completions.create.assert_not_called()
        client._bedrock_client.converse.assert_not_called()

    def test_embed_raises_budget_exceeded_without_calling_provider(self, client):
        mock_redis = self._mock_redis(existing_spend=999.0)

        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("redis.from_url", return_value=mock_redis),
        ):
            mock_settings.LLM_DAILY_BUDGET_USD_PER_USER = 5.0
            mock_settings.REDIS_URL = "redis://fake"

            with pytest.raises(BudgetExceededError):
                client.embed("some text", user_id="user-1")

        client._openai_client.embeddings.create.assert_not_called()

    def test_call_under_budget_succeeds_and_records_spend(self, client):
        client._provider = "openai"
        client._openai_client.chat.completions.create.return_value = (
            _fake_openai_chat_response("hi", model="gpt-4o-mini")
        )
        mock_redis = self._mock_redis(existing_spend=1.0)

        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("redis.from_url", return_value=mock_redis),
        ):
            mock_settings.LLM_DAILY_BUDGET_USD_PER_USER = 5.0
            mock_settings.REDIS_URL = "redis://fake"

            result = client.chat(
                messages=[{"role": "user", "content": "hi"}],
                task="summary",
                user_id="user-1",
            )

        assert result.content == "hi"
        mock_redis.incrbyfloat.assert_called_once()
        mock_redis.expire.assert_called_once()

    def test_call_that_crosses_ceiling_still_succeeds(self, client):
        """The ceiling gates the NEXT call, not the one that crosses it —
        no surprise mid-call rejection."""
        client._provider = "openai"
        client._openai_client.chat.completions.create.return_value = (
            _fake_openai_chat_response("hi", model="gpt-4o-mini")
        )
        # Just under the ceiling — this call is allowed even though its own
        # cost will push the running total over.
        mock_redis = self._mock_redis(existing_spend=4.999999)

        with (
            patch("app.core.llm_client.settings") as mock_settings,
            patch("redis.from_url", return_value=mock_redis),
        ):
            mock_settings.LLM_DAILY_BUDGET_USD_PER_USER = 5.0
            mock_settings.REDIS_URL = "redis://fake"

            result = client.chat(
                messages=[{"role": "user", "content": "hi"}],
                task="summary",
                user_id="user-1",
            )

        assert result.content == "hi"

    def test_no_budget_check_when_user_id_omitted(self, client):
        """user_id=None (the default) skips budget enforcement entirely —
        no Redis call at all."""
        client._provider = "openai"
        client._openai_client.chat.completions.create.return_value = (
            _fake_openai_chat_response("hi")
        )

        with patch("redis.from_url") as mock_from_url:
            result = client.chat(
                messages=[{"role": "user", "content": "hi"}], task="summary"
            )

        assert result.content == "hi"
        mock_from_url.assert_not_called()

    def test_budget_check_degrades_open_when_redis_unavailable(self, client):
        """If Redis can't be reached, the call proceeds rather than blocking
        the pipeline on an unrelated outage."""
        client._provider = "openai"
        client._openai_client.chat.completions.create.return_value = (
            _fake_openai_chat_response("hi")
        )

        with patch("redis.from_url", side_effect=ConnectionError("down")):
            result = client.chat(
                messages=[{"role": "user", "content": "hi"}],
                task="summary",
                user_id="user-1",
            )

        assert result.content == "hi"
