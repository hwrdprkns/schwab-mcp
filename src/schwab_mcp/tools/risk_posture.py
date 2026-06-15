#
# Risk-posture snapshot: positions joined to their protective stops, with
# buying power, margin health, and day P&L. De-emphasizes raw cash balances.
#

from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP

from schwab_mcp.context import SchwabContext
from schwab_mcp.tools._registration import register_tool
from schwab_mcp.tools.utils import JSONType, call


_STOP_TYPES = {"STOP", "STOP_LIMIT", "TRAILING_STOP"}
_OPEN_STATUSES = {
    "WORKING",
    "PENDING_ACTIVATION",
    "AWAITING_STOP_CONDITION",
    "QUEUED",
    "ACCEPTED",
}


def _order_symbol(order: dict[str, Any]) -> str | None:
    legs = order.get("orderLegCollection") or []
    if legs and isinstance(legs[0], dict):
        instrument = legs[0].get("instrument") or {}
        symbol = instrument.get("symbol")
        return str(symbol) if symbol else None
    return None


def _stops_by_symbol(orders: Any) -> dict[str, dict[str, Any]]:
    """Index open protective stop orders by symbol (first open stop wins)."""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(orders, list):
        return result
    for order in orders:
        if not isinstance(order, dict):
            continue
        if str(order.get("orderType", "")).upper() not in _STOP_TYPES:
            continue
        if str(order.get("status", "")).upper() not in _OPEN_STATUSES:
            continue
        symbol = _order_symbol(order)
        if not symbol or symbol in result:
            continue
        stop: dict[str, Any] = {
            "type": order.get("orderType"),
            "stopPrice": order.get("stopPrice"),
            "orderId": order.get("orderId"),
        }
        if order.get("stopPriceOffset") is not None:
            stop["offset"] = order.get("stopPriceOffset")
            stop["offsetType"] = order.get("stopPriceLinkType")
        result[symbol] = stop
    return result


def _position_view(
    position: dict[str, Any], stops: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    instrument = position.get("instrument") or {}
    symbol = instrument.get("symbol") or position.get("symbol")
    long_q = position.get("longQuantity") or 0
    short_q = position.get("shortQuantity") or 0
    quantity = long_q - short_q
    market_value = position.get("marketValue")
    stop = stops.get(symbol) if symbol else None

    view: dict[str, Any] = {
        "symbol": symbol,
        "quantity": quantity,
        "averagePrice": position.get("averagePrice"),
        "marketValue": market_value,
        "dayPnL": position.get("currentDayProfitLoss"),
        "dayPnLPct": position.get("currentDayProfitLossPercentage"),
        "protected": stop is not None,
    }
    if stop is not None:
        view["stop"] = stop
        stop_price = stop.get("stopPrice")
        if (
            isinstance(stop_price, (int, float))
            and isinstance(market_value, (int, float))
            and quantity
        ):
            current = market_value / quantity
            if current:
                view["stopDistancePct"] = round((current - stop_price) / current * 100, 2)
    return view


def _build_posture(account: Any, orders: Any) -> dict[str, Any]:
    securities = (
        account.get("securitiesAccount", account) if isinstance(account, dict) else {}
    )
    if not isinstance(securities, dict):
        securities = {}
    positions = securities.get("positions") or []
    balances = securities.get("currentBalances") or {}
    if not isinstance(balances, dict):
        balances = {}

    stops = _stops_by_symbol(orders)
    views = [_position_view(p, stops) for p in positions if isinstance(p, dict)]
    unprotected = [v["symbol"] for v in views if not v["protected"] and v["quantity"]]
    day_pnl_total = sum(
        v["dayPnL"] for v in views if isinstance(v.get("dayPnL"), (int, float))
    )

    maintenance_call = balances.get("maintenanceCall") or 0
    reg_t_call = balances.get("regTCall") or 0

    return {
        "account": securities.get("accountNumber"),
        "positions": views,
        "unprotected": unprotected,
        "dayPnLTotal": round(day_pnl_total, 2),
        "buyingPower": {
            "buyingPower": balances.get("buyingPower"),
            "buyingPowerNonMarginableTrade": balances.get(
                "buyingPowerNonMarginableTrade"
            ),
        },
        "margin": {
            "marginBalance": balances.get("marginBalance"),
            "marginEquity": balances.get("marginEquity"),
            "maintenanceRequirement": balances.get("maintenanceRequirement"),
            "maintenanceCall": balances.get("maintenanceCall"),
            "regTCall": balances.get("regTCall"),
            "marginCall": bool(maintenance_call > 0 or reg_t_call > 0),
        },
    }


async def get_risk_posture(
    ctx: SchwabContext,
    account_hash: Annotated[
        str | None,
        "Account hash, number, or nickname. Omit to use the default account.",
    ] = None,
) -> JSONType:
    """
    Risk posture for an account: each position with its protective stop (and a flag for UNPROTECTED holdings), plus buying power, margin health (incl. margin-call warnings), and day P&L. Accepts an account hash, number, or nickname; omit for the default account.
    """
    account = await call(
        ctx.accounts.get_account,
        account_hash,
        fields=[ctx.accounts.Account.Fields.POSITIONS],
    )
    orders = await call(ctx.orders.get_orders_for_account, account_hash)
    return _build_posture(account, orders)


_READ_ONLY_TOOLS = (get_risk_posture,)


def register(
    server: FastMCP,
    *,
    allow_write: bool,
    result_transform: Callable[[Any], Any] | None = None,
) -> None:
    _ = allow_write
    for func in _READ_ONLY_TOOLS:
        register_tool(server, func, result_transform=result_transform)
