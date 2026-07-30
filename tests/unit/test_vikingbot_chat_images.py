import base64

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from vikingbot.agent.context import ContextBuilder
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import OpenAPIChannel, OpenAPIChannelConfig
from vikingbot.channels.openapi_models import ChatRequest

from openviking.utils.multimodal import redact_image_data_urls


def _data_url(mime_type: str, data: bytes) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode()}"


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_chat_request_accepts_text_only():
    request = ChatRequest(message="hello")

    assert request.message == "hello"
    assert request.images == []


def test_chat_request_accepts_https_and_base64_images_without_text():
    inline_url = _data_url("image/png", PNG_SIGNATURE)

    request = ChatRequest(
        images=[
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.png", "detail": "high"},
            },
            {
                "type": "image_url",
                "image_url": {"url": inline_url},
            },
        ]
    )

    assert request.message == ""
    assert request.images[0].image_url.detail == "high"
    assert request.images[1].image_url.url == inline_url


def test_chat_request_passes_https_url_without_fetching_remote_content():
    remote_url = "https://example.com/image.svg?version=1"

    request = ChatRequest(
        images=[
            {
                "type": "image_url",
                "image_url": {"url": remote_url},
            }
        ]
    )

    assert request.images[0].image_url.url == remote_url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/image.png",
        "file:///tmp/image.png",
        "data:image/svg+xml;base64,PHN2Zz4=",
        _data_url("image/jpeg", PNG_SIGNATURE),
        "data:image/png;base64,not-valid-base64!",
    ],
)
def test_chat_request_rejects_unsafe_or_invalid_image_urls(url):
    with pytest.raises(ValidationError):
        ChatRequest(images=[{"type": "image_url", "image_url": {"url": url}}])


def test_chat_request_requires_text_or_image():
    with pytest.raises(ValidationError, match="message or at least one image is required"):
        ChatRequest()


def test_chat_request_limits_image_count():
    images = [
        {
            "type": "image_url",
            "image_url": {"url": f"https://example.com/image-{index}.png"},
        }
        for index in range(5)
    ]

    with pytest.raises(ValidationError):
        ChatRequest(images=images)


@pytest.mark.asyncio
async def test_chat_route_does_not_reflect_invalid_base64_input(tmp_path):
    secret = "sensitive-image-content-" * 10_000
    data_url = _data_url("image/png", secret.encode())
    app = FastAPI()
    channel = OpenAPIChannel(
        config=OpenAPIChannelConfig(),
        bus=MessageBus(),
        workspace_path=tmp_path,
        app=app,
    )
    channel._setup_routes()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.post(
            "/bot/v1/chat",
            json={
                "images": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    }
                ]
            },
        )

    assert response.status_code == 422
    assert data_url not in response.text
    assert base64.b64encode(secret.encode()).decode() not in response.text
    assert len(response.content) < 2_000
    assert response.json() == {
        "detail": [
            {
                "type": "value_error",
                "loc": ["body", "images", 0, "image_url", "url"],
                "msg": "Value error, image data does not have a supported image signature",
            }
        ]
    }


def test_openapi_request_media_preserves_openai_image_parts():
    request = ChatRequest(
        message="describe",
        images=[
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.png", "detail": "low"},
            }
        ],
    )
    channel = OpenAPIChannel.__new__(OpenAPIChannel)

    assert channel._request_media(request) == [
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.png", "detail": "low"},
        }
    ]


def test_openapi_request_media_omits_unspecified_detail():
    request = ChatRequest(
        images=[
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.png"},
            }
        ]
    )
    channel = OpenAPIChannel.__new__(OpenAPIChannel)

    assert channel._request_media(request) == [
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.png"},
        }
    ]


def test_openapi_request_content_uses_image_placeholder_for_image_only_request():
    request = ChatRequest(
        images=[
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.png"},
            }
        ]
    )
    channel = OpenAPIChannel.__new__(OpenAPIChannel)

    assert channel._request_content(request) == "[Image]"


def test_context_builder_passes_remote_and_inline_images_to_model():
    inline_url = _data_url("image/png", PNG_SIGNATURE)
    builder = ContextBuilder.__new__(ContextBuilder)

    content = builder._build_user_content(
        "compare",
        [
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.png", "detail": "high"},
            },
            inline_url,
        ],
    )

    assert content == [
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/image.png", "detail": "high"},
        },
        {"type": "image_url", "image_url": {"url": inline_url}},
        {"type": "text", "text": "compare"},
    ]


def test_context_builder_keeps_trusted_local_file_support(tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(PNG_SIGNATURE)
    builder = ContextBuilder.__new__(ContextBuilder)

    content = builder._build_user_content("describe", [str(image_path)])

    assert isinstance(content, list)
    assert content[0]["image_url"]["url"] == _data_url("image/png", PNG_SIGNATURE)
    assert content[-1] == {"type": "text", "text": "describe"}


def test_redact_image_data_urls_does_not_mutate_request_messages():
    inline_url = _data_url("image/png", PNG_SIGNATURE)
    messages = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": inline_url}}],
        }
    ]

    redacted = redact_image_data_urls(messages)

    assert "redacted" in redacted[0]["content"][0]["image_url"]["url"]
    assert messages[0]["content"][0]["image_url"]["url"] == inline_url
