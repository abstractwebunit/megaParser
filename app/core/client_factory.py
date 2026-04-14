"""Factory for building Kurigram Clients from DB accounts."""
from pathlib import Path
from typing import Any

from pyrogram import Client

from app.crypto import get_crypto
from app.db.models import TelegramAccount


def _proxy_to_pyrogram(proxy: dict[str, Any] | None) -> dict[str, Any] | None:
    if not proxy:
        return None
    return {
        "scheme": proxy.get("scheme", "http"),
        "hostname": proxy.get("hostname") or proxy.get("host"),
        "port": int(proxy.get("port")),
        "username": proxy.get("username"),
        "password": proxy.get("password"),
    }


def build_client(
    account: TelegramAccount,
    *,
    workdir: Path,
    no_updates: bool = True,
) -> Client:
    crypto = get_crypto()
    session_string = crypto.decrypt(account.session_string_encrypted)
    password = crypto.decrypt(account.password_encrypted) if account.password_encrypted else None

    kwargs: dict[str, Any] = {
        "name": account.name,
        "api_id": int(account.api_id),
        "api_hash": account.api_hash,
        "no_updates": no_updates,
        "sleep_threshold": 0,
        "workdir": str(workdir),
    }
    if session_string:
        kwargs["session_string"] = session_string
        kwargs["in_memory"] = True
    else:
        kwargs["in_memory"] = False

    if password:
        kwargs["password"] = password

    if account.device_model:
        kwargs["device_model"] = account.device_model
    if account.system_version:
        kwargs["system_version"] = account.system_version
    if account.app_version:
        kwargs["app_version"] = account.app_version
    if account.lang_code:
        kwargs["lang_code"] = account.lang_code
    if account.system_lang_code:
        kwargs["system_lang_code"] = account.system_lang_code

    pyro_proxy = _proxy_to_pyrogram(account.proxy)
    if pyro_proxy:
        kwargs["proxy"] = pyro_proxy

    return Client(**kwargs)
