"""Separate logical commands, execution attempts, and actor controls.

Revision ID: 20260716_0500
Revises: 20260716_0400
Create Date: 2026-07-16 11:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260716_0500"
down_revision: str | None = "20260716_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``agent_commands`` remains the stable logical command trace.  Attempt
    # rows prevent a retry from ever reusing a stale running lease.
    op.add_column("agent_commands", sa.Column("confirmation_event_id", sa.Text(), nullable=True))
    op.add_column("agent_commands", sa.Column("impact_identity", sa.Text(), nullable=True))
    op.add_column(
        "agent_commands",
        sa.Column(
            "direct_execution", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "agent_commands",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_commands", sa.Column("active_attempt_id", sa.Text(), nullable=True))
    op.add_column(
        "agent_commands",
        sa.Column("cancel_requested_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("agent_commands", sa.Column("cancel_requested_by", sa.Text(), nullable=True))
    op.add_column("agent_commands", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column(
        "agent_commands",
        sa.Column("cancel_accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_commands",
        sa.Column("cancel_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_commands", sa.Column("terminal_event_id", sa.Text(), nullable=True))

    op.create_table(
        "agent_command_attempts",
        sa.Column("attempt_id", sa.Text(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "available_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_id", sa.Text(), nullable=True),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("leased_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint("command_id", "attempt_no", name="uq_agent_command_attempt_number"),
    )
    op.create_index(
        "ix_agent_command_attempts_due",
        "agent_command_attempts",
        ["workspace_id", "cluster_id", "status", "available_at"],
    )
    op.create_index(
        "ix_agent_command_attempts_command",
        "agent_command_attempts",
        ["workspace_id", "command_id", "attempt_no"],
    )

    op.create_table(
        "command_control_actions",
        sa.Column("control_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("audit_event_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("control_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "command_id",
            "action",
            "idempotency_key",
            name="uq_command_control_action_idempotency",
        ),
    )
    op.create_index(
        "ix_command_control_actions_command",
        "command_control_actions",
        ["workspace_id", "command_id", "created_at"],
    )

    # Preserve observability for pre-migration rows.  A legacy direct command
    # has no immutable confirmation event, so a later retry will fail closed
    # until the user submits a fresh confirmed command.
    op.execute(
        """
        insert into agent_command_attempts (
            attempt_id, command_id, workspace_id, cluster_id, attempt_no,
            status, available_at, lease_id, agent_id, leased_until, started_at,
            completed_at, result, created_at, updated_at
        )
        select
            'legacy:' || command_id || ':' || '1',
            command_id,
            workspace_id,
            cluster_id,
            1,
            status,
            created_at,
            lease_id,
            agent_id,
            leased_until,
            started_at,
            completed_at,
            result,
            created_at,
            updated_at
        from agent_commands
        on conflict (command_id, attempt_no) do nothing
        """
    )
    op.execute(
        """
        update agent_commands
        set
            attempt_count = greatest(attempt_count, 1),
            active_attempt_id = case
                when status in ('queued', 'leased', 'running')
                    then 'legacy:' || command_id || ':' || '1'
                else active_attempt_id
            end,
            direct_execution = direct_execution
                or coalesce(payload ->> 'direct_execution', 'false') = 'true'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_command_control_actions_command", table_name="command_control_actions")
    op.drop_table("command_control_actions")
    op.drop_index("ix_agent_command_attempts_command", table_name="agent_command_attempts")
    op.drop_index("ix_agent_command_attempts_due", table_name="agent_command_attempts")
    op.drop_table("agent_command_attempts")
    op.drop_column("agent_commands", "terminal_event_id")
    op.drop_column("agent_commands", "cancel_generation")
    op.drop_column("agent_commands", "cancel_accepted_at")
    op.drop_column("agent_commands", "cancel_reason")
    op.drop_column("agent_commands", "cancel_requested_by")
    op.drop_column("agent_commands", "cancel_requested_at")
    op.drop_column("agent_commands", "active_attempt_id")
    op.drop_column("agent_commands", "attempt_count")
    op.drop_column("agent_commands", "direct_execution")
    op.drop_column("agent_commands", "impact_identity")
    op.drop_column("agent_commands", "confirmation_event_id")
