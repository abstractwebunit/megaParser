from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    api_id: Mapped[int] = mapped_column(Integer)
    api_hash: Mapped[str] = mapped_column(String(64))

    session_string_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    session_file_path: Mapped[str | None] = mapped_column(String(255))
    password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)

    proxy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    device_model: Mapped[str | None] = mapped_column(String(128))
    system_version: Mapped[str | None] = mapped_column(String(128))
    app_version: Mapped[str | None] = mapped_column(String(64))
    lang_code: Mapped[str | None] = mapped_column(String(16))
    system_lang_code: Mapped[str | None] = mapped_column(String(16))

    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    warmup_done: Mapped[bool] = mapped_column(Boolean, default=False)
    can_search: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(16), default="scanner", index=True)
    # scanner / discovery / monitor

    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    state: Mapped["AccountState"] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )


class AccountState(Base):
    __tablename__ = "account_states"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), default="idle", index=True)
    # idle / active / resting / banned / disconnected / dead

    day_date: Mapped[date | None] = mapped_column(Date)
    groups_today: Mapped[int] = mapped_column(Integer, default=0)
    members_today: Mapped[int] = mapped_column(Integer, default=0)
    messages_today: Mapped[int] = mapped_column(Integer, default=0)
    profiles_today: Mapped[int] = mapped_column(Integer, default=0)
    searches_today: Mapped[int] = mapped_column(Integer, default=0)

    hour_slot: Mapped[int | None] = mapped_column(Integer)
    groups_hour: Mapped[int] = mapped_column(Integer, default=0)

    floods_hour: Mapped[int] = mapped_column(Integer, default=0)
    last_flood_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flood_backoff_multiplier: Mapped[float] = mapped_column(default=1.0)

    banned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ban_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ban_seconds: Mapped[int] = mapped_column(Integer, default=0)
    ban_reason: Mapped[str] = mapped_column(String(64), default="")
    total_bans: Mapped[int] = mapped_column(Integer, default=0)

    spamblock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats_spam_count: Mapped[int] = mapped_column(Integer, default=0)

    work_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rest_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[TelegramAccount] = relationship(back_populates="state")


class TargetGroup(Base):
    __tablename__ = "target_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    type: Mapped[str] = mapped_column(String(16), default="")
    members_count: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")

    discovered_via: Mapped[str] = mapped_column(String(16), default="seed", index=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    depth: Mapped[int] = mapped_column(Integer, default=0)

    scan_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    members_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scanned_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    last_members_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error: Mapped[str | None] = mapped_column(String(255))


class ParsedMessage(Base):
    __tablename__ = "parsed_messages"
    __table_args__ = (
        UniqueConstraint("group_tg_id", "message_id", name="uq_msg"),
        Index("ix_msg_group_msg", "group_tg_id", "message_id"),
        Index("ix_msg_date", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    sender_username: Mapped[str | None] = mapped_column(String(64))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    has_links: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_keywords: Mapped[list[str] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(16), default="history")  # history / realtime


class ParsedUser(Base):
    __tablename__ = "parsed_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(Text)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    seen_in_groups_count: Mapped[int] = mapped_column(Integer, default=0)
    seen_in_messages_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (
        UniqueConstraint("group_tg_id", "user_tg_id", name="uq_membership"),
        Index("ix_membership_group", "group_tg_id"),
        Index("ix_membership_user", "user_tg_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_tg_id: Mapped[int] = mapped_column(BigInteger)
    user_tg_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(16), default="member")
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DiscoveredLink(Base):
    __tablename__ = "discovered_links"
    __table_args__ = (UniqueConstraint("target_username", name="uq_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_group_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    target_username: Mapped[str] = mapped_column(String(64), index=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    depth: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(255))


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    last_searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParserTask(Base):
    __tablename__ = "parser_tasks"
    __table_args__ = (
        Index("ix_task_status_prio", "status", "priority"),
        Index("ix_task_type", "task_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(32))
    # discover / scan_messages / scan_members / monitor / resolve_link
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending / running / done / error
    target: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    assigned_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme: Mapped[str] = mapped_column(String(16), default="http")
    host: Mapped[str] = mapped_column(String(128), index=True)
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(128))
    password: Mapped[str | None] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fails_count: Mapped[int] = mapped_column(Integer, default=0)
    assigned_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("host", "port", "username", name="uq_proxy_hostportuser"),
    )
