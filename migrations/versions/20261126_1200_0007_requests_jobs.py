"""requests_jobs: request lifecycle columns, jobs queue and notification preferences

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLOSE_REASONS = "'client_closed', 'client_cancelled', 'auto_expired', 'auto_stale'"
EVENTS = (
    "'new_request', 'new_response', 'chosen', 'request_cancelled', "
    "'review_prompt', 'expiry_reminder', 'done_check'"
)
PHONE_RE = "^[+][1-9][0-9]{6,14}$"


def upgrade() -> None:
    # master_chosen/completed rows would have no client_phone (it is new and required for them).
    # Staging should have none; fail loudly instead of guessing a phone number.
    stuck = (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM requests WHERE status IN ('master_chosen', 'completed')")
        )
        .scalar_one()
    )
    if stuck:
        raise RuntimeError(
            f"{stuck} request(s) are master_chosen or completed and have no client_phone. "
            "Close or delete them (or backfill client_phone and master_chosen_at) before "
            "running migration 0007."
        )

    op.add_column(
        "users", sa.Column("telegram_blocked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("responses", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        "requests",
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
    )
    op.execute("UPDATE requests SET opened_at = created_at")
    op.alter_column("requests", "opened_at", nullable=False)
    op.add_column(
        "requests", sa.Column("master_chosen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("requests", sa.Column("close_reason", sa.String(length=20), nullable=True))
    op.add_column(
        "requests", sa.Column("expiry_reminded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("requests", sa.Column("done_asked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("requests", sa.Column("client_phone", sa.String(length=16), nullable=True))
    op.add_column(
        "requests",
        sa.Column("feed_pause_at", sa.Integer(), server_default="15", nullable=False),
    )
    # Requests closed or cancelled before this migration have no recorded reason.
    op.execute(
        "UPDATE requests SET close_reason = "
        "CASE status WHEN 'cancelled' THEN 'client_cancelled' ELSE 'client_closed' END "
        "WHERE status IN ('closed', 'cancelled')"
    )
    op.create_check_constraint(
        op.f("ck_requests_close_reason_value"), "requests", f"close_reason IN ({CLOSE_REASONS})"
    )
    op.create_check_constraint(
        op.f("ck_requests_close_reason"),
        "requests",
        "(status IN ('closed', 'cancelled')) = (close_reason IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_requests_master_chosen_at"),
        "requests",
        "(chosen_master_id IS NOT NULL) = (master_chosen_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_requests_client_phone_format"),
        "requests",
        f"client_phone IS NULL OR client_phone ~ '{PHONE_RE}'",
    )
    op.create_check_constraint(
        op.f("ck_requests_client_phone_when_chosen"),
        "requests",
        "status NOT IN ('master_chosen', 'completed') OR client_phone IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_requests_feed_pause_at_positive"), "requests", "feed_pause_at > 0"
    )
    op.create_index("ix_requests_status_opened_at", "requests", ["status", "opened_at"])
    op.create_index(
        "ix_requests_status_master_chosen_at", "requests", ["status", "master_chosen_at"]
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "run_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(kind) <> ''", name=op.f("ck_jobs_kind_not_blank")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_jobs_attempts_not_negative")),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_jobs_max_attempts_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_jobs_dedupe_key")),
    )
    op.create_index(
        "ix_jobs_run_at_pending",
        "jobs",
        ["run_at"],
        postgresql_where=sa.text("done_at IS NULL AND failed_at IS NULL"),
    )

    op.create_table(
        "notification_prefs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event", sa.String(length=24), nullable=False),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint(f"event IN ({EVENTS})", name=op.f("ck_notification_prefs_event")),
        sa.CheckConstraint(
            "channel IN ('telegram', 'email')", name=op.f("ck_notification_prefs_channel")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_prefs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "event", "channel", name=op.f("pk_notification_prefs")),
    )


def downgrade() -> None:
    op.drop_table("notification_prefs")
    op.drop_index(
        "ix_jobs_run_at_pending",
        table_name="jobs",
        postgresql_where=sa.text("done_at IS NULL AND failed_at IS NULL"),
    )
    op.drop_table("jobs")
    op.drop_index("ix_requests_status_master_chosen_at", table_name="requests")
    op.drop_index("ix_requests_status_opened_at", table_name="requests")
    for name in (
        "ck_requests_feed_pause_at_positive",
        "ck_requests_client_phone_when_chosen",
        "ck_requests_client_phone_format",
        "ck_requests_master_chosen_at",
        "ck_requests_close_reason",
        "ck_requests_close_reason_value",
    ):
        op.drop_constraint(op.f(name), "requests", type_="check")
    for col in (
        "feed_pause_at",
        "client_phone",
        "done_asked_at",
        "expiry_reminded_at",
        "close_reason",
        "master_chosen_at",
        "opened_at",
    ):
        op.drop_column("requests", col)
    op.drop_column("responses", "withdrawn_at")
    op.drop_column("users", "telegram_blocked_at")
