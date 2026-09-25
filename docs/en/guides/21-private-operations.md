---
description: Apply private-delivery configuration changes, prepare upgrades and rollback, and diagnose deployment failures.
---

# Enterprise Deployment: Upgrades and Troubleshooting

Use ovadmin and Operators to update configuration, upgrade, and troubleshoot your [Enterprise Deployment](20-private-deployment.md). For open-source container updates, see [server deployment](03-deployment.md); for OpenViking data-model migration, see the [migration guide](../migration/01-user-peer-model.md). Check delivery and runtime versions separately.

## Applying configuration changes

| Change | Source | Apply through |
| --- | --- | --- |
| Model endpoint, credentials, embedding configuration | Secret / ConfigMap Template | Re-render and roll the workspace |
| Workspace storage or vector backend | Workspace declaration and delivery configuration | `workspace update`; assess data migration first |
| VikingDB images, scheduling, observability | `vdb.yaml` / corresponding values | Preview, then `setup apply --module vikingdb` |
| Client endpoint | Endpoint selection during client configuration generation | Regenerate and verify caller connectivity |

After editing templates, use the actual namespace:

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace restart "${WORKSPACE_NAME}" \
  --namespace vikingdb --yes --wait
```

This rolls workloads; schedule a change window. Do not maintain configuration by editing the generated Secret, Pod files, or Operator-managed Deployments. Plan index rebuilding before changing embedding semantics or dimensions.

## Upgrade checklist

1. **Record the baseline.** Preserve configuration, versions, CR status, workspace inventory, and license state. Resolve unhealthy or unlicensed states first.
2. **Establish recovery.** Back up workspace data, external dependency data, configuration, credentials, and license materials, with a restore procedure for each. A configuration copy is not a data backup. [OpenViking snapshots](15-snapshot.md) do not back up the entire external infrastructure.
3. **Check the release set.** Record `ovadmin version --output json` and `version --cluster`. Follow release notes for cross-`x.y` upgrades. Replacing an individual product image invalidates automatic reliance on the original release-set compatibility statement.
4. **Generate a candidate configuration.** Run the new delivery's `ovadmin init config` in a separate directory. Transfer confirmed namespaces, Registry, Secret references, StorageClass, scheduling, and resource values. Do not overwrite the current directory or copy old default images.
5. **Check and preview.** Run `check`, then `setup apply --dry-run` for each product being changed. Confirm image availability, dependencies, and licensing.
6. **Apply and verify.** Follow the bundled upgrade order, retain command output and events, and repeat CR Ready, License Active when enabled, doctor, and each product's P0 smoke. Validate application access afterwards.

New workspace configuration defaults requests and limits to 2 CPU / 4 GiB. Preserve approved workload sizing when preparing an upgrade; defaults do not replace a capacity plan.

If an upgrade fails, stop subsequent changes and retain failure state, previews, and events. Follow the prepared rollback procedure using the previous configuration, delivery materials, and data restore where required. **An image downgrade does not guarantee data compatibility.** Repeat acceptance after rollback; do not bypass the Operator by editing managed child resources.

## Observe before repairing

These commands inspect state. Set the configuration directory and replace cluster / workspace names:

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" version --cluster
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" cluster get vikingdb
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace get "${WORKSPACE_NAME}"
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" doctor
kubectl -n vikingdb get pods,pvc,jobs,svc -o wide
kubectl -n vikingdb get events --sort-by=.lastTimestamp
```

| Symptom | Inspect first | Next action |
| --- | --- | --- |
| ImagePullBackOff / failed image check | Full prefix, delivery tag, image synchronization, per-namespace pull Secrets | Fix source configuration or synchronize images, then preview |
| Pod Pending | Labels, taints, resource requests, PVCs, node affinity | Use Pod / PVC events to distinguish scheduling from storage failures |
| PVC Pending / no StorageClass reported | Actual classes, kubeconfig context, RBAC for listing classes | Set the appropriate class explicitly; do not delete PVCs as a first response |
| License not Active | Fingerprint, expiry, system namespace, first CR synchronization | Follow the bundled licensing procedure and check status again |
| `license checksum mismatch` | Whether the `.vlic` was issued for this cluster's fingerprint and left unmodified | Do not edit the file; request the original `.vlic` again using this cluster's `fingerprint.json` |
| API Server fails after resources were submitted | Host-to-API network and API health | After recovery, inspect `cluster get` / `doctor` instead of reinstalling |
| Workspace Ready but import or retrieval fails | Model credentials, dimensions, API paths, limits, vector service, user key | Run OpenViking P0 and inspect the failed stage |
| Root Key works for administration but fails on data | Key type used by the application | Use a User / Admin Key |
| Model configuration did not change | Whether source templates were edited and re-rendered | Run `workspace restart`, then verify model requests |
| Internal access works but external access fails | Service DNS endpoint, Ingress / LB / TLS | Configure the external entry point and generate matching client configuration |

## Evidence for support

Provide delivery versions, timestamps, affected operations, CR conditions, relevant events, the failing doctor / P0 stage, and redacted configuration differences. Exclude kubeconfig credentials, model keys, Root/User keys, license files, full Secrets, and signed download links.

An enabled collector does not establish a working monitoring platform. The package requires VictoriaMetrics / Grafana to be prepared separately through the delivery plan. Verify metric ingestion, dashboards, alert delivery, and retention. Offline licensed environments also need renewal and telemetry return arrangements under their license policy.
