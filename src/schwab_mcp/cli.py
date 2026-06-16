import click
import functools
import os
import plistlib
import shutil
import subprocess
import sys
import anyio
from schwab.client import AsyncClient

from schwab_mcp.server import SchwabMCPServer, send_error_response
from schwab_mcp import auth as schwab_auth
from schwab_mcp import config as appconfig
from schwab_mcp import onepassword
from schwab_mcp import tokens
from schwab_mcp.approvals import (
    DiscordApprovalManager,
    DiscordApprovalSettings,
    NoOpApprovalManager,
    send_discord_notification,
)


APP_NAME = "schwab-mcp"
TOKEN_MAX_AGE_SECONDS = schwab_auth.DEFAULT_MAX_TOKEN_AGE_SECONDS


def _maybe_send_login_reminder(
    discord_token: str | None,
    discord_channel_id: int | None,
    *,
    title: str,
    description: str,
) -> None:
    """Best-effort Discord nudge that the weekly login is due.

    No-op when Discord isn't configured. A failed reminder must never block the
    command's primary outcome, so all errors are swallowed.
    """
    if not discord_token or not discord_channel_id:
        return
    try:
        anyio.run(
            functools.partial(
                send_discord_notification,
                token=discord_token,
                channel_id=int(discord_channel_id),
                title=title,
                description=description,
            ),
            backend="asyncio",
        )
    except Exception:  # pragma: no cover - best-effort notification
        pass


def _is_reauth_required_error(text: str) -> bool:
    """True if a Schwab error means the refresh token must be re-minted.

    Covers what Schwab actually returns when the 7-day refresh token has lapsed
    or been revoked: a ``400 invalid_grant`` ("Refresh token is invalid, expired
    or revoked") / ``unsupported_token_type`` at the token endpoint, plus
    ``401 invalid_client``.
    """
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "invalid_grant",
            "invalid_client",
            "unsupported_token_type",
            "refresh token is invalid",
        )
    )


def _resolve_credentials(
    client_id: str | None,
    client_secret: str | None,
) -> tuple[str | None, str | None]:
    """Resolve Schwab app credentials.

    Precedence: explicit flag/env value > local credentials file > 1Password
    (`op://` references in :mod:`schwab_mcp.onepassword`, resolved via the `op`
    CLI). Returns whatever could be found; callers validate that both are set.
    """
    creds = tokens.load_credentials(tokens.credentials_path(APP_NAME))
    client_id = client_id or creds.get("client_id")
    client_secret = client_secret or creds.get("client_secret")
    if not client_id or not client_secret:
        client_id, client_secret = onepassword.resolve_credentials(
            client_id, client_secret
        )
    return client_id, client_secret


@click.group()
def cli():
    """Schwab Model Context Protocol CLI."""
    pass


@cli.command("auth")
@click.option(
    "--token-path",
    type=str,
    default=tokens.token_path(APP_NAME),
    help="Path to save Schwab token file",
)
@click.option(
    "--client-id",
    type=str,
    required=False,
    default=None,
    envvar="SCHWAB_CLIENT_ID",
    help="Schwab Client ID",
)
@click.option(
    "--client-secret",
    type=str,
    required=False,
    default=None,
    envvar="SCHWAB_CLIENT_SECRET",
    help="Schwab Client Secret",
)
@click.option(
    "--callback-url",
    type=str,
    envvar="SCHWAB_CALLBACK_URL",
    default="https://127.0.0.1:8182",
    help="Schwab callback URL",
)
def auth(
    token_path: str,
    client_id: str | None,
    client_secret: str | None,
    callback_url: str,
) -> int:
    """Initialize Schwab client authentication."""
    client_id, client_secret = _resolve_credentials(client_id, client_secret)
    if not client_id or not client_secret:
        click.echo(
            "Error: client-id and client-secret are required. "
            "Provide via --client-id/--client-secret, env vars, "
            "or store in credentials file with 'schwab-mcp save-credentials'.",
            err=True,
        )
        raise SystemExit(1)

    click.echo(f"Initializing authentication flow to create token at: {token_path}")
    token_manager = tokens.Manager(token_path)

    try:
        # This will initiate the manual authentication flow
        schwab_auth.easy_client(
            client_id=client_id,
            client_secret=client_secret,
            callback_url=callback_url,
            token_manager=token_manager,
            max_token_age=TOKEN_MAX_AGE_SECONDS,
        )

        # If we get here, the authentication was successful
        click.echo(f"Authentication successful! Token saved to: {token_path}")
        return 0
    except Exception as e:
        # raise SystemExit so the shell sees a non-zero exit (Click drops `return 1`).
        click.echo(f"Authentication failed: {str(e)}", err=True)
        raise SystemExit(1)


@cli.command("server")
@click.option(
    "--token-path",
    type=str,
    default=tokens.token_path(APP_NAME),
    help="Path to Schwab token file",
)
@click.option(
    "--client-id",
    type=str,
    required=False,
    default=None,
    envvar="SCHWAB_CLIENT_ID",
    help="Schwab Client ID",
)
@click.option(
    "--client-secret",
    type=str,
    required=False,
    default=None,
    envvar="SCHWAB_CLIENT_SECRET",
    help="Schwab Client Secret",
)
@click.option(
    "--callback-url",
    type=str,
    envvar="SCHWAB_CALLBACK_URL",
    default="https://127.0.0.1:8182",
    help="Schwab callback URL",
)
@click.option(
    "--jesus-take-the-wheel",
    default=False,
    is_flag=True,
    help="Allow tools to modify the portfolios, placing trades, etc.",
)
@click.option(
    "--no-technical-tools",
    default=False,
    is_flag=True,
    help="Disable optional technical analysis tools.",
)
@click.option(
    "--discord-token",
    type=str,
    envvar="SCHWAB_MCP_DISCORD_TOKEN",
    help="Discord bot token used for approval prompts.",
)
@click.option(
    "--discord-channel-id",
    type=int,
    envvar="SCHWAB_MCP_DISCORD_CHANNEL_ID",
    help="Discord channel ID where approval requests are posted.",
)
@click.option(
    "--discord-approver",
    type=str,
    multiple=True,
    help="Discord user ID allowed to approve or deny requests. Pass multiple times for several reviewers.",
)
@click.option(
    "--discord-timeout",
    type=int,
    default=600,
    show_default=True,
    envvar="SCHWAB_MCP_DISCORD_TIMEOUT",
    help="Seconds to wait for Discord approval before timing out.",
)
@click.option(
    "--json",
    "json_output",
    default=False,
    is_flag=True,
    help="Return JSON payloads from tools instead of Toon-encoded strings.",
)
def server(
    token_path: str,
    client_id: str | None,
    client_secret: str | None,
    callback_url: str,
    jesus_take_the_wheel: bool,
    discord_token: str | None,
    discord_channel_id: int | None,
    discord_approver: tuple[str, ...],
    discord_timeout: int,
    no_technical_tools: bool,
    json_output: bool,
) -> int:
    """Run the Schwab MCP server."""
    client_id, client_secret = _resolve_credentials(client_id, client_secret)
    if not client_id or not client_secret:
        send_error_response(
            "client-id and client-secret are required. "
            "Provide via --client-id/--client-secret, env vars, "
            "or store in credentials file with 'schwab-mcp save-credentials'.",
            code=400,
            details={
                "missing_client_id": not bool(client_id),
                "missing_client_secret": not bool(client_secret),
            },
        )
        return 1

    # No logging to stderr when in MCP mode (we'll use proper MCP responses)
    token_manager = tokens.Manager(token_path)

    try:
        # max_token_age=None so a stale-but-refreshable token is never discarded.
        # (With interactive=False, discarding it would fall into a browserless
        # login flow that just hangs ~300s and then 500s.) schwab-py silently
        # refreshes the access token on the first authenticated call; we gate the
        # true 7-day refresh-token cliff explicitly below.
        client = schwab_auth.easy_client(
            client_id=client_id,
            client_secret=client_secret,
            callback_url=callback_url,
            token_manager=token_manager,
            asyncio=True,
            interactive=False,
            enforce_enums=False,
            max_token_age=None,
        )

        if not isinstance(client, AsyncClient):
            send_error_response(
                "Async client required when starting the MCP server.",
                code=500,
                details={"client_type": type(client).__name__},
            )
            return 1
    except Exception as e:
        send_error_response(
            f"Error initializing Schwab client: {str(e)}",
            code=500,
            details={"error": str(e)},
        )
        return 1

    # Only refuse to start at Schwab's true ~7-day refresh-token cliff (minus a
    # safety margin) — not prematurely. Below the cliff the token is still
    # refreshable and the server works normally.
    if client.token_age() >= schwab_auth.REAUTH_THRESHOLD_SECONDS:
        _maybe_send_login_reminder(
            discord_token,
            discord_channel_id,
            title="Schwab weekly login required",
            description=(
                "Your Schwab refresh token has reached its ~7-day limit. "
                "Run `schwab-mcp auth` to re-authenticate (browser + 2FA)."
            ),
        )
        send_error_response(
            "Schwab refresh token has reached its ~7-day limit. "
            "Please run 'schwab-mcp auth' to re-authenticate.",
            code=401,
            details={
                "token_expired": True,
                "token_age_days": client.token_age() / 86400,
            },
        )
        return 1

    try:
        approver_values: tuple[str, ...] = discord_approver
        if not approver_values:
            env_approvers = os.getenv("SCHWAB_MCP_DISCORD_APPROVERS")
            if env_approvers:
                approver_values = tuple(
                    value.strip() for value in env_approvers.split(",") if value.strip()
                )

        discord_requested = any(
            (
                discord_token,
                discord_channel_id,
                approver_values,
            )
        )
        allow_write = False

        if jesus_take_the_wheel:
            approval_manager = NoOpApprovalManager()
            allow_write = True
        elif discord_requested:
            if not discord_token or not discord_channel_id:
                send_error_response(
                    "Discord approval configuration is required to enable write tools.",
                    code=400,
                    details={
                        "missing_token": not bool(discord_token),
                        "missing_channel_id": not bool(discord_channel_id),
                    },
                )
                return 1

            approver_ids = DiscordApprovalManager.authorized_user_ids(
                [int(value) for value in approver_values] if approver_values else None
            )
            if not approver_ids:
                send_error_response(
                    "Discord approver list cannot be empty. Configure at least one reviewer.",
                    code=400,
                    details={"approver_source": "flags_or_env"},
                )
                return 1
            settings = DiscordApprovalSettings(
                token=discord_token,
                channel_id=discord_channel_id,
                approver_ids=approver_ids,
                timeout_seconds=float(discord_timeout),
            )
            approval_manager = DiscordApprovalManager(settings)
            allow_write = True
        else:
            approval_manager = NoOpApprovalManager()

        if jesus_take_the_wheel and discord_token:
            click.echo(
                "Warning: --jesus-take-the-wheel bypasses Discord approvals.", err=True
            )

        server = SchwabMCPServer(
            APP_NAME,
            client,
            approval_manager=approval_manager,
            allow_write=allow_write,
            enable_technical_tools=not no_technical_tools,
            use_json=json_output,
            config=appconfig.load_config(appconfig.config_path(APP_NAME)),
        )
        anyio.run(server.run, backend="asyncio")
        return 0
    except Exception as e:
        send_error_response(
            f"Error running server: {str(e)}", code=500, details={"error": str(e)}
        )
        return 1


@cli.command("save-credentials")
@click.option(
    "--client-id",
    type=str,
    prompt="Schwab Client ID",
    help="Schwab Client ID",
)
@click.option(
    "--client-secret",
    type=str,
    prompt="Schwab Client Secret",
    help="Schwab Client Secret",
)
def save_credentials(client_id: str, client_secret: str) -> None:
    """Save Schwab client credentials to a local file."""
    path = tokens.credentials_path(APP_NAME)
    tokens.save_credentials(path, client_id, client_secret)
    click.echo(f"Credentials saved to: {path}")


@cli.command("refresh-token")
@click.option(
    "--token-path",
    type=str,
    default=tokens.token_path(APP_NAME),
    help="Path to Schwab token file",
)
@click.option(
    "--client-id",
    type=str,
    required=False,
    default=None,
    envvar="SCHWAB_CLIENT_ID",
    help="Schwab Client ID",
)
@click.option(
    "--client-secret",
    type=str,
    required=False,
    default=None,
    envvar="SCHWAB_CLIENT_SECRET",
    help="Schwab Client Secret",
)
@click.option(
    "--callback-url",
    type=str,
    envvar="SCHWAB_CALLBACK_URL",
    default="https://127.0.0.1:8182",
    help="Schwab callback URL",
)
@click.option(
    "--discord-token",
    type=str,
    envvar="SCHWAB_MCP_DISCORD_TOKEN",
    help="Discord bot token used to send the weekly-login reminder.",
)
@click.option(
    "--discord-channel-id",
    type=int,
    envvar="SCHWAB_MCP_DISCORD_CHANNEL_ID",
    help="Discord channel ID where the weekly-login reminder is posted.",
)
def refresh_token(
    token_path: str,
    client_id: str | None,
    client_secret: str | None,
    callback_url: str,
    discord_token: str | None,
    discord_channel_id: int | None,
) -> int:
    """Keep the Schwab token warm (no browser, no 2FA).

    Loads the existing token and makes one trivial authenticated call so
    schwab-py silently refreshes the access token and rewrites the token file.
    Run on a schedule (see 'install-scheduler') so the token stays valid all week
    even when Claude Desktop is closed. Requires a still-valid 7-day refresh
    token; if it has lapsed, fires a Discord reminder and exits non-zero.
    """
    client_id, client_secret = _resolve_credentials(client_id, client_secret)
    if not client_id or not client_secret:
        click.echo(
            "Error: client-id and client-secret are required. "
            "Provide via --client-id/--client-secret, env vars, "
            "or store in credentials file with 'schwab-mcp save-credentials'.",
            err=True,
        )
        raise SystemExit(1)

    token_manager = tokens.Manager(token_path)
    if not token_manager.exists():
        click.echo(
            f"No token file at {token_path}. Run 'schwab-mcp auth' first.",
            err=True,
        )
        raise SystemExit(1)

    try:
        client = schwab_auth.easy_client(
            client_id=client_id,
            client_secret=client_secret,
            callback_url=callback_url,
            token_manager=token_manager,
            asyncio=True,
            interactive=False,
            enforce_enums=False,
            max_token_age=None,
        )
    except Exception as e:
        click.echo(f"Error initializing Schwab client: {e}", err=True)
        raise SystemExit(1)

    auth_expired = False

    async def _warm() -> None:
        nonlocal auth_expired
        try:
            response = await client.get_account_numbers()
            body = getattr(response, "text", "") or ""
            if response.status_code in (400, 401) and _is_reauth_required_error(body):
                auth_expired = True
                return
            response.raise_for_status()
        finally:
            close = getattr(client, "close_async_session", None)
            if close is not None:
                await close()

    try:
        anyio.run(_warm, backend="asyncio")
    except Exception as e:
        # schwab-py raises during the silent token refresh when the refresh
        # token has lapsed/been revoked — surface that as the weekly-login nudge
        # rather than an opaque failure.
        if _is_reauth_required_error(str(e)):
            auth_expired = True
        else:
            click.echo(f"Token refresh failed: {e}", err=True)
            raise SystemExit(1)

    if auth_expired:
        _maybe_send_login_reminder(
            discord_token,
            discord_channel_id,
            title="Schwab weekly login expired",
            description=(
                "Your Schwab refresh token has lapsed (past its 7-day limit). "
                "Run `schwab-mcp auth` to re-authenticate (browser + 2FA)."
            ),
        )
        click.echo(
            "Schwab refresh token has expired. Run 'schwab-mcp auth' to "
            "re-authenticate.",
            err=True,
        )
        raise SystemExit(1)

    click.echo(f"Token refreshed: {token_path}")
    return 0


def _keepwarm_plist_dict(
    *,
    label: str,
    executable: str,
    interval: int,
    log_path: str,
    env: dict[str, str],
) -> dict:
    """Build the launchd plist payload for the keep-warm job."""
    return {
        "Label": label,
        "ProgramArguments": [executable, "refresh-token"],
        "EnvironmentVariables": env,
        "StartInterval": interval,
        "RunAtLoad": True,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }


@cli.command("install-scheduler")
@click.option(
    "--interval",
    type=int,
    default=1800,
    show_default=True,
    help="Seconds between keep-warm runs (default 30 min).",
)
@click.option(
    "--label",
    type=str,
    default="com.user.schwab-mcp-keepwarm",
    show_default=True,
    help="launchd job label / plist filename.",
)
@click.option(
    "--discord-token",
    type=str,
    envvar="SCHWAB_MCP_DISCORD_TOKEN",
    help="Discord bot token passed to the scheduled job for reminders.",
)
@click.option(
    "--discord-channel-id",
    type=int,
    envvar="SCHWAB_MCP_DISCORD_CHANNEL_ID",
    help="Discord channel ID passed to the scheduled job for reminders.",
)
@click.option(
    "--load/--no-load",
    "load_job",
    default=True,
    show_default=True,
    help="Run 'launchctl load' after writing the plist.",
)
def install_scheduler(
    interval: int,
    label: str,
    discord_token: str | None,
    discord_channel_id: int | None,
    load_job: bool,
) -> int:
    """Install a macOS launchd job that runs 'refresh-token' on a schedule.

    Writes ~/Library/LaunchAgents/<label>.plist and (by default) loads it so the
    token is kept warm independently of Claude Desktop's on-demand server.
    """
    executable = shutil.which("schwab-mcp") or os.path.join(
        os.path.dirname(sys.executable), "schwab-mcp"
    )
    if not os.path.exists(executable):
        click.echo(
            f"Could not locate the 'schwab-mcp' executable (looked at {executable}). "
            "Ensure it is installed and on PATH.",
            err=True,
        )
        raise SystemExit(1)

    data_dir = os.path.dirname(tokens.token_path(APP_NAME))
    log_path = os.path.join(data_dir, "keepwarm.log")

    env: dict[str, str] = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if discord_token:
        env["SCHWAB_MCP_DISCORD_TOKEN"] = discord_token
    if discord_channel_id:
        env["SCHWAB_MCP_DISCORD_CHANNEL_ID"] = str(discord_channel_id)

    plist = _keepwarm_plist_dict(
        label=label,
        executable=executable,
        interval=interval,
        log_path=log_path,
        env=env,
    )

    dest = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(plistlib.dumps(plist))
    click.echo(f"Wrote launchd plist: {dest}")

    if load_job:
        # Unload first so re-running the command refreshes an existing job.
        subprocess.run(
            ["launchctl", "unload", dest], check=False, capture_output=True
        )
        result = subprocess.run(
            ["launchctl", "load", dest],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            click.echo(
                f"launchctl load failed: {result.stderr.strip()}", err=True
            )
            raise SystemExit(1)
        click.echo(f"Loaded launchd job '{label}' (every {interval}s).")
    else:
        click.echo(
            f"Plist written but not loaded. Load it with:\n"
            f"  launchctl load {dest}"
        )

    return 0


@cli.command("show-config")
def show_config() -> int:
    """Print the resolved personal config (default account, nicknames, risk limits)."""
    path = appconfig.config_path(APP_NAME)
    cfg = appconfig.load_config(path)
    click.echo(f"Config file: {path}")
    click.echo(f"Default account: {cfg.default_account or '(none)'}")
    click.echo(f"Account nicknames: {cfg.accounts or '(none)'}")
    risk = cfg.risk
    click.echo("Risk policy:")
    click.echo(f"  max_order_notional:   {risk.max_order_notional}")
    click.echo(f"  max_quantity:         {risk.max_quantity}")
    click.echo(f"  max_buying_power_pct: {risk.max_buying_power_pct}")
    click.echo(f"  enforce_buying_power: {risk.enforce_buying_power}")
    click.echo(f"  block_on_margin_call: {risk.block_on_margin_call}")
    click.echo(f"  fail_open_on_error:   {risk.fail_open_on_error}")
    click.echo(f"  symbol_allow:         {list(risk.symbol_allow) or '(any)'}")
    click.echo(f"  symbol_deny:          {list(risk.symbol_deny) or '(none)'}")
    return 0


def main():
    """Main entry point for the application."""
    return cli()


if __name__ == "__main__":
    sys.exit(main())
