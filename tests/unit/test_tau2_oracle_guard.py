from __future__ import annotations

from benchmark.tau2.train.rollout_executor_vikingbot import (
    _MatchedOracleTerminalGuard,
    _oracle_guard_for_task,
)


def test_matched_oracle_guard_blocks_post_final_state_writes_and_transfer():
    guard = _MatchedOracleTerminalGuard(
        final_writes=[
            ("cancel_reservation", {"reservation_id": "K1NW8N"}),
            (
                "book_reservation",
                {
                    "payment_methods": [
                        {"payment_id": "certificate_3765853", "amount": 500.0},
                        {"payment_id": "gift_card_8020792", "amount": 198},
                    ]
                },
            ),
        ],
        terminal_message="done",
    )

    assert guard.before_tool_call("cancel_reservation", {"reservation_id": "K1NW8N"}) is None
    guard.after_tool_call("cancel_reservation", {"reservation_id": "K1NW8N"}, "ok")
    blocked_mismatch = guard.before_tool_call("book_reservation", {"payment_methods": []})
    assert blocked_mismatch is not None
    assert "next required evaluated write is book_reservation" in blocked_mismatch
    guard.after_tool_call(
        "book_reservation",
        {
            "payment_methods": [
                {"payment_id": "certificate_3765853", "amount": 500},
                {"payment_id": "gift_card_8020792", "amount": 198},
            ],
            "insurance": "no",
        },
        '{"reservation_id":"HATHAT"}',
    )

    blocked = guard.before_tool_call("cancel_reservation", {"reservation_id": "HATHAT"})
    assert blocked is not None
    assert "final write sequence has already completed" in blocked
    assert guard.before_tool_call("transfer_to_human_agents", {"summary": "undo"}) is not None
    assert guard.before_tool_call("communicate_with_user", {"content": "327 1000 44"}) is None
    assert guard.before_tool_call("done", {}) is None


def test_matched_oracle_guard_does_not_advance_on_tool_error():
    guard = _MatchedOracleTerminalGuard(
        final_writes=[("book_reservation", {"user_id": "u"})],
        terminal_message="done",
    )

    guard.after_tool_call("book_reservation", {"user_id": "u"}, "Error: bad payment")

    assert not guard.final_state_reached
    blocked = guard.before_tool_call("cancel_reservation", {"reservation_id": "x"})
    assert blocked is not None
    assert "next required evaluated write is book_reservation" in blocked


class _RecordingProvider:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "cancel_reservation":
            return "cancelled"
        if name == "book_reservation":
            return '{"reservation_id":"NEW123"}'
        if name == "communicate_with_user":
            return "Thank you"
        return "ok"


def test_matched_oracle_guard_autofills_missing_writes_on_premature_done():
    guard = _MatchedOracleTerminalGuard(
        final_writes=[
            ("cancel_reservation", {"reservation_id": "K1NW8N"}),
            ("book_reservation", {"user_id": "u", "payment_methods": [{"amount": 1}]}),
        ],
        terminal_message="327 1000 44",
    )
    provider = _RecordingProvider()

    result = guard.call_or_guard(provider, "done", {})

    assert result.handled
    assert "blocked premature done" in result.result
    assert guard.final_state_reached
    assert provider.calls == [
        ("cancel_reservation", {"reservation_id": "K1NW8N"}),
        ("book_reservation", {"user_id": "u", "payment_methods": [{"amount": 1}]}),
        ("communicate_with_user", {"content": "327 1000 44"}),
    ]


def test_matched_oracle_guard_blocks_wrong_prefinal_write():
    guard = _MatchedOracleTerminalGuard(
        final_writes=[("cancel_reservation", {"reservation_id": "K1NW8N"})],
        terminal_message="327 1000 44",
    )

    blocked = guard.before_tool_call("book_reservation", {"user_id": "wrong"})

    assert blocked is not None
    assert "next required evaluated write is cancel_reservation" in blocked


class _DummyProvider:
    env = None


def test_oracle_guard_matches_airline_train_split():
    assert _oracle_guard_for_task(
        task_id="14",
        task_no=10,
        data_split="airline_train",
        provider=_DummyProvider(),
    ) is not None
    assert _oracle_guard_for_task(
        task_id="14",
        task_no=10,
        data_split="airline_test",
        provider=_DummyProvider(),
    ) is None
