---
description: Deploy VikingDB and OpenViking through configuration preview, VikingDB, OpenViking Workspaces, and P0 acceptance.
---

# Enterprise Deployment

<p><VPButton text="Contact us for deployment materials" href="https://github.com/volcengine/openviking/#commercial-editions" /></p>

Deploy VikingDB and OpenViking in your own Kubernetes cluster using the deployment package. Complete the [deployment checklist](19-deployment-checklist.md) first. For Python, Docker, or the open-source Helm chart, see [server deployment](03-deployment.md).

## 1. Prepare materials and configuration directories

After extracting the package, check `bin/ovadmin`, `viking-docs/`, and the delivery manifest. The ZIP contains the CLI and documentation; **it does not contain all runtime images**. If a `vikinglist` is supplied, it can download additional materials. Skip material downloads when images have already been synchronized.

Run these commands on the target deployment host. Replace all placeholders. `ovadmin` manages the deployment; `ov` is the application client CLI.

```bash
export VIKING_HOME=/opt/viking-deploy
export CONFIG_DIR=/opt/viking-deploy/conf
export PATH="${VIKING_HOME}/bin:${PATH}"

ovadmin version --output json

# Only when downloading through a material manifest: review first
ovadmin material download --listfile "${VIKING_HOME}/vikinglist" \
  --output-dir "${VIKING_HOME}/materials" --dry-run
```

After reviewing the download plan, remove `--dry-run` to fetch materials, then import images into the customer Registry following the package manual. Isolated environments also require import tools, infrastructure dependencies, model services, and a license renewal / telemetry return plan. Downloading the ZIP alone does not make the system offline-ready.

## 2. Generate and edit configuration

```bash
ovadmin init config \
  --dir "${CONFIG_DIR}" \
  --profile cluster \
  --image-registry '<registry.example.com/team/viking>' \
  --image-pull-secret viking-registry-secret \
  --openviking-storage-class '<storage-class-name>'
```

Edit the generated configuration before deploying:

| File or object | Responsibility |
| --- | --- |
| `ovadmin.conf` | Cluster access, configuration directory, OpenViking image, workspace resources and storage |
| `vdb.yaml` | VikingDB images, dependency references, scheduling, storage, and observability |
| ConfigMap Template | Base OpenViking runtime configuration |
| Secret Template | Model credentials and other sensitive configuration in `ov.conf.secret` |
| `OpenVikingWorkspace` | Workspace declaration, including storage and vector database overrides |
| Generated `*.ovcli.conf` | Client endpoint and API key; handle as credentials |

The full image prefix includes the repository path. Use Operator image names from the manifest: this release uses `vikingdb_operator` and `openviking_operator`, with underscores. Use tags from the delivery set, not old example tags.

Check namespaces, external Secret / ConfigMap references, node labels, and StorageClass. Complete dependency initialization using the bundled infrastructure requirements. Run preflight checks and initialize pull Secrets for namespaces configured in the delivery:

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" init secret --all-namespaces
```

## 3. Deploy VikingDB

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" setup apply \
  --module vikingdb --dir "${CONFIG_DIR}" --dry-run
```

Review namespaces, images, pull Secrets, dependencies, resources, and scheduling. Resolve mismatches before applying:

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" setup apply \
  --module vikingdb --dir "${CONFIG_DIR}" --yes
```

When licensing is enabled, the first apply may exit while waiting for License Active. Before importing, the `VikingDbCluster` CRD and target CR must exist, and the Operator must have completed its first status synchronization. Import the license bound to this cluster using the package's licensing procedure, then repeat apply with the same configuration:

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license import --file '<license.vlic>'
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license status
```

Skip licensing steps when licensing is disabled. Verify VikingDB before proceeding:

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" cluster get vikingdb
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" doctor
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check smoketest --target vdb --p0
```

Replace `vikingdb` if your cluster has a different name. Smoke tests create test objects and perform writes; run them in the agreed acceptance environment.

## 4. Deploy OpenViking and create a Workspace

Skip this step for a VikingDB-only delivery. Prepare the release's ConfigMap Template and model Secret Template, then preview and install the OpenViking Operator:

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" setup apply \
  --module openviking --dir "${CONFIG_DIR}" --dry-run

# Apply after reviewing the preview
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" setup apply \
  --module openviking --dir "${CONFIG_DIR}" --yes

export WORKSPACE_NAME='<workspace-name>'
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace create "${WORKSPACE_NAME}" \
  --namespace vikingdb \
  --image '<runtime-image-from-delivery-manifest>' \
  --conf-template '<configmap-template-name>' \
  --conf-secret '<secret-template-name>' \
  --wait

ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace get "${WORKSPACE_NAME}"
```

Installing the Operator does not create the workspace. For an existing workspace, follow the package's `workspace update` procedure.

Configuration is merged in this order: ConfigMap Template → Secret Template → Workspace CR `spec.vectordb` / `spec.storage` overrides. The result is stored in `<workspace-name>-ov-conf` and mounted at `/app/ov.conf`. Edit source templates or the CR, not the generated Secret or Pod file. See [operations](21-private-operations.md) for applying template changes.

## 5. Connect and verify

```bash
export OV_CLIENT_CONF="${CONFIG_DIR}/${WORKSPACE_NAME}.ovcli.conf"
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace gen-conf "${WORKSPACE_NAME}" \
  --output "${OV_CLIENT_CONF}"
chmod 600 "${OV_CLIENT_CONF}"

ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check smoketest \
  --target openviking --p0 --openviking-conf "${OV_CLIENT_CONF}"
```

The generated configuration contains a Root API Key for initialization and administration. Application data access requires a User / Admin Key; the P0 smoke provisions a test User Key. Do not commit the client configuration or copy it into logs.

For an in-cluster client, use `gen-conf --endpoint-type service`. For an external client, supply a reachable entry point with `--endpoint '<openviking-endpoint>'`. Generating configuration does not create Ingress, TLS, or a load balancer. Add `--force` only after deciding to overwrite an existing output file.

Record all applicable acceptance results:

- Materials and running versions match; the deployment preview matches the target environment.
- `VikingDbCluster` is Ready for its current generation; License is Active when enabled.
- `OpenVikingWorkspace` is Ready when OpenViking is deployed.
- `doctor` passes; each delivered product passes its own P0 smoke.
- The application client can authenticate, import, read, and retrieve through its actual endpoint.

VikingDB P0 does not replace OpenViking P0. Running Pods do not replace these checks. These checks do not establish capacity, recoverability, or high availability.

## Bundled reference manuals

Under `viking-docs/`, consult `install.md` (entry point), `Viking部署手册.md` (full procedure), `ovadmin使用手册.md` (arguments), `Viking模型要求.md` (model templates), `基础组件配置要求.md` (dependency initialization), `版本兼容性说明.md` (compatibility), and `Viking升级说明.md` (upgrades). Continue with [upgrades and troubleshooting](21-private-operations.md).
