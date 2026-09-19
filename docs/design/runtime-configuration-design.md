# Runtime Configuration Design

## 1. Scope and goals

OpenViking has two configuration planes:

- `ov.conf` is the startup configuration. It defines the process baseline and the runtime-config source selection. It is read during startup and is never rewritten by the runtime configuration API.
- Runtime overrides are sparse values persisted by a `ConfigSource`. They can be published without rebuilding the process and are resolved independently at Cluster and Account scopes.

The runtime configuration layer is narrower than the complete `OpenVikingConfig` model. A field must be declared with `RuntimeField` to be exposed through the generic configuration API. Plain Pydantic fields remain startup-only.

The design goals are:

1. Keep the startup baseline immutable while rebuilding published Cluster configuration from sparse overrides.
2. Keep Account overrides sparse and resolve fallback at read time instead of materializing inherited values.
3. Use one PATCH and validation contract for Cluster and Account scopes.
4. Separate persistence, publication, cache invalidation, and consumer notification.
5. Allow storage providers to be replaced without coupling the manager to AGFS or a concrete database.

## 2. Configuration model

`OpenVikingConfig` is the Cluster model. `AccountConfig` is a sparse Account model; every Account field defaults to `None`, which means that the account has no explicit override. `RuntimeField` records two independent properties:

- `dynamic`: whether the field can be modified after creation;
- `fallback`: an optional Cluster field path used when an Account field is unset.

Cluster-versus-Account scope comes from the model declaring the field, not from `RuntimeField` itself. A plain `Field` is outside the runtime API surface.

Current runtime field declarations:

| Scope | Field | Lifecycle | Fallback / consumer status |
| --- | --- | --- | --- |
| Cluster | `agent_evolution` | Dynamic | Cluster default; consumed by session and Agent Evolution paths |
| Account | `vlm`, `memory`, `feishu`, `agent_evolution` | Dynamic | All four declare Cluster fallback; Agent Evolution is consumed through the manager, while the complete VLM, memory, and Feishu consumer paths are not yet wired |
| Account | `github`, `acl` | Dynamic | No Cluster fallback; GitHub and ACL consumers read the Account value through the manager |
| Account | `embedding`, `vectordb` | Create-only | Fallback to Cluster `embedding` and `storage.vectordb`; both fields must be set together when explicitly configured |

`query_planner`, Cluster `embedding`, Cluster `vlm`, Cluster `memory`, and the other plain configuration sections are not currently writable through the runtime configuration API.

## 3. RuntimeConfigManager

`RuntimeConfigManager` owns storage-independent behavior:

- sparse three-state PATCH merging;
- scope-local read/merge/write serialization;
- model construction and validation through caller-provided hooks;
- copy-on-write publication;
- changed-section calculation;
- consumer notification;
- Account lazy loading, caching, refresh, and idle eviction.

The manager does not import concrete Cluster or Account models. The service supplies hooks for reading and replacing the Cluster singleton, building a validated Cluster model, and building an `AccountConfig` model.

Cluster overrides are always applied to the immutable startup baseline. They are not merged into the previously published effective object, which prevents removed overrides from remaining accidentally materialized. Account overrides remain sparse; fallback is resolved only when an Account field is read.

## 4. PATCH and validation

Both scopes use the same request shape:

```json
{
  "settings": {
    "agent_evolution": {
      "enabled": true
    }
  }
}
```

The stored override has three states:

- an absent field keeps its current value;
- a concrete value sets or replaces the value;
- `null` removes the value at the addressed scope.

Objects merge recursively and arrays replace as a whole. A nested `null` removes only that leaf. It does not create a missing parent or remove an existing parent that becomes empty. Removing a parent requires an explicit parent-level `null`; `{}` remains an explicit empty object.

Validation has two stages:

1. Structural validation checks that every path is on the target model's `RuntimeField` surface, rejects unknown paths, and rejects `dynamic=False` paths on a non-creating PATCH.
2. The merged result is constructed into the target Pydantic model for type validation and cross-field constraints.

A `dynamic=False` field can be supplied while an Account is created, but any later PATCH touching that field is rejected. `embedding` and `vectordb` are validated as a pair, including their dimensions.

The API returns the explicit override at the addressed scope. It does not return inherited or effective values. Fallback is a whole-section operation: if an Account supplies `vlm`, omitted properties use the VLM model defaults rather than being merged property-by-property from the Cluster VLM.

## 5. Publication and consumers

A successful update follows this order:

1. Read the current sparse override.
2. Apply and validate the patch.
3. Persist the new override.
4. Publish the new Cluster object or Account cache entry.
5. Release configuration update locks.
6. Run matching consumers in registration order and wait for them.

Consumer exceptions are logged and swallowed. They do not roll back persistence or publication, so a successful PATCH confirms that the configuration layer was updated, not that every derived client has applied it. Consumers may update in-memory state or invalidate derived clients, but must not write configuration or recursively publish another configuration update.

Cluster publication uses atomic pointer replacement. The new object is built and validated before the singleton is swapped, so concurrent readers see either the old or new object. Account consumers are notified of eviction independently of section filters so they can drop account-local derived state.

The current implementation only wires a subset of declared fields into business consumers. Account GitHub, ACL, and Agent Evolution reads use the manager. Account `vlm`, `memory`, `feishu`, `embedding`, and `vectordb` can be validated and persisted, but their complete business read paths remain follow-up work.

## 6. Persistence

The built-in file source is selected by the startup configuration:

```json
{"runtime_config": {"source": "file"}}
```

It stores Account overrides at `/local/{account_id}/_system/setting.json` and Cluster overrides at `/local/_system/runtime_config/cluster.json`. Before replacing an existing file, it writes the previously read AGFS-visible bytes to the corresponding backup file. Reads recover from a valid backup when the primary is missing or invalid; if neither file is valid JSON, the read fails closed instead of treating corruption as an empty override.

The file source relies on exact-path AGFS locks and lease-bound writes. Keeping the historical path and JSON format does not make concurrent old and new writers safe by itself. The source does not interpret provider parameters such as `namespace`; deployment isolation is provided by the mounted storage.

`memory` is an explicitly volatile source. External sources implement the `ConfigSource` contract, register a factory, own their `params` interpretation, protect credentials, and provide idempotent scope deletion.

## 7. Refresh and failure behavior

The manager refreshes the Cluster scope and loaded Account scopes every 30 seconds. Accounts unused for 24 hours are evicted from the process cache.

- A refresh failure retains the last valid published configuration and retries later.
- A first load failure is returned to the caller.
- An administrative read reads the explicit persisted override rather than an inherited effective value.

This distinction prevents a transient storage failure from replacing a known-good in-memory configuration with defaults.

## 8. Compatibility and verification

The old Account `settings` endpoints and the Agent Evolution endpoint remain compatibility adapters. They translate writes into `RuntimeConfigManager` operations and do not maintain a second persistence path.

Extended Account sections should not be written while old replicas share the same Account directory if those replicas reject unknown fields in `setting.json`.

The focused mechanism tests are:

```bash
pytest tests/config/test_runtime_config.py
```

They cover field discovery, three-state merging, dynamic/create-only validation, lazy Account loading, publication isolation, consumer notification, idle eviction, fallback, and file-source backup behavior.
