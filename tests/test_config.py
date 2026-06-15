from __future__ import annotations

import yaml

from schwab_mcp.config import PersonalConfig, load_config


def test_missing_file_yields_permissive_defaults(tmp_path):
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.default_account is None
    assert cfg.accounts == {}
    assert cfg.risk.max_order_notional is None
    assert cfg.risk.enforce_buying_power is False
    assert cfg.risk.symbol_deny == ()


def test_empty_is_default():
    cfg = PersonalConfig.empty()
    assert cfg.default_account is None
    assert cfg.risk.max_quantity is None


def test_loads_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "default_account": "MAIN",
                "accounts": {"MAIN": "123456789", "IRA": "987654321"},
                "risk": {
                    "max_order_notional": 25000,
                    "max_quantity": 1000,
                    "max_buying_power_pct": 50,
                    "enforce_buying_power": True,
                    "block_on_margin_call": True,
                    "symbol_deny": ["gme", "amc"],
                },
            }
        )
    )
    cfg = load_config(str(path))
    assert cfg.default_account == "MAIN"
    assert cfg.accounts == {"MAIN": "123456789", "IRA": "987654321"}
    assert cfg.risk.max_order_notional == 25000
    assert cfg.risk.max_quantity == 1000
    assert cfg.risk.max_buying_power_pct == 50
    assert cfg.risk.enforce_buying_power is True
    assert cfg.risk.block_on_margin_call is True
    # symbols are upper-cased and stored as a tuple.
    assert cfg.risk.symbol_deny == ("GME", "AMC")


def test_env_overrides(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"risk": {"max_order_notional": 100}}))
    monkeypatch.setenv("SCHWAB_MCP_MAX_ORDER_NOTIONAL", "5000")
    monkeypatch.setenv("SCHWAB_MCP_DEFAULT_ACCOUNT", "IRA")
    monkeypatch.setenv("SCHWAB_MCP_SYMBOL_DENY", "tsla, nvda")
    monkeypatch.setenv("SCHWAB_MCP_ENFORCE_BUYING_POWER", "true")

    cfg = load_config(str(path))
    assert cfg.risk.max_order_notional == 5000  # env wins over file
    assert cfg.default_account == "IRA"
    assert cfg.risk.symbol_deny == ("TSLA", "NVDA")
    assert cfg.risk.enforce_buying_power is True
