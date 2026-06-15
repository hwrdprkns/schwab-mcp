from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from schwab_mcp import risk
from schwab_mcp.approvals import NoOpApprovalManager
from schwab_mcp.config import PersonalConfig, RiskPolicy
from schwab_mcp.context import SchwabContext, SchwabServerContext
from schwab_mcp.tools import _registration


def run(coro):
    return asyncio.run(coro)


def make_ctx(policy: RiskPolicy) -> SchwabContext:
    server = SchwabServerContext(
        client=cast(Any, object()),
        approval_manager=NoOpApprovalManager(),
        config=PersonalConfig(risk=policy),
    )
    rc = SimpleNamespace(lifespan_context=server)
    return SchwabContext.model_construct(_request_context=cast(Any, rc), _fastmcp=None)


_ran: dict[str, bool] = {}


async def place_equity_order(
    ctx: SchwabContext, symbol: str, quantity: int, price: float | None = None
) -> str:
    _ran["yes"] = True
    return "placed"


def wrapped():
    return _registration._wrap_with_risk(place_equity_order)


def test_blocks_before_running_inner():
    _ran.clear()
    ctx = make_ctx(RiskPolicy(symbol_deny=("GME",)))
    tool = wrapped()

    with pytest.raises(risk.RiskViolation):
        run(tool(ctx, "GME", 1, 10.0))

    assert "yes" not in _ran  # inner tool never executed


def test_risk_violation_is_permission_error():
    ctx = make_ctx(RiskPolicy(max_quantity=1))
    tool = wrapped()
    with pytest.raises(PermissionError):
        run(tool(ctx, "SPY", 5, 10.0))


def test_passes_through_within_limits():
    _ran.clear()
    ctx = make_ctx(RiskPolicy(max_order_notional=100000, max_quantity=1000))
    tool = wrapped()

    result = run(tool(ctx, "SPY", 10, 50.0))

    assert result == "placed"
    assert _ran.get("yes") is True
