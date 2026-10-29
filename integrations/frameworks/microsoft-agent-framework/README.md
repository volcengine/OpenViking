# OpenViking for Microsoft Agent Framework

An optional Python package for MAF agents. It recalls OpenViking context before a
run and captures completed agent runs, including tool calls and results.
The application controls when memory extraction is requested.

## Install and try

The distribution is named `openviking-ms-agent-framework` and imports as
`openviking_agent_framework`.

Requires Python 3.10+, MAF core 1.20–1.x, OpenViking SDK 0.1.13–0.1.x, and a
running OpenViking 0.4.23+ server. Install a released version from PyPI:

```bash
pip install openviking-ms-agent-framework
```

For an unreleased version, or to run the repository example, install from an
OpenViking checkout with the optional model client:

```bash
pip install './integrations/frameworks/microsoft-agent-framework[example]'
```

Set `OPENVIKING_URL`, `OPENVIKING_API_KEY` (a normal user key), `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, and `OPENAI_MODEL`, then run:

```bash
python integrations/frameworks/microsoft-agent-framework/examples/example.py
```

The example records a preference, waits for extraction, and asks about it in a new
conversation. Its OpenAI-compatible model client is optional; the provider works
with other MAF chat clients that support chat middleware (including the built-in
clients). The package installs neither the OpenViking server
nor a model client unless the `example` extra is selected.

## Attach to an existing agent

With an initialized, application-owned `AsyncHTTPClient`:

```python
from agent_framework import Agent
from openviking_agent_framework import OpenVikingContextProvider

memory = OpenVikingContextProvider(client)
agent = Agent(chat_client, context_providers=[memory])
session = agent.create_session()
response = await agent.run("Remember my preferred reply format: JSON.", session=session)
job = await memory.commit(session)
```

`commit()` returns task acceptance. Poll `client.get_task(job["task_id"])` until
completion before checking extracted memory. Close the client in the application.

## Capture and commit policies

```python
memory = OpenVikingContextProvider(
    client,
    capture=True,
    commit_policy="pending_tokens",
    commit_token_threshold=20_000,
)
```

| `commit_policy` | Behavior |
| --- | --- |
| `"never"` (default) | Capture automatically; the application calls `commit(session)` |
| `"always"` | Request a commit after each completed, captured agent invocation |
| `"pending_tokens"` | Request a commit when OV's post-capture pending tokens reach the threshold |

The threshold defaults to 20,000 and must be a positive integer. It counts the
session's captured, uncommitted tokens; `token_budget` controls recalled context.
Policies run only after all message batches are acknowledged. A successful
`flush()` of pending capture also applies the policy. Commit or metadata errors
propagate, but acknowledged messages remain recorded and are not sent again;
the next completed invocation or an explicit `commit()` can retry the commit.
Automatic commits start background extraction and do not wait for completion.

Set `capture=False` for automatic recall without conversation uploads or
automatic commits. Explicit `flush()` and `commit()` remain available. Turning
capture off does not discard existing pending messages. The application still
calls `commit()` at its conversation/workflow boundary to extract a tail below
the threshold. These policies do not change OV's server-side auto-commit settings.

## State and failure handling

- Use one client/provider per user and actor; serialize work for the same session.
- Persist `session.to_dict()` and restore with `AgentSession.from_dict()` under the
  same authenticated user. Preserve message IDs when replaying previous messages.
- Recalled context is attributed reference data and is excluded from capture.
  System instructions, hidden reasoning, and nontext media are also excluded.
- Fully consume streaming responses. Capture happens after each completed agent
  run, including each `AgentLoopMiddleware` iteration, for both streaming and
  non-streaming runs. If a later iteration is interrupted, earlier completed
  iterations remain captured; the interrupted iteration is excluded.
- Middleware must preserve provider state. `fresh_context=True` rolls that state
  back between loop iterations and is unsupported; detected state resets raise
  before further capture or commit.
- Errors propagate. Unconfirmed capture blocks another run and `commit()`.
  `flush(session)` retries pending messages after a known failure before sending.
  A lost response can mean the server already appended the messages: inspect
  active records and archives by `source_message_ids` before retrying or updating
  the saved provider state. Blind retries can duplicate capture.

The application owns MAF session persistence; there is no exactly-once guarantee.
Options are `source_id="openviking"`, `limit=5`, `token_budget=4000` (64–32000),
and `peer_scope="all"`. Use `peer_scope="actor"` with the SDK client's actor ID.
The server owns access control, retrieval, storage, and extraction; MAF owns agent
execution and chat history. No framework modifications or MCP tools are required.

## Validate

```bash
pip install -e './integrations/frameworks/microsoft-agent-framework[test,dev]'
cd integrations/frameworks/microsoft-agent-framework
pytest -q --confcutdir=tests tests
ruff check .
ruff format --check .
mypy src
python -m build
```

Offline tests use the real MAF agent lifecycle with deterministic model and SDK
boundaries. They require no server, credentials, or model calls. Support belongs
in [OpenViking issues](https://github.com/volcengine/OpenViking/issues/new/choose).

See the [framework adapter guide](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/README.md) for adding other integrations.
The adapter is licensed under MIT; see [LICENSE](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/microsoft-agent-framework/LICENSE).
The runnable [example](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/microsoft-agent-framework/examples/LICENSE) retains Apache-2.0.
