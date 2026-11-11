# OpenViking Gateway tests

Tests for `openviking_gateway`. They need no vector engine, model SDK, OpenViking
Server or network access beyond localhost: OpenViking and the model upstreams are faked in
process. For what the gateway does and how to deploy it, see the user guides
[OpenViking Gateway](../../docs/en/guides/15-gateway.md) and
[OpenViking Gateway deployment and operations](../../docs/en/guides/22-gateway-operations.md).

## Run the suite

Install the core package and test dependencies in your development environment:

```bash
pip install -e ".[test]"
```

Run from the repository root:

```bash
PYTHONPATH=. pytest tests/gateway --confcutdir=tests/gateway -o addopts=''
```

`--confcutdir` keeps pytest from loading `tests/conftest.py`, which needs the full OpenViking
service, and `-o addopts=''` drops the repository-wide coverage options. Select a single file or
test as usual, for example `tests/gateway/test_kernel.py::<test_name>`.

`fixtures/` holds sanitized requests recorded from real clients; see
[fixtures/README.md](fixtures/README.md) before updating them.

## Host, management and migration coverage

Management tests call `/gateway/admin/*` directly with OpenViking identities and the optional `X-OpenViking-Gateway-Management-Key`; there is no shared admin token. Cover root/admin/user/dev account and user scope, management-key-only object access, read-only secret redaction, key issuance, revocation only on an explicit OpenViking 401, VLM import permissions and re-import preserving edits, and changing base_url clearing retained credentials and headers.

`tests/fixtures/gateway_upstream_urls.json` is shared with Studio URL tests. Keep SDK-style base joins, alternate client prefixes and Anthropic validation in sync. Storage tests cover the schema v5 → v6 migration, which re-encodes v0.5.0 Fernet values with the OpenViking encryptor (or as plaintext when encryption is off). Stores run the encryptor on the event loop, so tests that encode or decode directly must do it through `store.run`. Embedded host and VLM mapping integration tests live under `tests/server/`; standalone startup reads `gateway.conf` and `OPENVIKING_GATEWAY_*` variables, never `ov.conf`. Run those host tests separately with the full server test environment.

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

`scripts/gateway_acceptance.py` sends a three-turn conversation through a running
gateway to a real model provider, using one protocol per run. Each turn must return 200 and a
complete response. The script prints one JSON line per turn with usage and timing, and never
prints credentials or response bodies. It spends real tokens.

You need a gateway key whose upstream serves the model over the chosen protocol. Pass the
connection through environment variables:

| Variable | Value |
| --- | --- |
| `OV_GW_TEST_BASE_URL` | Gateway address without `/v1`, for example `http://127.0.0.1:1933/gateway` (embedded) or `http://127.0.0.1:1935/gateway` (standalone) |
| `OV_GW_TEST_KEY` | Gateway key (`ovgw_…`) |
| `OV_GW_TEST_MODEL` | A model name the key allows |

```bash
PYTHONPATH=. python scripts/gateway_acceptance.py --protocol chat --require-cache
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

`scripts/gateway_benchmark.py` measures gateway overhead on one machine. It starts a
synthetic streaming upstream and the gateway in one process with temporary storage, and needs
no OpenViking Server, provider or credentials. Recall and saving are off, so the numbers cover
parsing, replay, transport and bookkeeping. The JSON report gives p50/p95 milliseconds for:

- `parse_8mib`: parsing an 8 MiB request body;
- `replay`: preparing a request for 10-, 100- and 1,000-turn conversations;
- `tool_dense`: parsing (with plain `orjson` for comparison) and preparing an ~8.5 MB tool-call history;
- `direct_sse` / `gateway_sse`: first byte and completion for a burst of concurrent streamed
  requests, straight to the synthetic upstream and through the gateway.

```bash
PYTHONPATH=. python scripts/gateway_benchmark.py --concurrency 300
PYTHONPATH=. python scripts/gateway_benchmark.py --concurrency 300 --profile --output bench.json
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--concurrency N` | `300` | Requests per burst |
| `--profile` | off | Instrument the warmed gateway burst and add a `profile` section: executor wait, work time and event-loop resume per storage call and request phase, plus the top cProfile entries |
| `--output PATH` | none | Also write the report to a file |

`--profile` relies on `scripts/gateway_profile.py` and slows the burst, so compare
timings from runs without it. Compare results only between runs on the same machine and Python
version.

## Redis storage contracts

`test_storage_ports.py` runs each storage contract against SQLite, Redis standalone and
Redis Cluster. Redis cases skip when their endpoint variable is absent; configured
endpoints must be reachable. Install `pip install "redis>=5.3.1,<7"` and run:

```bash
export OV_TEST_REDIS=redis://127.0.0.1:6379
export OV_TEST_REDIS_CLUSTER=redis://127.0.0.1:7000,redis://127.0.0.1:7001,redis://127.0.0.1:7002
export OV_TEST_REDIS_USERNAME=gateway
export OV_TEST_REDIS_PASSWORD=gateway-test-password
PYTHONPATH=. pytest tests/gateway/test_storage_ports.py --confcutdir=tests/gateway -o addopts=''
```

The gateway workflow starts Redis 7.2 standalone and a three-node Cluster and creates
the [documented ACL](../../docs/en/gateway/13-configuration.md#storage) on every node.
Each fixture uses a random `ovgw:test:<uuid>:` prefix and removes only its own keys.
CI runs the whole gateway suite on redis-py 6.4.0 and this file again on 5.3.1, the
oldest supported client; the client options are explained in
`openviking_gateway/redis_store.py`.

## kind deployment acceptance

`e2e/` runs the standalone Helm chart on a dedicated kind cluster with one control
plane and two workers. It uses a mock OpenViking server with two libraries that
both identify as `default/default`, three model protocols, and Redis with the
minimum gateway ACL. All credentials are synthetic. Each case owns a fresh
namespace, persistent Redis volume, and gateway release; cleanup only removes
those resources. Do not point this suite at a shared production cluster.

Install `kind` 0.27+, Docker 28+, kubectl, Helm 3, Python 3.10+, `pytest`, `httpx`,
`openai`, `anthropic`, and Codex CLI (`@openai/codex@0.146.0`). The test
clients run on the host; `kubectl port-forward` targets individual gateway Pods.
The rolling-update load runs inside the cluster through its Service.

```bash
mkdir -p /tmp/ovgw-e2e
kind create cluster --name ovgw-e2e --config tests/gateway/e2e/kind.yaml \
  --kubeconfig /tmp/ovgw-e2e/kubeconfig --wait 120s
python tests/gateway/e2e/build_overlay.py --tag ovgw-e2e:local
docker pull redis:7.2-alpine
python tests/gateway/e2e/load_images.py --cluster ovgw-e2e ovgw-e2e:local redis:7.2-alpine
OV_TEST_KIND_KUBECONFIG=/tmp/ovgw-e2e/kubeconfig OV_TEST_GATEWAY_IMAGE=ovgw-e2e:local \
  pytest tests/gateway/e2e --confcutdir=tests/gateway/e2e -o addopts='' -v \
  --basetemp=/tmp/ovgw-e2e/results --junitxml=/tmp/ovgw-e2e/results.xml
kind delete cluster --name ovgw-e2e
```

Without `OV_TEST_KIND_KUBECONFIG`, every deployment case skips before calling
kubectl. Set `OV_TEST_KIND_REDIS=standalone` or `cluster` to select one backend;
by default both run. `OV_TEST_GATEWAY_CHART` can point to the chart in a
separate integration checkout. The image must include the
Redis gateway changes; the overlay helper copies the gateway package and every
changed Python dependency under `openviking/` and `openviking_cli/` relative to
the supplied release tag. It does not rebuild native extensions or Studio.

| Scenario | Standalone | Cluster |
| --- | --- | --- |
| 1. Eight alternating session turns | Yes | Yes |
| 2. Graceful deletion during a stream | Yes | Yes |
| 3. Truncation under a 25-second grace (negative control) | Yes | — |
| 4. Rollout with short, chat, keep-alive, and 60-second stream traffic | Yes | Yes |
| 5. Eviction under 30 MiB and credential reconstruction | Yes | — |
| 6. AOF restart with pending capture | Yes | — |
| 7. Data loss without AOF | Yes | — |
| 8. Isolation of libraries with the same OV identity | Yes | Yes |
| 9. Revocation, paused capture, and replacement key | Yes | — |
| 10. Startup recovery from 503; refusal of dev mode and 404 | Yes | — |
| 11. Forty-second silence with Python SDKs and Codex; compressed SSE | Yes | — |
| 12. One hundred captures with eight workers | Yes | Yes |
| 13. Existing unordered key returns 402 before upstream dispatch | Yes | — |
| 14. Hidden tool deduplication, missing receipts, and Responses chains | Yes | Yes |
| 15. Studio and management changes across the cache window | Yes | — |
| 16. Shared root key and missing-key startup failure | Yes | — |

Assertions inspect raw mock calls, source message IDs, Redis keys, Pod lifecycle,
and client completion markers. Missing SDKs, incomplete streams, and failed
infrastructure are failures when the suite is opted in. A Redis outage may
produce either a logged `memory_store_failure` passthrough or a plaintext
HTTP 500 at authentication; scenario 6 records the observed proportions.
Scenario 7 asserts loss of an undelivered tail when persistence is disabled.

The `gateway-e2e.yml` workflow runs nightly against `ghcr.io/volcengine/openviking:main`.
Manual dispatch can build the selected ref over a release image. Both paths
upload pytest results, per-scenario logs, and kind logs, then delete the cluster.
Cloud Redis failover, TLS, and real OV/model providers remain separate acceptance
checks. For a cgroup-v1 development host, use a local copy of `kind.yaml` with
`KubeletConfiguration.cgroupDriver: cgroupfs` and containerd's
`SystemdCgroup = false`; CI uses cgroup v2 without that host-specific patch.
