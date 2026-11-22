## DingTalk

`source=dingtalk` identifies a group text message that mentions this bot. Use the full `msg=` value for `reply_message`. The reply posts to that message's group.

For a reminder explicitly requested in a DingTalk group, pass `delivery: {mode: "chat", source: "dingtalk", chat_id: "<full chat= value>"}` to `create_timer`. When the timer fires, call `finish_timer_occurrence` with `delivery.tool: "send_message"` and the saved source and chat id. Use the timer event's own `event=` as the delivery decision id.
