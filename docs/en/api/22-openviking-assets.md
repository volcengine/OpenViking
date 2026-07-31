# OpenViking Assets Resolver

The OpenViking Assets Resolver parses and validates an
[`openviking-assets/1`](../guides/18-openviking-assets.md) Manifest — either
self-contained, with assets defined under its `catalog` field, or paired with a
separate Catalog file — then returns a normalized asset plan for a client to
execute. It does not clone repositories, create resources, or start
synchronization jobs.

In normal use, run `ov add-resource --manifest <file>`; the CLI calls the
Resolver and permission-preflight endpoints automatically. Call these endpoints
directly only when implementing a custom client.

## Resolve a Manifest

```http
POST /api/v1/openviking-assets/resolve
```

### Authentication

The endpoint uses the standard OpenViking Server authentication mechanism. When
API-key authentication is enabled, include:

```http
X-API-Key: <your-api-key>
```

### Request body

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `manifest_yaml` | string | Yes | — | Complete Manifest YAML, 1–4,000,000 characters |
| `catalog_yaml` | string | No | — | Complete Catalog YAML, 1–4,000,000 characters. Required when the Manifest selects assets by name; must be omitted when the Manifest defines assets under `catalog`. |
| `manifest_label` | string | No | `manifest.yaml` | Manifest source label used in errors, 1–1,024 characters |
| `catalog_label` | string | No | `catalog.yaml` | Catalog source label used in errors, 1–1,024 characters |

Example with a self-contained Manifest:

```bash
curl -X POST "${OPENVIKING_BASE_URL}/api/v1/openviking-assets/resolve" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${OPENVIKING_API_KEY}" \
  --data-binary @- <<'JSON'
{
  "manifest_yaml": "protocol: openviking-assets/1\ncatalog:\n  - name: openviking\n    connector: git\n    watch_interval: 1440\n    params:\n      repo_url: https://github.com/volcengine/OpenViking\n      branch: main\n",
  "manifest_label": "manifest.yaml"
}
JSON
```

When a Manifest selects assets by name, send the Catalog YAML in `catalog_yaml`
and its label in `catalog_label`.

### Success response

```json
{
  "status": "ok",
  "result": {
    "protocol": "openviking-assets/1",
    "manifest": "manifest.yaml",
    "catalog": "manifest.yaml",
    "assets": [
      {
        "name": "openviking",
        "connector": "git",
        "repo_url": "https://github.com/volcengine/OpenViking",
        "branch": "main",
        "auth_ref": null,
        "watch_interval": 1440.0,
        "locator": "github.com/volcengine/OpenViking",
        "git_ref": "main",
        "asset_id": "a1b2c3d4e5f6"
      }
    ]
  }
}
```

Where:

- `locator` is the normalized repository locator.
- `git_ref` is the resolved Git reference.
- `asset_id` is a stable 12-character identifier derived from the connector,
  normalized locator, and Git reference. The value above illustrates the format.
- `watch_interval` is measured in minutes.
- `catalog` echoes `catalog_label`; for a self-contained Manifest it equals the
  Manifest label.

### Error responses

Protocol or content validation failures return HTTP `400` with the error code
`INVALID_ARGUMENT`. Common causes include:

- malformed YAML or unknown fields;
- a `protocol` other than `openviking-assets/1`, or a Manifest that defines
  `catalog` without declaring `protocol`;
- a non-empty `include`, which v1 does not support;
- a Manifest that defines `catalog` submitted together with `catalog_yaml`;
- a Manifest that selects assets by name without any `catalog_yaml`;
- a Manifest referencing an asset absent from the Catalog;
- an invalid connector, repository URL, Git reference, or asset identity;
- duplicate asset identities in one Manifest.

Empty fields, incorrect field types, or length-limit violations are rejected by
request validation with HTTP `422`.

## Preflight Git repository access

```http
POST /api/v1/openviking-assets/preflight
```

This endpoint runs read-only `git ls-remote` in the OpenViking Server execution
environment to verify that a repository and optional ref are readable. It does
not clone the repository, create a resource, or start a task. Manifest mode
calls it during both dry-run and pre-submission validation.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | Yes | Asset name |
| `connector` | string | Yes | Must currently be `git` |
| `repo_url` | string | Yes | Git clone URL |
| `branch` | string | No | Branch or tag to verify; the remote `HEAD` is checked when omitted |
| `auth_config.username` | string | No | HTTP Basic username; defaults to `oauth2` |
| `auth_config.token` | string | No | One-shot Git token; never persisted |

```bash
curl -X POST "${OPENVIKING_BASE_URL}/api/v1/openviking-assets/preflight" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${OPENVIKING_API_KEY}" \
  -d '{
    "name": "private-repository",
    "connector": "git",
    "repo_url": "https://github.com/example/private-repository",
    "branch": "main",
    "auth_config": {
      "username": "oauth2",
      "token": "<github-token>"
    }
  }'
```

When a token is provided explicitly, preflight does not fall back to a server
Git credential helper. The token is passed through the child-process
environment and does not appear in Git command arguments or the response.

### Success response

```json
{
  "status": "ok",
  "result": {
    "name": "private-repository",
    "connector": "git",
    "locator": "github.com/example/private-repository",
    "git_ref": "main",
    "accessible": true
  }
}
```

### Error responses

| HTTP status | Error code | Meaning |
| --- | --- | --- |
| `403` | `PERMISSION_DENIED` | Repository missing, invalid credentials, or insufficient read permission |
| `404` | `NOT_FOUND` | Repository is readable, but the requested branch/tag is absent |
| `503` | `UNAVAILABLE` | DNS, connection, or Git executable unavailable |
| `504` | `DEADLINE_EXCEEDED` | Permission preflight exceeded 15 seconds |

## Related documentation

- [OpenViking Assets protocol and operations guide](../guides/18-openviking-assets.md)
- [Resource Management API](02-resources.md)
