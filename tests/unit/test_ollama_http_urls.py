"""Check the Ollama tags request URL for supported host spellings."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import openviking_cli.utils.ollama as ollama


@pytest.mark.parametrize(
    ("host", "port", "expected_url"),
    [
        ("localhost", 11434, "http://localhost:11434/api/tags"),
        ("127.0.0.1", 8080, "http://127.0.0.1:8080/api/tags"),
        ("gpu-server", 11434, "http://gpu-server:11434/api/tags"),
        ("::1", 11434, "http://[::1]:11434/api/tags"),
        ("2001:db8::1", 8080, "http://[2001:db8::1]:8080/api/tags"),
        ("[::1]", 11434, "http://[::1]:11434/api/tags"),
    ],
)
@pytest.mark.parametrize("reader", ["check_ollama_running", "get_ollama_models"])
def test_tags_request_preserves_host(host, port, expected_url, reader):
    response = MagicMock()
    response.read.return_value = json.dumps({"models": [{"name": "example:latest"}]}).encode()
    with patch.object(ollama.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        result = getattr(ollama, reader)(host, port)

    assert result == (True if reader == "check_ollama_running" else ["example:latest"])
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.full_url == expected_url
    assert request.get_method() == "GET"
    assert urlopen.call_args.kwargs["timeout"] == (3 if reader == "check_ollama_running" else 5)


@pytest.mark.parametrize("reader", ["check_ollama_running", "get_ollama_models"])
def test_parsed_ipv6_endpoint_builds_a_valid_tags_url(reader):
    host, port = ollama.parse_ollama_url("http://[::1]:11434/v1")
    assert (host, port) == ("::1", 11434)
    response = MagicMock()
    response.read.return_value = b'{"models": []}'
    with patch.object(ollama.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        getattr(ollama, reader)(host, port)

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://[::1]:11434/api/tags"
