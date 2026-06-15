from __future__ import annotations

#
# Resolve a user-facing account identifier (nickname, account number, raw hash,
# or "use my default") into the account hash Schwab APIs require, caching the
# account-number -> hash mapping on the server context.
#

from typing import TYPE_CHECKING

from schwab_mcp.tools.utils import call

if TYPE_CHECKING:
    from schwab_mcp.context import SchwabContext


# Schwab account hashes are long hex strings; account numbers are short digits.
_HASH_MIN_LEN = 20


async def _account_hash_map(ctx: "SchwabContext") -> dict[str, str]:
    """Return (and lazily cache) the account number -> hash mapping."""
    server = ctx.schwab
    if server.account_hashes is None:
        raw = await call(ctx.accounts.get_account_numbers)
        mapping: dict[str, str] = {}
        items = raw if isinstance(raw, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            number = item.get("accountNumber")
            hash_value = item.get("hashValue")
            if number and hash_value:
                mapping[str(number)] = str(hash_value)
        server.account_hashes = mapping
    return server.account_hashes


async def resolve_account_hash(
    ctx: "SchwabContext", account: str | None
) -> str:
    """Resolve ``account`` to a Schwab account hash.

    Accepts a nickname (from config), an account number, a raw hash, or ``None``
    (use the configured default, or the only account if there is just one).
    """
    config = ctx.schwab.config
    requested = account if account else config.default_account

    # Translate a configured nickname to its account number.
    if requested is not None and requested in config.accounts:
        requested = config.accounts[requested]

    # Short-circuit: a value that already looks like a hash needs no lookup.
    # Keeps the common direct-hash path (and existing tests) free of an extra
    # get_account_numbers call.
    if (
        requested is not None
        and not requested.isdigit()
        and len(requested) >= _HASH_MIN_LEN
    ):
        return requested

    mapping = await _account_hash_map(ctx)

    if requested is None:
        if len(mapping) == 1:
            return next(iter(mapping.values()))
        raise ValueError(
            "No account specified and no default configured. Pass an account "
            "(nickname, number, or hash) or set 'default_account' in config.yaml. "
            f"Known accounts: {sorted(mapping)}."
        )

    # Already a known hash.
    if requested in mapping.values():
        return requested
    # An account number we know.
    if requested in mapping:
        return mapping[requested]

    raise ValueError(
        f"Could not resolve account {requested!r}. Known nicknames: "
        f"{sorted(config.accounts)}; known accounts: {sorted(mapping)}."
    )


__all__ = ["resolve_account_hash"]
