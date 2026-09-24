---
description: Choose hosted, self-managed, or private delivery and prepare infrastructure, models, storage, and acceptance checks.
---

# Deployment Checklist

Choose how the service will run before provisioning infrastructure. If you already have an endpoint and API key, connect through the [CLI quick start](../getting-started/02-quickstart.md).

| Goal | Deployment path | Next step |
| --- | --- | --- |
| Let the platform operate the infrastructure | Volcengine hosted OpenViking | [Hosted service entry](03-deployment.md) |
| Run the open-source service yourself | Python, Docker, or the open-source Helm chart | [Server deployment](03-deployment.md) |
| Deploy VikingDB / OpenViking on your infrastructure | Kubernetes, Operators, and ovadmin | [Enterprise Deployment](20-private-deployment.md) |

Private delivery has its own components, licensing, and configuration lifecycle. Requirements marked as private delivery do not apply to every OpenViking installation.

## For every self-managed installation

| Area | Decide before deployment | Reference |
| --- | --- | --- |
| Workload | Document count and size, vector dimensions, concurrency, ingestion frequency, retention | [Observability](05-observability.md) |
| Models | Provider, model name, API base, credentials, output dimensions, rate limits, timeouts, connectivity from the runtime | [Configuration](01-configuration.md) |
| Storage | Persistent workspace location, capacity, permissions, backup destination, restore procedure | [Storage](../concepts/05-storage.md), [snapshots](15-snapshot.md) |
| Network and identity | Client endpoint, TLS, DNS, proxy, administrator and application keys | [Public access](12-public-access.md), [authentication](04-authentication.md) |
| Operations | Owners for alerts, upgrades, credentials, and recovery | [Diagnostics](05-observability.md) |

Measure capacity with representative data. A running container or a successful health probe does not demonstrate that the deployment can handle your workload.

## Private delivery: versions and environment

Review the environment and version requirements below, then confirm component compatibility against your deployment package.

| Area | Requirement | Verify |
| --- | --- | --- |
| OS / CPU | Linux, x86_64 by default | arm64 requires matching images and binaries |
| Kubernetes | Compatibility matrix specifies 1.24+ | Actual version, CRDs, CNI, CSI, RBAC; no maximum version is declared |
| Helm / kubectl | Helm 3.x, not Helm 4; kubectl within one minor of the cluster | Tool versions on the deployment host |
| Profile | `standalone` for demos and reproductions; `cluster` for cluster evaluation | Choose a profile for the environment and verify replicas, scheduling, and recovery |
| Release combination | ovadmin, VikingDB, OpenViking runtime, and Operators form a delivery set | Record `ovadmin version --output json`; the archive version is not the runtime version |

The package gives a cluster planning reference of at least three machines with 32 CPU cores, 256 GiB RAM, and 2 TiB local disk per machine. **These are not minimum requirements for open-source OpenViking or a validated capacity guarantee.** Suitability depends on index size, vector dimensions, QPS, build concurrency, replicas, and dependency placement. A three-node deployment that also hosts infrastructure dependencies is a starting point for development, demos, or POCs.

## Private delivery: infrastructure checklist

| Area | Confirm |
| --- | --- |
| Permissions | Deployment kubeconfig; CRD and RBAC creation; Operator access to target namespaces; use the package permission matrix |
| Namespaces | Application and Operator namespaces, illustrated as `vikingdb` and `viking-system`; Secret scope |
| Scheduling | Default `cluster` selectors are `nodeLevel=online` / `nodeLevel=offline`; matching labels, tolerations, and resources |
| Registry | Full repository prefix, delivery tags, pull credentials; validate host login and cluster pulls separately |
| Storage | Available StorageClass; separate settings for VikingDB data, OpenViking workspace PVCs, and external dependencies |
| Network | Pod / Service DNS, API Server, dependency and model access, cluster DNS suffix, external endpoint and TLS |
| License | When enabled, a license matching the cluster fingerprint; expiry, renewal, and offline telemetry arrangements |

Review generated `local-path`, disk capacity, and node selectors before use. The default OpenViking workspace requests and limits are both 2 CPU / 4 GiB; these are configuration defaults, not workload sizing results.

The `cluster` delivery expects infrastructure supplied by the customer or prepared through the delivery plan:

| Component | Package recommendation | Declared minimum / condition |
| --- | --- | --- |
| MySQL | 8.0 | 5.7+ |
| Redis | 6.2.x | No minimum declared |
| Kafka | 3.x | 3.0+ |
| ZooKeeper | 3.7.2 | No minimum declared |
| HDFS | 3.3.x | High-availability topology |
| HBase | 2.5.x | Depends on HDFS and ZooKeeper |

This records the package's compatibility statement, not an ongoing certification. In addition to connectivity, verify databases, topics, namespaces, directories, permissions, and initialization against the bundled infrastructure requirements. Re-run acceptance checks after dependency changes.

## Model integration checks

- Embedding must return fixed-size dense vectors. Align actual output, `embedding.dense.dimension`, and vector database dimensions.
- Confirm whether the API base includes `/v1` or `/api/v3`. Text and multimodal embedding may use different paths and request bodies; “OpenAI compatible” alone is insufficient.
- VLM / LLM supports understanding, summaries, and memory extraction. Validate image input for image tasks and tool calls for agents that use tools.
- Test authentication, DNS, certificates, and timeouts from the Pod network. Connectivity from a laptop does not establish Pod connectivity.
- Changing embedding models can change vector semantics even when dimensions match. Plan index rebuilding before switching an existing dataset.

In private delivery, `OpenVikingWorkspace.spec.vectordb.dimension` overrides the template's storage dimension, but does not change `embedding.dense.dimension`. Check both.

Continue to [Enterprise Deployment](20-private-deployment.md) and [upgrades and troubleshooting](21-private-operations.md).
