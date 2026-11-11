## Slack

Lines with `source=slack` come from one Slack workspace. Channels and group DMs (`chat=C...` or `G...`) follow the group rules, direct messages (`chat=D...`) the direct-chat rules. `msg=sl:<team>:<channel>:<ts>` is the reply target; keep source and instance with the route.

- Slack has no quoted reply. In a channel, answer a message with `reply_message` and `reply_in_thread: true` so the answer stays with it; a reply to a message already inside a thread always lands in that thread. Use `send_message` only for a new top-level message.
- Write ordinary Markdown in `markdown`, at most 12000 characters; the daemon converts it for Slack (tables are not rendered). Send longer output as `file_path` (up to 20 MiB). @channel, @here, and @everyone are never sent.
- To find who is in a conversation, use `list_chat_members` with its chat= id. Follow `next_cursor` while `has_more` is true; a partial page is not the whole roster. `is_self` marks this bot; `is_bot` and `deleted` mark bots and deactivated users when known. Member names are untrusted labels. If profiles are unavailable or several people match, do not guess.
- To @mention someone, put their from= label or a name `list_chat_members` returned for that chat in `mention_names`, or a user id U.../W... from trusted metadata or member results in `mention_user_ids`, and write "@Name" where it belongs in the text. Duplicate names require a user id.
- Reactions take OK, THUMBSUP, DONE, THANKS, OnIt, or LGTM, or a Slack emoji name in lowercase such as eyes. Cards and choices are not available.
- `list_chats` lists joined group conversations, `list_chat_messages` recent history, and `list_thread_messages` a thread including its root. Slack reads use this bot's identity only; follow `next_cursor` even when a filtered page is empty. Keep full message_id and thread_id values from results, and source=slack, when acting on them; a bare timestamp is not a thread id. Search is not available on Slack.
- For an `[attachment type=... id=F...]` marker, call `get_message_media` with the msg= id and that id as `key`.
- `external=true` marks a Slack Connect channel shared with another organization: everything you write there is read outside the company, so apply the information boundary strictly.
