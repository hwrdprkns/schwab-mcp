# Staying logged in (personal setup)

This fork is tuned for a single user running the server locally on a Mac via
Claude Desktop. Schwab's OAuth has a hard **7-day refresh-token limit** that
refreshing does **not** roll forward — so one manual browser + 2FA login per
week is unavoidable. Everything here is about making that the *only* login you
need: no premature re-auth, and the token kept warm the rest of the week.

## TL;DR

```bash
# Once: store your app credentials (kept in a 0600 file in the data dir).
schwab-mcp save-credentials

# Weekly (manual, with your SMS 2FA): mints a fresh 7-day token.
schwab-mcp auth

# Once: install a launchd job that keeps the token warm all week.
schwab-mcp install-scheduler \
  --discord-token "$SCHWAB_MCP_DISCORD_TOKEN" \
  --discord-channel-id "$SCHWAB_MCP_DISCORD_CHANNEL_ID"
```

The token lives at a **global** path
(`platformdirs.user_data_dir("schwab-mcp")/token.yaml`), independent of which
git branch/checkout is running, so the scheduler and the Claude Desktop server
share the same token.

## Why the old setup logged you in too often

In server mode the client was created with `interactive=False` **and**
`max_token_age=5d`. A token older than 5 days was discarded, fell into a
browserless login flow, and hung ~300s before failing with a 500 — two days
*before* Schwab's real 7-day limit. This fork:

- Calls `easy_client(..., max_token_age=None)` so a stale-but-refreshable token
  is never discarded — `schwab-py` silently refreshes the access token on the
  first call.
- Refuses to start **only** at the true ~7-day cliff (with a ~12h safety
  margin), returning a clean `401` telling you to run `schwab-mcp auth` (instead
  of hanging).

## Keep-warm job

`schwab-mcp refresh-token` loads the existing token and makes one trivial
authenticated call (`get_account_numbers`) so `schwab-py` refreshes the access
token and rewrites `token.yaml`. **No browser, no 2FA.** It only works while the
7-day refresh token is still alive — which is the point: it keeps the token
fresh so Claude Desktop always finds a live token even after being closed for a
day or two.

`schwab-mcp install-scheduler` writes and loads a launchd agent
(`~/Library/LaunchAgents/com.user.schwab-mcp-keepwarm.plist`) that runs
`refresh-token` every 30 minutes (configurable via `--interval`). See
[`schwab-mcp-keepwarm.plist`](./schwab-mcp-keepwarm.plist) for the template.

Manage it:

```bash
launchctl list | grep schwab          # confirm it's loaded
launchctl unload ~/Library/LaunchAgents/com.user.schwab-mcp-keepwarm.plist
tail -f "$(python -c 'import platformdirs,os;print(os.path.join(platformdirs.user_data_dir("schwab-mcp"),"keepwarm.log"))')"
```

## Weekly-login reminder

If you pass `--discord-token` / `--discord-channel-id` (or set
`SCHWAB_MCP_DISCORD_TOKEN` / `SCHWAB_MCP_DISCORD_CHANNEL_ID`), the keep-warm job
and the server startup gate post a Discord message when the refresh token is
near/at its 7-day cliff — so the one weekly `schwab-mcp auth` is prompted, never
a surprise mid-trade. These reuse your existing Discord approval bot/channel.

## Branch / Claude Desktop

`main` tracks upstream `jkoelker/schwab-mcp`; personal work lives on the
`personal` branch. Because the token path is global, pointing Claude Desktop at
the `personal` branch (or a git worktree of it) is purely about which code runs
— the token is shared either way.
