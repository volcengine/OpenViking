# OpenViking Assets Resolver

The OpenViking Assets Resolver parses and validates an
[`openviking-assets/1`](../guides/18-openviking-assets.md) Catalog and Manifest,
then returns a normalized asset plan for a client to execute. It does not clone
repositories, create resources, or start synchronization jobs.

In normal use, run `openviking assets create`, `openviking assets sync`, or
`openviking assets watch`; those commands call this endpoint automatically.
Call the Resolver directly only when implementing a custom client.

## Resolve a Catalog and Manifest

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
| `manifest_yaml` | string | Yes | — | Complete Manifest YAML, 1–1,000,000 characters |
| `catalog_yaml` | string | Yes | — | Complete Catalog YAML, 1–4,000,000 characters |
| `manifest_label` | string | No | `manifest.yaml` | Manifest source label used in errors, 1–1,024 characters |
| `catalog_label` | string | No | `assets.yaml` | Catalog source label used in errors, 1–1,024 characters |

Example:

```bash
curl -X POST "${OPENVIKING_BASE_URL}/api/v1/openviking-assets/resolve" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${OPENVIKING_API_KEY}" \
  --data-binary @- <<'JSON'
{
  "manifest_yaml": "protocol: openviking-assets/1\ncatalog: assets.yaml\nassets:\n  - name: openviking\n",
  "catalog_yaml": "protocol: openviking-assets/1\nassets:\n  openviking:\n    connector: git\n    repo_url: https://github.com/volcengine/OpenViking\n    branch: main\n    watch_interval: 1440\n",
  "manifest_label": "manifests/code-qa.yaml",
  "catalog_label": "assets.yaml"
}
JSON
```

### Success response

```json
{
  "status": "ok",
  "result": {
    "protocol": "openviking-assets/1",
    "manifest": "manifests/code-qa.yaml",
    "catalog": "assets.yaml",
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

### Error responses

Protocol or content validation failures return HTTP `400` with the error code
`INVALID_ARGUMENT`. Common causes include:

- malformed YAML or unknown fields;
- a `protocol` other than `openviking-assets/1`;
- a non-empty `include`, which v1 does not support;
- a Manifest referencing an asset absent from the Catalog;
- an invalid connector, repository URL, Git reference, or asset identity;
- duplicate asset identities in one Manifest.

Empty fields, incorrect field types, or length-limit violations are rejected by
request validation with HTTP `422`.

## Related documentation

- [OpenViking Assets protocol and operations guide](../guides/18-openviking-assets.md)
- [Resource Management API](02-resources.md)

