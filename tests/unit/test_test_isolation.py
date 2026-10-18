"""Regression checks for complete credential and network isolation."""

from __future__ import annotations

import socket

import pytest


@pytest.mark.parametrize("name", ["ZOT_AI_API_KEY", "ZOT_AI_BASE_URL", "ZOT_AI_MODEL", "ZOT_PROFILE"])
def test_live_service_environment_is_not_inherited(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from tests.conftest import isolate_test_environment

    monkeypatch.setenv(name, "unexpected-live-setting")
    with pytest.MonkeyPatch.context() as isolated:
        isolate_test_environment.__wrapped__(isolated)
        assert name not in os.environ, f"Inherited live configuration: {name}"


def test_unmocked_outbound_network_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from conftest import block_live_services

    monkeypatch.setattr(socket.socket, "connect", lambda *_args: None)
    with pytest.MonkeyPatch.context() as isolated:
        block_live_services.__wrapped__(isolated)
        with socket.socket() as client, pytest.raises(RuntimeError, match="network.*disabled"):
            client.connect(("203.0.113.1", 443))
