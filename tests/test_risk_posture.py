from __future__ import annotations

from schwab_mcp.tools.risk_posture import _build_posture


ACCOUNT = {
    "securitiesAccount": {
        "accountNumber": "123456789",
        "positions": [
            {
                "instrument": {"symbol": "AAPL"},
                "longQuantity": 100,
                "shortQuantity": 0,
                "averagePrice": 150.0,
                "marketValue": 16000.0,  # -> current ~160/sh
                "currentDayProfitLoss": 250.0,
                "currentDayProfitLossPercentage": 1.6,
            },
            {
                "instrument": {"symbol": "TSLA"},
                "longQuantity": 10,
                "shortQuantity": 0,
                "averagePrice": 200.0,
                "marketValue": 1800.0,
                "currentDayProfitLoss": -50.0,
            },
        ],
        "currentBalances": {
            "buyingPower": 50000.0,
            "buyingPowerNonMarginableTrade": 25000.0,
            "marginBalance": 1500.0,
            "marginEquity": 50000.0,
            "maintenanceRequirement": 5000.0,
            "maintenanceCall": 0,
            "regTCall": 0,
        },
    }
}

# AAPL has a protective stop; TSLA does not.
ORDERS = [
    {
        "orderType": "STOP",
        "status": "WORKING",
        "stopPrice": 152.0,
        "orderId": 999,
        "orderLegCollection": [
            {"instruction": "SELL", "instrument": {"symbol": "AAPL"}}
        ],
    },
    {
        "orderType": "LIMIT",  # not a stop -> ignored
        "status": "WORKING",
        "orderLegCollection": [
            {"instruction": "SELL", "instrument": {"symbol": "TSLA"}}
        ],
    },
]


def test_joins_stops_and_flags_unprotected():
    out = _build_posture(ACCOUNT, ORDERS)

    assert out["account"] == "123456789"
    by_symbol = {p["symbol"]: p for p in out["positions"]}

    assert by_symbol["AAPL"]["protected"] is True
    assert by_symbol["AAPL"]["stop"]["stopPrice"] == 152.0
    # current ~160, stop 152 -> ~5% below
    assert by_symbol["AAPL"]["stopDistancePct"] == 5.0

    assert by_symbol["TSLA"]["protected"] is False
    assert out["unprotected"] == ["TSLA"]


def test_aggregates_margin_buying_power_and_day_pnl():
    out = _build_posture(ACCOUNT, ORDERS)

    assert out["dayPnLTotal"] == 200.0  # 250 + (-50)
    assert out["buyingPower"]["buyingPower"] == 50000.0
    assert out["margin"]["maintenanceRequirement"] == 5000.0
    assert out["margin"]["marginCall"] is False


def test_margin_call_flagged_when_present():
    account = {
        "securitiesAccount": {
            "accountNumber": "1",
            "positions": [],
            "currentBalances": {"maintenanceCall": 1200.0, "buyingPower": 0},
        }
    }
    out = _build_posture(account, [])
    assert out["margin"]["marginCall"] is True
    assert out["unprotected"] == []


def test_handles_empty_account_gracefully():
    out = _build_posture({}, None)
    assert out["positions"] == []
    assert out["unprotected"] == []
    assert out["dayPnLTotal"] == 0
