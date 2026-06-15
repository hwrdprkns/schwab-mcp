from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from schwab_mcp import cli


def test_auth_resolves_credentials_from_1password(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}

    # No credentials file values.
    monkeypatch.setattr(cli.tokens, "load_credentials", lambda _p: {})
    # 1Password supplies both.
    monkeypatch.setattr(
        cli.onepassword,
        "resolve_credentials",
        lambda cid, secret: ("op-client-id", "op-client-secret"),
    )

    class DummyManager:
        def __init__(self, path: str) -> None:
            self.path = path

    def fake_easy_client(**kwargs):
        captured["easy_client_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(cli.tokens, "Manager", DummyManager)
    monkeypatch.setattr(cli.schwab_auth, "easy_client", fake_easy_client)

    result = CliRunner().invoke(
        cli.cli,
        ["auth", "--token-path", str(tmp_path / "token.yaml")],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured["easy_client_kwargs"]["client_id"] == "op-client-id"
    assert captured["easy_client_kwargs"]["client_secret"] == "op-client-secret"


def test_explicit_flags_take_precedence_over_1password(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}

    monkeypatch.setattr(cli.tokens, "load_credentials", lambda _p: {})

    def fail_resolve(cid, secret):  # pragma: no cover - must not be called
        raise AssertionError("1Password should not be consulted when flags given")

    monkeypatch.setattr(cli.onepassword, "resolve_credentials", fail_resolve)

    class DummyManager:
        def __init__(self, path: str) -> None:
            self.path = path

    monkeypatch.setattr(cli.tokens, "Manager", DummyManager)
    monkeypatch.setattr(
        cli.schwab_auth,
        "easy_client",
        lambda **kwargs: captured.setdefault("kwargs", kwargs) or object(),
    )

    result = CliRunner().invoke(
        cli.cli,
        [
            "auth",
            "--token-path",
            str(tmp_path / "token.yaml"),
            "--client-id",
            "flag-id",
            "--client-secret",
            "flag-secret",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert captured["kwargs"]["client_id"] == "flag-id"
    assert captured["kwargs"]["client_secret"] == "flag-secret"
