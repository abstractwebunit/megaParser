"""Recursive sftp upload — walks a local dir, mirrors to remote."""
import os
import stat
import sys
from pathlib import Path

import paramiko

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "logs",
    "dumps",
    "sessions",
    "tmp",
    "_deploy",
    "node_modules",
    "build",
    "dist",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".zip", ".session-journal"}


def walk(local: Path):
    for root, dirs, files in os.walk(local):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git")]
        rootp = Path(root)
        for d in dirs:
            yield rootp / d, True
        for f in files:
            if Path(f).suffix in SKIP_SUFFIX:
                continue
            if f in (".env", ".env.local"):
                continue
            yield rootp / f, False


_dir_cache: set[str] = set()


def ensure_remote_dir(sftp: paramiko.SFTPClient, path: str) -> None:
    path = path.rstrip("/")
    if not path or path in _dir_cache:
        return
    parts = path.split("/")
    accum = ""
    for p in parts:
        if not p:
            accum = "/"
            continue
        accum = (accum.rstrip("/") + "/" + p) if accum else p
        if accum in _dir_cache:
            continue
        try:
            sftp.stat(accum)
        except FileNotFoundError:
            try:
                sftp.mkdir(accum)
            except OSError:
                pass
        _dir_cache.add(accum)


def main() -> None:
    host = os.environ["SSH_HOST"]
    user = os.environ["SSH_USER"]
    pwd = os.environ["SSH_PASSWORD"]
    port = int(os.environ.get("SSH_PORT", "22"))
    local_root = Path(sys.argv[1]).resolve()
    remote_root = sys.argv[2].rstrip("/")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        port=port,
        username=user,
        password=pwd,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )
    try:
        sftp = c.open_sftp()
        ensure_remote_dir(sftp, remote_root)
        uploaded = 0
        bytes_total = 0
        for p, is_dir in walk(local_root):
            rel = p.relative_to(local_root).as_posix()
            remote = f"{remote_root}/{rel}"
            if is_dir:
                ensure_remote_dir(sftp, remote)
                continue
            parent = os.path.dirname(remote)
            if parent:
                ensure_remote_dir(sftp, parent)
            sftp.put(str(p), remote)
            size = p.stat().st_size
            uploaded += 1
            bytes_total += size
            print(f"  {rel} ({size}B)", flush=True)
        sftp.close()
        print(f"--- uploaded {uploaded} files, {bytes_total} bytes total", flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    main()
