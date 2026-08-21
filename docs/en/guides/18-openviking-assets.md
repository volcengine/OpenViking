# OpenViking Assets

> Experimental. The `openviking-assets/1` protocol and CLI behavior may change in later releases.

OpenViking Assets describes what a knowledge base should contain as declarative files. In the
simplest form, one Manifest file defines the assets to ingest. A team can also keep its ingestible
sources in a shared Catalog and write Manifests that select named assets for different use cases.
Applying a Manifest creates or updates each resource and stores the mapping between assets and
`viking://` resources locally.

It is intended for multi-repository code knowledge bases, shared documentation sets, and other
resource collections that need to be reproducible and continuously refreshed.

## How It Differs from Other Resource Operations

| Capability | Description |
| --- | --- |
| `ov add-resource <source>` | Adds or updates one resource; it describes one operation. |
| OpenViking Assets | Declares the expected composition of a resource set for review, sharing, and repeated application. |
| OVPack | Exports or imports an existing data snapshot, including content and optional index data. |

OpenViking Assets does not replace the existing ingestion pipeline. Git fetching, parsing,
semantic extraction, vectorization, and Watch refreshes still use `add_resource` and server-side
connectors. Assets adds only the declaration, resolution, and per-asset orchestration layers.

## Conceptual Model

OpenViking Assets has three primary objects:

- **Manifest**: the file you apply. It defines the assets to ingest directly under `catalog:`, or
  selects assets by name from a separate Catalog file.
- **Catalog**: the inventory of sources a team can ingest, including source locations, branches,
  default refresh intervals, and credential aliases. It is a separate file only when several
  Manifests share it; otherwise it lives inside the Manifest.
- **State**: the result of the last Manifest application and the mapping from assets to
  `viking://` resources.

```text
manifest.yaml (+ catalog.yaml when a shared Catalog is used)
          |
          v
Server resolves and validates openviking-assets/1
          |
          v
Resolved Assets
          |
          v
CLI resolves local credentials and State
          |
          v
One add_resource call per asset -> viking:// resources
```

The server is the authoritative protocol parser. The CLI sends the raw Manifest YAML — plus the
Catalog YAML when a separate Catalog file is used — to the configured OpenViking service. The
server validates them and returns an execution plan; the resolver endpoint itself does not create
resources.

## Protocol

### Manifest

A Manifest describes one knowledge-base build. In the simplest form it is the only file you need:
define the assets directly under `catalog:`:

```yaml
protocol: openviking-assets/1

defaults:
  git:
    auth_ref: team-git
    watch_interval: 60

catalog:
  - name: openviking
    connector: git
    description: OpenViking main repository
    params:
      repo_url: https://github.com/volcengine/OpenViking
      branch: main

  - name: requests
    connector: git
    description: Requests HTTP client source
    watch_interval: 0
    params:
      repo_url: https://github.com/psf/requests
      branch: main

assets: [openviking]   # optional; omit to apply every asset defined above
```

Manifest top-level fields:

| Field | Required | Description |
| --- | --- | --- |
| `protocol` | Yes when `catalog` is present | Must currently be `openviking-assets/1`. Optional for Manifests that only select names, but still checked when set. |
| `defaults` | No | Connector defaults for the assets defined in this Manifest; only allowed together with `catalog`. |
| `catalog` | No | The list of asset definitions (fields below). A Manifest that defines `catalog` is complete on its own. |
| `assets` | See description | Asset names to apply. Optional when `catalog` is in the same file — omitting it applies every defined asset. Required when the definitions live in a separate Catalog file. |
| `include` | No | v1 cannot compose other Manifests; a non-empty value fails resolution. |

Duplicate selected names are removed while preserving their first position. Selecting an unknown
asset fails the whole resolution.

`defaults.git` supports:

| Field | Description |
| --- | --- |
| `auth_ref` | Default alias in the local credentials file. |
| `watch_interval` | Default Watch interval in minutes; `0` disables automatic refresh. |

Git assets support:

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Unique asset name matching `[A-Za-z0-9][A-Za-z0-9._-]*`. |
| `connector` | Yes | v1 supports only `git`. |
| `description` | No | Human-readable purpose of the asset. |
| `params.repo_url` | Yes | Git clone URL. |
| `params.branch` | No | Branch to ingest; it cannot be empty when set. |
| `auth_ref` | No | Overrides `defaults.git.auth_ref`. |
| `watch_interval` | No | Overrides `defaults.git.watch_interval`. |

Validation is strict. Unknown fields, duplicate names, and unsupported connectors fail the whole
resolution, even for assets the current run does not select. `params` contents and clone URL
safety are validated for the selected assets. The same rules apply wherever the definitions live —
in the Manifest's `catalog` or in a separate Catalog file.

### Sharing a Catalog Across Manifests

When several Manifests reuse the same sources, move the asset definitions into a Catalog file,
normally named `catalog.yaml`. A Catalog holds `protocol`, optional `defaults`, and the same
`catalog` block — a Catalog file is simply a Manifest that selects nothing:

```yaml
protocol: openviking-assets/1

defaults:
  git:
    auth_ref: team-git
    watch_interval: 60

catalog:
  - name: openviking
    connector: git
    description: OpenViking main repository
    params:
      repo_url: https://github.com/volcengine/OpenViking
      branch: main

  - name: requests
    connector: git
    description: Requests HTTP client source
    watch_interval: 0
    params:
      repo_url: https://github.com/psf/requests
      branch: main
```

Each Manifest then only selects names:

```yaml
assets:
  - openviking
  - requests
```

The team maintains one Catalog; editing an asset there updates every Manifest that selects it.
Because the two documents share a shape, a Catalog can also be applied directly with
`ov add-resource -m catalog.yaml`, which ingests everything it defines.

The CLI locates the Catalog file as follows:

1. The path passed to `--args catalog:<file>`, resolved from the current working directory.
2. `catalog.yaml` next to the Manifest when `catalog` is omitted.

A Manifest that defines `catalog` itself never uses a separate Catalog file; passing one with it
fails resolution.

### Asset Identity

The server generates a stable `asset_id` from:

```text
connector + normalized locator + ref
```

Git URL normalization removes the protocol, user prefix, host port, trailing `.git`, and trailing
slashes, and lowercases the host. HTTPS, SSH, and SCP-style URLs for the same repository therefore
normally produce the same locator, while different branches produce different assets.

The asset name is not part of the identity. Renaming an asset without changing its source and
branch keeps it associated with the existing resource. Changing the source or branch produces a
new asset and leaves the previous one as an orphan.

For safety, clone URLs cannot:

- be empty or contain control characters;
- begin with `-`;
- use Git remote-helper transports such as `ext::` or `fd::`.

## Quick Start

### Prerequisites

1. Install an `ov` CLI version that supports OpenViking Assets.
2. Configure an OpenViking service that provides `/api/v1/openviking-assets/resolve`.
3. Verify the connection:

```bash
ov health
```

### Write and Validate a Manifest

Create `manifest.yaml`:

```yaml
protocol: openviking-assets/1

catalog:
  - name: openviking
    connector: git
    params:
      repo_url: https://github.com/volcengine/OpenViking
      branch: main
```

Validate it first:

```bash
ov add-resource --manifest manifest.yaml --args dry_run:true
```

`dry_run`:

- reads the local YAML file, plus the Catalog file when one is used;
- asks the configured OpenViking service to resolve and validate the protocol;
- checks that all selected `auth_ref` aliases resolve locally;
- asks the server to run a read-only `git ls-remote` permission preflight for every repository
  with the effective credentials;
- prints the create or sync action planned for each asset;
- does not clone repositories, submit resources, create tasks, or write State.

If any repository is unreadable, dry-run exits immediately with `PERMISSION_DENIED` and does not
produce an executable plan.

### Apply the Manifest

Remove `dry_run` after reviewing the plan:

```bash
ov add-resource --manifest manifest.yaml
```

Wait for each resource to finish processing:

```bash
ov add-resource --manifest manifest.yaml --wait --timeout 600
```

The repository contains a complete example — a shared Catalog plus a Manifest that selects from
it — under
[`examples/openviking-assets`](https://github.com/volcengine/OpenViking/tree/main/examples/openviking-assets).

## Credentials

Manifests and Catalogs carry only `auth_ref` aliases and must not contain tokens, passwords, or
private keys.
The CLI resolves aliases from this file by default:

```text
~/.openviking/openviking_assets_credentials.yaml
```

Example:

```yaml
credentials:
  team-git:
    username: oauth2
    token: replace-with-your-token
```

Override the path with:

```bash
export OPENVIKING_ASSETS_CREDENTIALS_FILE=/secure/path/assets-credentials.yaml
```

Before submitting any resource, the CLI resolves every selected `auth_ref`, then the server runs
`git ls-remote` in the execution environment to verify read access to every repository. A missing
alias or unreadable repository fails the whole operation before the first submission; dry-run
performs the same preflight. `username` and `token` are the only supported fields under a native
Git credentials alias; keep them flat as shown above. For the standard, native Git path, the CLI sends them to `add_resource` as
`args.auth_config`, while `branch` or `commit` remains a top-level member of `args`. Resolved Git
arguments are sent over the configured OpenViking service connection. Use TLS for remote
deployments and restrict local access to the credentials file.

When the effective `watch_interval` is positive, OpenViking stores an HTTPS Git token resolved
from `auth_ref` in the Watch task's private, repository-bound authentication state. The token is
not written to Manifest State, ordinary ingestion queues, or watch API/MCP/CLI responses. A zero
interval keeps the token request-local. Git PATs have no generic refresh flow, so recreate the
Watch when a token expires or is revoked.

Private watch state is stored in `viking://resources/.watch_tasks.json`. It is encrypted at rest
when VikingFS file encryption is enabled; otherwise the server-side control file and its backup
contain plaintext token state. Restrict server storage access and enable encryption in production.

Native credential imports wait for clone and parse before the server returns a task, even without
`--wait`; the CLI therefore uses a 300-second request timeout by default for these assets. Use
`--timeout <seconds>` for a larger repository. The token is carried in the HTTPS request body, so
keep diagnostic request-body dumping disabled in production.

Omit `auth_ref` when the target service already has the SSH keys or other authentication needed to
access the repository.

## Create, Sync, and State

After a non-dry-run application, the CLI writes this file next to the Manifest:

```text
<manifest-file>.state.json
```

For example:

```text
manifest.yaml.state.json
```

State uses the `openviking-assets-state/1` protocol and records:

- the `asset_id`, name, connector, locator, and ref;
- the corresponding `resource_uri` and `task_id`;
- the latest status, error, and application time.

Application rules:

| Condition | Behavior |
| --- | --- |
| State has no resource URI for the `asset_id` | Create a new resource. |
| State has an existing resource URI | Sync by passing the URI as `to` to `add_resource`. |
| An asset is no longer selected | Report it as an orphan; keep its resource and State entry. |
| The source or branch changes the `asset_id` | Create a new asset and report the old one as an orphan. |

State belongs to one execution environment and is not part of the Catalog or Manifest protocol.
A repository that shares Manifests should normally add this to its `.gitignore`:

```text
*.state.json
```

Do not apply the same Manifest concurrently. The current State file has no cross-process lock.

Content-level synchronization cursors do not live in Manifest State. Continuous refreshes are
managed by OpenViking Watches and connectors.

## Refresh Intervals

`watch_interval` precedence, from highest to lowest, is:

1. CLI `--watch-interval`;
2. per-asset `watch_interval`;
3. `defaults.git.watch_interval`;
4. `0`, which disables automatic refresh.

Temporarily apply a 60-minute interval to every selected asset:

```bash
ov add-resource --manifest manifest.yaml --watch-interval 60
```

Subsequent content refreshes are performed by Watches. For native HTTPS Git assets using
`auth_ref`, the server restores the repository-bound token from private Watch state for each
refresh. Reapply assets to pick up Catalog or Manifest composition changes, retry failures, or
explicitly trigger synchronization.

## Failure Handling

Permission preflight runs before every resource submission. If any asset fails preflight:

1. the command exits immediately with the original error code, such as `PERMISSION_DENIED`;
2. no asset is submitted and no background task is created;
3. State is not written;
4. `skip_failed` does not bypass the preflight failure.

Per-asset execution starts only after all preflights succeed.

The default behavior is fail-fast:

1. the current asset fails;
2. later assets are marked not attempted;
3. successful assets and the failure are written to State;
4. the command exits non-zero.

Use `skip_failed` to continue with the remaining assets:

```bash
ov add-resource --manifest manifest.yaml --args skip_failed:true
```

`skip_failed` does not turn a partial failure into success. The command still exits non-zero when
any asset fails, and successfully created resources are not rolled back. If every asset fails, the
command reports that nothing was applied successfully.

## CLI Options

Options used with `--manifest`:

| Option | Description |
| --- | --- |
| `-m, --manifest <file>` | Manifest file. |
| `--args <key:value,...>` | Manifest-run options, comma-separated; supported keys below. |
| `--wait` | Wait for each resource to finish processing. |
| `--timeout <seconds>` | HTTP request timeout. Native private Git imports honor it even without `--wait`; their default is 300 seconds. |
| `--watch-interval <minutes>` | Override the refresh interval for all assets. |

Run options supported by `--args`:

| Key | Description |
| --- | --- |
| `catalog:<file>` | Separate Catalog file for Manifests that select assets by name; defaults to `catalog.yaml` next to the Manifest. Not used when the Manifest defines `catalog` itself. |
| `dry_run:true` | Resolve the protocol and validate read access to every repository without submitting resources, creating tasks, or writing State. |
| `skip_failed:true` | Continue processing after an asset fails. |

`--args` accepts the comma-separated `key:value,...` form and a full JSON object, e.g.
`--args '{"dry_run": true, "catalog": "shared/catalog.yaml"}'`.

Run options are consumed locally by the CLI and are never sent to the server as resource
arguments; an unknown key is an error.

## Current Limitations

`openviking-assets/1` currently has these boundaries:

- only Git assets are supported;
- Manifests are flat and cannot recursively `include` other Manifests;
- the server resolver returns a plan and does not perform batch submission;
- the server preflight uses read-only `git ls-remote` to check repository access and does not
  download repository contents;
- the CLI executes assets sequentially;
- orphans are never deleted automatically;
- `ov share` pointer codes and exporting a Manifest from an existing knowledge base are not
  included;
- State is a local file and is not synchronized across machines;
- the CLI and server must both support the same protocol version.

## Related Documentation

- [OpenViking Assets API](../api/22-openviking-assets.md)
- [Resource Management API](../api/02-resources.md)
- [Resource Watch API](../api/15-watches.md)
- [OVPack Import and Export](09-ovpack.md)
- [OpenViking Assets Examples](https://github.com/volcengine/OpenViking/tree/main/examples/openviking-assets)
