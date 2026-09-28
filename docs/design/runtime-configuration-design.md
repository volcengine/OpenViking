# Runtime Configuration Design

## 1. Scope and goals

OpenViking has two configuration planes:

- `ov.conf` is the startup configuration. It defines the process baseline and the runtime-config source selection. It is read during startup and is never rewritten by the runtime configuration API.
- Runtime settings are persisted independently for Cluster and each Account by a `ConfigSource`. They can be published without rebuilding the process.

The runtime configuration layer is narrower than the complete `OpenVikingConfig` model. A field must be declared with `RuntimeField` to be exposed through the generic configuration API. Plain Pydantic fields remain startup-only.

The design goals are:

1. Keep the startup baseline immutable while rebuilding published Cluster configuration from its runtime settings.
2. Keep Account configuration independent from Cluster configuration.
3. Use one PATCH and validation contract for Cluster and Account scopes.
4. Separate persistence, publication, cache invalidation, and consumer notification.
5. Allow storage providers to be replaced without coupling the manager to AGFS or a concrete database.
6. Keep business defaults and Account/Cluster composition outside the generic configuration manager.

## 2. Configuration model

`OpenVikingConfig` is the Cluster model. `AccountConfig` is the Account model.
An optional Account field set to `None` means only that the Account has not
configured that value. The consuming business decides what absence means.
`RuntimeField` records lifecycle metadata:

- `dynamic`: whether the field can be modified after creation;
- `fallback`: a deprecated compatibility-only Cluster field path.

Legacy `fallback` applies only to a completely unset top-level Account section
and returns the complete Cluster section. It does not recursively merge fields.
New configuration must not use it; business resolvers should explicitly combine
the independently published Account and Cluster models when that is the desired
behavior.

Runtime fallback in the current VLM and vector resolvers also keeps existing
Account documents usable when those documents predate complete Account settings.
This is a migration compatibility path, not the target lifecycle for new
configuration. New Account provisioning and new Account-scoped features should
use Cluster configuration only as a creation template: materialize a complete
Account-owned configuration during create, persist it with create-if-absent
semantics, and thereafter change it only through that Account's API. Later
Cluster changes then define defaults for future Accounts and do not implicitly
change existing Accounts. Until existing Account documents are migrated or
materialized, their missing sections continue to use the current resolver
fallback behavior.

Cluster-versus-Account scope comes from the model declaring the field, not from `RuntimeField` itself. A plain `Field` is outside the runtime API surface.

Current runtime field declarations:

| Scope | Field | Lifecycle | Fallback / consumer status |
| --- | --- | --- | --- |
| Cluster | `agent_evolution` | Dynamic | Cluster default; consumed by session and Agent Evolution paths |
| Account | `agent_evolution` | Dynamic | Uses deprecated whole-section fallback to Cluster `agent_evolution` for compatibility |
| Account | `feishu` | Dynamic | No declarative fallback; an unset section uses Cluster `feishu`, while a set section uses Account values/defaults for every field except the Cluster-owned `domain`. Used by Feishu imports, preflight, and watch token refresh |
| Account | `github`, `acl` | Dynamic | No Cluster fallback; GitHub and ACL consumers read the Account value through the manager |
| Account | `vlm`, `query_planner` | Dynamic | ROOT-only; the VLM resolver combines Account-owned model identity and credentials with selected Cluster runtime behavior |
| Account | `embedding` | Mixed | ROOT-only; credentials and runtime policy are dynamic, while model identity, vector-space fields, text source and input token limit are create-only |
| Account | `vectordb` | Create-only | ROOT-only; an explicit Account section owns the remote connection and authentication; an absent section selects the Cluster backend |

Account `memory` is not declared on the current Account runtime model and is rejected by both creation-time settings validation and later PATCH requests. ADMIN responses omit `vlm`, `query_planner`, `embedding`, and `vectordb`; only ROOT can read or write these sections. Cluster `embedding`, `vlm`, `query_planner`, `memory`, `feishu`, and the other plain Cluster configuration sections are not writable through the runtime configuration API.

An Account VLM or Query Planner section requires `model` and a non-empty
`credentials` array. Model-service identity, endpoints and credentials come from
that Account section. Selected runtime behavior comes from Cluster; Account
`timeout`, when set, overrides Cluster `timeout`, and an unset or reset value
uses Cluster `timeout`. Query Planner selection is Account `query_planner`,
Account `vlm`, Cluster `query_planner`, then Cluster `vlm`.

The vector resolver resolves and validates Embedding and VectorDB together.
Account embedding may contain only runtime policy or an explicit empty object;
without an Account model mode, it uses Cluster model bindings. Declared Account
model modes replace the Cluster mode set, and their credential arrays provide
complete provider bindings without borrowing Cluster connection or authentication
fields. An explicit Account VectorDB section similarly owns its complete
connection and authentication, even when it uses the same backend type as Cluster.
Account backends support `http`, `volcengine`, and `vikingdb`; their remote
collections, indexes and authorization must already be provisioned externally.
See the [Admin API](../en/api/08-admin.md#runtime-configuration) for the field
surface and compatibility requirements.

## 3. RuntimeConfigManager

`RuntimeConfigManager` owns storage-independent behavior:

- three-state PATCH merging within one scope;
- scope-local read/merge/write serialization;
- model construction and validation through caller-provided hooks;
- copy-on-write publication;
- changed-section calculation;
- consumer notification;
- Account lazy loading, caching, refresh, and idle eviction.

The manager does not import concrete Cluster or Account models. The service supplies hooks for reading and replacing the Cluster singleton, building a validated Cluster model, and building an `AccountConfig` model.

Cluster runtime settings are always applied to the immutable startup baseline.
They are not merged into the previously published Cluster object, which prevents
removed values from remaining accidentally materialized. Account settings are
constructed and published independently. The manager retains whole-section
fallback only for existing compatibility fields; domain-specific defaults are
resolved by business code.

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

The PATCH has three states:

- an absent field keeps its current value;
- a concrete value sets or replaces the value;
- `null` removes the value at the addressed scope.

Objects merge recursively and arrays replace as a whole. A nested `null` removes only that leaf. It does not create a missing parent or remove an existing parent that becomes empty. Removing a parent requires an explicit parent-level `null`; `{}` remains an explicit empty object.

Validation has three stages:

1. Structural validation checks that every path is on the target model's `RuntimeField` surface, rejects unknown paths, and rejects `dynamic=False` paths on a non-creating PATCH.
2. The merged result is constructed into the target Pydantic model for type validation and cross-field constraints.
3. Registered Account candidate validators check the business-effective configuration before persistence. Vector validation checks the complete Embedding/VectorDB pair on creation, loading, refresh and PATCH; an embedding update also checks that an existing Account's mode and dimension remain unchanged.

A `dynamic=False` field can be supplied in Account creation `settings`, but later additions, changes and resets are rejected, including ROOT requests. This applies to the whole `vectordb` section and to embedding model identity, dimension, input, query/document parameters, version, `text_source` and `max_input_tokens`. Removing a parent object cannot bypass the restrictions on its stored create-only fields.

The API returns settings from the addressed scope, not a business-effective
Account/Cluster composition. Declarative fallback is deprecated compatibility
behavior, applies only to a whole section, and is currently used by Account
`agent_evolution`. Feishu demonstrates the preferred design: its business
resolver receives Account and Cluster configuration and applies Feishu-specific
defaulting without extending the generic manager.

## 5. Publication and consumers

A successful update follows this order:

1. Read the current settings for the addressed scope.
2. Apply and validate the patch.
3. Persist the new override.
4. Publish the new Cluster object or Account cache entry.
5. Release configuration update locks.
6. Run matching consumers in registration order and wait for them.

Consumer exceptions are logged and swallowed. They do not roll back persistence or publication, so a successful PATCH confirms that the configuration layer was updated, not that every derived client has applied it. Consumers may update in-memory state or invalidate derived clients, but must not write configuration or recursively publish another configuration update.

Cluster publication uses atomic pointer replacement. The new object is built and validated before the singleton is swapped, so concurrent readers see either the old or new object. Account consumers are notified of eviction independently of section filters so they can drop account-local derived state.

All currently declared Account sections have business consumers. GitHub, ACL,
and Agent Evolution read through the manager; Feishu is resolved for resource
imports, source preflight, and watch token refresh. Account VLM and Query Planner
configuration is resolved for model work and retrieval planning. Account
Embedding and VectorDB configuration is used by queries, queue writes, reindexing,
OVPack and account-scoped observers. Configuration consumers invalidate derived
model and vector resources after relevant updates or Account eviction. Account
`memory` remains outside the runtime configuration API.

Embedding and vector backend resources track active operations so retirement
waits for in-flight work. Account VLM callers retain an account-bound proxy
rather than a model client. Each asynchronous model call borrows the current
client; configuration invalidation retires the old client and closes it after
that call completes. No task-wide configuration lease is used.

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

The old Account `settings` endpoints, the Agent Evolution endpoint, and
declarative whole-section fallback remain compatibility adapters. New Account
features should read complete Account-owned configuration directly. Cluster
defaults should be materialized by Account provisioning rather than introduced
as new runtime fallback dependencies. Existing resolver fallback remains until
pre-materialization Account documents have been migrated.

Extended Account sections should not be written while old replicas share the same Account directory if those replicas reject unknown fields in `setting.json`.

The focused mechanism tests are:

```bash
.venv/bin/python -m pytest tests/config --no-cov
```

They cover field discovery, three-state merging, runtime-surface validation,
lazy Account loading, publication isolation, consumer notification, idle
eviction, legacy whole-section fallback, Feishu business resolution, and
file-source backup behavior, together with Account vector compatibility,
credential isolation, cache invalidation and embedding resource lifecycle.
