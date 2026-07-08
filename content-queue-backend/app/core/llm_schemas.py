"""Pydantic models for structured LLM outputs."""

from typing import Literal  # noqa: F401 — used by EntityItem.type

from pydantic import BaseModel, field_validator, model_validator

# Valid sets as constants so normalizers can reference them
_VALID_ENTITY_TYPES = {"PERSON", "CONCEPT", "ORGANIZATION", "PAPER", "TOOL"}

_ENTITY_TYPE_ALIASES: dict[str, str] = {
    # Types the LLM invents that map cleanly to existing ones
    "MATERIAL": "CONCEPT",
    "TECHNIQUE": "CONCEPT",
    "METHOD": "CONCEPT",
    "ALGORITHM": "CONCEPT",
    "MODEL": "TOOL",
    "FRAMEWORK": "TOOL",
    "LIBRARY": "TOOL",
    "DATASET": "PAPER",
    "RESEARCHER": "PERSON",
    "INSTITUTION": "ORGANIZATION",
    "COMPANY": "ORGANIZATION",
    "LAB": "ORGANIZATION",
}


def _normalize_entity_type(value: str) -> str:
    """Map LLM entity type variants to a valid type. Falls back to CONCEPT."""
    upper = value.strip().upper()
    if upper in _VALID_ENTITY_TYPES:
        return upper
    if upper in _ENTITY_TYPE_ALIASES:
        return _ENTITY_TYPE_ALIASES[upper]
    return (
        "CONCEPT"  # safe fallback — don't fail the whole extraction over a type label
    )


class EntityItem(BaseModel):
    name: str
    type: Literal["PERSON", "CONCEPT", "ORGANIZATION", "PAPER", "TOOL"]
    description: str = ""
    mention_context: str = ""  # one sentence from the article where this entity appears

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        return _normalize_entity_type(v)


class RelationItem(BaseModel):
    source: str
    target: str
    predicate: str  # free-text, 3-8 words: "founded and transformed", "outperforms on benchmarks"
    strength: int = 3  # 1-5: 5=explicitly stated, 3=strongly implied, 1=weak inference
    description: str = ""  # verbatim or near-verbatim sentence from article as evidence


class EntityExtractionResponse(BaseModel):
    entities: list[EntityItem]
    relations: list[RelationItem]


class ArticleAnalysisResponse(BaseModel):
    domain_tags: list[str]
    concept_tags: list[str]
    entities: list[EntityItem]
    relations: list[RelationItem]

    @field_validator("domain_tags", "concept_tags", mode="before")
    @classmethod
    def validate_tags(cls, tags: list) -> list:
        result = []
        for tag in tags:
            tag = str(tag).strip()
            words = tag.split()
            if len(words) < 2:
                raise ValueError(
                    f"Tag '{tag}' is a single word. All tags must be 2-4 words."
                )
            result.append(tag)
        return result[
            :4
        ]  # domain capped at 4 total; concept same — combined enforced in task

    @model_validator(mode="after")
    def filter_relations_to_entity_list(self) -> "ArticleAnalysisResponse":
        """Drop any relation whose source or target isn't in the extracted entity list."""
        entity_names = {e.name.lower().strip() for e in self.entities}
        self.relations = [
            r
            for r in self.relations
            if r.source.lower().strip() in entity_names
            and r.target.lower().strip() in entity_names
        ]
        return self


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
