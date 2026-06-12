# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Normalize image messages into extraction-friendly text messages."""

from collections.abc import Callable
from typing import Any, Dict, List

from openviking.message import Message
from openviking.message.part import ImagePart, TextPart

IMAGE_DESCRIPTION_PROMPT = (
    "Describe this image for later memory extraction. Focus on durable, user-relevant "
    "details such as visible people, objects, places, actions, text, dates, and other "
    "facts that may matter in future conversations. Return only the description."
)


def message_has_image_part(message: Message) -> bool:
    return any(isinstance(part, ImagePart) for part in getattr(message, "parts", []))


def image_part_to_openai_content(part: ImagePart) -> Dict[str, Any]:
    image_url: Dict[str, Any] = {"url": part.url}
    if part.detail is not None:
        image_url["detail"] = part.detail
    return {"type": "image_url", "image_url": image_url}


def build_vision_description_messages(message: Message) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = [{"type": "text", "text": IMAGE_DESCRIPTION_PROMPT}]
    for part in getattr(message, "parts", []):
        if isinstance(part, TextPart) and part.text:
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append(image_part_to_openai_content(part))
    return [{"role": "user", "content": content}]


def fallback_image_description(message: Message) -> str:
    return ""


def normalize_vision_response(response: Any) -> str:
    content = getattr(response, "content", response)
    return str(content or "").strip()


def _original_text_parts(message: Message) -> List[TextPart]:
    return [
        TextPart(text=part.text)
        for part in getattr(message, "parts", [])
        if isinstance(part, TextPart) and part.text
    ]


async def describe_image_message(
    message: Message,
    *,
    vlm: Any,
    logger: Any = None,
) -> str:
    if vlm is None:
        return fallback_image_description(message)

    try:
        response = await vlm.get_vision_completion_async(
            messages=build_vision_description_messages(message),
            thinking=False,
        )
        description = normalize_vision_response(response)
        return description or fallback_image_description(message)
    except Exception as exc:
        if logger is not None:
            logger.warning("Failed to describe image message %s: %s", message.id, exc)
        return fallback_image_description(message)


async def replace_image_parts_with_descriptions(
    messages: List[Message],
    *,
    get_vlm: Callable[[], Any],
    logger: Any = None,
) -> List[Message]:
    prepared_messages: List[Message] = []
    for message in messages:
        if not message_has_image_part(message):
            prepared_messages.append(message)
            continue

        description = await describe_image_message(
            message,
            vlm=get_vlm(),
            logger=logger,
        )
        parts = _original_text_parts(message)
        if description:
            parts.append(TextPart(text=f"[Image description]: {description}"))
        if parts:
            prepared_messages.append(
                Message(
                    id=message.id,
                    role=message.role,
                    parts=parts,
                    peer_id=message.peer_id,
                    created_at=message.created_at,
                )
            )
    return prepared_messages
