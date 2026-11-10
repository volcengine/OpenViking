# VikingBot tests

All Bot tests live in this directory, including provider and OpenViking session
tests previously under `vikingbot/tests/unit`.

From the repository root, with the bot and dev dependencies installed:

```sh
bash bot/scripts/test_all.sh -q
```

The runner uses the repository `.venv` without syncing dependencies. Set
`VIKINGBOT_TEST_PYTHON` to use another Python environment. Pytest arguments are
forwarded, for example `-k streaming` or `--cov=vikingbot`.

Keep tests of observable behavior on active paths: agent/tool execution,
streaming and provider failover, authentication and session isolation, memory
recall/commit, sandbox boundaries, channel delivery, and Compile task lifecycle.
Use real package imports and pytest-managed temporary directories. Mock external
services at their boundaries; do not replace package modules to bypass imports.
Parameterize cases that exercise the same path and assertions, and share setup
for persisted sessions instead of copying JSONL fixtures across tests.
Assert observable results rather than internal call avoidance, object identity,
or unrelated behavior staying unchanged after a feature change. Keep negative
checks that protect authorization, data integrity, and duplicate delivery.

Enable the feature being tested before asserting that it rejects or skips work;
otherwise an early return can make the test pass for the wrong reason. Check
persisted state with a fresh reader. Budget and concurrency tests should also
prove that useful work completed, and recovery tests should reach a successful
retry. Keep each test focused on one behavior, with explicit expected results.
