# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test that provider instruction correctly instructs LLM.
"""

from openviking.message import ImagePart, Message, TextPart, ToolPart
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.vision_message_normalizer import IMAGE_DESCRIPTION_PROMPT


class TestProviderInstruction:
    """Test the provider instruction contains correct instructions."""

    def test_instruction_contains_read_before_edit_instructions(self):
        """Test that instruction explicitly tells LLM to read files before editing."""
        # Create provider with mock messages
        mock_messages = []
        provider = SessionExtractContextProvider(messages=mock_messages)

        instruction = provider.instruction()

        # Check for critical instructions
        assert (
            "Before editing ANY existing memory file, you MUST first read its complete content"
            in instruction
        )
        assert (
            "ONLY read URIs that are explicitly listed in ls/search tool results, returned by previous tool calls"
            in instruction
        )

    def test_instruction_contains_output_language(self):
        """Test that instruction includes the output language setting."""
        mock_messages = []
        provider = SessionExtractContextProvider(messages=mock_messages)

        instruction = provider.instruction()

        # Check that output language instruction is present
        assert "Target Output Language" in instruction
        assert "All memory content MUST be written in" in instruction

    def test_instruction_explains_peer_memory_routing(self):
        provider = SessionExtractContextProvider(messages=[])

        instruction = provider.instruction()

        assert "Peer Memory" in instruction
        assert "profile/preferences/entities/events" in instruction
        assert "cases/patterns/tools/skills" in instruction

    def test_instruction_omits_resource_uri_handling_without_resource_uri(self):
        provider = SessionExtractContextProvider(
            messages=[Message(id="m1", role="user", parts=[TextPart("我喜欢越前龙马。")])]
        )

        instruction = provider.instruction()

        assert "Resource URI Handling" not in instruction
        assert "Affected memory URIs" not in instruction

    def test_instruction_includes_resource_uri_handling_for_user_scoped_resource_uri(self):
        provider = SessionExtractContextProvider(
            messages=[
                Message(
                    id="m1",
                    role="user",
                    parts=[
                        TextPart(
                            "这张图是越前龙马："
                            "viking://user/ryoma/peers/fuji/resources/images/yueqian_jpeg"
                        )
                    ],
                )
            ]
        )

        instruction = provider.instruction()

        assert "Resource URI Handling" in instruction
        assert "viking://user/{user_id}/resources/..." in instruction
        assert "viking://user/{user_id}/peers/{peer_id}/resources/..." in instruction
        assert (
            "system-generated `## Resource Deletion` block's `Affected memory URIs`" in instruction
        )


class TestSkillToolCallExposure:
    def test_assemble_conversation_includes_skill_tool_call(self):
        messages = [
            Message(
                id="m1",
                role="assistant",
                parts=[
                    TextPart("Running a skill."),
                    ToolPart(
                        tool_id="tool_1",
                        tool_name="read",
                        tool_uri="viking://session/test/tools/tool_1",
                        skill_uri="viking://user/skills/create_presentation",
                        tool_input={"file_path": "/skills/ppt/SKILL.md"},
                        tool_output="ok",
                        tool_status="completed",
                        duration_ms=123,
                    ),
                ],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)

        conversation = provider._assemble_conversation(messages)

        assert "[ToolCall]" in conversation
        assert '"skill_name": "create_presentation"' in conversation

    def test_assemble_conversation_without_skill_tool_call_has_no_skill_name(self):
        messages = [
            Message(
                id="m1",
                role="assistant",
                parts=[
                    TextPart("Running a tool."),
                    ToolPart(
                        tool_id="tool_1",
                        tool_name="read",
                        tool_uri="viking://session/test/tools/tool_1",
                        tool_input={"file_path": "README.md"},
                        tool_output="ok",
                        tool_status="completed",
                        duration_ms=123,
                    ),
                ],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)

        conversation = provider._assemble_conversation(messages)

        assert "[ToolCall]" in conversation
        assert '"tool_name": "read"' in conversation
        assert '"skill_name":' not in conversation

    def test_assemble_conversation_uses_peer_id_when_present(self):
        messages = [
            Message(
                id="m1",
                role="user",
                parts=[TextPart("My invoice is still missing.")],
                peer_id="web-visitor-alice",
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)

        conversation = provider._assemble_conversation(messages)

        assert "[0][user][web-visitor-alice]" in conversation
        assert "[0][user][default]" not in conversation

    def test_detect_language_only_uses_text_parts(self):
        messages = [
            Message(
                id="m1",
                role="assistant",
                parts=[TextPart("Please keep the memory in English.")],
            ),
            Message(
                id="m2",
                role="assistant",
                parts=[
                    ToolPart(
                        tool_id="tool_1",
                        tool_name="read",
                        tool_uri="viking://session/test/tools/tool_1",
                        tool_input={"file_path": "README.md"},
                        tool_output="这是中文工具输出",
                        tool_status="completed",
                    )
                ],
            ),
        ]

        provider = SessionExtractContextProvider(messages=messages)

        assert provider._detect_language() == "en"

    def test_detect_language_prefers_user_text_over_assistant_text(self):
        messages = [
            Message(
                id="m1",
                role="user",
                parts=[TextPart("请把记忆保持为中文，继续优化。")],
            ),
            Message(
                id="m2",
                role="assistant",
                parts=[TextPart("한국어 응답이 섞였습니다")],
            ),
        ]

        provider = SessionExtractContextProvider(messages=messages)

        assert provider._detect_language() == "zh-CN"

    async def test_prepare_extraction_messages_replaces_image_part_with_vlm_description(self):
        class FakeVisionVLM:
            def __init__(self):
                self.messages = None

            async def get_vision_completion_async(self, **kwargs):
                self.messages = kwargs.get("messages")
                return "A high level image description."

        messages = [
            Message(
                id="m1",
                role="user",
                parts=[
                    TextPart("Please remember this image."),
                    ImagePart(url="https://example.com/image.png", detail="auto"),
                ],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)
        fake_vlm = FakeVisionVLM()
        provider._vision_vlm = fake_vlm

        await provider.prepare_extraction_messages()
        prompt_message = provider._build_conversation_message()

        assert "Please remember this image." in prompt_message["content"]
        assert "A high level image description." in prompt_message["content"]
        assert "https://example.com/image.png" not in prompt_message["content"]
        assert fake_vlm.messages == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": IMAGE_DESCRIPTION_PROMPT,
                    },
                    {"type": "text", "text": "Please remember this image."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://example.com/image.png",
                            "detail": "auto",
                        },
                    },
                ],
            }
        ]

    async def test_prepare_extraction_messages_skips_image_only_without_description(self):
        messages = [
            Message(
                id="m1",
                role="user",
                parts=[ImagePart(url="https://example.com/private-family-photo.png")],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)
        provider._vision_vlm = None

        await provider.prepare_extraction_messages()
        prompt_message = provider._build_conversation_message()

        assert "[Image description]" not in prompt_message["content"]
        assert "https://example.com/private-family-photo.png" not in prompt_message["content"]

    async def test_prepare_extraction_messages_keeps_text_when_image_is_undescribed(self):
        messages = [
            Message(
                id="m1",
                role="user",
                parts=[
                    TextPart("Please remember the surrounding text."),
                    ImagePart(url="https://example.com/private-family-photo.png"),
                ],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)
        provider._vision_vlm = None

        await provider.prepare_extraction_messages()
        prompt_message = provider._build_conversation_message()

        assert "Please remember the surrounding text." in prompt_message["content"]
        assert "[Image description]" not in prompt_message["content"]
        assert "https://example.com/private-family-photo.png" not in prompt_message["content"]

    async def test_prepare_extraction_messages_does_not_replace_caller_message_list(self):
        messages = [
            Message(
                id="m1",
                role="user",
                parts=[
                    TextPart("Please remember this image."),
                    ImagePart(url="https://example.com/image.png"),
                ],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)
        provider._vision_vlm = None

        await provider.prepare_extraction_messages()

        assert len(messages) == 1
        assert any(isinstance(part, ImagePart) for part in messages[0].parts)
        assert provider.messages is not messages
