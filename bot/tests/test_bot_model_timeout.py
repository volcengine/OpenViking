import json
from types import SimpleNamespace

import pytest
from vikingbot.cli.commands import _make_provider
from vikingbot.config import loader


def _chat_response(content: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


@pytest.mark.parametrize(
    "agent_options, vlm_options, expected_timeout",
    [
        pytest.param({}, {"timeout": 180.0}, 180.0, id="inherit"),
        pytest.param({}, {}, 60.0, id="default"),
        pytest.param({"timeout": 45.0}, {"timeout": 180.0}, 45.0, id="override"),
    ],
)
@pytest.mark.asyncio
async def test_configured_timeout_reaches_chat_request(
    tmp_path, monkeypatch, agent_options, vlm_options, expected_timeout
):
    path = tmp_path / "ov.conf"
    path.write_text(
        json.dumps(
            {
                "vlm": {
                    "provider": "litellm",
                    "model": "openai/gpt-4o-mini",
                    "api_key": "test-key",
                    **vlm_options,
                },
                "bot": {"agents": agent_options},
            }
        )
    )
    monkeypatch.setattr(loader, "CONFIG_PATH", path)
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(path))
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _chat_response()

    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", fake_acompletion)

    provider = _make_provider(loader.load_config())
    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    assert captured["timeout"] == expected_timeout
