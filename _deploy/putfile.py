"""Single-shot SFTP put using the shared paramiko client from remote.py (with retry)."""
import sys

from remote import client


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: putfile.py <local> <remote>")
        sys.exit(2)
    local, remote = sys.argv[1], sys.argv[2]
    c = client()
    sftp = c.open_sftp()
    sftp.put(local, remote)
    sftp.close()
    print(f"uploaded {local} -> {remote}")


if __name__ == "__main__":
    main()
