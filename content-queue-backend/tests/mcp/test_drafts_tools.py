"""
TDD tests for MCP drafts tool: get_draft.
"""

import pytest
from app.mcp.tools.drafts import get_draft


class TestGetDraft:
    def test_returns_draft_for_list(self, db, user, reading_list, draft):
        result = get_draft(list_id=str(reading_list.id), user=user, db=db)
        assert result is not None
        assert result["title"] == draft.title
        assert result["content"] == draft.content

    def test_returns_none_when_no_draft(self, db, user, reading_list):
        result = get_draft(list_id=str(reading_list.id), user=user, db=db)
        assert result is None

    def test_raises_on_unknown_list(self, db, user):
        with pytest.raises(ValueError, match="not found"):
            get_draft(list_id="00000000-0000-0000-0000-000000000000", user=user, db=db)

    def test_raises_on_other_users_list(self, db, user, other_user):
        from app.models.list import List

        other_list = List(name="Other", owner_id=other_user.id)
        db.add(other_list)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            get_draft(list_id=str(other_list.id), user=user, db=db)

    def test_result_contains_required_fields(self, db, user, reading_list, draft):
        result = get_draft(list_id=str(reading_list.id), user=user, db=db)
        for field in ("title", "content", "word_count", "updated_at"):
            assert field in result, f"Missing field: {field}"

    def test_does_not_return_other_users_draft(
        self, db, user, other_user, reading_list
    ):
        from app.models.draft import Draft

        other_draft = Draft(
            list_id=reading_list.id,
            user_id=other_user.id,
            content="other content",
            word_count=2,
        )
        db.add(other_draft)
        db.commit()
        # User has no draft for this list (only other_user does)
        result = get_draft(list_id=str(reading_list.id), user=user, db=db)
        assert result is None
