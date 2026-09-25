"""
LLMClient — single entry point for all LLM calls in sed.i.

Embeddings always use EMBED_PROVIDER (default "openai") regardless of LLM_PROVIDER.
Changing EMBED_PROVIDER after content is indexed requires a full re-embed migration.

Chat routes to LLM_PROVIDER ("openai" | "bedrock") with per-task model selection.
Failover: transient errors (rate limit, timeout, connection) retry once on the
same provider before falling back; other errors fall back immediately. Embed has
no fallback — it fails loudly rather than producing vectors in a different space.

Observability: both OpenAI and Bedrock calls are traced in Braintrust when
BRAINTRUST_API_KEY is set — OpenAI via automatic wrap_openai instrumentation,
Bedrock via manual braintrust_span + span.log(metrics=...) since wrap_openai
can't instrument boto3. Pass metadata={"user_id": ...} to braintrust_span (or
thread user_id through call sites that already wrap chat/embed calls) for
cost-by-user attribution in the Braintrust UI.

Usage:
    from app.core.llm_client import llm_client, TASK_TAGGING, TASK_SQL_GEN

    result = llm_client.embed("some text")
    result = llm_client.chat(messages=[...], task=TASK_TAGGING)
    result = llm_client.structured_chat(messages=[...], response_model=TagResponse, task=TASK_TAGGING)
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

import openai
from pydantic import BaseModel
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# Transient errors are retried once on the same provider before falling back —
# a rate limit or timeout doesn't mean the provider itself is broken, so a
# 1-second retry is cheaper than a full cross-provider failover (different
# model, different pricing, different eval-validated quality). Any other
# exception (auth, bad request, content policy) falls back immediately, as
# retrying those on the same provider never resolves them.
TRANSIENT_CHAT_ERRORS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
)
_TRANSIENT_RETRY_BACKOFF_SECONDS = 1.0

# Task name constants — use these at call sites instead of raw strings
TASK_TAGGING = "tagging"
TASK_SUMMARY = "summary"
TASK_MCP_SUMMARY = "mcp_summary"
TASK_SQL_GEN = "sql_gen"
TASK_INSIGHT = "insight"
TASK_ENTITY_EXTRACTION = "entity_extraction"
TASK_ARTICLE_ANALYSIS = "article_analysis"
TASK_ENTITY_DEDUP = "entity_dedup"
TASK_MEMORY_CONSOLIDATION = "memory_consolidation"
TASK_ROUTING = "routing"
TASK_SYNTHESIS = "synthesis"
# Research pipeline — granular tags so Braintrust can slice by step
TASK_RESEARCH_PLANNING = "research_planning"
TASK_RESEARCH_EXPANSION = "research_expansion"
TASK_RESEARCH_FILTER = "research_filter"
TASK_RESEARCH_SUMMARY = "research_article_summary"
TASK_RESEARCH_SYNTHESIS = "research_synthesis"
# embed()-only call sites — no per-task model routing (embed always uses
# EMBED_PROVIDER), but named span tags so Braintrust can attribute cost by caller
TASK_EMBEDDING = "embedding"
TASK_HIGHLIGHT_EMBEDDING = "highlight_embedding"
TASK_TAG_EMBEDDING = "tag_embedding"
TASK_ENTITY_EMBEDDING = "entity_embedding"
TASK_CHUNK_EMBEDDING = "chunk_embedding"
TASK_MEMORY_RESEARCH = "memory_research"

_EMBED_MODEL_OPENAI = "text-embedding-3-small"
_EMBED_MODEL_BEDROCK = (
    "amazon.titan-embed-text-v1"  # 1536-dim, compatible with existing schema
)

_US_BEDROCK_REGIONS = {"us-east-1", "us-east-2", "us-west-2"}

# Per-model USD price per token, keyed by exact model ID string (not a flat
# per-provider rate — Bedrock model families differ, e.g. Nova vs Claude).
# Used only for the Bedrock manual span logging (Phase 4) and the Redis daily
# budget counter (Phase 3) — the OpenAI path's cost figures shown in Braintrust
# come from Braintrust's own built-in pricing registry and are unaffected by
# this table. Last verified 2026-07-21 against:
#   https://openai.com/api/pricing/
#   https://aws.amazon.com/bedrock/pricing/
_PRICE_PER_TOKEN_USD: dict[str, dict[str, float]] = {
    # OpenAI chat
    "gpt-4o-mini": {"prompt": 0.15 / 1_000_000, "completion": 0.60 / 1_000_000},
    "gpt-4o": {"prompt": 2.50 / 1_000_000, "completion": 10.00 / 1_000_000},
    # OpenAI embedding (completion price unused — embeddings have no output tokens)
    "text-embedding-3-small": {"prompt": 0.02 / 1_000_000, "completion": 0.0},
    # Bedrock chat
    "amazon.nova-micro-v1:0": {
        "prompt": 0.035 / 1_000_000,
        "completion": 0.14 / 1_000_000,
    },
    "amazon.nova-lite-v1:0": {
        "prompt": 0.06 / 1_000_000,
        "completion": 0.24 / 1_000_000,
    },
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "prompt": 3.00 / 1_000_000,
        "completion": 15.00 / 1_000_000,
    },
    # Bedrock embedding
    "amazon.titan-embed-text-v1": {"prompt": 0.10 / 1_000_000, "completion": 0.0},
}


def estimate_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int = 0
) -> float:
    """
    Estimate USD cost for a call from token counts, using _PRICE_PER_TOKEN_USD.
    Returns 0.0 for an unrecognized model rather than raising — an unknown model
    shouldn't block the budget check or the Bedrock trace it's computed for.
    """
    prices = _PRICE_PER_TOKEN_USD.get(model)
    if prices is None:
        logger.warning(f"No price entry for model '{model}', estimating cost as $0")
        return 0.0
    return prompt_tokens * prices["prompt"] + completion_tokens * prices["completion"]


class BudgetExceededError(Exception):
    """Raised when a user's daily LLM spend ceiling (LLM_DAILY_BUDGET_USD_PER_USER)
    has already been reached. Callers should log + skip, not retry — retrying a
    budget-exceeded call wastes a retry slot on a condition that won't resolve."""


def _spend_key(user_id: str) -> str:
    from datetime import datetime, timezone

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"llm_spend:{user_id}:{day}"


def _get_redis_client() -> Any | None:
    """Lazy Redis client, matching the pattern in app/core/hybrid_search.py.
    Returns None (not raises) on connection failure — a budget check that can't
    reach Redis should not block the LLM call it's guarding."""
    try:
        import redis as redis_lib

        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception as e:
        logger.warning(
            f"Redis unavailable for budget check ({e}), skipping budget enforcement"
        )
        return None


def check_budget(user_id: str) -> None:
    """
    Raise BudgetExceededError if user_id has already spent
    LLM_DAILY_BUDGET_USD_PER_USER today. No-op (does not raise) if Redis is
    unavailable — the budget check degrades open rather than blocking the
    pipeline on an unrelated Redis outage.
    """
    r = _get_redis_client()
    if r is None:
        return

    spent = r.get(_spend_key(user_id))
    if spent is not None and float(spent) >= settings.LLM_DAILY_BUDGET_USD_PER_USER:
        raise BudgetExceededError(
            f"User {user_id} has spent ${float(spent):.4f} today, "
            f"at or above the ${settings.LLM_DAILY_BUDGET_USD_PER_USER:.2f} daily ceiling"
        )


def record_spend(user_id: str, cost_usd: float) -> None:
    """Add cost_usd to user_id's running daily total. No-op if Redis is
    unavailable or cost_usd is 0 (unrecognized model — nothing meaningful to add)."""
    if cost_usd <= 0:
        return
    r = _get_redis_client()
    if r is None:
        return

    key = _spend_key(user_id)
    r.incrbyfloat(key, cost_usd)
    r.expire(key, 60 * 60 * 25)  # 25h — outlives one UTC day, self-expires


def _make_openai_client() -> OpenAI:
    """Build an OpenAI client, wrapped with Braintrust tracing if configured."""
    # max_retries=0: the SDK's own silent retries are disabled so retry behavior
    # is explicit in chat()'s same-provider-retry-then-fallback logic instead of
    # hidden inside the client.
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=30.0, max_retries=0)
    if settings.BRAINTRUST_API_KEY:
        try:
            import braintrust

            braintrust.login(api_key=settings.BRAINTRUST_API_KEY)
            braintrust.init_logger(project="sedi")
            client = braintrust.wrap_openai(client)
            logger.info("Braintrust tracing enabled for LLM calls")
        except Exception as e:
            logger.warning(f"Braintrust init failed, tracing disabled: {e}")
    return client


def _make_bedrock_client() -> Any:
    """Build a boto3 bedrock-runtime client with connection timeouts."""
    import boto3
    from botocore.config import Config

    if settings.AWS_REGION not in _US_BEDROCK_REGIONS:
        logger.warning(
            f"AWS_REGION={settings.AWS_REGION} is outside US regions. "
            "us.* Claude model IDs route across us-east-1/us-east-2/us-west-2 — "
            "set AWS_REGION=us-east-2 to avoid cross-region latency."
        )

    kwargs: dict[str, Any] = {
        "region_name": settings.AWS_REGION,
        "config": Config(connect_timeout=5, read_timeout=30),
    }
    if settings.AWS_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("bedrock-runtime", **kwargs)


@dataclass
class EmbedResult:
    embeddings: list[list[float]]
    model: str
    prompt_tokens: int


@dataclass
class ChatResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class LLMClient:
    """
    Provider-agnostic LLM client.

    embed()          — always uses EMBED_PROVIDER (default "openai")
    chat()           — routes to LLM_PROVIDER; task= selects the right model
    structured_chat()— returns a validated Pydantic model; retries on parse failure
    """

    def __init__(self) -> None:
        self._provider = settings.LLM_PROVIDER
        self._openai_client: OpenAI | None = None
        self._bedrock_client: Any | None = None

    def _openai(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = _make_openai_client()
        return self._openai_client

    def _bedrock(self) -> Any:
        if self._bedrock_client is None:
            self._bedrock_client = _make_bedrock_client()
        return self._bedrock_client

    def _resolve_model(self, task: str, provider: str) -> str:
        attr = f"LLM_MODEL_{task.upper()}_{provider.upper()}"
        return getattr(settings, attr)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def embed(
        self,
        texts: list[str] | str,
        *,
        model: str | None = None,
        user_id: str | None = None,
    ) -> EmbedResult:
        """
        Embed one or more texts. Always uses EMBED_PROVIDER (default "openai").
        No provider fallback — fails loudly rather than producing incompatible vectors.

        user_id: if set, checks LLM_DAILY_BUDGET_USD_PER_USER before calling out
        and records spend after. Omit to skip budget enforcement (e.g. call sites
        without a user in scope).
        """
        if user_id is not None:
            check_budget(user_id)
        if isinstance(texts, str):
            texts = [texts]
        result = self._embed_with(settings.EMBED_PROVIDER, texts, model=model)
        if user_id is not None:
            record_spend(user_id, estimate_cost_usd(result.model, result.prompt_tokens))
        return result

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        task: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> ChatResult:
        """
        Single-turn or multi-turn chat completion.

        task: routes to the configured model for that task (e.g. TASK_TAGGING, TASK_SQL_GEN).
        model: explicit override, bypasses all routing.
        user_id: if set, checks LLM_DAILY_BUDGET_USD_PER_USER before calling out
        and records spend after. Omit to skip budget enforcement.

        Transient errors (rate limit, timeout, connection) are retried once on the
        same provider before falling back. Any other error falls back immediately.
        """
        if user_id is not None:
            check_budget(user_id)

        primary = self._provider
        fallback = "bedrock" if primary == "openai" else "openai"

        try:
            result = self._chat_with(
                primary,
                messages,
                model=model,
                task=task,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )
        except TRANSIENT_CHAT_ERRORS as e:
            logger.warning(
                f"chat transient error on {primary} ({e}), retrying same provider"
            )
            time.sleep(_TRANSIENT_RETRY_BACKOFF_SECONDS)
            try:
                result = self._chat_with(
                    primary,
                    messages,
                    model=model,
                    task=task,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )
            except Exception as retry_e:
                logger.warning(
                    f"chat retry failed on {primary} ({retry_e}), falling back to {fallback}"
                )
                result = self._chat_with(
                    fallback,
                    messages,
                    model=model,
                    task=task,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )
        except Exception as e:
            logger.warning(f"chat failed on {primary} ({e}), retrying on {fallback}")
            result = self._chat_with(
                fallback,
                messages,
                model=model,
                task=task,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )

        if user_id is not None:
            record_spend(
                user_id,
                estimate_cost_usd(
                    result.model, result.prompt_tokens, result.completion_tokens
                ),
            )
        return result

    def structured_chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModel],
        task: str,
        max_tokens: int = 512,
        max_retries: int = 2,
        user_id: str | None = None,
    ) -> BaseModel:
        """
        Chat that returns a validated Pydantic model instance.

        OpenAI: uses instructor for retry-on-validation-error.
        Bedrock: instruction-injection + manual retry loop with validation feedback.
        user_id: if set, checks LLM_DAILY_BUDGET_USD_PER_USER before calling out
        and records spend after. Omit to skip budget enforcement.
        """
        if user_id is not None:
            check_budget(user_id)

        if self._provider == "openai":
            result = self._openai_structured_chat(
                messages,
                response_model=response_model,
                task=task,
                max_tokens=max_tokens,
                max_retries=max_retries,
                user_id=user_id,
            )
        else:
            result = self._bedrock_structured_chat(
                messages,
                response_model=response_model,
                task=task,
                max_tokens=max_tokens,
                max_retries=max_retries,
                user_id=user_id,
            )
        return result

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    def _embed_with(
        self, provider: str, texts: list[str], *, model: str | None
    ) -> EmbedResult:
        if provider == "openai":
            return self._openai_embed(texts, model=model or _EMBED_MODEL_OPENAI)
        if provider == "bedrock":
            return self._bedrock_embed(texts, model=model or _EMBED_MODEL_BEDROCK)
        raise NotImplementedError(f"Unknown embed provider '{provider}'")

    def _chat_with(
        self,
        provider: str,
        messages: list[dict[str, str]],
        *,
        model: str | None,
        task: str | None,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None,
    ) -> ChatResult:
        if provider == "openai":
            resolved = model or (
                self._resolve_model(task, "openai") if task else "gpt-4o-mini"
            )
            return self._openai_chat(
                messages,
                model=resolved,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )
        if provider == "bedrock":
            resolved = model or (
                self._resolve_model(task, "bedrock")
                if task
                else settings.LLM_MODEL_SUMMARY_BEDROCK
            )
            return self._bedrock_chat(
                messages,
                model=resolved,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )
        raise NotImplementedError(f"Unknown provider '{provider}'")

    # ------------------------------------------------------------------
    # OpenAI implementation
    # ------------------------------------------------------------------

    def _openai_embed(self, texts: list[str], *, model: str) -> EmbedResult:
        client = self._openai()
        response = client.embeddings.create(
            model=model,
            input=texts,
            encoding_format="float",
        )
        return EmbedResult(
            embeddings=[d.embedding for d in response.data],
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
        )

    def _openai_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None,
    ) -> ChatResult:
        client = self._openai()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        usage = response.usage
        return ChatResult(
            content=choice.message.content or "",
            model=response.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

    def _openai_structured_chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModel],
        task: str,
        max_tokens: int,
        max_retries: int,
        user_id: str | None = None,
    ) -> BaseModel:
        import instructor

        model = self._resolve_model(task, "openai")
        client = instructor.from_openai(self._openai())

        if user_id is None:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )

        # create_with_completion surfaces the raw completion (for usage tokens)
        # alongside the validated model — only needed when recording spend.
        instance, completion = client.chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=response_model,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )
        usage = completion.usage
        record_spend(
            user_id,
            estimate_cost_usd(model, usage.prompt_tokens, usage.completion_tokens),
        )
        return instance

    # ------------------------------------------------------------------
    # Bedrock implementation
    # ------------------------------------------------------------------

    def _bedrock_embed(self, texts: list[str], *, model: str) -> EmbedResult:
        """
        Embed via Amazon Titan. Titan processes one text at a time,
        so we loop and aggregate — same interface as OpenAI batch endpoint.
        Titan v1 is 1536-dim compatible with the existing pgvector schema.
        """
        client = self._bedrock()
        embeddings = []
        total_tokens = 0
        with braintrust_span(
            "bedrock_embed", input={"model": model, "count": len(texts)}
        ) as span:
            for text in texts:
                body = json.dumps({"inputText": text})
                response = client.invoke_model(
                    modelId=model,
                    body=body,
                    contentType="application/json",
                    accept="application/json",
                )
                result = json.loads(response["body"].read())
                embeddings.append(result["embedding"])
                total_tokens += result.get("inputTextTokenCount", 0)

            if span is not None:
                span.log(
                    metrics={
                        "prompt_tokens": total_tokens,
                        "estimated_cost": estimate_cost_usd(model, total_tokens),
                    }
                )

        return EmbedResult(
            embeddings=embeddings,
            model=model,
            prompt_tokens=total_tokens,
        )

    def _bedrock_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None,
    ) -> ChatResult:
        """
        Chat via Bedrock Converse API (unified across all Claude families).
        system messages are extracted and passed in the system parameter.
        response_format={"type": "json_object"} is emulated by appending a
        JSON instruction to the last user message — Bedrock has no native JSON mode.
        """
        client = self._bedrock()

        system_parts: list[dict] = []
        converse_messages: list[dict] = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                system_parts.append({"text": content})
            else:
                converse_messages.append({"role": role, "content": [{"text": content}]})

        json_mode = response_format and response_format.get("type") == "json_object"
        if json_mode and converse_messages and converse_messages[-1]["role"] == "user":
            last = converse_messages[-1]["content"][0]["text"]
            converse_messages[-1]["content"][0]["text"] = (
                last
                + "\n\nOutput raw JSON only. No markdown, no code blocks, no explanation."
            )

        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": converse_messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system_parts:
            kwargs["system"] = system_parts

        with braintrust_span("bedrock_chat", input={"model": model}) as span:
            response = client.converse(**kwargs)
            output = response["output"]["message"]["content"][0]["text"]

            if json_mode and output.startswith("```"):
                output = output.split("\n", 1)[-1]
                output = output.rsplit("```", 1)[0].strip()

            usage = response.get("usage", {})
            prompt_tokens = usage.get("inputTokens", 0)
            completion_tokens = usage.get("outputTokens", 0)

            if span is not None:
                span.log(
                    metrics={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "estimated_cost": estimate_cost_usd(
                            model, prompt_tokens, completion_tokens
                        ),
                    }
                )

        return ChatResult(
            content=output,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _bedrock_structured_chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModel],
        task: str,
        max_tokens: int,
        max_retries: int,
        user_id: str | None = None,
    ) -> BaseModel:
        """Structured output via Bedrock with manual retry-on-validation-error loop."""
        model = self._resolve_model(task, "bedrock")
        current_messages = list(messages)
        last_error: Exception = ValueError("no attempts made")
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for attempt in range(max_retries + 1):
            chat_result = self._bedrock_chat(
                current_messages,
                model=model,
                max_tokens=max_tokens,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            total_prompt_tokens += chat_result.prompt_tokens
            total_completion_tokens += chat_result.completion_tokens
            try:
                data = json.loads(chat_result.content)
                instance = response_model.model_validate(data)
                if user_id is not None:
                    record_spend(
                        user_id,
                        estimate_cost_usd(
                            model, total_prompt_tokens, total_completion_tokens
                        ),
                    )
                return instance
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    current_messages = current_messages + [
                        {"role": "assistant", "content": chat_result.content},
                        {
                            "role": "user",
                            "content": (
                                f"Invalid response: {e}. "
                                "Return valid JSON matching the required schema."
                            ),
                        },
                    ]

        if user_id is not None:
            record_spend(
                user_id,
                estimate_cost_usd(model, total_prompt_tokens, total_completion_tokens),
            )
        raise ValueError(
            f"structured_chat failed after {max_retries} retries: {last_error}"
        )


# Module-level singleton — import this everywhere
llm_client = LLMClient()


@contextmanager
def braintrust_span(
    name: str,
    *,
    input: dict | None = None,
    metadata: dict | None = None,
) -> Generator[Any, None, None]:
    """
    Context manager that wraps a logical step in a Braintrust span when tracing
    is active. No-op when BRAINTRUST_API_KEY is not set.

    Usage:
        with braintrust_span("planning", input={"question": q}, metadata={"user_id": uid}):
            result = llm_client.structured_chat(...)

    The Braintrust wrap_openai integration auto-attaches child LLM call spans
    under whichever span is current — so wrapping each pipeline step here groups
    all its LLM calls together in the trace UI, and metadata (e.g. user_id) set
    here is attached to the span for cost-by-user / cost-by-task rollups.
    """
    if not settings.BRAINTRUST_API_KEY:
        yield None
        return

    try:
        import braintrust
    except Exception:
        yield None
        return

    with braintrust.start_span(
        name=name, input=input or {}, metadata=metadata or {}
    ) as span:
        yield span
