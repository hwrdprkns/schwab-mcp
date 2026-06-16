from __future__ import annotations

#
# Server-side risk guardrails for write/trade tools. Enforced BEFORE Discord
# approval (and independently of it, so they also cap --jesus-take-the-wheel).
#
# Static checks (symbol allow/deny, max quantity, max notional) need no network.
# The buying-power/margin checks fetch live balances; they fail closed unless
# risk.fail_open_on_error is set.
#

import logging
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from schwab_mcp.tools._accounts import resolve_account_hash
from schwab_mcp.tools.utils import call

if TYPE_CHECKING:
    from schwab_mcp.context import SchwabContext


logger = logging.getLogger(__name__)


class RiskViolation(PermissionError):
    """Raised when an order is blocked by the configured risk policy."""


# Risk-reducing or spec-only tools we don't gate on size/notional.
_EXEMPT_TOOLS = {"cancel_order"}
# Option tools carry a 100x contract multiplier for notional.
_OPTION_TOOLS = {"place_option_order", "place_option_combo_order"}


# Args that carry pre-built order specs (OCO / trigger tools).
_SPEC_KEYS = ("first_order_spec", "second_order_spec", "order_spec")


def _leg_symbol(leg: Any) -> str | None:
    if not isinstance(leg, dict):
        return None
    symbol = leg.get("symbol")
    if symbol:
        return str(symbol)
    instrument = leg.get("instrument")
    if isinstance(instrument, dict) and instrument.get("symbol"):
        return str(instrument["symbol"])
    return None


def _leg_quantity(leg: Any) -> float | None:
    if isinstance(leg, dict):
        q = leg.get("quantity")
        if isinstance(q, (int, float)):
            return abs(float(q))
    return None


def _spec_legs(spec: Any) -> list[Any]:
    """Flatten orderLegCollection from a built order-spec dict (recursing children)."""
    legs: list[Any] = []
    if not isinstance(spec, dict):
        return legs
    for leg in spec.get("orderLegCollection") or []:
        legs.append(leg)
    for child in spec.get("childOrderStrategies") or []:
        legs.extend(_spec_legs(child))
    return legs


def _all_legs(arguments: Mapping[str, Any]) -> list[Any]:
    """Order legs from a flat ``legs`` arg AND any pre-built order-spec dicts."""
    legs: list[Any] = []
    raw = arguments.get("legs")
    if isinstance(raw, list):
        legs.extend(leg for leg in raw if isinstance(leg, dict))
    for key in _SPEC_KEYS:
        legs.extend(_spec_legs(arguments.get(key)))
    return legs


def _symbols(arguments: Mapping[str, Any]) -> list[str]:
    symbols = [s for s in (_leg_symbol(leg) for leg in _all_legs(arguments)) if s]
    if symbols:
        return symbols
    symbol = arguments.get("symbol")
    return [str(symbol)] if symbol else []


def _primary_symbol(arguments: Mapping[str, Any]) -> str | None:
    symbols = _symbols(arguments)
    return symbols[0] if symbols else None


def _total_quantity(arguments: Mapping[str, Any]) -> float | None:
    quantities = [
        q for q in (_leg_quantity(leg) for leg in _all_legs(arguments)) if q is not None
    ]
    if quantities:
        return sum(quantities)
    q = arguments.get("quantity")
    return abs(float(q)) if isinstance(q, (int, float)) else None


def _spec_prices(spec: Any) -> list[float]:
    prices: list[float] = []
    if isinstance(spec, dict):
        for key in ("price", "stopPrice"):
            v = spec.get(key)
            if isinstance(v, (int, float)) and v > 0:
                prices.append(float(v))
        for child in spec.get("childOrderStrategies") or []:
            prices.extend(_spec_prices(child))
    return prices


def _explicit_price(arguments: Mapping[str, Any]) -> float | None:
    for key in ("price", "entry_price", "stop_price", "loss_price", "profit_price"):
        v = arguments.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    # Pre-built specs carry their price nested; use the highest (conservative).
    prices: list[float] = []
    for key in _SPEC_KEYS:
        prices.extend(_spec_prices(arguments.get(key)))
    return max(prices) if prices else None


async def _mark_price(ctx: "SchwabContext", symbol: str) -> float | None:
    try:
        raw = await call(ctx.quotes.get_quotes, [symbol])
    except Exception:  # noqa: BLE001 - best-effort mark; caller handles None
        return None
    if not isinstance(raw, dict):
        return None
    entry = raw.get(symbol)
    if entry is None:
        entry = next(iter(raw.values()), None)
    if not isinstance(entry, dict):
        return None
    quote = entry.get("quote", entry)
    if not isinstance(quote, dict):
        return None
    for key in ("mark", "lastPrice", "askPrice", "closePrice"):
        v = quote.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


async def _estimate_notional(
    ctx: "SchwabContext", tool_name: str, arguments: Mapping[str, Any]
) -> float | None:
    qty = _total_quantity(arguments)
    if not qty:
        return None
    multiplier = 100 if tool_name in _OPTION_TOOLS else 1
    price = _explicit_price(arguments)
    if price is None:
        symbol = _primary_symbol(arguments)
        if symbol:
            price = await _mark_price(ctx, symbol)
    if price is None:
        return None
    return abs(price) * abs(qty) * multiplier


def _current_balances(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    account = raw.get("securitiesAccount", raw)
    if not isinstance(account, dict):
        return {}
    balances = account.get("currentBalances")
    return balances if isinstance(balances, dict) else {}


def _check_symbols(policy: Any, symbols: Sequence[str]) -> None:
    for symbol in symbols:
        upper = symbol.upper()
        if policy.symbol_deny and upper in policy.symbol_deny:
            raise RiskViolation(f"Blocked by risk policy: {upper} is on the deny list.")
        if policy.symbol_allow and upper not in policy.symbol_allow:
            raise RiskViolation(
                f"Blocked by risk policy: {upper} is not on the allow list."
            )


def _check_quantity(policy: Any, quantity: float | None) -> None:
    if (
        policy.max_quantity is not None
        and quantity is not None
        and abs(quantity) > policy.max_quantity
    ):
        raise RiskViolation(
            f"Blocked by risk policy: quantity {quantity:g} exceeds max_quantity "
            f"{policy.max_quantity}."
        )


def _check_notional(policy: Any, notional: float | None) -> None:
    if (
        policy.max_order_notional is not None
        and notional is not None
        and notional > policy.max_order_notional
    ):
        raise RiskViolation(
            f"Blocked by risk policy: order notional ${notional:,.0f} exceeds "
            f"max_order_notional ${policy.max_order_notional:,.0f}."
        )


async def _check_buying_power(
    ctx: "SchwabContext",
    policy: Any,
    arguments: Mapping[str, Any],
    notional: float | None,
) -> None:
    try:
        account_hash = await resolve_account_hash(ctx, arguments.get("account_hash"))
        raw = await call(ctx.accounts.get_account, account_hash)
    except Exception as exc:  # noqa: BLE001 - decided by fail_open_on_error
        if policy.fail_open_on_error:
            logger.warning("risk: could not verify buying power/margin: %s", exc)
            return
        raise RiskViolation(
            "Blocked by risk policy: could not verify buying power/margin "
            f"({exc}). Set risk.fail_open_on_error to allow on lookup failure."
        ) from exc

    balances = _current_balances(raw)

    if policy.block_on_margin_call:
        maintenance = balances.get("maintenanceCall") or 0
        reg_t = balances.get("regTCall") or 0
        if maintenance > 0 or reg_t > 0:
            raise RiskViolation(
                "Blocked by risk policy: account has an open margin/Reg-T call."
            )

    if policy.max_buying_power_pct is not None and notional is not None:
        buying_power = balances.get("buyingPower")
        if isinstance(buying_power, (int, float)) and buying_power > 0:
            limit = buying_power * policy.max_buying_power_pct / 100.0
            if notional > limit:
                raise RiskViolation(
                    f"Blocked by risk policy: order notional ${notional:,.0f} exceeds "
                    f"{policy.max_buying_power_pct:g}% of buying power "
                    f"(${buying_power:,.0f})."
                )


async def enforce(
    ctx: "SchwabContext", tool_name: str, arguments: Mapping[str, Any]
) -> None:
    """Enforce the configured risk policy for a write tool. Raises RiskViolation."""
    policy = ctx.schwab.config.risk
    if tool_name in _EXEMPT_TOOLS:
        return

    _check_symbols(policy, _symbols(arguments))
    _check_quantity(policy, _total_quantity(arguments))

    notional = await _estimate_notional(ctx, tool_name, arguments)
    _check_notional(policy, notional)

    if policy.enforce_buying_power and (
        policy.max_buying_power_pct is not None or policy.block_on_margin_call
    ):
        await _check_buying_power(ctx, policy, arguments, notional)


__all__ = ["enforce", "RiskViolation"]
