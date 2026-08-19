# SessionCommit Default 8 with Local Override 50

## Goal

Change the repository-wide default `SessionCommit` worker concurrency from 4 to 8. Keep the developer's local OpenViking instance at 50 through an explicit `~/.openviking/ov.conf` override.

## Repository behavior

An installation that does not configure `queue_workers.session_commit.max_concurrent` uses 8. The configuration model, `QueueManager` constructor and initializer, and `OpenVikingService` storage fallback all use the same value so different construction paths cannot silently select different limits.

`examples/ov.conf.example` explicitly shows 8, and the English and Chinese server configuration references document 8 as the default. Other queue concurrency defaults remain unchanged.

An explicit configuration value continues to take precedence. Existing validation still rejects zero and negative concurrency values.

## Local behavior

Update only `queue_workers.session_commit.max_concurrent` in `~/.openviking/ov.conf` to 50, preserving all other local configuration. This local override is not committed to the repository. Do not restart the running OpenViking service as part of this change; the new local value takes effect on its next restart.

## Testing

- Verify an empty `OpenVikingConfig` selects 8 for `session_commit` while the other queue defaults remain 4.
- Verify `QueueManager` direct construction selects 8 for `SessionCommit`.
- Verify an explicit `session_commit.max_concurrent` value, including 50, is preserved.
- Run focused configuration, queue-manager, and service consistency tests plus Ruff and whitespace checks.
