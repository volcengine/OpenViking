from vikingbot.integrations.langfuse import LangfuseClient


class _FakeGeneration:
    def __init__(self, metadata=None):
        self.metadata = metadata or {}
        self.trace_id = "trace-123"
        self.id = "obs-123"

    def update(self, **kwargs):
        if "metadata" in kwargs:
            self.metadata = kwargs["metadata"]


class _FakeGenerationWithoutMetadata:
    def __init__(self):
        self.updated_metadata = None
        self.trace_id = "trace-456"
        self.id = "obs-456"

    def update(self, **kwargs):
        self.updated_metadata = kwargs.get("metadata")


class _FakeClient:
    def __init__(self):
        self.flush_calls = 0
        self.events = []
        self.scores = []

    def flush(self):
        self.flush_calls += 1

    def create_event(self, **kwargs):
        self.events.append(kwargs)

    def create_score(self, **kwargs):
        self.scores.append(kwargs)


def test_langfuse_client_creates_event_and_score_for_outcome():
    client = LangfuseClient.__new__(LangfuseClient)
    client.enabled = True
    client._client = _FakeClient()
    client._observations_by_response_id = {}
    client._metadata_by_response_id = {}
    client._trace_context_by_response_id = {}

    generation = _FakeGeneration(metadata={"response_id": "resp-123"})
    client.register_generation("resp-123", generation, metadata={"response_id": "resp-123"})
    client.update_generation_metadata("resp-123", {"finish_reason": "stop"})
    client.update_response_outcome(
        "resp-123",
        "positive_feedback",
        {"response_id": "resp-123", "outcome_label": "positive_feedback"},
    )

    assert client._trace_context_by_response_id["resp-123"]["trace_id"] == "trace-123"
    assert client._trace_context_by_response_id["resp-123"]["observation_id"] == "obs-123"
    assert client._client.events[0]["name"] == "response_outcome_evaluated"
    assert client._client.events[0]["trace_context"] == {"trace_id": "trace-123"}
    assert client._client.events[0]["metadata"]["response_id"] == "resp-123"
    assert client._client.events[0]["metadata"]["finish_reason"] == "stop"
    assert client._client.events[0]["metadata"]["outcome_label"] == "positive_feedback"
    assert client._client.scores[0]["name"] == "response_outcome_label"
    assert client._client.scores[0]["value"] == "positive_feedback"
    assert client._client.scores[0]["trace_id"] == "trace-123"
    assert client._client.scores[0]["observation_id"] == "obs-123"
    assert client._client.scores[0]["metadata"]["response_id"] == "resp-123"
    assert client._client.flush_calls == 1


def test_langfuse_client_records_outcome_without_generation_metadata_attribute():
    client = LangfuseClient.__new__(LangfuseClient)
    client.enabled = True
    client._client = _FakeClient()
    client._observations_by_response_id = {}
    client._metadata_by_response_id = {}
    client._trace_context_by_response_id = {}

    generation = _FakeGenerationWithoutMetadata()
    client.register_generation(
        "resp-456",
        generation,
        metadata={"response_id": "resp-456", "has_tools": False},
    )
    client.update_generation_metadata("resp-456", {"finish_reason": "stop"})
    client.update_response_outcome(
        "resp-456",
        "reasked",
        {
            "response_id": "resp-456",
            "outcome_label": "reasked",
            "reask_within_10m": True,
        },
    )

    assert client._client.events[0]["trace_context"] == {"trace_id": "trace-456"}
    assert client._client.events[0]["metadata"]["response_id"] == "resp-456"
    assert client._client.events[0]["metadata"]["has_tools"] is False
    assert client._client.events[0]["metadata"]["finish_reason"] == "stop"
    assert client._client.events[0]["metadata"]["outcome_label"] == "reasked"
    assert client._client.scores[0]["observation_id"] == "obs-456"
    assert client._client.scores[0]["metadata"]["reask_within_10m"] is True
    assert client._client.flush_calls == 1


def test_langfuse_client_merges_generation_metadata_into_tracked_generation():
    client = LangfuseClient.__new__(LangfuseClient)
    client.enabled = True
    client._client = _FakeClient()
    client._observations_by_response_id = {}
    client._metadata_by_response_id = {}
    client._trace_context_by_response_id = {}

    generation = _FakeGeneration(metadata={"response_id": "resp-789"})
    client.register_generation(
        "resp-789",
        generation,
        metadata={"response_id": "resp-789", "has_tools": False},
    )

    merged = client.update_generation_metadata(
        "resp-789",
        {"channel": "cli__default", "tool_count": 0},
    )

    assert merged["response_id"] == "resp-789"
    assert merged["has_tools"] is False
    assert merged["channel"] == "cli__default"
    assert merged["tool_count"] == 0
    assert generation.metadata == merged
