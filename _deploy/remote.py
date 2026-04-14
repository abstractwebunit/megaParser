"""SSH helper — reads creds from env vars, runs commands on remote.

Usage:
    set SSH_HOST=2.26.91.59
    set SSH_USER=root
    set SSH_PASSWORD=***
    python _deploy/remote.py "uname -a"
"""
import os
import sys
import time
from pathlib import Path

import paramiko

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


_shared_client: paramiko.SSHClient | None = None


def _connect() -> paramiko.SSHClient:
    host = os.environ["SSH_HOST"]
    user = os.environ["SSH_USER"]
    pwd = os.environ["SSH_PASSWORD"]
    port = int(os.environ.get("SSH_PORT", "22"))
    last_err: Exception | None = None
    for attempt in range(5):
        try:
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
                banner_timeout=30,
                auth_timeout=30,
            )
            transport = c.get_transport()
            if transport is not None:
                transport.set_keepalive(15)
            return c
        except Exception as e:
            last_err = e
            delay = 2 + attempt * 3
            print(
                f"ssh connect attempt {attempt + 1} failed: {type(e).__name__}: {e}; retry in {delay}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
    raise SystemExit(f"ssh connect failed after 5 retries: {last_err}")


def client() -> paramiko.SSHClient:
    global _shared_client
    if _shared_client is None or _shared_client.get_transport() is None or not _shared_client.get_transport().is_active():
        _shared_client = _connect()
    return _shared_client


def run(cmd: str, *, ignore_err: bool = False, show: bool = True) -> tuple[int, str, str]:
    c = client()
    if show:
        print(f"$ {cmd}", flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=False, timeout=600)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if show and out:
        print(out, end="" if out.endswith("\n") else "\n", flush=True)
    if show and err:
        print(err, end="" if err.endswith("\n") else "\n", flush=True, file=sys.stderr)
    if code != 0 and not ignore_err:
        raise SystemExit(f"remote command failed (exit {code}): {cmd}")
    return code, out, err


def put(local: Path, remote: str) -> None:
    c = client()
    try:
        sftp = c.open_sftp()
        sftp.put(str(local), remote)
        sftp.close()
        print(f"uploaded {local} -> {remote}", flush=True)
    finally:
        c.close()


def put_bytes(data: bytes, remote: str) -> None:
    c = client()
    try:
        sftp = c.open_sftp()
        with sftp.open(remote, "wb") as f:
            f.write(data)
        sftp.close()
        print(f"wrote {len(data)} bytes -> {remote}", flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: remote.py <command>")
        sys.exit(2)
    run(sys.argv[1])
