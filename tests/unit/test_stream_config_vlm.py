# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM stream configuration support."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openviking.utils.model_retry as model_retry
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from tests.unit._streaming_support import (
    AcloseOnlyStream,
    DetailedMockUsage,
    MockChunk,
    MockUsage,
    NonAwaitableCloseStream,
    ScriptedAsyncStream,
    ScriptedSyncStream,
)


class TestVLMStreamConfig:
    """Test stream configuration is passed to OpenAI API calls."""

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_stream_false_by_default(self, mock_openai_class):
        """stream should default to False."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
            }
        )

        vlm.get_completion("test prompt")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is False

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_stream_true_passed_to_api(self, mock_openai_class):
        """stream=True should be passed to API call."""
        mock_client = MagicMock()
        # Simulate streaming response
        chunks = [
            MockChunk(content="Hello"),
            MockChunk(content=" world"),
            MockChunk(content="!", usage=MockUsage(prompt_tokens=10, completion_tokens=3)),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = vlm.get_completion("test prompt")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Hello world!"

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_stream_false_uses_non_streaming_path(self, mock_openai_class):
        """stream=False should use non-streaming response handling."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Non-streaming response"
        mock_response.usage = MockUsage(prompt_tokens=5, completion_tokens=10)
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": False,
            }
        )

        result = vlm.get_completion("test prompt")

        assert result == "Non-streaming response"
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is False

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    async def test_async_stream_true(self, mock_async_openai_class):
        """stream=True should work with async methods."""
        mock_client = MagicMock()

        async def async_generator():
            chunks = [
                MockChunk(content="Async"),
                MockChunk(content=" result"),
                MockChunk(content="!", usage=MockUsage(prompt_tokens=8, completion_tokens=4)),
            ]
            for chunk in chunks:
                yield chunk

        mock_client.chat.completions.create = AsyncMock(return_value=async_generator())
        mock_async_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = await vlm.get_completion_async("test prompt")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Async result!"

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    async def test_async_stream_false(self, mock_async_openai_class):
        """stream=False should work with async methods."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Async non-streaming"
        mock_response.usage = MockUsage(prompt_tokens=5, completion_tokens=5)

        async def mock_create(*args, **kwargs):
            return mock_response

        mock_client.chat.completions.create = mock_create
        mock_async_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": False,
            }
        )

        result = await vlm.get_completion_async("test prompt")

        assert result == "Async non-streaming"

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_vision_completion_stream_true(self, mock_openai_class):
        """stream=True should work with vision completion."""
        mock_client = MagicMock()
        chunks = [
            MockChunk(content="Image"),
            MockChunk(content=" description"),
            MockChunk(content=".", usage=MockUsage(prompt_tokens=20, completion_tokens=5)),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = vlm.get_vision_completion("describe this", ["http://example.com/image.jpg"])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Image description."

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    async def test_vision_completion_async_stream_true(self, mock_async_openai_class):
        """stream=True should work with async vision completion."""
        mock_client = MagicMock()

        async def async_generator():
            chunks = [
                MockChunk(content="Async"),
                MockChunk(content=" image"),
                MockChunk(
                    content=" result", usage=MockUsage(prompt_tokens=15, completion_tokens=6)
                ),
            ]
            for chunk in chunks:
                yield chunk

        mock_client.chat.completions.create = AsyncMock(return_value=async_generator())
        mock_async_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = await vlm.get_vision_completion_async(
            "describe this", ["http://example.com/image.jpg"]
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Async image result"


class TestVLMBaseStreamConfig:
    """Test VLMBase extracts stream from config."""

    def test_stream_defaults_to_false(self):
        """VLMBase should default stream to False."""

        class StubVLM(OpenAIVLM):
            def get_completion(self, prompt, thinking=False):
                return ""

            async def get_completion_async(self, prompt, thinking=False):
                return ""

            def get_vision_completion(self, prompt, images, thinking=False):
                return ""

            async def get_vision_completion_async(self, prompt, images, thinking=False):
                return ""

        vlm = StubVLM(
            {
                "api_key": "sk-test",
            }
        )

        assert vlm.stream is False

    def test_stream_extracted_from_config(self):
        """VLMBase should extract stream from config."""

        class StubVLM(OpenAIVLM):
            def get_completion(self, prompt, thinking=False):
                return ""

            async def get_completion_async(self, prompt, thinking=False):
                return ""

            def get_vision_completion(self, prompt, images, thinking=False):
                return ""

            async def get_vision_completion_async(self, prompt, images, thinking=False):
                return ""

        vlm = StubVLM(
            {
                "api_key": "sk-test",
                "stream": True,
            }
        )

        assert vlm.stream is True


class TestVLMConfigStream:
    """Test VLMConfig passes stream to VLM instance."""

    def test_vlm_config_accepts_stream(self):
        """VLMConfig should accept stream field."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=True,
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "api_base": "https://api.openai.com/v1",
                }
            },
        )

        assert config.stream is True

    def test_vlm_config_stream_defaults_to_false(self):
        """VLMConfig should default stream to False."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        assert config.stream is False

    def test_vlm_config_stream_passed_to_vlm_dict(self):
        """VLMConfig should pass stream to _build_vlm_config_dict."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=True,
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_stream_migrated_to_providers(self):
        """VLMConfig should migrate stream to providers structure."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            stream=True,
        )

        # Verify stream is migrated to providers structure
        assert config.providers["openai"]["stream"] is True

        # Verify _build_vlm_config_dict uses the migrated value
        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_stream_in_providers_takes_precedence(self):
        """stream in providers config should take precedence over flat config."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=False,  # flat config is False
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "stream": True,  # provider config is True, should take precedence
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_max_retries_defaults_to_three(self):
        """VLMConfig should default max_retries to 3."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        assert config.max_retries == 3
        assert config._build_vlm_config_dict()["max_retries"] == 3


class TestStreamingResponseProcessing:
    """Test streaming response processing logic."""

    def test_process_streaming_response_with_content(self):
        """_process_streaming_response should extract content from chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        chunks = [
            MockChunk(content="Hello"),
            MockChunk(content=" "),
            MockChunk(content="world"),
        ]

        result = vlm._process_streaming_response(iter(chunks))
        assert result == "Hello world"

    def test_process_streaming_response_with_usage(self):
        """_process_streaming_response should extract usage from chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        chunks = [
            MockChunk(content="Hello", usage=MockUsage(prompt_tokens=10, completion_tokens=5)),
        ]

        with patch.object(vlm, "update_token_usage") as mock_update:
            vlm._process_streaming_response(iter(chunks))

            mock_update.assert_called_once_with(
                model_name="gpt-4o-mini",
                provider="openai",
                prompt_tokens=10,
                completion_tokens=5,
                duration_seconds=0.0,
                prompt_cached_tokens=0,
                completion_reasoning_tokens=0,
            )

    def test_process_streaming_response_empty_chunks(self):
        """_process_streaming_response should handle empty chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        result = vlm._process_streaming_response(iter([]))
        assert result == ""

    @pytest.mark.asyncio
    async def test_process_streaming_response_async(self):
        """_process_streaming_response_async should extract content from async chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        async def async_chunks():
            yield MockChunk(content="Async")
            yield MockChunk(content=" result")
            yield MockChunk(content="!", usage=MockUsage(prompt_tokens=5, completion_tokens=3))

        result = await vlm._process_streaming_response_async(async_chunks())
        assert result == "Async result!"

    @pytest.mark.asyncio
    async def test_process_streaming_response_async_with_usage(self):
        """_process_streaming_response_async should extract usage from chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        async def async_chunks():
            yield MockChunk(content="Test")
            yield MockChunk(content="", usage=MockUsage(prompt_tokens=15, completion_tokens=8))

        with patch.object(vlm, "update_token_usage") as mock_update:
            await vlm._process_streaming_response_async(async_chunks())

            mock_update.assert_called_once_with(
                model_name="gpt-4o-mini",
                provider="openai",
                prompt_tokens=15,
                completion_tokens=8,
                duration_seconds=0.0,
                prompt_cached_tokens=0,
                completion_reasoning_tokens=0,
            )


def _is_marked(error):
    check = getattr(model_retry, "is_vlm_error_non_retryable", None)
    assert callable(check), "model_retry must define is_vlm_error_non_retryable"
    return check(error)


class TestToolStreamingPreflight:
    def test_text_sync_rejects_tools_before_builder_or_client(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True})
        builder = MagicMock(side_effect=AssertionError("request builder reached"))
        client = MagicMock(side_effect=AssertionError("client reached"))
        vlm._build_text_kwargs = builder
        vlm.get_client = client
        with pytest.raises(NotImplementedError, match="stream.*tools|tools.*stream"):
            vlm.get_completion("test", tools=[{"type": "function"}])
        builder.assert_not_called()
        client.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_async_rejects_tools_before_builder_or_client(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True})
        builder = MagicMock(side_effect=AssertionError("request builder reached"))
        client = MagicMock(side_effect=AssertionError("client reached"))
        vlm._build_text_kwargs = builder
        vlm.get_async_client = client
        with pytest.raises(NotImplementedError, match="stream.*tools|tools.*stream"):
            await vlm.get_completion_async("test", tools=[{"type": "function"}])
        builder.assert_not_called()
        client.assert_not_called()

    def test_vision_sync_rejects_tools_before_builder_client_or_image_io(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True})
        builder = MagicMock(side_effect=AssertionError("request builder reached"))
        client = MagicMock(side_effect=AssertionError("client reached"))
        image_io = MagicMock(side_effect=AssertionError("vision I/O reached"))
        vlm._build_vision_kwargs = builder
        vlm.get_client = client
        vlm._prepare_image = image_io
        with pytest.raises(NotImplementedError, match="stream.*tools|tools.*stream"):
            vlm.get_vision_completion(
                "test",
                images=[object()],
                tools=[{"type": "function"}],
            )
        builder.assert_not_called()
        client.assert_not_called()
        image_io.assert_not_called()

    @pytest.mark.asyncio
    async def test_vision_async_rejects_tools_before_builder_client_or_image_io(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True})
        builder = MagicMock(side_effect=AssertionError("request builder reached"))
        client = MagicMock(side_effect=AssertionError("client reached"))
        image_io = MagicMock(side_effect=AssertionError("vision I/O reached"))
        vlm._build_vision_kwargs = builder
        vlm.get_async_client = client
        vlm._prepare_image = image_io
        with pytest.raises(NotImplementedError, match="stream.*tools|tools.*stream"):
            await vlm.get_vision_completion_async(
                "test",
                images=[object()],
                tools=[{"type": "function"}],
            )
        builder.assert_not_called()
        client.assert_not_called()
        image_io.assert_not_called()


@pytest.mark.parametrize("builder_name", ["_build_text_kwargs", "_build_vision_kwargs"])
@pytest.mark.parametrize("stream", [False, True])
def test_every_openai_request_builder_carries_explicit_stream_flag(builder_name, stream):
    vlm = OpenAIVLM({"api_key": "sk-test", "stream": stream})
    kwargs = getattr(vlm, builder_name)()
    assert kwargs["stream"] is stream


class TestStreamingReducerContract:
    def test_sync_reducer_keeps_string_content_and_only_last_usage(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        first_usage = DetailedMockUsage(2, 1, cached_tokens=1)
        last_usage = DetailedMockUsage(7, 5, cached_tokens=3, reasoning_tokens=4)
        stream = ScriptedSyncStream(
            [
                MockChunk(content="A", usage=first_usage),
                MockChunk(content=17),
                MockChunk(),
                MockChunk(content="B", usage=last_usage),
            ]
        )
        with patch.object(vlm, "update_token_usage") as update:
            result = vlm._process_streaming_response(stream)
        assert result == "AB"
        update.assert_called_once_with(
            model_name="gpt-4o-mini",
            provider="openai",
            prompt_tokens=7,
            completion_tokens=5,
            duration_seconds=0.0,
            prompt_cached_tokens=3,
            completion_reasoning_tokens=4,
        )
        assert stream.close_count == 1

    @pytest.mark.asyncio
    async def test_async_reducer_matches_content_empty_and_last_usage_contract(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        last_usage = DetailedMockUsage(9, 6, cached_tokens=2, reasoning_tokens=5)
        stream = ScriptedAsyncStream(
            [
                MockChunk(),
                MockChunk(content="async "),
                MockChunk(usage=last_usage),
                MockChunk(content="ok"),
            ]
        )
        with patch.object(vlm, "update_token_usage") as update:
            result = await vlm._process_streaming_response_async(stream)
        assert result == "async ok"
        update.assert_called_once_with(
            model_name="gpt-4o-mini",
            provider="openai",
            prompt_tokens=9,
            completion_tokens=6,
            duration_seconds=0.0,
            prompt_cached_tokens=2,
            completion_reasoning_tokens=5,
        )
        assert stream.close_count == 1

    def test_sync_iterator_error_before_first_event_is_unmarked_and_closed_once(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        error = RuntimeError("503 before first event")
        stream = ScriptedSyncStream([error])
        with pytest.raises(RuntimeError) as raised:
            vlm._process_streaming_response(stream)
        assert raised.value is error
        assert _is_marked(error) is False
        assert stream.close_count == 1

    def test_sync_iterator_error_after_event_is_marked_and_closed_once(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        error = RuntimeError("503 after event")
        stream = ScriptedSyncStream([MockChunk(), error])
        with pytest.raises(RuntimeError) as raised:
            vlm._process_streaming_response(stream)
        assert raised.value is error
        assert _is_marked(error) is True
        assert stream.close_count == 1

    def test_sync_parser_error_after_read_event_is_marked_and_closed_once(self):
        class MalformedChunk:
            usage = None

            @property
            def choices(self):
                raise parser_error

        vlm = OpenAIVLM({"api_key": "sk-test"})
        parser_error = RuntimeError("malformed chunk")
        stream = ScriptedSyncStream([MalformedChunk()])

        with pytest.raises(RuntimeError) as raised:
            vlm._process_streaming_response(stream)

        assert raised.value is parser_error
        assert _is_marked(parser_error) is True
        assert stream.close_count == 1

    def test_sync_primary_error_wins_over_redacted_cleanup_error(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        primary = RuntimeError("primary")
        cleanup = RuntimeError("SENTINEL-CLEANUP-SECRET")
        stream = ScriptedSyncStream([MockChunk(), primary], close_error=cleanup)
        fake_logger = MagicMock()

        with (
            patch("openviking.models.vlm.backends.openai_vlm.logger", fake_logger),
            pytest.raises(RuntimeError) as raised,
        ):
            vlm._process_streaming_response(stream)

        assert raised.value is primary
        assert stream.close_count == 1
        assert fake_logger.warning.call_count == 1
        assert "SENTINEL-CLEANUP-SECRET" not in repr(fake_logger.warning.call_args)

    def test_sync_cleanup_only_error_after_event_is_marked(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        cleanup = RuntimeError("cleanup only")
        stream = ScriptedSyncStream([MockChunk(content="ok")], close_error=cleanup)

        with pytest.raises(RuntimeError) as raised:
            vlm._process_streaming_response(stream)

        assert raised.value is cleanup
        assert _is_marked(cleanup) is True
        assert stream.close_count == 1

    @pytest.mark.asyncio
    async def test_async_nonawaitable_close_prevents_aclose_fallback(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        stream = NonAwaitableCloseStream([MockChunk(content="ok")])

        assert await vlm._process_streaming_response_async(stream) == "ok"
        assert stream.close_count == 1
        assert stream.aclose_count == 0

    @pytest.mark.asyncio
    async def test_async_uses_aclose_only_when_close_is_absent(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        stream = AcloseOnlyStream([MockChunk(content="ok")])

        assert await vlm._process_streaming_response_async(stream) == "ok"
        assert stream.aclose_count == 1

    @pytest.mark.asyncio
    async def test_async_cleanup_only_error_after_event_is_marked(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        cleanup = RuntimeError("cleanup only")
        stream = ScriptedAsyncStream([MockChunk(content="ok")], close_error=cleanup)

        with pytest.raises(RuntimeError) as raised:
            await vlm._process_streaming_response_async(stream)

        assert raised.value is cleanup
        assert _is_marked(cleanup) is True
        assert stream.close_count == 1

    @pytest.mark.asyncio
    async def test_unmarkable_async_cleanup_error_raises_marker_return_value(self):
        class MarkerAssignmentRejectingCleanupError(RuntimeError):
            def __setattr__(self, name, value):
                if name == "_openviking_vlm_non_retryable":
                    raise RuntimeError("instance marker assignment denied")
                super().__setattr__(name, value)

        vlm = OpenAIVLM({"api_key": "sk-test"})
        original = MarkerAssignmentRejectingCleanupError("SENTINEL-CLEANUP-MARKER")
        stream = ScriptedAsyncStream([MockChunk(content="partial")], close_error=original)

        with pytest.raises(BaseException) as raised:
            await vlm._process_streaming_response_async(stream)

        assert stream.close_count == 1
        assert raised.value is not original
        assert raised.value.__cause__ is original
        assert _is_marked(raised.value) is True

    @pytest.mark.asyncio
    async def test_async_cancellation_after_event_preserves_identity_and_cleans_up_once(self):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        cancellation = asyncio.CancelledError("post-event cancellation")
        stream = ScriptedAsyncStream([MockChunk(content="partial"), cancellation])
        original_create_task = asyncio.create_task
        cleanup_tasks = []

        def capture_cleanup_task(coro):
            task = original_create_task(coro)
            cleanup_tasks.append(task)
            return task

        with patch.object(asyncio, "create_task", capture_cleanup_task):
            subject = original_create_task(vlm._process_streaming_response_async(stream))
            with pytest.raises(asyncio.CancelledError) as raised:
                await subject

        assert raised.value is cancellation
        assert stream.close_count == 1
        assert subject.done()
        assert len(cleanup_tasks) == 1
        assert cleanup_tasks[0].done()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["sync", "async"])
    async def test_assignment_rejecting_error_is_wrapped_after_exactly_one_cleanup(self, mode):
        class MarkerAssignmentRejectingError(RuntimeError):
            def __setattr__(self, name, value):
                if name == "_openviking_vlm_non_retryable":
                    raise RuntimeError("instance marker assignment denied")
                super().__setattr__(name, value)

        vlm = OpenAIVLM({"api_key": "sk-test"})
        original = MarkerAssignmentRejectingError("SENTINEL-STREAM-SECRET")
        stream_type = ScriptedSyncStream if mode == "sync" else ScriptedAsyncStream
        stream = stream_type([MockChunk(content="partial"), original])
        fake_logger = MagicMock()

        with patch("openviking.models.vlm.backends.openai_vlm.logger", fake_logger):
            with pytest.raises(BaseException) as raised:
                if mode == "sync":
                    vlm._process_streaming_response(stream)
                else:
                    await vlm._process_streaming_response_async(stream)

        assert stream.close_count == 1
        assert raised.value is not original
        assert raised.value.__cause__ is original
        assert _is_marked(raised.value) is True
        assert "SENTINEL-STREAM-SECRET" not in repr((raised.value, fake_logger.mock_calls))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", ["success", "primary", "body-cancel"])
    async def test_async_cleanup_uses_one_task_and_deterministic_cancellation_priority(self, body):
        vlm = OpenAIVLM({"api_key": "sk-test"})
        primary = RuntimeError("identical-primary")
        body_cancel = asyncio.CancelledError("identical-body-cancel")
        first_cancel = asyncio.CancelledError("identical-first-wait-cancel")
        second_cancel = asyncio.CancelledError("identical-second-wait-cancel")
        expected = {"success": first_cancel, "primary": primary, "body-cancel": body_cancel}[body]
        events = {
            "success": [MockChunk(content="ok")],
            "primary": [MockChunk(), primary],
            "body-cancel": [body_cancel],
        }[body]
        first_seen, second_seen, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        stream = ScriptedAsyncStream(
            events, RuntimeError("SENTINEL-CLEANUP"), close_release=release
        )
        original_create_task = asyncio.create_task
        cleanup_tasks, shield_tasks = [], []
        baseline = {task for task in asyncio.all_tasks() if not task.done()}

        def capture_create_task(coro):
            task = original_create_task(coro)
            cleanup_tasks.append(task)
            return task

        async def controlled_shield(task):
            shield_tasks.append(task)
            call = len(shield_tasks)
            if call == 1:
                first_seen.set()
                raise first_cancel
            if call == 2:
                second_seen.set()
                raise second_cancel
            return await task

        async def release_after_both_observations():
            await first_seen.wait()
            await second_seen.wait()
            release.set()

        helper = original_create_task(release_after_both_observations())
        subject = None
        try:
            with (
                patch.object(asyncio, "create_task", capture_create_task),
                patch.object(asyncio, "shield", controlled_shield),
            ):
                subject = original_create_task(vlm._process_streaming_response_async(stream))
                with pytest.raises(type(expected)) as raised:
                    await subject
            assert raised.value is expected
            assert len(cleanup_tasks) == 1
            assert shield_tasks and all(task is cleanup_tasks[0] for task in shield_tasks)
            assert stream.close_count == 1
        finally:
            first_seen.set()
            second_seen.set()
            release.set()
            await asyncio.gather(helper, *(cleanup_tasks or []), return_exceptions=True)
            if subject is not None and not subject.done():
                subject.cancel()
                await asyncio.gather(subject, return_exceptions=True)
        assert subject.done() and helper.done() and all(task.done() for task in cleanup_tasks)
        assert not ({task for task in asyncio.all_tasks() if not task.done()} - baseline)


class TestStreamingRetryBoundary:
    def test_sync_retries_stream_creation_but_not_stream_iteration(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True, "max_retries": 1})
        stream = ScriptedSyncStream([MockChunk(content="ok")])
        create = MagicMock(side_effect=[RuntimeError("503 create"), stream])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        vlm.get_client = MagicMock(return_value=client)

        with patch.object(model_retry.time, "sleep") as sleep:
            assert vlm.get_completion("test") == "ok"

        assert create.call_count == 2
        sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_retries_stream_creation_within_budget(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True, "max_retries": 1})
        stream = ScriptedAsyncStream([MockChunk(content="ok")])
        create = AsyncMock(side_effect=[RuntimeError("503 create"), stream])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        vlm.get_async_client = MagicMock(return_value=client)

        with patch.object(model_retry.asyncio, "sleep") as sleep:
            assert await vlm.get_completion_async("test") == "ok"

        assert create.call_count == 2
        sleep.assert_awaited_once()

    def test_sync_never_retries_iterator_error_before_first_event(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True, "max_retries": 2})
        error = RuntimeError("503 before first event")
        first = ScriptedSyncStream([error])
        second = ScriptedSyncStream([MockChunk(content="must not run")])
        create = MagicMock(side_effect=[first, second])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        vlm.get_client = MagicMock(return_value=client)

        with (
            patch.object(model_retry.time, "sleep") as sleep,
            pytest.raises(RuntimeError) as raised,
        ):
            vlm.get_completion("test")

        assert raised.value is error
        assert _is_marked(error) is False
        assert create.call_count == 1
        sleep.assert_not_called()

    def test_sync_never_retries_iteration_after_first_event(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True, "max_retries": 2})
        error = RuntimeError("503 during iteration")
        first = ScriptedSyncStream([MockChunk(content="partial"), error])
        second = ScriptedSyncStream([MockChunk(content="must not run")])
        create = MagicMock(side_effect=[first, second])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        vlm.get_client = MagicMock(return_value=client)

        with (
            patch.object(model_retry.time, "sleep") as sleep,
            pytest.raises(RuntimeError) as raised,
        ):
            vlm.get_completion("test")

        assert raised.value is error
        assert _is_marked(error) is True
        assert create.call_count == 1
        sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_never_retries_iteration_after_usage_only_event(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "stream": True, "max_retries": 2})
        error = RuntimeError("503 during async iteration")
        first = ScriptedAsyncStream([MockChunk(usage=MockUsage(1, 0)), error])
        second = ScriptedAsyncStream([MockChunk(content="must not run")])
        create = AsyncMock(side_effect=[first, second])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        vlm.get_async_client = MagicMock(return_value=client)

        with (
            patch.object(model_retry.asyncio, "sleep") as sleep,
            pytest.raises(RuntimeError) as raised,
        ):
            await vlm.get_completion_async("test")

        assert raised.value is error
        assert _is_marked(error) is True
        assert create.call_count == 1
        sleep.assert_not_called()
