"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("api_id", sa.Integer(), nullable=False),
        sa.Column("api_hash", sa.String(64), nullable=False),
        sa.Column("session_string_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("session_file_path", sa.String(255), nullable=True),
        sa.Column("password_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("proxy", JSONB(), nullable=True),
        sa.Column("device_model", sa.String(128), nullable=True),
        sa.Column("system_version", sa.String(128), nullable=True),
        sa.Column("app_version", sa.String(64), nullable=True),
        sa.Column("lang_code", sa.String(16), nullable=True),
        sa.Column("system_lang_code", sa.String(16), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=True),
        sa.Column("last_name", sa.String(128), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("is_premium", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("warmup_done", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("can_search", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("role", sa.String(16), server_default="scanner", nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("ix_accounts_name", "telegram_accounts", ["name"])
    op.create_index("ix_accounts_enabled", "telegram_accounts", ["enabled"])
    op.create_index("ix_accounts_role", "telegram_accounts", ["role"])
    op.create_index("ix_accounts_username", "telegram_accounts", ["username"])

    op.create_table(
        "account_states",
        sa.Column("account_id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), server_default="idle", nullable=False),
        sa.Column("day_date", sa.Date(), nullable=True),
        sa.Column("groups_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("members_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("messages_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("profiles_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("searches_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("hour_slot", sa.Integer(), nullable=True),
        sa.Column("groups_hour", sa.Integer(), server_default="0", nullable=False),
        sa.Column("floods_hour", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_flood_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flood_backoff_multiplier", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ban_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ban_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ban_reason", sa.String(64), server_default="", nullable=False),
        sa.Column("total_bans", sa.Integer(), server_default="0", nullable=False),
        sa.Column("spamblock_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats_spam_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("work_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rest_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["telegram_accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_state_status", "account_states", ["status"])

    op.create_table(
        "target_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("title", sa.String(255), server_default="", nullable=False),
        sa.Column("type", sa.String(16), server_default="", nullable=False),
        sa.Column("members_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("discovered_via", sa.String(16), server_default="seed", nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("depth", sa.Integer(), server_default="0", nullable=False),
        sa.Column("scan_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("members_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scanned_msg_id", sa.BigInteger(), nullable=True),
        sa.Column("last_members_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("monitor_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(255), nullable=True),
        sa.UniqueConstraint("tg_id"),
    )
    op.create_index("ix_group_tg_id", "target_groups", ["tg_id"])
    op.create_index("ix_group_username", "target_groups", ["username"])
    op.create_index("ix_group_scan_status", "target_groups", ["scan_status"])
    op.create_index("ix_group_members_status", "target_groups", ["members_status"])
    op.create_index("ix_group_discovered_via", "target_groups", ["discovered_via"])
    op.create_index("ix_group_monitor_enabled", "target_groups", ["monitor_enabled"])

    op.create_table(
        "parsed_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_username", sa.String(64), nullable=True),
        sa.Column("sender_name", sa.String(255), nullable=True),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("has_links", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("matched_keywords", JSONB(), nullable=True),
        sa.Column("source", sa.String(16), server_default="history", nullable=False),
        sa.UniqueConstraint("group_tg_id", "message_id", name="uq_msg"),
    )
    op.create_index("ix_msg_group_msg", "parsed_messages", ["group_tg_id", "message_id"])
    op.create_index("ix_msg_date", "parsed_messages", ["date"])
    op.create_index("ix_msg_sender", "parsed_messages", ["sender_id"])

    op.create_table(
        "parsed_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=True),
        sa.Column("last_name", sa.String(128), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("is_bot", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("seen_in_groups_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("seen_in_messages_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tg_id"),
    )
    op.create_index("ix_user_tg_id", "parsed_users", ["tg_id"])
    op.create_index("ix_user_username", "parsed_users", ["username"])

    op.create_table(
        "group_memberships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("user_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), server_default="member", nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("group_tg_id", "user_tg_id", name="uq_membership"),
    )
    op.create_index("ix_membership_group", "group_memberships", ["group_tg_id"])
    op.create_index("ix_membership_user", "group_memberships", ["user_tg_id"])

    op.create_table(
        "discovered_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_group_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("target_username", sa.String(64), nullable=False),
        sa.Column("resolved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("depth", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(255), nullable=True),
        sa.UniqueConstraint("target_username", name="uq_link"),
    )
    op.create_index("ix_link_target", "discovered_links", ["target_username"])
    op.create_index("ix_link_resolved", "discovered_links", ["resolved"])

    op.create_table(
        "keywords",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("word", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("found_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_searched_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("word"),
    )
    op.create_index("ix_keyword_word", "keywords", ["word"])

    op.create_table(
        "parser_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("target", sa.String(255), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assigned_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result", JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["assigned_account_id"], ["telegram_accounts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_task_status_prio", "parser_tasks", ["status", "priority"])
    op.create_index("ix_task_type", "parser_tasks", ["task_type"])
    op.create_index("ix_task_created_at", "parser_tasks", ["created_at"])

    op.create_table(
        "proxies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scheme", sa.String(16), server_default="http", nullable=False),
        sa.Column("host", sa.String(128), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(128), nullable=True),
        sa.Column("password", sa.String(128), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fails_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assigned_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("host", "port", "username", name="uq_proxy_hostportuser"),
        sa.ForeignKeyConstraint(["assigned_account_id"], ["telegram_accounts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_proxy_host", "proxies", ["host"])
    op.create_index("ix_proxy_active", "proxies", ["active"])
    op.create_index("ix_proxy_assigned", "proxies", ["assigned_account_id"])


def downgrade() -> None:
    op.drop_table("proxies")
    op.drop_table("parser_tasks")
    op.drop_table("keywords")
    op.drop_table("discovered_links")
    op.drop_table("group_memberships")
    op.drop_table("parsed_users")
    op.drop_table("parsed_messages")
    op.drop_table("target_groups")
    op.drop_table("account_states")
    op.drop_table("telegram_accounts")
