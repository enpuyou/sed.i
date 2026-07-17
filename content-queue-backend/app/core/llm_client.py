"""
LLMClient — single entry point for all LLM calls in sed.i.

Embeddings always use EMBED_PROVIDER (default "openai") regardless of LLM_PROVIDER.
Changing EMBED_PROVIDER after content is indexed requires a full re-embed migration.

Chat routes to LLM_PROVIDER ("openai" | "bedrock") with per-task model selection.
Failover: if chat fails on primary provider, retries on the other. Embed has no
fallback — it fails loudly rather than producing vectors in a different space.

Observability: OpenAI calls are traced in Braintrust when BRAINTRUST_API_KEY is set.
Bedrock calls are covered only by OTEL/Sentry task-level spans — no prompt-level detail.

Usage:
    from app.core.llm_client import llm_client, TASK_TAGGING, TASK_SQL_GEN

    result = llm_client.embed("some text")
    result = llm_client.chat(messages=[...], task=TASK_TAGGING)
    result = llm_client.structured_chat(messages=[...], response_model=TagResponse, task=TASK_TAGGING)
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

from pydantic import BaseModel
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

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
TASK_MEMORY_RESEARCH = "memory_research"

_EMBED_MODEL_OPENAI = "text-embedding-3-small"
_EMBED_MODEL_BEDROCK = (
    "amazon.titan-embed-text-v1"  # 1536-dim, compatible with existing schema
)

_US_BEDROCK_REGIONS = {"us-east-1", "us-east-2", "us-west-2"}


def _make_openai_client() -> OpenAI:
    """Build an OpenAI client, wrapped with Braintrust tracing if configured."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
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

    def embed(self, texts: list[str] | str, *, model: str | None = None) -> EmbedResult:
        """
        Embed one or more texts. Always uses EMBED_PROVIDER (default "openai").
        No provider fallback — fails loudly rather than producing incompatible vectors.
        """
        if isinstance(texts, str):
            texts = [texts]
        return self._embed_with(settings.EMBED_PROVIDER, texts, model=model)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        task: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """
        Single-turn or multi-turn chat completion.

        task: routes to the configured model for that task (e.g. TASK_TAGGING, TASK_SQL_GEN).
        model: explicit override, bypasses all routing.
        """
        primary = self._provider
        fallback = "bedrock" if primary == "openai" else "openai"

        try:
            return self._chat_with(
                primary,
                messages,
                model=model,
                task=task,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )
        except Exception as e:
            logger.warning(f"chat failed on {primary} ({e}), retrying on {fallback}")
            return self._chat_with(
                fallback,
                messages,
                model=model,
                task=task,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )

    def structured_chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModel],
        task: str,
        max_tokens: int = 512,
        max_retries: int = 2,
    ) -> BaseModel:
        """
        Chat that returns a validated Pydantic model instance.

        OpenAI: uses instructor for retry-on-validation-error.
        Bedrock: instruction-injection + manual retry loop with validation feedback.
        """
        if self._provider == "openai":
            return self._openai_structured_chat(
                messages,
                response_model=response_model,
                task=task,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
        return self._bedrock_structured_chat(
            messages,
            response_model=response_model,
            task=task,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )

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
    ) -> BaseModel:
        import instructor

        model = self._resolve_model(task, "openai")
        client = instructor.from_openai(self._openai())
        return client.chat.completions.create(
            model=model,
            messages=messages,
            response_model=response_model,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )

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

        response = client.converse(**kwargs)
        output = response["output"]["message"]["content"][0]["text"]

        if json_mode and output.startswith("```"):
            output = output.split("\n", 1)[-1]
            output = output.rsplit("```", 1)[0].strip()

        usage = response.get("usage", {})
        return ChatResult(
            content=output,
            model=model,
            prompt_tokens=usage.get("inputTokens", 0),
            completion_tokens=usage.get("outputTokens", 0),
        )

    def _bedrock_structured_chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModel],
        task: str,
        max_tokens: int,
        max_retries: int,
    ) -> BaseModel:
        """Structured output via Bedrock with manual retry-on-validation-error loop."""
        model = self._resolve_model(task, "bedrock")
        current_messages = list(messages)
        last_error: Exception = ValueError("no attempts made")

        for attempt in range(max_retries + 1):
            chat_result = self._bedrock_chat(
                current_messages,
                model=model,
                max_tokens=max_tokens,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            try:
                data = json.loads(chat_result.content)
                return response_model.model_validate(data)
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

        raise ValueError(
            f"structured_chat failed after {max_retries} retries: {last_error}"
        )


# Module-level singleton — import this everywhere
llm_client = LLMClient()


@contextmanager
def braintrust_span(
    name: str, *, input: dict | None = None
) -> Generator[Any, None, None]:
    """
    Context manager that wraps a logical step in a Braintrust span when tracing
    is active. No-op when BRAINTRUST_API_KEY is not set.

    Usage:
        with braintrust_span("planning", input={"question": q}):
            result = llm_client.structured_chat(...)

    The Braintrust wrap_openai integration auto-attaches child LLM call spans
    under whichever span is current — so wrapping each pipeline step here groups
    all its LLM calls together in the trace UI.
    """
    if not settings.BRAINTRUST_API_KEY:
        yield None
        return

    try:
        import braintrust
    except Exception:
        yield None
        return

    with braintrust.start_span(name=name, input=input or {}) as span:
        yield span
