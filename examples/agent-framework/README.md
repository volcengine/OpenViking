# OpenViking for Microsoft Agent Framework

`OpenVikingContextProvider` adds memory recall and conversation capture to a Python
Microsoft Agent Framework (MAF) agent. The application controls when to extract
long-term memory. No change to MAF is required.

## Install

From an OpenViking checkout:

```bash
pip install ./examples/agent-framework 'agent-framework-openai>=1.15,<2'
```

Requires Python 3.10+, `agent-framework-core>=1.20,<2`, and
`openviking-sdk>=0.1.13,<0.2`. Run an OpenViking 0.4.23 or later server separately.
The OpenAI client is only needed for this example. The provider accepts any MAF
chat client. This separate Python distribution does not install the OpenViking
server, LangChain, or AutoGen.

## Connect an agent

Set `OPENVIKING_URL`, `OPENVIKING_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and
`OPENAI_MODEL` for your server and model. Use a normal OpenViking user key.

```python
import asyncio
import os

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI
from openviking_sdk import AsyncHTTPClient

from openviking_agent_framework import OpenVikingContextProvider


async def main():
    client = AsyncHTTPClient(
        url=os.environ["OPENVIKING_URL"],
        api_key=os.environ["OPENVIKING_API_KEY"],
        timeout=60,
    )
    await client.initialize()
    try:
        memory = OpenVikingContextProvider(client)
        async with AsyncOpenAI() as model:
            agent = Agent(
                OpenAIChatCompletionClient(model=os.environ["OPENAI_MODEL"], async_client=model),
                instructions="Use relevant reference data. Say when a fact is unknown.",
                context_providers=[memory],
            )
            session = agent.create_session()
            response = await agent.run(
                "Remember that I prefer short technical answers.", session=session
            )
            print(response.text)
            job = await memory.commit(session)
            print("Extraction task:", job.get("task_id"))
    finally:
        await client.close()


asyncio.run(main())
```

`commit()` requests archiving and memory extraction. Poll
`await client.get_task(job["task_id"])` with a timeout. Wait for `completed` before
testing recall in a new conversation; treat `failed` or `cancelled` as failures.
An accepted task does not prove that a memory was extracted. Inspect the stored
memory or search result when the distinction matters.

## Behavior

| Operation | Behavior |
| --- | --- |
| Before a run | Search from user input and insert rendered context as attributed reference data. Empty results add nothing. |
| After a completed turn | Append user and assistant text, function calls, and function results. Exclude recalled context and system instructions. |
| Streaming | Capture after the stream completes. Consume it fully. An interrupted stream is not captured. |
| Agent loops | Capture once after the outer turn, including intermediate responses supplied by MAF. |
| Extraction | Call `commit(session)` at an application-selected boundary. No automatic commit at process exit. |
| Failure | Raise the server or network error. Keep unconfirmed message batches in session state. |

The SDK owns authentication and HTTP requests. The server owns retrieval, token
limits, access control, archiving, and extraction. MAF owns agent execution and
chat history. No MCP tools or CLI lifecycle hooks are installed.

Options are `source_id="openviking"`, `limit=5`, `token_budget=4000` (64–32000), and
`peer_scope="all"`. The server searches data visible to the authenticated user.
Use `peer_scope="actor"` with the client's `actor_peer_id` to select actor scope.
Peer scope does not replace user authentication. The shared JavaScript plugin's
environment switches do not configure this Python provider.

Use one initialized client and provider per authenticated user and actor. The
application closes the client. Serialize runs, flushes, and commits for each MAF
session. Different sessions can share a provider on one event loop. Use different
`source_id` values for multiple providers in the same agent.

## State and recovery

Persist `session.to_dict()` with MAF's normal application storage. Restore it with
`AgentSession.from_dict(saved_state)` before continuing a conversation. Keep that
state with the same authenticated user. It contains:

- `session_id`: the corresponding OpenViking conversation.
- `recorded_message_ids`: acknowledged message IDs.
- `pending_messages`: complete messages awaiting acknowledgement.

Pass new messages to each run. If you submit previous messages again, preserve
their `message_id` values. Equal text with distinct IDs is a new message. The ID
list grows with the conversation; use a new MAF session for a new conversation.

After capture fails, save the session state, including pending messages. A new
run and `commit()` stop while pending messages exist. Resolve the failure first:

1. If the request was not sent, call `await memory.flush(session)` to retry it.
2. If the response was lost, inspect the OpenViking session and its archives.
   Compare `source_message_ids` with the pending messages. Move confirmed IDs to
   `recorded_message_ids` and remove only the confirmed prefix from
   `pending_messages`. Then flush the remaining messages.
3. Save the updated MAF state before continuing.

The append API is not idempotent. Blind retries can duplicate messages. There is
no exactly-once guarantee across a crash or failed state save. A process killed
before MAF state is saved can lose pending capture. A failed commit retains the
OpenViking session ID; inspect server tasks and archives before retrying an
ambiguous commit.

Only text and function calls/results are captured. Images, audio, files, hidden
reasoning, and other content types are omitted. This provider does not replace
MAF's full chat-history store. It does not support AutoGen's `Memory` interface.

## Validate

```bash
pip install -e './examples/agent-framework[test,dev]'
ruff check examples/agent-framework
ruff format --check examples/agent-framework
mypy --config-file examples/agent-framework/pyproject.toml examples/agent-framework/src
python -m build examples/agent-framework
```

Live tests need a dedicated server with real extraction and embedding models.
Set `OPENVIKING_TEST_URL`, `OPENVIKING_TEST_ROOT_KEY`, `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, and `OPENAI_MODEL`. Tests create a temporary account with two
ordinary users, then delete it. They incur model usage. `OPENVIKING_TEST_ENV` can
point to a local, untracked dotenv file with these values.

```bash
pytest examples/agent-framework/tests -m live -q
```

Tests inspect stored records, extracted memory, source URIs, and actual model
requests. They also check controls without memory and with another user,
streaming tools, repeated text, state restoration, loops, partial batch failure,
lost acknowledgements, and cancellation. CI runs static and package checks;
live tests require credentials and run separately.

## Upgrade, remove, and release

Reinstall from the required checkout to upgrade. To remove this integration,
remove the provider from the agent and run `pip uninstall openviking-agent-framework`.
Server memory and saved MAF state remain in application-owned storage.

The release workflow builds a wheel and source distribution for tags named
`openviking-agent-framework@<version>`. The tag must match `pyproject.toml`.
Maintainers must configure a PyPI trusted publisher for that workflow and the
`pypi` environment before the first release. Until a release is published, use
the checkout installation above.
