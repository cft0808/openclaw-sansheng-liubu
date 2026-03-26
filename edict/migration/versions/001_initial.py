"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tasks 表 ──
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("priority", sa.String(10), server_default="中"),
        sa.Column("state", sa.String(20), nullable=False, server_default="Taizi"),
        sa.Column("assignee_org", sa.String(50), nullable=True),
        sa.Column("creator", sa.String(50), server_default="emperor"),
        sa.Column("tags", postgresql.JSONB(), server_default="[]"),
        sa.Column("org", sa.String(32), nullable=False, server_default="太子"),
        sa.Column("official", sa.String(32), server_default=""),
        sa.Column("now", sa.Text(), server_default=""),
        sa.Column("eta", sa.String(64), server_default="-"),
        sa.Column("block", sa.Text(), server_default="无"),
        sa.Column("output", sa.Text(), server_default=""),
        sa.Column("archived", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("flow_log", postgresql.JSONB(), server_default="[]"),
        sa.Column("progress_log", postgresql.JSONB(), server_default="[]"),
        sa.Column("todos", postgresql.JSONB(), server_default="[]"),
        sa.Column("scheduler", postgresql.JSONB(), server_default="{}"),
        sa.Column("template_id", sa.String(64), server_default=""),
        sa.Column("template_params", postgresql.JSONB(), server_default="{}"),
        sa.Column("ac", sa.Text(), server_default=""),
        sa.Column("target_dept", sa.String(64), server_default=""),
        sa.Column("meta", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("ix_tasks_state", "tasks", ["state"])
    op.create_index("ix_tasks_trace_id", "tasks", ["trace_id"])
    op.create_index("ix_tasks_assignee_org", "tasks", ["assignee_org"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_index("ix_tasks_updated_at", "tasks", ["updated_at"])
    op.create_index("ix_tasks_state_archived", "tasks", ["state", "archived"])
    op.create_index("ix_tasks_tags", "tasks", ["tags"], postgresql_using="gin")

    # ── events 表 ──
    op.create_table(
        "events",
        sa.Column("event_id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("producer", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("meta", postgresql.JSONB(), server_default="{}"),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_events_trace_id", "events", ["trace_id"])
    op.create_index("ix_events_topic", "events", ["topic"])
    op.create_index("ix_events_timestamp", "events", ["timestamp"])

    # ── thoughts 表 ──
    op.create_table(
        "thoughts",
        sa.Column("thought_id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(50), nullable=False),
        sa.Column("step", sa.Integer(), server_default="0"),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("thought_id"),
    )
    op.create_index("ix_thoughts_trace_id", "thoughts", ["trace_id"])
    op.create_index("ix_thoughts_agent", "thoughts", ["agent"])

    # ── todos 表 ──
    op.create_table(
        "todos",
        sa.Column("todo_id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default="not-started"),
        sa.Column("priority", sa.Integer(), server_default="0"),
        sa.Column("assignee", sa.String(50), nullable=True),
        sa.Column("detail", sa.Text(), server_default=""),
        sa.Column("checkpoints", postgresql.JSONB(), server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("todo_id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["todos.todo_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_todos_task_id", "todos", ["task_id"])
    op.create_index("ix_todos_status", "todos", ["status"])


def downgrade() -> None:
    op.drop_table("todos")
    op.drop_table("thoughts")
    op.drop_table("events")
    op.drop_table("tasks")
