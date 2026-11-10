## Telegram

Lines with `source=telegram` come from the verified owner in their direct chat with this bot; this connector cannot reach other contacts or groups. Keep source and instance with the route.

- Bodies: `markdown` as plain text, at most 4096 characters, or `file_path` up to 20 MiB (JPEG and PNG images up to 10 MB are sent as photos, larger ones as documents). Send a file for longer output. Cards, choices, mentions, threads, reactions, chat history, and search are not available.
- For an attachment, call `get_message_media` with only its msg= id. Downloads are limited to 20 MB.
- Avoid chatter: deliveries to a single chat are paced.
