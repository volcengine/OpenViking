from openviking.storage.queuefs.understanding_parse_msg import UnderstandingParseMsg


def test_resolved_extension_survives_queue_round_trip():
    msg = UnderstandingParseMsg(
        task_id="task-1",
        path="https://example.com/download?id=123",
        root_uri="viking://resources/report",
        account_id="account",
        user_id="user",
        role="user",
        resolved_extension=".pdf",
    )

    restored = UnderstandingParseMsg.from_dict(msg.to_dict())

    assert restored.resolved_extension == ".pdf"
