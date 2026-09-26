"""Tests for MCP Skills registration (Step 1.3)."""

import json

from app.mcp.skills import (
    CONNECT_NEW_SAVE_SKILL,
    DRAFT_FROM_HIGHLIGHTS_SKILL,
    SEDI_SKILLS,
    WEEKLY_DIGEST_SKILL,
)


class TestSkillsRegistration:
    def test_skills_resource_has_all_three_keys(self):
        assert "weekly-digest" in SEDI_SKILLS
        assert "connect-new-save" in SEDI_SKILLS
        assert "draft-from-highlights" in SEDI_SKILLS

    def test_sedi_skills_is_json_serializable(self):
        dumped = json.dumps(SEDI_SKILLS)
        parsed = json.loads(dumped)
        assert set(parsed.keys()) == {
            "weekly-digest",
            "connect-new-save",
            "draft-from-highlights",
        }

    def test_each_skill_has_required_sections(self):
        for skill in [
            WEEKLY_DIGEST_SKILL,
            CONNECT_NEW_SAVE_SKILL,
            DRAFT_FROM_HIGHLIGHTS_SKILL,
        ]:
            assert "Goal:" in skill
            assert "Steps:" in skill

    def test_draft_skill_contains_write_constraint(self):
        skill_lower = DRAFT_FROM_HIGHLIGHTS_SKILL.lower()
        assert "update_draft" in DRAFT_FROM_HIGHLIGHTS_SKILL
        assert (
            "do not modify the library" in skill_lower
            or "only call update_draft" in skill_lower
        )

    def test_connect_skill_references_entity_traversal(self):
        assert "explore_concept" in CONNECT_NEW_SAVE_SKILL

    def test_weekly_skill_references_memory(self):
        skill_lower = WEEKLY_DIGEST_SKILL.lower()
        assert "current_focus" in WEEKLY_DIGEST_SKILL or "user memory" in skill_lower

    def test_skills_values_are_nonempty_strings(self):
        for key, value in SEDI_SKILLS.items():
            assert isinstance(value, str), f"{key} must be a string"
            assert len(value) > 50, f"{key} skill text is suspiciously short"
