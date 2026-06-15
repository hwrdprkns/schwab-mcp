from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from schwab_mcp.approvals import NoOpApprovalManager
from schwab_mcp.config import PersonalConfig
from schwab_mcp.context import SchwabContext, SchwabServerContext
from schwab_mcp.tools import _accounts


def run(coro):
    return asyncio.run(coro)


def make_ctx(config: PersonalConfig, numbers: list[dict] | None) -> SchwabContext:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def get_account_numbers(self):
            self.calls += 1
            return _Resp(numbers if numbers is not None else [])

    server = SchwabServerContext(
        client=cast(Any, Client()),
        approval_manager=NoOpApprovalManager(),
        config=config,
    )
    rc = SimpleNamespace(lifespan_context=server)
    return SchwabContext.model_construct(_request_context=cast(Any, rc), _fastmcp=None)


class _Resp:
    def __init__(self, data) -> None:
        self._data = data
        self.status_code = 200
        self.url = "https://api.schwabapi.com/accounts/accountNumbers"
        self.text = ""
        self.content = b"[]" if not data else b"[{}]"

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._data


_NUMS = [
    {"accountNumber": "111111111", "hashValue": "HASHAAAAAAAAAAAAAAAAAAAA"},
    {"accountNumber": "222222222", "hashValue": "HASHBBBBBBBBBBBBBBBBBBBB"},
]


def test_resolves_nickname_to_hash():
    cfg = PersonalConfig(default_account="MAIN", accounts={"MAIN": "111111111"})
    ctx = make_ctx(cfg, _NUMS)
    assert run(_accounts.resolve_account_hash(ctx, "MAIN")) == "HASHAAAAAAAAAAAAAAAAAAAA"


def test_resolves_account_number_to_hash():
    ctx = make_ctx(PersonalConfig(), _NUMS)
    assert run(_accounts.resolve_account_hash(ctx, "222222222")) == "HASHBBBBBBBBBBBBBBBBBBBB"


def test_default_account_used_when_omitted():
    cfg = PersonalConfig(default_account="222222222")
    ctx = make_ctx(cfg, _NUMS)
    assert run(_accounts.resolve_account_hash(ctx, None)) == "HASHBBBBBBBBBBBBBBBBBBBB"


def test_raw_hash_short_circuits_without_fetch():
    # No account numbers available; a real-looking hash must pass through with
    # zero get_account_numbers calls.
    ctx = make_ctx(PersonalConfig(), None)
    client = ctx.schwab.client
    out = run(_accounts.resolve_account_hash(ctx, "HASHCCCCCCCCCCCCCCCCCCCC"))
    assert out == "HASHCCCCCCCCCCCCCCCCCCCC"
    assert client.calls == 0  # type: ignore[attr-defined]


def test_single_account_is_implicit_default():
    ctx = make_ctx(PersonalConfig(), [_NUMS[0]])
    assert run(_accounts.resolve_account_hash(ctx, None)) == "HASHAAAAAAAAAAAAAAAAAAAA"


def test_ambiguous_without_default_raises():
    ctx = make_ctx(PersonalConfig(), _NUMS)
    with pytest.raises(ValueError, match="No account specified"):
        run(_accounts.resolve_account_hash(ctx, None))


def test_caches_account_numbers():
    ctx = make_ctx(PersonalConfig(accounts={"A": "111111111"}), _NUMS)
    client = ctx.schwab.client
    run(_accounts.resolve_account_hash(ctx, "A"))
    run(_accounts.resolve_account_hash(ctx, "222222222"))
    assert client.calls == 1  # type: ignore[attr-defined]  # fetched once, then cached
