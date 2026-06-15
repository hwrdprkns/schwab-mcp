from __future__ import annotations

import plistlib
from typing import Any

from click.testing import CliRunner

from schwab_mcp import cli


def test_keepwarm_plist_dict_shape():
    plist = cli._keepwarm_plist_dict(
        label="com.user.schwab-mcp-keepwarm",
        executable="/usr/local/bin/schwab-mcp",
        interval=1800,
        log_path="/tmp/keepwarm.log",
        env={"PATH": "/usr/bin"},
    )

    assert plist["Label"] == "com.user.schwab-mcp-keepwarm"
    assert plist["ProgramArguments"] == [
        "/usr/local/bin/schwab-mcp",
        "refresh-token",
    ]
    assert plist["StartInterval"] == 1800
    assert plist["RunAtLoad"] is True
    # Round-trips through plistlib (validates it is a serializable plist).
    assert plistlib.loads(plistlib.dumps(plist)) == plist


def test_install_scheduler_writes_and_loads(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}

    # Point HOME at tmp so ~/Library/LaunchAgents lands under the test dir.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/schwab-mcp")
    monkeypatch.setattr(cli.os.path, "exists", lambda _p: True)

    def fake_run(args, **kwargs):
        captured.setdefault("calls", []).append(args)

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = CliRunner().invoke(
        cli.cli,
        [
            "install-scheduler",
            "--interval",
            "900",
            "--discord-token",
            "bot-token",
            "--discord-channel-id",
            "42",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    dest = tmp_path / "Library" / "LaunchAgents" / "com.user.schwab-mcp-keepwarm.plist"
    assert dest.exists()

    plist = plistlib.loads(dest.read_bytes())
    assert plist["StartInterval"] == 900
    assert plist["ProgramArguments"][1] == "refresh-token"
    # Discord settings are propagated to the scheduled job's environment.
    assert plist["EnvironmentVariables"]["SCHWAB_MCP_DISCORD_TOKEN"] == "bot-token"
    assert plist["EnvironmentVariables"]["SCHWAB_MCP_DISCORD_CHANNEL_ID"] == "42"
    # launchctl unload + load were invoked.
    assert any("load" in c for c in captured["calls"])


def test_install_scheduler_no_load(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/schwab-mcp")
    monkeypatch.setattr(cli.os.path, "exists", lambda _p: True)

    def fail_run(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("launchctl should not run with --no-load")

    monkeypatch.setattr(cli.subprocess, "run", fail_run)

    result = CliRunner().invoke(
        cli.cli,
        ["install-scheduler", "--no-load"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "launchctl load" in result.output
