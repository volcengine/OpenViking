# Vika Channel + Managed Agent cloud

The Channel process runs locally or in one VKE pod. SQLite, connector identities,
decisions, timers and the delivery outbox stay on Channel. MA owns reasoning and
its cloud sandbox. Every offered Core tool comes from `ActionService.tools()` and
executes locally through `agent.custom_tool_use` / `user.custom_tool_result`.
Native Bash, file and web tools remain cloud tools. No public inbound endpoint is
needed: Channel opens outbound MA SSE and Feishu WebSocket connections.

## Local setup

Use a separate deployment manifest, data directory, workspace and CLI profile.
Do not reuse another running bot's state or profile. `VIKA_CONFIG` is required
when launching `packages/daemon/src/main.mjs`; export the MA API key privately as
`ARK_MA_API_KEY` (or `ARK_API_KEY`). Do not put secrets in the manifest.

1. Run `npm ci --ignore-scripts`, `node scripts/install-lark-cli.mjs` and, if using
   Slack, `node scripts/install-slack-cli.mjs` from `vika/`.
2. Configure the intended bot with the pinned CLI under a dedicated
   `LARKSUITE_CLI_CONFIG_DIR` and profile. Enable long connection event delivery,
   message receive, card actions, document comments and meeting subscriptions as
   needed. Permissions must cover the chosen connector operations.
3. Copy `config.example.json` and replace absolute paths, profile and instance.
   Start with temporary nonempty template/environment placeholders, then run
   `VIKA_CONFIG=/absolute/config.json node scripts/volc-sync-agent.mjs`.
   It creates a **new** `vika-channel-*` agent and cloud environment, persists
   resumable provisioning IDs in `dataDir/ma-provision.json`, and prints the
   template/environment IDs to copy into the manifest. No Vault is created.
   Re-runs update only the dedicated Vika agent, with a new template version.
   Use the same manifest and capability flags during provisioning and startup.
   `--socket` can read the exact offered tools from an existing local Core socket.
4. For SDK subscriptions, write an owned mode-0600 regular JSON file containing
   `appId` and `appSecret`, and set `VIKA_LARK_WS_CREDENTIALS` to its path.
   It must describe the same bot as the CLI profile. `VIKA_LARK_SUBSCRIPTION=sdk`
   uses official Node SDK 1.76.0 for events, while the existing pinned CLI retains
   queries, sends, media, reactions, recalls and identity/OAuth. `cli` remains the
   default transport. SDK startup waits for a real WebSocket connection, registers
   every existing event type and suppresses provider logs containing credentials.
   Optional `VIKA_LARK_ALLOWED_CHAT_IDS` (comma separated) restricts SDK ingress;
   events without a matching chat ID are ignored, including non-chat events.
5. `VIKA_CONFIG=/absolute/config.json node packages/daemon/src/main.mjs` starts the
   instance. `VIKA_PORT=3738 node packages/daemon/scripts/healthcheck.mjs` checks it.
   Owner-only operations require the CLI's authenticated user identity. Bot-only
   reads use `larkMessageAccess: "bot"`; absence of owner login is not fatal.

OV is independent of this phase. The existing manifest requires an `ovConfig`
path; for a local instance without OV, put `{"url":"http://127.0.0.1:1"}` there,
set `VIKA_TIMER_STORAGE=sqlite`, and leave recall off. Startup degrades gracefully;
OV context tools report unavailable until a real isolated config is supplied.

## BYOK model through the OpenViking gateway

MA can call a registered "Direct" (BYOK) model provider instead of a built-in
model, for example the OpenViking gateway (with `agents[].ovViaGateway`). BYOK
is on iff `VIKA_VOLC_MODEL_PROVIDER` is set. Put these in the manifest `env`
(`config.example.json` shows only `VIKA_VOLC_MODEL`; add the others to enable BYOK):

| Key | Value |
| --- | --- |
| `VIKA_VOLC_MODEL` | Model id (required with BYOK; otherwise the built-in model, default `doubao-seed-2-1-turbo-260628`) |
| `VIKA_VOLC_MODEL_PROVIDER` | Provider name registered in MA |
| `VIKA_VOLC_MODEL_BASE_URL` | The **full** endpoint URL, path included, e.g. `https://GATEWAY_HOST/gateway/api/v3/chat/completions` |
| `VIKA_VOLC_MODEL_PROTOCOL` | `openai_chat_completions` (default) or `anthropic_messages` |
| `VIKA_VOLC_MODEL_CONTEXT_WINDOW` | Positive integer |
| `VIKA_VOLC_MODEL_MAX_INPUT_TOKENS` | Positive integer |
| `VIKA_VOLC_MODEL_MAX_OUTPUT_TOKENS` | Positive integer |

`VIKA_VOLC_MODEL_BASE_URL` must be HTTPS without credentials, query or fragment.
The model key is a secret: set `VIKA_VOLC_MODEL_API_KEY` in the operator
environment or `~/.vika/secrets.env` (`VIKA_SECRETS_FILE`), never in the manifest
(a manifest value is ignored with a warning). On VKE, add it to the
`vika-credentials` Secret; `statefulset.yaml` exposes it through an optional
`secretKeyRef`, as it does `ARK_MA_API_KEY`. It is never stored in the MA Agent
or session: the daemon sends it as `X-User-Model-Header-Authorization: Bearer
<key>` on each `POST /sessions/{id}/events` only, and strips it from every child
process. With BYOK on, startup fails when any of the keys above or the model key
is missing or invalid; with it off, the Agent body and requests are unchanged.

Re-run `node scripts/volc-sync-agent.mjs` after changing any of these keys: it
writes the model block (`id`, `provider`, `base_url`, `protocol`,
`token_limits`) to the dedicated `vika-channel-*` agent, so an existing agent
migrates to BYOK (or back off it), and copy the new template session ID into the
manifest. `VIKA_VOLC_AGENT_SYNC=update` at daemon startup syncs tools only, not
the model; startup fails when the template session's Agent model does not match
the BYOK config.

## Recovery and media

`ma_tool_calls` journals each `(session_id, tool_use_id)` with its turn fence,
epoch, authority, original arguments and durable wire result. POSTED calls are
skipped; DONE results are resent after history reconciliation without executing
Core again. A crashed RUNNING call reports `uncertain_not_retried` and is **never
blindly replayed**, including timer, recall, reaction, inbox-ack and delivery
operations. All mutating Core tools are blocked for the rest of that uncertain turn, even
if the model changes tool name; read-only reconciliation remains available.
Use the outbox and provider records to reconcile the outcome before a new user
instruction. SEEN calls from an expired process authority are abandoned unless their durable
`preparing=1` flag proves Core has not started: that preparation phase may resume
a partial file under recovered turn authority. RUNNING is set only immediately
before invoking Core, so an interrupted send still cannot be replayed. User
message acceptance uncertainty still quarantines the whole session. Cancellation
also quarantines it before allowing another turn. Existing Core authority,
latest-input checks, scope checks and delivery deduplication remain authoritative.

Attachments returned by `get_message_media` upload through Files API and mount
under `/mnt/session/uploads/`. Images also return bounded native image/base64
content blocks so the model can see them directly. Outputs must be written under
`/mnt/session/outputs/`; `file_path` and `card_images` carry relative paths.
Channel verifies each file's session scope, downloads it without sending MA
credentials to storage, bounds size, and stages immutable bytes in a stable
private workspace directory. No media fields are removed from Core schemas.
`VIKA_VOLC_FILES=off` explicitly disables transfers and returns errors.

MA drops root `additionalProperties` and empty `required` in its stored schema;
the transport normalizes these known fields for schema hashing. All nested
constraints and all input fields remain registered; Core validates its full
original schemas. Startup fails on any other custom-tool schema difference.
`VIKA_VOLC_AGENT_SYNC=update` can update only a `vika-channel-*` agent; default
`check` is read-only. Existing template Vaults are retained with a startup warning;
keep IM credentials on Channel, never install them in a sandbox.

## VKE

Build with `docker build -f deploy/vke/Dockerfile -t REGISTRY/vika:TAG .` from
`vika/`. Replace the image and block-storage class in `statefulset.yaml`.
Create a ConfigMap `vika-config` with `config.json` and `ov.json`; create Secret
`vika-credentials` from the secret manager. The included init container runs as UID/GID 1000, creates mode-0700 state
directories, copies the SDK secret to an owned mode-0600 file, and initializes
the matching CLI profile through secret stdin. It refuses to overwrite a profile
belonging to another bot and preserves existing OAuth state. After rotating an
app secret, explicitly update the existing CLI profile with `config init
--name <profile> --app-id <id> --app-secret-stdin` before restarting; the init
container does not overwrite an existing profile. The daemon rejects
Secret symlinks intentionally, hence the private copy. Provision MA with this
manifest before starting the StatefulSet. Keep secrets and CLI state out of the
image. Additional connectors need their existing identity setup on the PVC.

The StatefulSet is **one replica**, stable hostname, OrderedReady replacement,
30-second termination grace, and one RWO **block** PVC. Do not use NAS/NFS for
SQLite WAL, scale replicas, force-delete a pod while its node might still run,
or mount this state in another process. The lock checks Linux boot ID, process
start ticks and PID namespace; a foreign hostname fails closed. These checks
prevent stale PID reuse, not distributed split-brain. Ensure old-node fencing
before attaching a volume on another node. Health probes use exec because the
HTTP listener intentionally remains loopback-only. Readiness waits for connector
connection and MA validation. Allow outbound Ark, signed TOS storage, Feishu and
any configured connector/OV endpoints.

Back up SQLite daily through its online backup API (`node:sqlite` backup or
SQLite `.backup`), plus the workspace and CLI profile. Do not copy just the main
SQLite file while WAL writes are active. Retain unresolved journal/outbox rows.
On a staging cluster, verify a DM round trip, delete the pod normally, and confirm
session recovery, one delivery, intact SQLite and successful lock reacquisition.
The templates are provided for deployment; a VKE cluster rollout is not claimed
without actual cluster access and these checks.

For generated test videos, the verified client-compatible combination was H.264
Baseline + yuv420p + silent AAC + MP4 `+faststart`. An earlier silent H.264 High
clip uploaded as a native media message with correct duration but failed playback.
The tests changed several parameters together and do not establish a single
cause. Local ffprobe validity and a successful upload alone do not prove playback.
The MA bridge and Core workspace snapshots no longer impose a 20 MiB cap.
Downloads stream to a private `.part` file and retain a sidecar with the MA
file/session identity, strong ETag, total length and final SHA-256. Network
interruptions resume with `Range` + `If-Range`; each retry refreshes the signed
URL through authenticated file detail. A server returning 200 to a Range request
causes a clean restart, while inconsistent ranges/validators fail closed. Without
a strong validator the transfer restarts from zero. Signed URLs and bearer keys
are never written to the checkpoint. Downloads use an idle timeout, so a steadily
progressing large file is not cut off by a fixed total download deadline.

Uploads use a disk-backed multipart Blob and Core freezes outbound files with an
asynchronous bounded-memory copy/hash. The existing target connector/platform
limits still apply (for example card images remain capped at 10 MiB). The current
[Files upload API](https://docs.volcengine.com/docs/ark/upload-files-api?lang=zh)
documents a 512 MB default managed-storage limit; larger video input via a chosen
TOS bucket is a separate upstream configuration, not enabled by this bridge.
Partial downloads remain in the instance workspace until completed or explicitly
cleaned up; budget persistent disk accordingly.

## Steering a running task

New input in the active event scope is posted immediately as another
`user.message`. MA accepts it into its queue and processes it at a scheduling
boundary (for example a model request or tool result), which can happen before
`end_turn`. It does not interrupt a running shell process. Real IM tests observed queued
input remaining unprocessed through both native Bash and a Core `get_time`
boundary, until a later custom result. There is no verified public priority or
force-steer switch: immediate in-task responsiveness is a remaining MA platform
requirement. HTTP acceptance is never reported as proof of model processing. Other event scopes
remain locally queued so their authority cannot mix with the active task.
See the official [Send Event API](https://docs.volcengine.com/docs/ark/send-event-api?lang=en)
and [Session Event Stream](https://docs.volcengine.com/docs/ark/session-event-stream?lang=en).

Each appended input is journaled POSTING before network I/O and ACCEPTED with its
provider event ID afterward. Only its processed user-message echo admits the new
input sequence into Core tool authority. Recovery replays ordered history under
the original root fence; an uncertain append POST quarantines the session and is
never blindly resent. Temporary working reactions require the existing policy:
use `stagedReplies: true` for continuous interactive work, with identical flags
when provisioning the MA schema. This is a Core reply-policy option, not an MA
steering or scheduling switch. A normal final reaction still settles its
one-delivery decision. A temporary `OnIt` leaves room for a later final result
and is cleared by Core after that final delivery.

## Stream liveness boundary

The adapter aborts a stream with no bytes for 15 minutes, reconciles session
history, and reconnects. Heartbeat bytes reset this timer; a separate watchdog
for a stream that emits heartbeats but no semantic events is not implemented.
Monitor `activeTurns` and provider history when diagnosing prolonged turns.

## Repeatable live verification

With the same `VIKA_CONFIG` and private MA key, run the following from `vika/`.
Choose new directories outside production state. These commands use actual MA,
ActionService and SQLite but a recording connector: they never send real IM.

```sh
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-text
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-freshness --freshness
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-steer --steer
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-steer-reaction --steer-reaction
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-steer-crash --crash-steer
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-steer-crash --recover
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-media --media
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-large --large-output --interrupt-download
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-large-crash --large-output --crash-download
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-large-crash --large-output --recover
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-image --image-file /absolute/test.png
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-crash --crash
# The preceding command intentionally exits from SIGKILL after one recorded effect.
node scripts/volc-verify.mjs --state-dir /tmp/vika-ma-crash --recover
```

Each directory retains `verification.sqlite3`, `connector-effects.ndjson` and
`evidence.json`. The `--crash` recovery must report exactly one effect and a posted
`uncertain_not_retried` result. In contrast, `--crash-download` stops during safe
preparation, and its recovery must resume the download and perform one successful
file delivery. Use explicit Feishu UI inputs for separate live
connector acceptance tests; a recorder test is not a real delivery claim. Freshness
mode inserts an authenticated simulated correction immediately before the old
MA delivery crosses Core, queues it through the actual adapter, and asserts
`stale_decision` followed by exactly one delivery containing the new marker.
Steer modes inject during a real cloud Bash call and assert the same logical run
processes the correction. `--steer-reaction` records temporary reaction, continued
work, final delivery and cleanup; without staged replies it supplies a simulated
idle hint, so use staged replies for the continuous-chat policy acceptance test.

Protocol references: [MA tools](https://docs.volcengine.com/docs/ark/tools?lang=zh),
[custom results and native image blocks](https://docs.volcengine.com/docs/ark/managed-agent-custom-tool-tutorial?lang=zh),
[Files upload, mounts and outputs](https://docs.volcengine.com/docs/ark/upload-and-mount-files?lang=zh).
