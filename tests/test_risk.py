from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from schwab_mcp import risk
from schwab_mcp.approvals import NoOpApprovalManager
from schwab_mcp.config import PersonalConfig, RiskPolicy
from schwab_mcp.context import SchwabContext, SchwabServerContext


def run(coro):
    return asyncio.run(coro)


def make_ctx(policy: RiskPolicy) -> SchwabContext:
    # A client with the attributes risk.enforce references as call() arguments;
    # the actual call() is monkeypatched in the buying-power tests.
    client = SimpleNamespace(
        get_account=lambda *a, **k: None,
        get_quotes=lambda *a, **k: None,
    )
    server = SchwabServerContext(
        client=cast(Any, client),
        approval_manager=NoOpApprovalManager(),
        config=PersonalConfig(risk=policy),
    )
    rc = SimpleNamespace(lifespan_context=server)
    return SchwabContext.model_construct(_request_context=cast(Any, rc), _fastmcp=None)


def enforce(ctx, tool, args):
    return run(risk.enforce(ctx, tool, args))


def test_symbol_deny_blocks():
    ctx = make_ctx(RiskPolicy(symbol_deny=("GME",)))
    with pytest.raises(risk.RiskViolation, match="deny list"):
        enforce(ctx, "place_equity_order", {"symbol": "gme", "quantity": 1, "price": 10})


def test_symbol_allow_blocks_others():
    ctx = make_ctx(RiskPolicy(symbol_allow=("SPY",)))
    with pytest.raises(risk.RiskViolation, match="allow list"):
        enforce(ctx, "place_equity_order", {"symbol": "AAPL", "quantity": 1, "price": 10})
    # allowed symbol passes
    enforce(ctx, "place_equity_order", {"symbol": "SPY", "quantity": 1, "price": 10})


def test_max_quantity_blocks():
    ctx = make_ctx(RiskPolicy(max_quantity=100))
    with pytest.raises(risk.RiskViolation, match="max_quantity"):
        enforce(ctx, "place_equity_order", {"symbol": "SPY", "quantity": 101, "price": 1})


def test_max_notional_blocks_priced_order():
    ctx = make_ctx(RiskPolicy(max_order_notional=1000))
    with pytest.raises(risk.RiskViolation, match="max_order_notional"):
        enforce(ctx, "place_equity_order", {"symbol": "SPY", "quantity": 100, "price": 50})


def test_option_notional_uses_100x_multiplier():
    ctx = make_ctx(RiskPolicy(max_order_notional=1000))
    # 2 contracts * $6 * 100 = $1200 > $1000 -> blocked
    with pytest.raises(risk.RiskViolation, match="max_order_notional"):
        enforce(ctx, "place_option_order", {"symbol": "SPY__C", "quantity": 2, "price": 6})


def _spec(symbol, qty, price=None, stop=None):
    leg = {"instruction": "SELL", "instrument": {"symbol": symbol}, "quantity": qty}
    spec = {"orderType": "LIMIT", "orderLegCollection": [leg]}
    if price is not None:
        spec["price"] = price
    if stop is not None:
        spec["stopPrice"] = stop
    return spec


def test_oco_spec_symbol_deny_is_enforced():
    # OCO/trigger tools pass pre-built spec dicts (no top-level symbol/quantity);
    # the guardrail must still inspect the nested legs.
    ctx = make_ctx(RiskPolicy(symbol_deny=("GME",)))
    args = {
        "account_hash": "H",
        "first_order_spec": _spec("GME", 1, price=100),
        "second_order_spec": _spec("GME", 1, stop=50),
    }
    with pytest.raises(risk.RiskViolation, match="deny list"):
        enforce(ctx, "place_one_cancels_other_order", args)


def test_trigger_spec_quantity_is_enforced():
    ctx = make_ctx(RiskPolicy(max_quantity=100))
    args = {
        "account_hash": "H",
        "first_order_spec": _spec("SPY", 500, price=10),
        "second_order_spec": _spec("SPY", 500, stop=5),
    }
    with pytest.raises(risk.RiskViolation, match="max_quantity"):
        enforce(ctx, "place_first_triggers_second_order", args)


def test_oco_spec_notional_is_enforced():
    ctx = make_ctx(RiskPolicy(max_order_notional=1000))
    # 50 sh total * $40 (highest spec price) = $2000 > $1000
    args = {
        "account_hash": "H",
        "first_order_spec": _spec("SPY", 25, price=40),
        "second_order_spec": _spec("SPY", 25, stop=30),
    }
    with pytest.raises(risk.RiskViolation, match="max_order_notional"):
        enforce(ctx, "place_one_cancels_other_order", args)


def test_cancel_order_is_exempt():
    ctx = make_ctx(RiskPolicy(max_quantity=1, symbol_deny=("ANY",)))
    # No symbol/quantity, and exempt anyway -> no raise.
    enforce(ctx, "cancel_order", {"order_id": "1"})


def test_in_limit_order_passes():
    ctx = make_ctx(RiskPolicy(max_order_notional=10000, max_quantity=1000))
    enforce(ctx, "place_equity_order", {"symbol": "SPY", "quantity": 10, "price": 50})


def test_margin_call_blocks(monkeypatch):
    ctx = make_ctx(
        RiskPolicy(enforce_buying_power=True, block_on_margin_call=True)
    )
    monkeypatch.setattr(risk, "resolve_account_hash", _fake_resolve)
    monkeypatch.setattr(
        risk,
        "call",
        _fake_balances({"buyingPower": 100000, "maintenanceCall": 500}),
    )
    with pytest.raises(risk.RiskViolation, match="margin/Reg-T call"):
        enforce(
            ctx,
            "place_equity_order",
            {"symbol": "SPY", "quantity": 1, "price": 10, "account_hash": "H"},
        )


def test_buying_power_pct_blocks(monkeypatch):
    ctx = make_ctx(
        RiskPolicy(enforce_buying_power=True, max_buying_power_pct=50)
    )
    monkeypatch.setattr(risk, "resolve_account_hash", _fake_resolve)
    monkeypatch.setattr(
        risk, "call", _fake_balances({"buyingPower": 1000, "maintenanceCall": 0})
    )
    # notional 100*20 = 2000 > 50% of 1000 (=500) -> blocked
    with pytest.raises(risk.RiskViolation, match="buying power"):
        enforce(
            ctx,
            "place_equity_order",
            {"symbol": "SPY", "quantity": 100, "price": 20, "account_hash": "H"},
        )


def test_fail_closed_on_lookup_error(monkeypatch):
    ctx = make_ctx(RiskPolicy(enforce_buying_power=True, block_on_margin_call=True))
    monkeypatch.setattr(risk, "resolve_account_hash", _fake_resolve)

    async def boom(*a, **k):
        raise RuntimeError("schwab down")

    monkeypatch.setattr(risk, "call", boom)
    with pytest.raises(risk.RiskViolation, match="could not verify"):
        enforce(
            ctx,
            "place_equity_order",
            {"symbol": "SPY", "quantity": 1, "price": 10, "account_hash": "H"},
        )


def test_fail_open_on_error_allows(monkeypatch):
    ctx = make_ctx(
        RiskPolicy(
            enforce_buying_power=True,
            block_on_margin_call=True,
            fail_open_on_error=True,
        )
    )
    monkeypatch.setattr(risk, "resolve_account_hash", _fake_resolve)

    async def boom(*a, **k):
        raise RuntimeError("schwab down")

    monkeypatch.setattr(risk, "call", boom)
    # fail_open_on_error -> no raise despite lookup failure
    enforce(
        ctx,
        "place_equity_order",
        {"symbol": "SPY", "quantity": 1, "price": 10, "account_hash": "H"},
    )


async def _fake_resolve(ctx, account):  # noqa: ARG001
    return "HASH"


def _fake_balances(current: dict):
    async def fake_call(func, *args, **kwargs):  # noqa: ARG001
        return {"securitiesAccount": {"currentBalances": current}}

    return fake_call
