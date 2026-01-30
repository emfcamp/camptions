"""Initial migration - create venues, sessions, and segments tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-01-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create venues table
    op.create_table(
        "venues",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("is_active", sa.Integer, default=1),
        sa.Column("created_at", sa.DateTime),
    )

    # Create sessions table
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("venue_id", sa.String(50), sa.ForeignKey("venues.id"), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("started_at", sa.DateTime),
        sa.Column("ended_at", sa.DateTime),
    )

    # Create segments table
    op.create_table(
        "segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("segment_type", sa.String(20), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("speaker", sa.String(50)),
        sa.Column("start_time", sa.Float, nullable=False),
        sa.Column("end_time", sa.Float),
        sa.Column("created_at", sa.DateTime),
    )

    # Create indexes
    op.create_index("idx_session_sequence", "segments", ["session_id", "sequence"])
    op.create_index("idx_session_type", "segments", ["session_id", "segment_type"])


def downgrade() -> None:
    op.drop_index("idx_session_type", table_name="segments")
    op.drop_index("idx_session_sequence", table_name="segments")
    op.drop_table("segments")
    op.drop_table("sessions")
    op.drop_table("venues")
