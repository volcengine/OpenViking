"""The AML adapter preserves roles while the caller can retain speaker names."""

import pytest

from benchmark.aml.eval import run_public
from benchmark.aml.server import AMLAdapter, AMLAddRequest, AMLSettings


async def test_adapter_preserves_public_and_native_roles():
    class Backend:
        def __init__(self):
            self.messages = None

        async def add_and_commit(self, *, user_id, session_id, messages):
            self.messages = messages

    backend = Backend()
    adapter = AMLAdapter(AMLSettings(), backend)
    request = AMLAddRequest(
        request_id="request-1",
        user_id="user-1",
        session_id="session-1",
        messages=[
            {"role": "user", "content": "[D15:25] Caroline: You play any instruments?"},
            {
                "role": "assistant",
                "content": "[D15:26] Melanie: I play clarinet!",
                "timestamp": 1683554161000,
            },
        ],
    )
    response = await adapter.add(request)
    assert response["success"] is True
    assert request.messages[1].role == "assistant"
    assert backend.messages[1] == {
        "role": "assistant",
        "content": "[D15:26] Melanie: I play clarinet!",
        "created_at": "2023-05-08T13:56:01.000Z",
    }


def test_locomo_request_adds_missing_name_without_changing_protocol_role():
    case = {
        "unit": "locomo_refined",
        "case_id": "conv-26:q45",
        "history_key": "conv-26",
        "histories": [
            {
                "session_id": "session-1",
                "messages": [
                    {"role": "user", "content": "[D15:25] Caroline: Any instruments?"},
                    {"role": "assistant", "content": "I play clarinet!"},
                ],
            }
        ],
        "question": "What instrument does Melanie play?",
        "extra": {"speaker_1_name": "Caroline", "speaker_2_name": "Melanie"},
    }
    groups, _ = run_public._prepare_unit("locomo_refined", [case], "isolated")
    messages = groups[0][0].payload()["messages"]
    assert messages == [
        {"role": "user", "content": "[D15:25] Caroline: Any instruments?"},
        {"role": "assistant", "content": "Melanie: I play clarinet!"},
    ]


def test_locomo_request_rejects_wrong_speaker_prefix():
    with pytest.raises(ValueError, match="expected 'Melanie'"):
        run_public._message_chunks(
            [{"role": "assistant", "content": "[D15:26] Caroline: I play clarinet!"}],
            "session-1",
            locomo_speakers={"user": "Caroline", "assistant": "Melanie"},
        )


@pytest.mark.parametrize("source_prefix", ["", "Melanie Smith: ", "[D15:26] Melanie Smith: "])
def test_long_locomo_messages_keep_speaker_and_role_in_every_fragment(source_prefix):
    words = [f"word-{i}" for i in range(run_public.MAX_WORDS + 17)]
    chunks = run_public._message_chunks(
        [{"role": "assistant", "content": source_prefix + " ".join(words), "timestamp": 123}],
        "session-1",
        locomo_speakers={"user": "Caroline", "assistant": "Melanie Smith"},
    )
    expected_prefix = source_prefix or "Melanie Smith: "
    restored_words = []
    for chunk in chunks:
        assert sum(len(message["content"].split()) for message in chunk) <= run_public.MAX_WORDS
        for message in chunk:
            assert message["role"] == "assistant" and message["timestamp"] == 123
            assert message["content"].startswith(expected_prefix)
            restored_words.extend(message["content"][len(expected_prefix) :].split())
    assert len(chunks) == 2
    assert restored_words == words


def test_non_locomo_messages_keep_original_text_and_roles():
    messages = [
        {"role": "user", "content": "I play clarinet!", "timestamp": 123},
        {"role": "assistant", "content": "That sounds fun."},
    ]
    assert run_public._message_chunks(messages, "session-1") == [messages]
