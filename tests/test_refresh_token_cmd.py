from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from schwab_mcp import cli


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.closed = False

    async def get_account_numbers(self) -> _FakeResponse:
        return self._response

    async def close_async_session(self) -> None:
        self.closed = True


def _patch(monkeypatch, captured: dict[str, Any], response: _FakeResponse) -> _FakeClient:
    client = _FakeClient(response)

    class DummyManager:
        def __init__(self, path: str) -> None:
            self.path = path
            captured["token_path"] = path

        def exists(self) -> bool:
            return True

    def fake_easy_client(**kwargs):
        captured["easy_client_kwargs"] = kwargs
        return client

    monkeypatch.setattr(cli.tokens, "Manager", DummyManager)
    monkeypatch.setattr(cli.schwab_auth, "easy_client", fake_easy_client)
    return client


def test_refresh_token_warms_silently(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}
    reminders: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli,
        "_maybe_send_login_reminder",
        lambda *a, **k: reminders.append(k),
    )
    client = _patch(monkeypatch, captured, _FakeResponse(200))

    result = CliRunner().invoke(
        cli.cli,
        [
            "refresh-token",
            "--token-path",
            str(tmp_path / "token.yaml"),
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    # Never discards a refreshable token, and never opens a browser.
    assert captured["easy_client_kwargs"]["max_token_age"] is None
    assert captured["easy_client_kwargs"]["interactive"] is False
    assert client.closed is True
    assert reminders == []  # no reminder while the token is still valid


def test_refresh_token_fires_reminder_when_expired(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}
    reminders: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli,
        "_maybe_send_login_reminder",
        lambda *a, **k: reminders.append(k),
    )
    _patch(
        monkeypatch,
        captured,
        _FakeResponse(401, text='{"error":"invalid_client"}'),
    )

    result = CliRunner().invoke(
        cli.cli,
        [
            "refresh-token",
            "--token-path",
            str(tmp_path / "token.yaml"),
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert len(reminders) == 1
    assert "auth" in reminders[0]["description"]


def test_refresh_token_requires_existing_token(monkeypatch, tmp_path):
    class MissingManager:
        def __init__(self, path: str) -> None:
            self.path = path

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(cli.tokens, "Manager", MissingManager)

    result = CliRunner().invoke(
        cli.cli,
        [
            "refresh-token",
            "--token-path",
            str(tmp_path / "token.yaml"),
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    # Message is emitted to stderr; combine both streams defensively.
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "schwab-mcp auth" in combined
