from __future__ import annotations

#
# Personal configuration: default account, account nicknames, and risk limits.
#
# Stored as YAML in the data dir (next to token.yaml / credentials.yaml), with
# environment-variable overrides for the common knobs. A missing file yields a
# permissive default config, so the server behaves exactly as before until you
# opt into limits.
#

import os
import pathlib
from dataclasses import dataclass, field, replace
from typing import Any

import yaml
from platformdirs import user_data_dir


def config_path(app_name: str, filename: str = "config.yaml") -> str:
    """Path to the personal config file in the user data dir."""
    data_dir = user_data_dir(app_name)
    pathlib.Path(data_dir).mkdir(parents=True, exist_ok=True)
    return os.path.join(data_dir, filename)


@dataclass(slots=True, frozen=True)
class RiskPolicy:
    """Server-side trade limits. ``None`` limits are unenforced."""

    max_order_notional: float | None = None
    max_quantity: int | None = None
    max_buying_power_pct: float | None = None
    block_on_margin_call: bool = False
    enforce_buying_power: bool = False
    fail_open_on_error: bool = False
    symbol_allow: tuple[str, ...] = ()
    symbol_deny: tuple[str, ...] = ()

    @property
    def needs_balances(self) -> bool:
        """True if enforcing this policy requires a live balances fetch."""
        return self.enforce_buying_power and (
            self.max_buying_power_pct is not None or self.block_on_margin_call
        )


@dataclass(slots=True, frozen=True)
class PersonalConfig:
    """Personal account context + risk limits."""

    default_account: str | None = None
    accounts: dict[str, str] = field(default_factory=dict)  # nickname -> account number
    risk: RiskPolicy = field(default_factory=RiskPolicy)

    @classmethod
    def empty(cls) -> "PersonalConfig":
        return cls()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _as_symbol_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = list(value)
    return tuple(s.strip().upper() for s in items if str(s).strip())


def _risk_from_dict(data: dict[str, Any]) -> RiskPolicy:
    return RiskPolicy(
        max_order_notional=_as_float(data.get("max_order_notional")),
        max_quantity=_as_int(data.get("max_quantity")),
        max_buying_power_pct=_as_float(data.get("max_buying_power_pct")),
        block_on_margin_call=_as_bool(data.get("block_on_margin_call", False)),
        enforce_buying_power=_as_bool(data.get("enforce_buying_power", False)),
        fail_open_on_error=_as_bool(data.get("fail_open_on_error", False)),
        symbol_allow=_as_symbol_tuple(data.get("symbol_allow")),
        symbol_deny=_as_symbol_tuple(data.get("symbol_deny")),
    )


def _apply_env_overrides(config: PersonalConfig) -> PersonalConfig:
    """Overlay environment variables onto a loaded config."""
    env = os.environ
    default_account = env.get("SCHWAB_MCP_DEFAULT_ACCOUNT", config.default_account)

    risk = config.risk
    overrides: dict[str, Any] = {}
    if "SCHWAB_MCP_MAX_ORDER_NOTIONAL" in env:
        overrides["max_order_notional"] = _as_float(env["SCHWAB_MCP_MAX_ORDER_NOTIONAL"])
    if "SCHWAB_MCP_MAX_QUANTITY" in env:
        overrides["max_quantity"] = _as_int(env["SCHWAB_MCP_MAX_QUANTITY"])
    if "SCHWAB_MCP_MAX_BUYING_POWER_PCT" in env:
        overrides["max_buying_power_pct"] = _as_float(
            env["SCHWAB_MCP_MAX_BUYING_POWER_PCT"]
        )
    if "SCHWAB_MCP_BLOCK_ON_MARGIN_CALL" in env:
        overrides["block_on_margin_call"] = _as_bool(env["SCHWAB_MCP_BLOCK_ON_MARGIN_CALL"])
    if "SCHWAB_MCP_ENFORCE_BUYING_POWER" in env:
        overrides["enforce_buying_power"] = _as_bool(
            env["SCHWAB_MCP_ENFORCE_BUYING_POWER"]
        )
    if "SCHWAB_MCP_FAIL_OPEN_ON_ERROR" in env:
        overrides["fail_open_on_error"] = _as_bool(env["SCHWAB_MCP_FAIL_OPEN_ON_ERROR"])
    if "SCHWAB_MCP_SYMBOL_DENY" in env:
        overrides["symbol_deny"] = _as_symbol_tuple(env["SCHWAB_MCP_SYMBOL_DENY"])
    if "SCHWAB_MCP_SYMBOL_ALLOW" in env:
        overrides["symbol_allow"] = _as_symbol_tuple(env["SCHWAB_MCP_SYMBOL_ALLOW"])

    if overrides:
        risk = replace(risk, **overrides)

    if default_account is config.default_account and risk is config.risk:
        return config
    return replace(config, default_account=default_account, risk=risk)


def load_config(path: str) -> PersonalConfig:
    """Load personal config from YAML, then overlay env overrides.

    A missing or empty/invalid file yields a permissive default config.
    """
    data: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path) as f:
            loaded = yaml.safe_load(f)
        if isinstance(loaded, dict):
            data = loaded

    accounts_raw = data.get("accounts") or {}
    accounts = {
        str(name): str(number)
        for name, number in dict(accounts_raw).items()
    }
    risk = _risk_from_dict(dict(data.get("risk") or {}))
    config = PersonalConfig(
        default_account=data.get("default_account"),
        accounts=accounts,
        risk=risk,
    )
    return _apply_env_overrides(config)


__all__ = [
    "RiskPolicy",
    "PersonalConfig",
    "config_path",
    "load_config",
]
