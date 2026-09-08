# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""A guard that cannot resolve a host must not approve it.

`ensure_public_remote_target` decides whether an outbound fetch is allowed by
resolving the host and rejecting non-public addresses. When resolution failed it
returned, which permitted the request — the one outcome a security control
should never reach by accident.

The escape hatches must keep working, so they are pinned here too: both of them
answer before resolution is attempted, and neither needs DNS.
"""

import socket

import pytest

from openviking.utils import network_guard
from openviking_cli.exceptions import PermissionDeniedError


@pytest.fixture
def no_dns(monkeypatch):
    """Every lookup fails, as it would with a broken or unreachable resolver."""

    def _raise(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(network_guard.socket, "getaddrinfo", _raise)


@pytest.fixture
def resolves_to(monkeypatch):
    def _install(address: str):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        monkeypatch.setattr(
            network_guard.socket,
            "getaddrinfo",
            lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 0))],
        )

    return _install


@pytest.fixture(autouse=True)
def no_global_opt_out(monkeypatch):
    monkeypatch.setattr(network_guard, "_is_allow_private_networks", lambda: False)
    monkeypatch.setattr(network_guard, "_get_allowed_code_hosting_domains", lambda: set())


def test_unresolvable_host_is_refused(no_dns):
    with pytest.raises(PermissionDeniedError) as excinfo:
        network_guard.ensure_public_remote_target("https://internal.example/repo.git")

    message = str(excinfo.value)
    assert "internal.example" in message
    # The message has to say what to do next, or the refusal is just a wall.
    assert "retry" in message.lower()


def test_public_host_still_passes(resolves_to):
    resolves_to("93.184.216.34")
    network_guard.ensure_public_remote_target("https://example.com/repo.git")


def test_private_address_is_still_refused_with_its_own_message(resolves_to):
    resolves_to("10.0.0.7")
    with pytest.raises(PermissionDeniedError) as excinfo:
        network_guard.ensure_public_remote_target("https://internal.example/repo.git")

    assert "resolves to non-public address" in str(excinfo.value)


def test_allow_private_networks_answers_before_dns(no_dns, monkeypatch):
    """The opt-out must not start depending on a resolver it never needed."""
    monkeypatch.setattr(network_guard, "_is_allow_private_networks", lambda: True)

    network_guard.ensure_public_remote_target("https://internal.example/repo.git")


def test_configured_code_hosting_domain_answers_before_dns(no_dns, monkeypatch):
    monkeypatch.setattr(
        network_guard,
        "_get_allowed_code_hosting_domains",
        lambda: {"git.internal.example"},
    )

    network_guard.ensure_public_remote_target("https://git.internal.example/repo.git")


def test_loopback_by_name_is_still_refused_without_touching_dns(no_dns):
    with pytest.raises(PermissionDeniedError) as excinfo:
        network_guard.ensure_public_remote_target("http://localhost:1933/admin")

    assert "loopback" in str(excinfo.value)
