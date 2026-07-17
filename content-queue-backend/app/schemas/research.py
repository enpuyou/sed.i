"""
Pydantic schemas for the multi-agent research brief feature.

ResearchBrief    — structured output produced by the synthesis task
SubagentResult   — what each parallel subagent returns to the lead agent
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SourceCitation(BaseModel):
    item_id: str
    title: str
    representative_highlight: str | None = None


class SubQuestionFinding(BaseModel):
    sub_question: str
    coverage: Literal["full", "partial", "none"]
    finding: str
    key_sources: list[SourceCitation]
    tensions: list[str]


class GapItem(BaseModel):
    sub_question: str
    what_is_missing: str
    partial_coverage: list[str]  # item_ids that touch this sub-Q but don't answer it


class ResearchBrief(BaseModel):
    summary: str
    sub_question_findings: list[SubQuestionFinding]
    cross_cutting_tensions: list[str]
    gaps: list[GapItem]
    engagement_note: str
    confidence: Literal["high", "medium", "low"]


class SubagentResult(BaseModel):
    sub_question: str
    articles: list[dict]  # {id, title, description, highlights, engagement_score}
    coverage_assessment: Literal["full", "partial", "none"]
