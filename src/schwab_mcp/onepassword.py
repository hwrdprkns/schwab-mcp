from __future__ import annotations

#
# Resolve Schwab app credentials from 1Password at runtime via the `op` CLI.
#
# The op:// references below are NOT secrets — they only name where to find the
# values — so they live in the repo. The actual client id/secret are read from
# the local 1Password vault at runtime and never persisted here. Each reference
# can be overridden with an environment variable for a different vault/item.
#

import os
import shutil
import subprocess


DEFAULT_CLIENT_ID_REF = "op://Private/SCHWAB_OAUTH_APP/client_id"
DEFAULT_CLIENT_SECRET_REF = "op://Private/SCHWAB_OAUTH_APP/client_secret"

CLIENT_ID_REF_ENV = "SCHWAB_OP_CLIENT_ID_REF"
CLIENT_SECRET_REF_ENV = "SCHWAB_OP_CLIENT_SECRET_REF"


def client_id_reference() -> str:
    return os.environ.get(CLIENT_ID_REF_ENV, DEFAULT_CLIENT_ID_REF)


def client_secret_reference() -> str:
    return os.environ.get(CLIENT_SECRET_REF_ENV, DEFAULT_CLIENT_SECRET_REF)


def op_read(reference: str | None, *, timeout: float = 10.0) -> str | None:
    """Resolve a 1Password secret reference (``op://...``) via the ``op`` CLI.

    Returns the secret value, or ``None`` if the CLI is missing, the user is not
    signed in, or the reference cannot be resolved. Never raises — credential
    resolution is best-effort and callers fall back to a clear error.
    """
    if not reference:
        return None

    op = shutil.which("op")
    if op is None:
        return None

    try:
        result = subprocess.run(
            [op, "read", "--no-newline", reference],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    value = result.stdout.strip()
    return value or None


def resolve_credentials(
    client_id: str | None,
    client_secret: str | None,
) -> tuple[str | None, str | None]:
    """Fill in any missing client id/secret from 1Password.

    Only resolves values that are still missing, so explicit flags, env vars, or
    a credentials file always take precedence.
    """
    if not client_id:
        client_id = op_read(client_id_reference())
    if not client_secret:
        client_secret = op_read(client_secret_reference())
    return client_id, client_secret


__all__ = [
    "DEFAULT_CLIENT_ID_REF",
    "DEFAULT_CLIENT_SECRET_REF",
    "client_id_reference",
    "client_secret_reference",
    "op_read",
    "resolve_credentials",
]
