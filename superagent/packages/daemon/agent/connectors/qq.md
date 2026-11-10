## QQ

Lines with `source=qq` come from the verified owner in their private bot conversation; groups and other contacts are unavailable. Keep source and instance with the authenticated route.

- Bodies: `markdown` as plain text up to 4000 characters, or `file_path` up to 20 MiB. For image/file attachments, call `get_message_media` with the authenticated msg= id; downloads are limited to 20 MiB. Cards, choices, reactions, mentions, threads, voice, video and chat search are unavailable.
- Prefer `reply_message` for responses to inbound messages. QQ permits at most four passive replies per incoming message within a limited time window (up to 60 minutes; the platform may expire it sooner). Avoid progress chatter and split deliveries.
- `send_message` and `notify_owner` require proactive permission and quota. Never bypass a reply limit by switching to proactive sends or retry an uncertain delivery. If the platform rejects a send, report the tool failure internally and wait for a fresh owner message.
