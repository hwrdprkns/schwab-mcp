from __future__ import annotations

from typing import Any

from schwab_mcp import cli


def test_reminder_noop_without_discord_config(monkeypatch):
    called: list[Any] = []
    monkeypatch.setattr(
        cli, "send_discord_notification", lambda **k: called.append(k)
    )

    # No token / channel -> no notification attempted.
    cli._maybe_send_login_reminder(None, None, title="t", description="d")
    cli._maybe_send_login_reminder("token", None, title="t", description="d")
    cli._maybe_send_login_reminder(None, 123, title="t", description="d")

    assert called == []


def test_reminder_sends_when_configured(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_notify(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "send_discord_notification", fake_notify)

    cli._maybe_send_login_reminder(
        "bot-token",
        99,
        title="Schwab weekly login",
        description="run auth",
    )

    assert captured["token"] == "bot-token"
    assert captured["channel_id"] == 99
    assert captured["title"] == "Schwab weekly login"


def test_reminder_swallows_errors(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("discord down")

    monkeypatch.setattr(cli, "send_discord_notification", boom)

    # Must not raise — a failed reminder cannot block the command outcome.
    cli._maybe_send_login_reminder(
        "bot-token", 99, title="t", description="d"
    )
