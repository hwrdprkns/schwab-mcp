# Risk posture, guardrails & account context

Personalization for a single-user server: a risk-focused read view, server-side
trade limits, and account nicknames so you stop passing hashes around.

## Config

A `config.yaml` in the data dir (same place as `token.yaml` / `credentials.yaml`),
env-overridable. Missing file = permissive defaults (no limits). See it resolved
with `schwab-mcp show-config`.

```yaml
default_account: MAIN
accounts:
  MAIN: "123456789"     # nickname -> account number
  IRA: "987654321"
risk:
  max_order_notional: 25000      # block orders above this $ notional
  max_quantity: 1000             # block orders above this size
  max_buying_power_pct: 50       # block orders > 50% of buying power
  enforce_buying_power: true     # turn on the live buying-power/margin check
  block_on_margin_call: true     # block new orders during a margin/Reg-T call
  fail_open_on_error: false      # if true, allow when balances can't be fetched
  symbol_deny: ["GME", "AMC"]
  symbol_allow: []               # empty = allow all
```

Env overrides (win over the file): `SCHWAB_MCP_DEFAULT_ACCOUNT`,
`SCHWAB_MCP_MAX_ORDER_NOTIONAL`, `SCHWAB_MCP_MAX_QUANTITY`,
`SCHWAB_MCP_MAX_BUYING_POWER_PCT`, `SCHWAB_MCP_ENFORCE_BUYING_POWER`,
`SCHWAB_MCP_BLOCK_ON_MARGIN_CALL`, `SCHWAB_MCP_FAIL_OPEN_ON_ERROR`,
`SCHWAB_MCP_SYMBOL_DENY`, `SCHWAB_MCP_SYMBOL_ALLOW`.

## `get_risk_posture` (read)

One call → positions joined to their open protective stops, with a flag for
**unprotected** holdings, plus buying power, margin health, and day P&L:

- `positions[]`: symbol, signed quantity, average/market value, day P&L, and the
  matched `stop` (price + `stopDistancePct`); `protected: false` when there's no
  open stop.
- `unprotected[]`: symbols with a position but no protective stop.
- `dayPnLTotal`, `buyingPower`, and `margin` (incl. a `marginCall` flag).

Accepts an account hash, number, or nickname; omit to use the default account.

## Account context

Any account-scoped tool accepts a hash, account number, or nickname. Tools whose
account is the last required argument (`get_account`, `get_account_with_positions`,
`get_orders`, `get_transactions`, `get_risk_posture`) can omit it entirely to use
`default_account`. The account-number → hash mapping is fetched once and cached
for the server's lifetime; real hashes pass through with no extra lookup.

## Guardrails (write)

Every order-placing tool is gated **before** Discord approval, and independently
of it — so limits also cap `--jesus-take-the-wheel` mode. Order of checks:

1. **Static (no network):** `symbol_deny`/`symbol_allow`, `max_quantity`,
   `max_order_notional` (notional = qty × price, ×100 for options; a quote is
   fetched for market orders without a price).
2. **Live (when `enforce_buying_power`):** blocks during a margin/Reg-T call
   (`block_on_margin_call`), and blocks orders whose notional exceeds
   `max_buying_power_pct%` of buying power.

A blocked order raises a `PermissionError` ("Blocked by risk policy: …") and never
reaches Discord. `cancel_order` is exempt (risk-reducing). The live check
**fails closed** if balances can't be fetched, unless `fail_open_on_error: true`.
