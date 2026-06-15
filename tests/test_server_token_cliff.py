from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from schwab_mcp import cli


class _Client:
    def __init__(self, age: int) -> None:
        self._age = age

    def token_age(self) -> int:
        return self._age

    async def close_async_session(self) -> None:
        return None


def _patch(monkeypatch, captured: dict[str, Any], age: int) -> None:
    client = _Client(age)
    monkeypatch.setattr(cli, "AsyncClient", _Client)

    def fake_easy_client(**kwargs):
        captured["easy_client_kwargs"] = kwargs
        return client

    monkeypatch.setattr(cli.schwab_auth, "easy_client", fake_easy_client)
    monkeypatch.setattr(
        cli, "_maybe_send_login_reminder", lambda *a, **k: captured.setdefault("reminders", []).append(k)
    )

    class FakeServer:
        def __init__(self, *a, **k):
            captured["server_built"] = True

        async def run(self):
            captured["run_called"] = True

    monkeypatch.setattr(cli, "SchwabMCPServer", FakeServer)
    monkeypatch.setattr(
        cli.anyio, "run", lambda func, backend="asyncio": captured.setdefault("ran", True)
    )


def _invoke():
    return CliRunner().invoke(
        cli.cli,
        ["server", "--client-id", "cid", "--client-secret", "secret"],
        catch_exceptions=False,
    )


def test_server_refuses_past_seven_day_cliff(monkeypatch):
    captured: dict[str, Any] = {}
    # 1s past the effective re-auth threshold.
    _patch(monkeypatch, captured, cli.schwab_auth.REAUTH_THRESHOLD_SECONDS + 1)

    result = _invoke()

    assert result.exit_code == 1
    assert captured.get("server_built") is None  # never started serving
    assert len(captured.get("reminders", [])) == 1


def test_server_starts_below_cliff_even_when_stale(monkeypatch):
    captured: dict[str, Any] = {}
    # 5.9 days old: older than the OLD 5-day gate, but still refreshable.
    _patch(monkeypatch, captured, int(5.9 * 24 * 60 * 60))

    result = _invoke()

    assert result.exit_code == 0
    assert captured["server_built"] is True
    assert captured["easy_client_kwargs"]["max_token_age"] is None
    assert "reminders" not in captured  # no premature reminder
