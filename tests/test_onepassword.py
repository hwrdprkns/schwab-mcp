from __future__ import annotations

from typing import Any

from schwab_mcp import onepassword


class _Result:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_op_read_returns_none_when_cli_missing(monkeypatch):
    monkeypatch.setattr(onepassword.shutil, "which", lambda _name: None)
    assert onepassword.op_read("op://Private/SCHWAB_OAUTH_APP/client_id") is None


def test_op_read_returns_value_on_success(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _Result(0, stdout="the-client-id")

    monkeypatch.setattr(onepassword.shutil, "which", lambda _name: "/usr/bin/op")
    monkeypatch.setattr(onepassword.subprocess, "run", fake_run)

    value = onepassword.op_read("op://Private/SCHWAB_OAUTH_APP/client_id")
    assert value == "the-client-id"
    assert captured["args"][:2] == ["/usr/bin/op", "read"]
    assert "op://Private/SCHWAB_OAUTH_APP/client_id" in captured["args"]


def test_op_read_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(onepassword.shutil, "which", lambda _name: "/usr/bin/op")
    monkeypatch.setattr(
        onepassword.subprocess, "run", lambda *a, **k: _Result(1, stdout="")
    )
    assert onepassword.op_read("op://Private/SCHWAB_OAUTH_APP/client_secret") is None


def test_op_read_swallows_subprocess_errors(monkeypatch):
    def boom(*a, **k):
        raise OSError("op blew up")

    monkeypatch.setattr(onepassword.shutil, "which", lambda _name: "/usr/bin/op")
    monkeypatch.setattr(onepassword.subprocess, "run", boom)
    assert onepassword.op_read("op://Private/SCHWAB_OAUTH_APP/client_id") is None


def test_references_are_env_overridable(monkeypatch):
    assert onepassword.client_id_reference() == onepassword.DEFAULT_CLIENT_ID_REF
    monkeypatch.setenv(onepassword.CLIENT_ID_REF_ENV, "op://Work/Schwab/id")
    assert onepassword.client_id_reference() == "op://Work/Schwab/id"


def test_resolve_only_fills_missing(monkeypatch):
    reads: list[str] = []

    def fake_op_read(ref, **kwargs):
        reads.append(ref)
        return "resolved-secret"

    monkeypatch.setattr(onepassword, "op_read", fake_op_read)

    # client_id already provided -> only the secret is looked up.
    cid, secret = onepassword.resolve_credentials("explicit-id", None)
    assert cid == "explicit-id"
    assert secret == "resolved-secret"
    assert reads == [onepassword.client_secret_reference()]
