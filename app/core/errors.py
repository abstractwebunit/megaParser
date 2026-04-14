"""Classification helpers around Kurigram (Pyrogram) exceptions.

Kurigram raises pyrogram.errors.* — we group them into actionable categories
so the runner can decide: retry / mark group / ban account / drop.
"""
from enum import Enum

from pyrogram.errors import (
    AuthKeyUnregistered,
    ChannelInvalid,
    ChannelPrivate,
    ChatAdminRequired,
    ChatWriteForbidden,
    FloodWait,
    PeerFlood,
    PeerIdInvalid,
    SessionPasswordNeeded,
    SessionRevoked,
    UserBannedInChannel,
    UserDeactivated,
    UserDeactivatedBan,
    UsernameInvalid,
    UsernameNotOccupied,
)


class ErrorKind(Enum):
    FLOOD_SHORT = "flood_short"      # FloodWait < threshold → sleep
    FLOOD_LONG = "flood_long"        # FloodWait >= threshold → temp ban
    PEER_FLOOD = "peer_flood"        # account soft-restricted, 24h pause
    ACCOUNT_DEAD = "account_dead"    # AuthKeyUnregistered, Deactivated
    GROUP_PRIVATE = "group_private"  # can't read, mark group and move on
    GROUP_INVALID = "group_invalid"  # bad username, drop
    NEEDS_PASSWORD = "needs_password"
    UNKNOWN = "unknown"


def classify(exc: Exception, flood_long_threshold: int = 300) -> ErrorKind:
    if isinstance(exc, FloodWait):
        return ErrorKind.FLOOD_LONG if int(getattr(exc, "value", 0)) >= flood_long_threshold else ErrorKind.FLOOD_SHORT
    if isinstance(exc, PeerFlood):
        return ErrorKind.PEER_FLOOD
    if isinstance(exc, (AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan, SessionRevoked)):
        return ErrorKind.ACCOUNT_DEAD
    if isinstance(exc, (ChannelPrivate, ChatAdminRequired, UserBannedInChannel, ChatWriteForbidden)):
        return ErrorKind.GROUP_PRIVATE
    if isinstance(exc, (ChannelInvalid, PeerIdInvalid, UsernameInvalid, UsernameNotOccupied)):
        return ErrorKind.GROUP_INVALID
    if isinstance(exc, SessionPasswordNeeded):
        return ErrorKind.NEEDS_PASSWORD
    return ErrorKind.UNKNOWN


def flood_seconds(exc: FloodWait) -> int:
    return int(getattr(exc, "value", 0))
