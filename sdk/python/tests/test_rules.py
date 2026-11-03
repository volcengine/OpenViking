from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openviking_sdk import AsyncHTTPClient


@pytest.mark.asyncio
async def test_rule_compile_ingests_local_file_before_submission(tmp_path):
    source = tmp_path / "rules.md"
    source.write_text("Always run focused tests.", encoding="utf-8")
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._upload_temp_file = AsyncMock(return_value="upload_rules")
    client._request = AsyncMock(return_value=SimpleNamespace())
    client._handle_response = lambda _response: {"task_id": "rul_123"}

    result = await client.rule_compile(
        "Compile these rules",
        options={"sources": [str(source), "https://example.com/team-rules.md"]},
    )

    assert result == {"task_id": "rul_123"}
    client._upload_temp_file.assert_awaited_once_with(str(source))
    client._request.assert_awaited_once_with(
        "POST",
        "/api/v1/rules/compile",
        json={
            "instruction": "Compile these rules",
            "sources": [
                {
                    "temp_file_id": "upload_rules",
                    "source_name": "rules.md",
                },
                {"path": "https://example.com/team-rules.md"},
            ],
        },
    )


@pytest.mark.asyncio
async def test_rule_compile_passes_complete_add_resource_source():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=SimpleNamespace())
    client._handle_response = lambda _response: {"task_id": "rul_456"}

    await client.rule_compile(
        options={
            "sources": [
                {
                    "path": "space:home",
                    "add_type": "feishu",
                    "to": "viking://resources/team-rules",
                    "args": {"feishu_access_token": "token"},
                    "strict": True,
                    "processing_mode": "vectors_only",
                    "tags": ["rules"],
                    "tag_mode": "append",
                }
            ]
        }
    )

    payload = client._request.await_args.kwargs["json"]
    assert payload["sources"] == [
        {
            "path": "space:home",
            "add_type": "feishu",
            "to": "viking://resources/team-rules",
            "args": {"feishu_access_token": "token"},
            "strict": True,
            "processing_mode": "vectors_only",
            "tags": ["rules"],
            "tag_mode": "append",
        }
    ]


@pytest.mark.asyncio
async def test_rule_search_posts_threshold_options():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=SimpleNamespace())
    client._handle_response = lambda _response: {"rules": []}

    result = await client.rule_search(
        "prepare a release",
        options={
            "rule_probability_threshold": 0.8,
        },
    )

    assert result == {"rules": []}
    client._request.assert_awaited_once_with(
        "POST",
        "/api/v1/rules/search",
        json={
            "task_context": "prepare a release",
            "rule_probability_threshold": 0.8,
        },
    )
