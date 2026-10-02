# Microsoft Agent Framework

Use `OpenVikingContextProvider` to add memory to a Python Microsoft Agent Framework
(MAF) agent. It recalls context before inference and records completed turns.
Your application selects when to commit the conversation for memory extraction.

## Install and connect

From an OpenViking checkout:

```bash
pip install ./examples/agent-framework
```

Requires Python 3.10+, MAF core 1.20+, OpenViking SDK 0.1.13+, and a running
OpenViking 0.4.23+ server. The package supports MAF core 1.x and SDK 0.1.x.
Install the MAF model client that your application uses separately.

```python
from agent_framework import Agent
from openviking_sdk import AsyncHTTPClient
from openviking_agent_framework import OpenVikingContextProvider

# Inside your application's async entrypoint:
client = AsyncHTTPClient(url=server_url, api_key=user_key, timeout=60)
await client.initialize()
try:
    memory = OpenVikingContextProvider(client)
    agent = Agent(chat_client, context_providers=[memory])
    session = agent.create_session()
    response = await agent.run("Remember my preferred output format: JSON.", session=session)
    job = await memory.commit(session)
finally:
    await client.close()
```

`chat_client` is your existing MAF chat client. `server_url` and `user_key` come
from your application configuration. Use a normal user key. The
[package README](https://github.com/volcengine/OpenViking/tree/main/examples/agent-framework)
has a complete runnable example, test commands, and recovery instructions.

## Responsibilities and limits

- Recall is automatic. Server-rendered context enters the model as attributed
  reference data. Server access rules determine which data the user can retrieve.
- Capture includes text, function calls, and function results. It excludes recalled
  context, system instructions, hidden reasoning, and nontext media.
- Consume streaming responses fully. An interrupted stream is not captured.
- `commit()` returns task acceptance. Poll the returned `task_id` with `get_task()`
  until it completes. Extraction requires the server's real model configuration.
- Persist `session.to_dict()` and restore with `AgentSession.from_dict()`. Use one
  client/provider per user and actor. Serialize work for the same session.
- Network and server errors propagate. Pending capture blocks the next run and
  commit. Retry with `flush(session)` only after resolving any ambiguous write.
  The append API is not idempotent; inspect active records and archives before
  retrying a request whose response was lost.

The provider uses MAF's public `ContextProvider` API and the lightweight
OpenViking HTTP SDK. It does not install MCP tools or replace MAF chat history.
MAF and OpenViking remain separate packages. AutoGen needs a separate adapter.
The shared CLI plugins' environment switches do not configure this provider.
