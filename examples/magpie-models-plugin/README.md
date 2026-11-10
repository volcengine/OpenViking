# OpenViking models for Magpie

Manage the models **OpenViking and VikingBot themselves use** from Magpie's **Agents → OpenViking** row. Magpie owns vendor credentials and routing; OV keeps the gateway URL, gateway key and full model identifier.

[中文说明](README_CN.md)

The display name is **OpenViking**; the npm package is `@openviking/magpie-models-plugin`.

This is independent of [`@openviking/magpie-plugin`](https://github.com/volcengine/OpenViking/blob/main/examples/magpie-memory-plugin/README.md), which recalls and captures memory for other agents behind Magpie.

## Install locally

The package is not published yet. Add the complete directory, including its Bun bridge:

```sh
magpie plugin add /absolute/path/to/OpenViking/examples/magpie-models-plugin
```

Or select the directory in Plugins → Add plugin. Requires Magpie Agent plugins, its Bun host, and OV's startup configuration API. The isolated host flow was tested with **Magpie 0.1.2818 on macOS**, and installation/restart with **0.1.3502 on Linux**. Windows is unverified. Keep Magpie and OV running. Run the `node cli.mjs ...` commands below from the plugin directory.

Configure the connection and pick a chat model in **Agents → OpenViking**. The adapter automatically saves, restarts and verifies activation. Check adapter logs or `node cli.mjs status`: `saved` means saved configuration, while `applied` means activation checks ran; check for conflicts/errors too. If automatic restart is off, run `node cli.mjs apply` or restart through the original service manager. With `openviking-server --with-bot`, the managed Bot restarts with OV.

`saved` means the startup file was saved, not that a running process switched models. VLM changes require an OV restart; Bot configuration changes, including changes to its inherited VLM, require a Bot restart. Managing only an independent Bot does not change OV's model. `ovRuntime` and `botRuntime` report these separately; a matching startup file cannot prove a standalone Bot reloaded. Restart reminders survive disconnect/restoration. `restartOnChange` defaults to true to restart and verify automatically, or run `node cli.mjs apply` explicitly. The existing Root restart API is called with the saved revision; success requires a new instance, matching file revision, and no pending restart. Failures/rollback are visible and never trigger a restart loop. There is no paid inference probe. Verify VLM image support and Bot streaming, tool execution and input budgets before use.

## Connection

Set advanced options in Plugins → Options or `~/.config/magpie-openviking-models/adapter.json`. Plugin options override that file:

```json
{
  "serverUrl": "http://127.0.0.1:1933",
  "rootApiKeyFile": "~/.config/magpie-openviking-models/root.key",
  "manageVlm": true,
  "botMode": "follow",
  "pollMs": 2000
}
```

The key file contains only an **OV Root key**; use `0600` permissions. This is separate from Magpie's gateway key and ordinary memory access credentials.

The adapter invokes `ov config show -o json` for current CLI connection discovery. Its keys are redacted: only the URL is used; Root credentials are read privately from the same CLI file, never from display masks. If the CLI is unavailable, use the configuration file. Without an explicit URL, read `url` from `OPENVIKING_CLI_CONFIG_FILE` or `~/.openviking/ovcli.conf`, then default to `http://127.0.0.1:1933`. Credential order: `rootApiKeyFile`, `OPENVIKING_ROOT_API_KEY`, `ovcli.conf`'s `root_api_key`. Ordinary `api_key` is not implicitly elevated. Set `ovcliConfig` for a custom client file. An unauthenticated development server can omit credentials. Desktop apps may not inherit shell variables; prefer the adapter file.

## Model roles

| Option | Behavior |
| --- | --- |
| `manageVlm: true` (default) | Manage startup `vlm` service binding |
| `botMode: "preserve"` (default) | Keep explicit Bot settings; a Bot already inheriting root VLM continues to inherit after reload |
| `botMode: "follow"` | Remove explicit Bot model/credentials so it inherits full root VLM; requires `manageVlm` |
| `botMode: "independent"` | Give Bot its own Magpie connection, using the Agent row's model unless `botModel` is set |
| `botModel` | Full `provider/model` or `group/name` for independent Bot mode |
| `manageVlm: false` with independent mode | Manage only VikingBot |
| `ovcliConfig` | Explicit client configuration file for resolving the server URL and Root credentials |
| `restartOnChange` | Default true; restart and verify after model changes/restoration |
| `pollMs` | Polling interval, 500–60000 ms; default 2000 |

For an independent Bot model:

```json
{
  "manageVlm": true,
  "botMode": "independent",
  "botModel": "your-provider/your-tool-capable-model"
}
```

The Agent module writes a private selection file; a Bun bridge validates the model catalog and synchronizes through OV's revision-checked configuration API. It replaces legacy service bindings, including credential chains and backups, while preserving other settings. Released roles restore their original fields. Known Bot context-window metadata is synchronized; unknown values remain unchanged.

### Generation parameters

This plugin provides only Agent model selection and automatic restart verification, with no configuration page or panel and no host modifications. Unspecified model parameters retain their existing values; output budgets are still checked against known model limits.

Use `vlmParameters` and `botParameters` in Options or `adapter.json`:

```json
{
  "vlmParameters": { "max_concurrent": 4, "max_tokens": 8192, "temperature": 0.2, "thinking": false, "timeout": 600 },
  "botParameters": { "max_tokens": 4096, "thinking": true, "timeout": 120 }
}
```

Omitted fields preserve original values. `max_tokens: null` uses provider defaults in OV and independent Bot mode; a following Bot instead continues to use OV's output limit. A non-null Bot budget overrides the inherited limit and is checked separately.

`vlmParameters.max_concurrent` is a positive integer for OV semantic processing and Compile capacity. It preserves the current value unless explicitly configured and requires an OV restart. It is not a global gateway/VikingBot request limit; concurrent processing paths can add load. VikingBot has no equivalent `bot.agents.max_concurrent` setting. For subscription-backed models, start with a small value such as 2–4 and adjust using observed latency/cancellations, rather than treating this as a provider quota.

Known catalog `max_output_tokens` caps requested output budgets, never using context-window size as an output limit. An original budget of 32768 switches to 8192 for a model with an 8192 output limit, with an `adjustments` entry. Primary-credential output limits are retained and checked too. A larger model can recover the original requested budget; disconnect restores original parameters. Unknown limits produce `warnings` instead of fabricated values. Environment-referenced output budgets require an explicit numeric parameter option. Adapter logs also report restart requirements, budget adjustments and compatibility warnings.

When catalog metadata explicitly disables temperature, inherited sampling values are cleared; an explicit unsupported temperature request blocks synchronization. Thinking settings are preserved with a protocol-compatibility warning. Retry settings remain unchanged; timeout changes only when explicitly configured. Parameter changes still require the affected process to restart.

Bot management first detects `/bot/v1/health`. A disabled or undetected Bot is never configured, launched or enabled; explicitly requesting Bot management in that state fails. A standalone Bot remains outside OV's restart lifecycle.

Embedding, Rerank, explicit Query Planner, Bot channels, tools and image-generation models remain configured as before. An unset Query Planner follows VLM according to OV's own rules. Cluster runtime VLM overrides block file management; Account model overrides are outside scope.

## Remote servers

Writes go through the server API to its actual startup file. Non-loopback OV and Magpie URLs require HTTPS; redirects and URL credentials are rejected.

The gateway address Magpie supplies must be reachable from the **OV / Bot machine**. In a container or remote server, loopback refers to that server. Configure a reachable shared gateway address and key in Magpie. Changing `serverUrl` alone does not make the gateway reachable. Catalog checks run on Magpie's machine and cannot prove remote OV connectivity.

## Disconnect and recover

Disconnect the Agent **before** disabling/removing the plugin or quitting Magpie. Keep OV and the bridge running until restoration completes:

```sh
magpie openviking-models default
```

Restart OV and Bot after restoring. If the host was removed first, automatic recovery is not guaranteed. From a retained copy of this directory:

```sh
node cli.mjs doctor
node cli.mjs status
node cli.mjs restore
node cli.mjs sync
node cli.mjs apply
```

`doctor` checks local CLI discovery, authenticated configuration access, restart capability and Bot state. `status` reads local diagnostics, without network calls or credentials. `restore` pauses the current selection while restoring owned fields. `sync` resumes management. `apply` restarts the saved configuration via the Root API and verifies its new instance. Managed `--with-bot` processes restart with OV and must recover health; standalone Bots require their own process manager. The standalone CLI does not read Magpie plugin options: use the same adapter file and credentials for recovery.

Keep `~/.config/magpie-openviking-models/state.json` until recovery completes. It contains original settings, potentially including credentials, and intended writes. The directory is `0700`; selection, journal and status are `0600`. `recoveryPending` means ownership/recovery records still exist, including while successfully connected.

Writes use server revisions and a write-ahead journal. Manual edits to managed fields produce `conflict`, listing field names without values. They are never forcibly overwritten. Restore the last adapter-written values before retrying restoration, or back up the recovery record and reconcile the changes manually. Restore the original target before changing backend URL or startup-file location.

## Memory integration

Managed OV and Bot calls use the `openviking-models` gateway identity. The memory plugin in this repository treats it as native and skips recall/capture, preventing recursive memory processing. With an older memory plugin, add `openviking-models` to its `nativeAgents` option or upgrade.

## Validation

```sh
npm run check
npm test
npm pack --dry-run
MAGPIE_BIN=/path/to/magpie node tests/magpie-smoke.mjs
```

Set `MAGPIE_BUN` to an existing Bun executable for offline host verification.

The opt-in actual-host smoke uses temporary child-process HOME/XDG directories and fake OV/vendor endpoints, with no real accounts or inference requests. `tests/ov-contract.py` accepts a temporary document on stdin to validate the real OV and Bot parsers in a repository Python environment.

No npm publication or marketplace registration has been performed. `marketplace.json` contains a proposed entry.


## Configuration files and status

| File or status | Meaning |
| --- | --- |
| `selection.json` | Magpie's model selection, read by the bridge, not OpenViking itself. Contains a gateway key; do not share it publicly |
| `adapter.json` | Connection and behavior settings; matching Magpie Options take precedence |
| `state.json` | Original configuration and recovery journal; may contain credentials. Keep until recovery completes |
| `status.json` | The last synchronization or activation-check result |
| `saved.json` | Target, full-document fingerprint and acknowledged revision of the plugin's own save; gates automatic restarts |
| `activation.json` | Restart request and verification records, used to avoid duplicate restarts |
| `paused.json` | Pause marker after manual restoration, preventing immediate reconnection |
| `saved` | The service configuration was saved; check restart requirements |
| `applied` | Activation checks completed; also inspect `ovRuntime`, `botRuntime` and `restartRequired` |
| `external_restart_required` | A configuration saved outside this plugin needs a restart; it is not restarted automatically |
| `conflict` / `apply_failed` | Manual-edit conflict or restart failure requiring attention |

Plugin files default to `~/.config/magpie-openviking-models/`. Magpie writes `selection.json`; the bridge calls the server API to update its actual `ov.conf`, then restarts and verifies as configured. Magpie's connected badge only confirms its wiring was written; it is not proof that OV switched models.

Automatic restart only applies a revision saved by this plugin, and checks its server and configuration file again immediately before requesting a restart. External pending changes are reported separately. The marker also recovers a lost save response by matching the complete intended document. Inactive Bot model IDs are ignored outside independent mode; an empty independent ID uses the Agent selection.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `root_authentication_required` / `root_permission_required` | Check the selected CLI Root key or `rootApiKeyFile`; ordinary memory credentials cannot manage models |
| `bot_not_enabled_or_detected` | Preserve Bot settings, or first enable Bot through the existing deployment method |
| `runtime_vlm_override` | Resolve the Cluster VLM runtime override; editing the startup file alone cannot prove activation |
| `adapter_locked` | Another sync/apply owns the lock. After a crash during stale-lock recovery, stop the adapter/CLI and inspect `adapter.lock.reclaim`; remove it only after verifying its recorded PID is no longer running. Keep recovery journals |
| `server_restart_unsupported` | Restart with the original systemd / Docker / process manager |
| Model unchanged after `saved` | Complete pending restarts; check Account overrides and that this is the intended OV server |
| Connected badge leaves activation unclear | Check adapter logs, `node cli.mjs doctor` and `node cli.mjs status` instead of relying on Magpie's badge |

## Marketplace metadata

[marketplace.json](marketplace.json) contains the proposed **OpenViking** listing, official icon and English/Chinese descriptions. `kind: "agent"` identifies model wiring, distinct from the memory plugin's `middleware` category. `package.json` declares `magpie.icon` for npm discovery and installed rows.

Publishing to npm does not automatically add a recommended listing. After publication, submit the entry separately to [Magpie's community registry](https://github.com/magpie-community/plugins/blob/main/registry.json). The entry does not claim the community-maintained badge.

See [Magpie's plugin documentation](https://usemagpie.ai/docs/plugins) for the host contract and installation/loading behavior.

## Hot-update scope

The Account configuration API (`PATCH /api/v1/admin/accounts/{account_id}/configuration`) can hot-update Account VLM bindings for subsequent calls. It does not hot-update root startup configuration or an already-created VikingBot model instance. This plugin manages root `ov.conf` and uses the restart lifecycle; it does not silently create an Account override instead.

## Installation and lifecycle detection

The Agent row discovers `openviking-server` or the OV config directory. `node cli.mjs doctor` reports local CLI presence separately from authenticated configuration access, restart API support and Bot state. A local server executable is not required to connect to remote OV.

The current server CLI has startup flags but no restart subcommand. The plugin uses the running instance's restart API to preserve its interpreter, arguments and existing --with-bot state. Unsupported lifecycles require the original systemd / Docker / process manager; the plugin never guesses a process to kill, launches a second service, or adds Bot startup flags.
