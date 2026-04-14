"""Import Telegram accounts from a ZIP archive or directory containing .session + .json pairs.

Expected JSON fields (seller convention):
    app_id, app_hash, sdk, device, app_version, lang_pack, system_lang_pack,
    twoFA, phone, first_name, last_name, username, is_premium,
    spamblock, spamblock_end_date, stats_spam_count, proxy, session_file
"""
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import null as sa_null
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.session_loader import extract_session_string
from app.crypto import get_crypto
from app.db import repo


@dataclass
class ImportResult:
    total: int
    imported: int
    skipped_spamblock: int
    errors: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_spamblock_end(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if isinstance(raw, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return None


def _resolve_source(path: Path) -> tuple[Path, bool]:
    """Return (directory, is_temp). If given a zip, unpack to tmp dir."""
    if path.is_dir():
        return path, False
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="megaparser_import_"))
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(tmp)
        logger.info("extracted {} to {}", path, tmp)
        return tmp, True
    raise ValueError(f"Not a .zip or directory: {path}")


def _find_pairs(root: Path) -> list[tuple[Path, Path]]:
    """Walk recursively, pair each .json with its .session by basename or session_file field."""
    pairs: list[tuple[Path, Path]] = []
    json_files = list(root.rglob("*.json"))
    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("bad json {}: {}", jf, e)
            continue
        session_name = data.get("session_file") or jf.stem
        candidates = [
            jf.with_name(f"{session_name}.session"),
            jf.with_name(f"{jf.stem}.session"),
            root / f"{session_name}.session",
        ]
        sf = next((p for p in candidates if p.exists()), None)
        if sf is None:
            logger.warning("no .session found for {}", jf.name)
            continue
        pairs.append((sf, jf))
    return pairs


def _build_account_row(data: dict[str, Any]) -> dict[str, Any]:
    crypto = get_crypto()
    two_fa = data.get("twoFA")
    pwd_enc = crypto.encrypt(two_fa) if two_fa else None

    name = data.get("phone") or data.get("session_file") or f"acc_{data.get('id')}"
    proxy_json = None
    proxy_raw = data.get("proxy")
    if isinstance(proxy_raw, dict):
        proxy_json = proxy_raw
    elif isinstance(proxy_raw, str) and proxy_raw:
        # best-effort: user:pass@host:port
        try:
            from app.cli.proxy_pool import parse_proxy_line

            p = parse_proxy_line(proxy_raw)
            if p:
                proxy_json = {
                    "scheme": p["scheme"],
                    "hostname": p["host"],
                    "port": p["port"],
                    "username": p.get("username"),
                    "password": p.get("password"),
                }
        except Exception:
            proxy_json = None

    return {
        "name": str(name),
        "phone": data.get("phone"),
        "api_id": int(data.get("app_id") or data.get("api_id") or 0),
        "api_hash": str(data.get("app_hash") or data.get("api_hash") or ""),
        "password_encrypted": pwd_enc,
        "proxy": proxy_json if proxy_json is not None else sa_null(),
        "device_model": data.get("device"),
        "system_version": data.get("sdk"),
        "app_version": data.get("app_version"),
        "lang_code": data.get("lang_pack"),
        "system_lang_code": data.get("system_lang_pack"),
        "first_name": data.get("first_name"),
        "last_name": data.get("last_name"),
        "username": data.get("username"),
        "is_premium": bool(data.get("is_premium", False)),
        "enabled": True,
        "warmup_done": False,
        "can_search": False,
        "role": "scanner",
        "imported_at": _utcnow(),
    }


async def import_accounts(
    sf: async_sessionmaker[AsyncSession],
    source: Path,
    sessions_backup_dir: Path,
) -> ImportResult:
    crypto = get_crypto()
    root, is_temp = _resolve_source(source)
    sessions_backup_dir.mkdir(parents=True, exist_ok=True)

    pairs = _find_pairs(root)
    logger.info("found {} account pairs in {}", len(pairs), root)

    total = len(pairs)
    imported = 0
    skipped_spamblock = 0
    errors = 0

    api_id_count: dict[int, int] = {}

    for session_path, json_path in pairs:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            row = _build_account_row(data)
            if row["api_id"] == 0 or not row["api_hash"]:
                logger.warning("skip {} — missing api_id/api_hash", session_path.name)
                errors += 1
                continue

            api_id_count[row["api_id"]] = api_id_count.get(row["api_id"], 0) + 1

            # extract session string from .session (Telethon needs user_id from JSON)
            json_user_id = data.get("id") or data.get("user_id")
            session_str = extract_session_string(
                session_path, row["api_id"], user_id=json_user_id
            )
            row["session_string_encrypted"] = crypto.encrypt(session_str)

            # backup original .session
            backup = sessions_backup_dir / session_path.name
            if session_path.resolve() != backup.resolve():
                shutil.copy2(session_path, backup)
            row["session_file_path"] = str(backup)

            # spamblock health check
            sb_end = _parse_spamblock_end(data.get("spamblock_end_date"))
            spam_count = int(data.get("stats_spam_count") or 0)
            if (sb_end and sb_end > _utcnow()) or spam_count > 5:
                row["enabled"] = False
                skipped_spamblock += 1

            account_id = await repo.upsert_account(sf, row)
            await repo.ensure_account_state(sf, account_id)
            if row["enabled"] is False:
                await repo.update_account_state(
                    sf,
                    account_id,
                    {
                        "status": "disconnected",
                        "ban_reason": "imported_spamblock",
                        "spamblock_until": sb_end,
                        "stats_spam_count": spam_count,
                    },
                )
            logger.info("imported {} (api_id={})", row["name"], row["api_id"])
            imported += 1
        except Exception as e:
            logger.exception("import error for {}: {}", session_path.name, e)
            errors += 1

    shared = {aid: cnt for aid, cnt in api_id_count.items() if cnt > 1}
    if shared:
        logger.warning("accounts sharing same api_id: {} — correlation risk!", shared)

    if is_temp:
        shutil.rmtree(root, ignore_errors=True)

    return ImportResult(
        total=total,
        imported=imported,
        skipped_spamblock=skipped_spamblock,
        errors=errors,
    )
