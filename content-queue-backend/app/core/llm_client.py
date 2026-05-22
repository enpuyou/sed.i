"""
LLMClient — single entry point for all LLM calls in sed.i.

All tasks (embedding, tagging, summarization, chat) go through here.
Provider is configured via settings.LLM_PROVIDER ("openai" | "bedrock").
Adding a new provider means implementing it once here, not touching call sites.

Observability: when BRAINTRUST_API_KEY is set, the OpenAI client is wrapped
with braintrust.wrap_openai so every call is traced in Braintrust with cost,
latency, input, and output. Leave the key empty to disable tracing (safe in
dev and test).

Provider notes:
- "openai": text-embedding-3-small for embeddings, gpt-4o-mini for fast chat.
- "bedrock": Amazon Titan Embed v2 for embeddings (Anthropic doesn't offer
  embeddings on Bedrock), Claude Haiku for fast chat, Claude Sonnet for
  synthesis. Uses the Converse API (unified across Claude model families).
  Failover: if primary provider errors, retries on the fallback provider.

Usage:
    from app.core.llm_client import llm_client

    result = llm_client.embed("some text")
    result = llm_client.chat(messages=[...])
    result = llm_client.chat(messages=[...], prefer_quality=True)  # Sonnet
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default OpenAI models
_EMBED_MODEL = "text-embedding-3-small"
_CHAT_MODEL_FAST = "gpt-4o-mini"

# Bedrock embed model — Titan Embed v2 (1536 dims, same as text-embedding-3-small)
_BEDROCK_EMBED_MODEL = "amazon.titan-embed-text-v2:0"


def _make_openai_client() -> OpenAI:
    """Build an OpenAI client, wrapped with Braintrust tracing if configured."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    if settings.BRAINTRUST_API_KEY:
        try:
            import braintrust

            braintrust.login(api_key=settings.BRAINTRUST_API_KEY)
            client = braintrust.wrap_openai(client)
            logger.info("Braintrust tracing enabled for LLM calls")
        except Exception as e:
            # Never block the app if Braintrust is misconfigured
            logger.warning(f"Braintrust init failed, tracing disabled: {e}")
    return client


def _make_bedrock_client() -> Any:
    """Build a boto3 bedrock-runtime client."""
    import boto3

    kwargs: dict[str, Any] = {"region_name": settings.AWS_REGION}
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

    LLM_PROVIDER="openai" (default): uses OpenAI for both embed and chat.
    LLM_PROVIDER="bedrock": uses Amazon Titan Embed v2 for embeddings and
      Claude Haiku/Sonnet via the Bedrock Converse API for chat.

    Failover: if the configured provider raises, the client logs a warning
    and retries on the other provider. This keeps the app running during
    partial outages without silent data loss.
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

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def embed(self, texts: list[str] | str, *, model: str | None = None) -> EmbedResult:
        """
        Embed one or more texts. Returns EmbedResult with .embeddings list
        in the same order as input. Single string input returns a list of one.
        """
        if isinstance(texts, str):
            texts = [texts]

        primary = self._provider
        fallback = "bedrock" if primary == "openai" else "openai"

        try:
            return self._embed_with(primary, texts, model=model)
        except Exception as e:
            logger.warning(f"embed failed on {primary} ({e}), retrying on {fallback}")
            return self._embed_with(fallback, texts, model=model)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
        prefer_quality: bool = False,
    ) -> ChatResult:
        """
        Single-turn or multi-turn chat completion.

        prefer_quality=True selects Sonnet (Bedrock) or gpt-4o (OpenAI)
        for tasks that need better reasoning (MCP synthesis, summaries).
        """
        primary = self._provider
        fallback = "bedrock" if primary == "openai" else "openai"

        try:
            return self._chat_with(
                primary,
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                prefer_quality=prefer_quality,
            )
        except Exception as e:
            logger.warning(f"chat failed on {primary} ({e}), retrying on {fallback}")
            return self._chat_with(
                fallback,
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                prefer_quality=prefer_quality,
            )

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    def _embed_with(
        self, provider: str, texts: list[str], *, model: str | None
    ) -> EmbedResult:
        if provider == "openai":
            return self._openai_embed(texts, model=model or _EMBED_MODEL)
        if provider == "bedrock":
            return self._bedrock_embed(texts, model=model or _BEDROCK_EMBED_MODEL)
        raise NotImplementedError(f"Unknown provider '{provider}'")

    def _chat_with(
        self,
        provider: str,
        messages: list[dict[str, str]],
        *,
        model: str | None,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None,
        prefer_quality: bool,
    ) -> ChatResult:
        if provider == "openai":
            resolved_model = model or ("gpt-4o" if prefer_quality else _CHAT_MODEL_FAST)
            return self._openai_chat(
                messages,
                model=resolved_model,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )
        if provider == "bedrock":
            resolved_model = model or (
                settings.BEDROCK_SMART_MODEL
                if prefer_quality
                else settings.BEDROCK_FAST_MODEL
            )
            return self._bedrock_chat(
                messages,
                model=resolved_model,
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

    # ------------------------------------------------------------------
    # Bedrock implementation
    # ------------------------------------------------------------------

    def _bedrock_embed(self, texts: list[str], *, model: str) -> EmbedResult:
        """
        Embed via Amazon Titan Embed v2. Titan processes one text at a time,
        so we loop and aggregate — same interface as OpenAI batch endpoint.
        """
        client = self._bedrock()
        embeddings = []
        total_tokens = 0
        for text in texts:
            body = json.dumps({"inputText": text, "dimensions": 1536})
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
        JSON instruction to the last user message — Bedrock doesn't have a
        native JSON mode.
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

        # Emulate JSON mode by instruction injection
        if response_format and response_format.get("type") == "json_object":
            if converse_messages and converse_messages[-1]["role"] == "user":
                last = converse_messages[-1]["content"][0]["text"]
                converse_messages[-1]["content"][0]["text"] = (
                    last + "\n\nRespond with valid JSON only."
                )

        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": converse_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_parts:
            kwargs["system"] = system_parts

        response = client.converse(**kwargs)
        output = response["output"]["message"]["content"][0]["text"]
        usage = response.get("usage", {})
        return ChatResult(
            content=output,
            model=model,
            prompt_tokens=usage.get("inputTokens", 0),
            completion_tokens=usage.get("outputTokens", 0),
        )


# Module-level singleton — import this everywhere
llm_client = LLMClient()
