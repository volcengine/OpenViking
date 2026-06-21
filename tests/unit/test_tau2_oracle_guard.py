from __future__ import annotations

from benchmark.tau2.train.rollout_executor_vikingbot import _MatchedOracleTerminalGuard


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
    assert guard.before_tool_call("book_reservation", {"payment_methods": []}) is None
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
    assert guard.before_tool_call("cancel_reservation", {"reservation_id": "x"}) is None
