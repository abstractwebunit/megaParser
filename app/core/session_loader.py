"""Detect .session file format (Telethon vs Pyrogram v2) and extract session_string.

Reads auth_key once, returns a Kurigram-compatible session_string. The original
.session file is never mutated.
"""
import sqlite3
import struct
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Literal

from loguru import logger

from pyrogram.storage.storage import Storage

SessionKind = Literal["pyrogram", "telethon", "unknown"]

PYROGRAM_TABLES = {"sessions", "peers", "version"}
TELETHON_TABLES = {"sessions", "entities", "sent_files", "update_state"}


def detect_kind(session_path: Path) -> SessionKind:
    if not session_path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")
    try:
        con = sqlite3.connect(session_path.as_posix())
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        con.close()
    except sqlite3.DatabaseError:
        return "unknown"

    if TELETHON_TABLES.issubset(tables):
        return "telethon"
    if PYROGRAM_TABLES.issubset(tables):
        return "pyrogram"
    if "sessions" in tables and "entities" in tables:
        return "telethon"
    if "sessions" in tables and "peers" in tables:
        return "pyrogram"
    return "unknown"


def _pyrogram_v2_session_string(
    dc_id: int, api_id: int, test_mode: bool, auth_key: bytes, user_id: int, is_bot: bool
) -> str:
    """Build a Kurigram/Pyrogram v2 session_string.

    Format (Storage.SESSION_STRING_FORMAT = '>BI?256sQ?'):
      B   dc_id (uint8)
      I   api_id (uint32)
      ?   test_mode
      256s auth_key
      Q   user_id (uint64)
      ?   is_bot
    """
    packed = struct.pack(
        Storage.SESSION_STRING_FORMAT,
        dc_id,
        api_id,
        test_mode,
        auth_key,
        user_id,
        is_bot,
    )
    return urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def extract_from_pyrogram(session_path: Path, api_id: int) -> str:
    con = sqlite3.connect(session_path.as_posix())
    cur = con.cursor()
    cur.execute("SELECT dc_id, api_id, test_mode, auth_key, date, user_id, is_bot FROM sessions")
    row = cur.fetchone()
    con.close()
    if not row:
        raise ValueError(f"Empty pyrogram sessions table: {session_path}")
    dc_id, stored_api_id, test_mode, auth_key, _date, user_id, is_bot = row
    use_api_id = int(stored_api_id or api_id)
    return _pyrogram_v2_session_string(
        int(dc_id), int(use_api_id), bool(test_mode), bytes(auth_key), int(user_id or 0), bool(is_bot)
    )


def extract_from_telethon(session_path: Path, api_id: int, user_id: int) -> str:
    con = sqlite3.connect(session_path.as_posix())
    cur = con.cursor()
    cur.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
    row = cur.fetchone()
    con.close()
    if not row:
        raise ValueError(f"Empty telethon sessions table: {session_path}")
    dc_id, _server, _port, auth_key = row
    # Telethon .session files don't store user_id — caller must pass it from JSON metadata.
    return _pyrogram_v2_session_string(
        int(dc_id), int(api_id), False, bytes(auth_key), int(user_id or 0), False
    )


def extract_session_string(session_path: Path, api_id: int, user_id: int | None = None) -> str:
    kind = detect_kind(session_path)
    logger.debug("session format for {}: {}", session_path.name, kind)
    if kind == "pyrogram":
        return extract_from_pyrogram(session_path, api_id)
    if kind == "telethon":
        if not user_id:
            logger.warning(
                "telethon session {} has no user_id from JSON — kurigram will prompt for phone",
                session_path.name,
            )
        return extract_from_telethon(session_path, api_id, int(user_id or 0))
    raise ValueError(f"Unknown/corrupted session format: {session_path}")
