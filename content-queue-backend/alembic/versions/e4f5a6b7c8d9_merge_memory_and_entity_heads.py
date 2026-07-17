"""merge memory and entity heads

Revision ID: e4f5a6b7c8d9
Revises: d1e2f3a4b5c6, 9377e6bfc72e
Create Date: 2026-07-10 08:00:00.000000

"""

from typing import Sequence, Union

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = ("d1e2f3a4b5c6", "9377e6bfc72e")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
