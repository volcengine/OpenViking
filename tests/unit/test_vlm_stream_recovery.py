# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the sanitized provider-recovery status emitted on stream failover.

A failover that happens before the first visible stream item used to be
completely silent (issue #6145): the caller could not tell that the provider
changed, and a slow recovery could outlive a transport-level timeout.  These
tests pin the contract: exactly one sanitized recovery status is emitted per
pre-output failover, before backup output; nothing is emitted for healthy
runs; failures after a visible delta keep the no-replay behavior.
"""

from types import SimpleNamespace

import pytest

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.models.vlm import FailoverVLM, MultiCredentialVLM, OpenAIVLM
from openviking.models.vlm.base import ProviderRecoveryStatus
from openviking.utils.exceptions import AllCredentialsFailedError


def rate_limit_error() -> ModelError:
    """Structured error both switchers advance on, without SDK types."""
    return ModelError(kind=ModelErrorKind.RATE_LIMIT_ERROR, message="rate limited")


class FakeCompletions:
    def __init__(self, *, chunks=None, error: Exception | None = None):
        self.chunks = chunks or []
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error

        async def generate():
            for chunk in self.chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        return generate()


def stream_chunk(content: str | None = None, *, finish_reason: str | None = None):
    choices = []
    if content is not None or finish_reason is not None:
        choices.append(
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(
                    reasoning_content=None,
                    content=content,
                    tool_calls=[],
                ),
            )
        )
    return SimpleNamespace(choices=choices, usage=None)


def attach_client(vlm, completions: FakeCompletions) -> None:
    vlm.get_async_client = lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions))


def make_openai_vlm(model: str) -> OpenAIVLM:
    return OpenAIVLM({"provider": "openai", "model": model, "api_key": f"{model}-key"})


def is_recovery(item) -> bool:
    return isinstance(item, ProviderRecoveryStatus)


class TestFailoverVLMStreamRecovery:
    @pytest.mark.asyncio
    async def test_pre_delta_failover_emits_one_recovery_before_backup_output(self):
        primary_completions = FakeCompletions(error=rate_limit_error())
        backup_completions = FakeCompletions(chunks=[stream_chunk("hello", finish_reason="stop")])
        primary = make_openai_vlm("primary-model")
        backup = make_openai_vlm("backup-model")
        attach_client(primary, primary_completions)
        attach_client(backup, backup_completions)
        failover = FailoverVLM(primary, backup)

        items = [item async for item in failover.stream_with_failover(_adapter_like_stream)]

        recoveries = [item for item in items if isinstance(item, ProviderRecoveryStatus)]
        assert len(recoveries) == 1
        assert items.index(recoveries[0]) == 0
        assert items[1:] == ["hello"]
        # Sanitized: coarse kind only, no secrets or exception text.
        assert recoveries[0].type == "provider_recovery"
        assert "unauthorized" not in str(recoveries[0].error_kind)
        assert "primary-key" not in repr(recoveries[0])
        assert "backup-model" not in repr(recoveries[0])
        assert failover.is_using_backup is True

    @pytest.mark.asyncio
    async def test_healthy_primary_stream_emits_no_recovery(self):
        primary_completions = FakeCompletions(chunks=[stream_chunk("hello", finish_reason="stop")])
        primary = make_openai_vlm("primary-model")
        backup = make_openai_vlm("backup-model")
        attach_client(primary, primary_completions)
        attach_client(backup, FakeCompletions())
        failover = FailoverVLM(primary, backup)

        items = [item async for item in failover.stream_with_failover(_adapter_like_stream)]

        assert items == ["hello"]
        assert not any(isinstance(item, ProviderRecoveryStatus) for item in items)

    @pytest.mark.asyncio
    async def test_post_delta_failure_does_not_replay_and_emits_no_recovery(self):
        primary_completions = FakeCompletions(chunks=[stream_chunk("partial"), rate_limit_error()])
        backup_completions = FakeCompletions(chunks=[stream_chunk("dup")])
        primary = make_openai_vlm("primary-model")
        backup = make_openai_vlm("backup-model")
        attach_client(primary, primary_completions)
        attach_client(backup, backup_completions)
        failover = FailoverVLM(primary, backup)

        with pytest.raises(ModelError):
            [item async for item in failover.stream_with_failover(_adapter_like_stream)]

        assert backup_completions.calls == []


class TestMultiCredentialVLMStreamRecovery:
    @pytest.mark.asyncio
    async def test_pre_delta_credential_failover_emits_one_recovery(self):
        first_completions = FakeCompletions(error=rate_limit_error())
        second_completions = FakeCompletions(
            chunks=[stream_chunk("from-second", finish_reason="stop")]
        )
        first = make_openai_vlm("first-model")
        second = make_openai_vlm("second-model")
        attach_client(first, first_completions)
        attach_client(second, second_completions)
        credentials = MultiCredentialVLM(
            [first, second],
            credential_ids=["first", "second"],
        )

        items = [item async for item in credentials.stream_with_failover(_adapter_like_stream)]

        recoveries = [item for item in items if isinstance(item, ProviderRecoveryStatus)]
        assert len(recoveries) == 1
        assert items[0] is recoveries[0]
        assert items[1] == "from-second"
        assert recoveries[0].type == "provider_recovery"
        assert credentials.active_credential_id == "second"

    @pytest.mark.asyncio
    async def test_healthy_first_credential_emits_no_recovery(self):
        first_completions = FakeCompletions(chunks=[stream_chunk("ok", finish_reason="stop")])
        first = make_openai_vlm("first-model")
        second = make_openai_vlm("second-model")
        attach_client(first, first_completions)
        attach_client(second, FakeCompletions())
        credentials = MultiCredentialVLM(
            [first, second],
            credential_ids=["first", "second"],
        )

        items = [item async for item in credentials.stream_with_failover(_adapter_like_stream)]

        assert items == ["ok"]

    @pytest.mark.asyncio
    async def test_all_credentials_fail_pre_delta_raises_after_recovery_notices(self):
        first_completions = FakeCompletions(error=rate_limit_error())
        second_completions = FakeCompletions(error=rate_limit_error())
        first = make_openai_vlm("first-model")
        second = make_openai_vlm("second-model")
        attach_client(first, first_completions)
        attach_client(second, second_completions)
        credentials = MultiCredentialVLM(
            [first, second],
            credential_ids=["first", "second"],
        )

        seen = []
        with pytest.raises(AllCredentialsFailedError):
            async for item in credentials.stream_with_failover(_adapter_like_stream):
                seen.append(item)

        # One sanitized notice per pre-output credential switch, then the
        # aggregated failure still surfaces to the caller.
        assert sum(1 for item in seen if is_recovery(item)) == 2
        assert second_completions.calls


async def _adapter_like_stream(vlm):
    """Mirror VLMProviderAdapter._chat_stream_backend's visible-item contract.

    The first visible item is yielded only after the underlying create() call
    succeeds, so a pre-delta failure raises before anything is yielded.
    """
    client = vlm.get_async_client()
    response = await client.chat.completions.create(stream=True)
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is not None and delta.content:
            yield delta.content
