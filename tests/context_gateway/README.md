# Context Gateway tests

Tests for `context_gateway`. They need no vector engine, model SDK, OpenViking
Server or network access beyond localhost: OpenViking and the model upstreams are faked in
process. For what the gateway does and how to deploy it, see the user guides
[Context Gateway](../../docs/en/guides/15-context-gateway.md) and
[Context Gateway deployment and operations](../../docs/en/guides/22-context-gateway-operations.md).

## Run the suite

Install the gateway extra and the test dependencies in your development environment:

```bash
pip install -e ".[context-gateway,test]"
```

Run from the repository root:

```bash
PYTHONPATH=. pytest tests/context_gateway --confcutdir=tests/context_gateway -o addopts=''
```

`--confcutdir` keeps pytest from loading `tests/conftest.py`, which needs the full OpenViking
service, and `-o addopts=''` drops the repository-wide coverage options. Select a single file or
test as usual, for example `tests/context_gateway/test_kernel.py::<test_name>`.

`fixtures/` holds sanitized requests recorded from real clients; see
[fixtures/README.md](fixtures/README.md) before updating them.

## Hidden tool protocol contracts

`test_native_tools.py` exercises native Responses and Anthropic wire messages through the
HTTP gateway, including fragmented streams, mixed client tools, budgets, cancellation,
restart/edit/archive replay and preserved thinking/signature blocks. The existing Chat
contracts remain in `test_app.py` and `test_tools.py`.

`HiddenToolLoop` owns execution, budgets, durable receipts and usage. The three adapters in
`tool_protocols` own native request/history rules, assemble native output and project
client-visible events. `ToolProtocol` declares the full adapter contract; `end()` returns
a `ToolRound` so the loop does not inspect partial protocol state. Relayed replies go
through the same assemblers via `ResponseCapture`, so completion follows one rule on every
path: a reply ending in client tool calls hands the turn off (finished, not complete), and
only a reply that ends the user's turn is staged for capture. All event producers
return native JSON (or the Chat `[DONE]` marker); only `encode()` returns bytes. No storage
port or record kind is protocol-specific. A hidden record's required `visible_count` makes an entire
Responses output array one replayable span. Responses requires full input history and
`store: false`; native reasoning items remain intact. Anthropic joins gateway and client
results in the user message immediately following the original tool call.

Protocol changes reviewers should account for:

- The former `tools_chat_only` skip reason is now `tools_require_full_history`, because
  Responses tools require full history with `store: false` and no `item_reference`.
- Responses tool requests add `reasoning.encrypted_content` to `include`, preserving the
  client's other entries. Opaque reasoning is retained in native records and continuations.
- When Anthropic tools are blocked, thinking is removed only if this request matches a
  stored hidden round; actual removal reports `hidden_tool_history_unavailable`.
  Ordinary client tool-result continuations keep their thinking and signatures.
- Responses stream failures use `response.failed` with the same response ID and monotonic
  sequence numbers. Empty Anthropic tool-argument deltas preserve the initial `{}` input;
  nonempty malformed JSON still fails without executing the tool.

Signatures in these tests are synthetic. A successful compatible-provider run does not
establish native Claude signature binding or native OpenAI reasoning-encryption support.
Those remain live acceptance items, and should be reported separately from protocol tests.

## Live acceptance check

`scripts/context_gateway_acceptance.py` sends a three-turn conversation through a running
gateway to a real model provider, using one protocol per run. Each turn must return 200 and a
complete response. The script prints one JSON line per turn with usage and timing, and never
prints credentials or response bodies. It spends real tokens.

You need a gateway key whose upstream serves the model over the chosen protocol. Pass the
connection through environment variables:

| Variable | Value |
| --- | --- |
| `OV_CG_TEST_BASE_URL` | Gateway origin without `/v1`, for example `http://127.0.0.1:1935` |
| `OV_CG_TEST_KEY` | Gateway key (`ovcg_…`) |
| `OV_CG_TEST_MODEL` | A model name the key allows |

```bash
PYTHONPATH=. python scripts/context_gateway_acceptance.py --protocol chat --require-cache
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--protocol {anthropic,chat,responses}` | required | `/v1/messages`, `/v1/chat/completions` or `/v1/responses` (full history, `store: false`) |
| `--prefix-rows N` | `180` | Rows of stable synthetic system prompt, so the provider can cache it; `0` for a minimal smoke test |
| `--stream-turn {0,1,2,3}` | `2` | Turn that streams over SSE; `0` disables streaming |
| `--require-cache` | off | Fail unless each turn's cached input tokens cover the previous turn's input |
| `--cache-block-tokens N` | `128` | Allowance for the provider's final partial cache block in `--require-cache` |
| `--thinking` | off | Anthropic only: enable thinking |
| `--binding-check` | off | Anthropic only: require thinking signatures and `input_transformations` binding metadata on every turn. Anthropic-compatible endpoints that return no signatures fail it |
| `--disable-thinking` | off | Chat and Responses only: send `thinking: {"type": "disabled"}` |
| `--timeout SECONDS` | `120` | Per-request timeout |
| `--pause SECONDS` | `1` | Pause between turns |
| `--output PATH` | none | Also write the report as JSON |

Each run uses a fresh `X-OpenViking-Session` and a unique first message, so runs never share a
conversation. Keep report files and credentials out of the repository.

## Local benchmark

`scripts/context_gateway_benchmark.py` measures gateway overhead on one machine. It starts a
synthetic streaming upstream and the gateway in one process with temporary storage, and needs
no OpenViking Server, provider or credentials. Recall and saving are off, so the numbers cover
parsing, replay, transport and bookkeeping. The JSON report gives p50/p95 milliseconds for:

- `parse_8mib`: parsing an 8 MiB request body;
- `replay`: preparing a request for 10-, 100- and 1,000-turn conversations;
- `tool_dense`: parsing (with plain `orjson` for comparison) and preparing an ~8.5 MB tool-call history;
- `direct_sse` / `gateway_sse`: first byte and completion for a burst of concurrent streamed
  requests, straight to the synthetic upstream and through the gateway.

```bash
PYTHONPATH=. python scripts/context_gateway_benchmark.py --concurrency 300
PYTHONPATH=. python scripts/context_gateway_benchmark.py --concurrency 300 --profile --output bench.json
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--concurrency N` | `300` | Requests per burst |
| `--profile` | off | Instrument the warmed gateway burst and add a `profile` section: executor wait, work time and event-loop resume per storage call and request phase, plus the top cProfile entries |
| `--output PATH` | none | Also write the report to a file |

`--profile` relies on `scripts/context_gateway_profile.py` and slows the burst, so compare
timings from runs without it. Compare results only between runs on the same machine and Python
version.
