# Agent framework integrations

These packages connect OpenViking to agent applications through each framework's
public extension points and the official OpenViking SDK. Install only the adapter
your application needs; each package owns its dependencies, tests, and release.

## Available adapters

| Framework | Package | Entry points | Installation |
| --- | --- | --- | --- |
| [LangChain / LangGraph](langchain/) | `langchain-openviking` | Retriever, context middleware, history, store, and tools | `pip install "langchain-openviking[langgraph]"` (omit the extra for base LangChain adapters) |
| [Microsoft Agent Framework](microsoft-agent-framework/) | `openviking-ms-agent-framework` | `OpenVikingContextProvider` | Released versions: `pip install openviking-ms-agent-framework`; see [checkout instructions](microsoft-agent-framework/#install-and-try) for unreleased versions |

LangChain and LangGraph share one package. Repository directory names do not
change the Python imports: use `langchain_openviking` or
`openviking_agent_framework`.

## Common lifecycle

1. **Recall:** retrieve relevant context before agent execution and attach it as
   attributed reference data using the framework's context or middleware API.
2. **Execute:** the framework owns model calls, tools, chat history, and streaming.
3. **Capture:** convert completed messages and tool evidence into OpenViking
   session messages. Track acknowledged progress in persistent framework state.
4. **Extract:** request a commit at an application-selected boundary or according
   to the adapter's documented commit policy. Commit acceptance starts a task;
   wait for that task when the application needs extracted memory immediately.

Native chat history and OpenViking memory capture have different responsibilities.
An adapter that implements a history/store interface must document and test that
interface separately. For example, MAF owns its chat history; the LangChain package
also provides explicit history and LangGraph store components.

## SDK operations to reuse

Use a supported SDK version that exposes the operations your adapter needs.
The existing adapters describe their minimum versions in their package manifests.

| Purpose | Python SDK | TypeScript SDK |
| --- | --- | --- |
| Recall assembled context | `search_context` | `searchContext` |
| Ensure/read session state | `get_session` | `getSession` |
| Append structured messages | `batch_add_messages` | `batchAddMessages` |
| Request extraction | `commit_session` | `commitSession` |
| Check extraction completion | `get_task` | `getTask` |

See the [Python SDK](../../sdk/python/) and [TypeScript SDK](../../sdk/typescript/).
The server owns retrieval, authorization, storage, and extraction. The application
binds an authenticated client, owns its lifetime, and persists framework state.
The adapter maps framework hooks, messages, and conversation IDs to SDK calls.
MCP can expose model-callable OpenViking tools alongside the lifecycle adapter.

## Add an adapter

Refer to the [LangChain/LangGraph adapter](langchain/) and
[Microsoft Agent Framework provider](microsoft-agent-framework/) for examples of
connecting a framework's lifecycle to the OpenViking SDK. Each package includes
its implementation, contract tests, and runnable examples.

Create one package under `integrations/frameworks/<framework>/` containing its
manifest, `src/`, `tests/`, README, and runnable `examples/`. Use the target
framework's public lifecycle and message APIs to implement the following contracts.

- **Identity and state:** use the application's authenticated OV client and keep
  a stable mapping from framework conversations to OV sessions. Preserve delivery
  IDs and unresolved capture through supported session restoration.
- **Messages:** preserve visible text and linked tool calls/results. Exclude
  system instructions, hidden reasoning, and injected recall from automatic
  conversation capture. Document any media support and sensitive tool data.
- **Completion:** capture completed invocations. Check streaming exhaustion and
  multi-step/tool-loop behavior; document which boundary counts as completed.
- **Policies:** expose capture controls and extraction timing. Apply automatic
  commit policies only after all captured messages are acknowledged. Use the
  server's pending-token count for a token-based threshold.
- **Failures:** report recall/capture/commit failures according to a documented
  policy. Keep confirmed progress when a later batch or commit fails. OV append
  is not idempotent: a lost acknowledgement requires reconciliation before retry.
- **Packaging:** keep framework dependencies in the adapter. The server's root
  environment and Docker build must not require adapter directories. Preserve
  the framework's own extension APIs; ship a runnable example and contract tests.

### Test checklist

Exercise the real framework lifecycle with deterministic model responses and a
controlled SDK boundary. Cover ordinary and streamed runs, tool calls/results,
multi-step loops, interrupted streams, session restoration, separate users and
sessions, partial/lost acknowledgements, and extraction-policy failures.

Build a wheel and sdist, install outside the checkout, and test the supported
minimum and current dependency versions. Verify remote SDK usage does not import
the OV server. A documented live smoke should demonstrate capture, extraction,
and recall in a new conversation when credentials and a server are available.

## Other frameworks

Google ADK, OpenAI Agents SDK, Vercel AI SDK, CrewAI, LlamaIndex, Mastra, Pydantic AI,
AutoGen/AG2, Strands, and Agno do not have adapters in this directory. Refer to
[Add an adapter](#add-an-adapter) and the SDK operations above for implementation
and validation guidance.
