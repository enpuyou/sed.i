"""Pydantic models for structured LLM outputs."""

from pydantic import BaseModel, field_validator


class TagResponse(BaseModel):
    tags: list[str]

    @field_validator("tags")
    @classmethod
    def tags_must_be_multi_word(cls, tags: list[str]) -> list[str]:
        for tag in tags:
            if len(tag.strip().split()) < 2:
                raise ValueError(
                    f"Tag '{tag}' is a single word. All tags must be 2-4 words "
                    "(e.g. 'machine learning' not 'ML', 'sleep science' not 'sleep')."
                )
        return tags
