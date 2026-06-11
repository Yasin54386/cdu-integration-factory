"""SFTP destination delivery checks, used by the test stage (spec §10, M6)."""

from __future__ import annotations

import io
import stat
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def sftp_client(sftp: dict):
    """Open an SFTP session from a resolved connection dict (key auth).

    The dict contains live credentials — never log it.
    """
    import paramiko

    key = paramiko.Ed25519Key.from_private_key(io.StringIO(sftp["key"]))
    transport = paramiko.Transport((sftp["host"], int(sftp.get("port", 22))))
    try:
        transport.connect(username=sftp["user"], pkey=key)
        client = paramiko.SFTPClient.from_transport(transport)
        try:
            yield client
        finally:
            client.close()
    finally:
        transport.close()


def wait_for_file(sftp: dict, remote_dir: str, name_prefix: str,
                  timeout_s: int = 300, poll_s: int = 10) -> str:
    """Poll the destination until a file matching the prefix appears."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with sftp_client(sftp) as client:
            for entry in client.listdir_attr(remote_dir):
                if entry.filename.startswith(name_prefix) and stat.S_ISREG(entry.st_mode):
                    return f"{remote_dir.rstrip('/')}/{entry.filename}"
        time.sleep(poll_s)
    raise TimeoutError(
        f"no file starting with '{name_prefix}' appeared in {remote_dir} "
        f"within {timeout_s}s"
    )


def fetch_file(sftp: dict, remote_path: str, local_path: Path) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with sftp_client(sftp) as client:
        client.get(remote_path, str(local_path))
    return local_path
