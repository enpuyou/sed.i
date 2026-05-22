"""
LLMClient — single entry point for all LLM calls in sed.i.

All tasks (embedding, tagging, summarization, chat) go through here.
Provider is configured via settings.LLM_PROVIDER ("openai" | "bedrock").
Adding a new provider means implementing it once here, not touching call sites.

Usage:
    from app.core.llm_client import llm_client

    embedding = await llm_client.embed("some text")
    tags = llm_client.chat(model="tag", messages=[...])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default models per task — override via settings in future layers
_EMBED_MODEL = "text-embedding-3-small"
_CHAT_MODEL_FAST = "gpt-4o-mini"


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

    Currently wraps OpenAI synchronously (matching existing Celery task patterns).
    Bedrock implementation slot: add a BedrockProvider class and wire via LLM_PROVIDER.
    """

    def __init__(self) -> None:
        self._provider = settings.LLM_PROVIDER
        self._openai_client: OpenAI | None = None

    def _openai(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        return self._openai_client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def embed(
        self, texts: list[str] | str, *, model: str = _EMBED_MODEL
    ) -> EmbedResult:
        """
        Embed one or more texts. Returns EmbedResult with .embeddings list
        in the same order as input. Single string input returns a list of one.
        """
        if isinstance(texts, str):
            texts = [texts]

        if self._provider == "openai":
            return self._openai_embed(texts, model=model)

        raise NotImplementedError(f"Provider '{self._provider}' not yet implemented")

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = _CHAT_MODEL_FAST,
        max_tokens: int = 512,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """
        Single-turn or multi-turn chat completion.
        """
        if self._provider == "openai":
            return self._openai_chat(
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )

        raise NotImplementedError(f"Provider '{self._provider}' not yet implemented")

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


# Module-level singleton — import this everywhere
llm_client = LLMClient()
