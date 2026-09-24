---
description: 使用 Viking 私有交付包，从配置预览、VikingDB 部署到 OpenViking Workspace 与 P0 验收。
---

# 企业私有化部署

<p><VPButton text="联系我们获取部署物料" href="https://github.com/volcengine/OpenViking/blob/main/README_CN.md#%E5%95%86%E4%B8%9A%E7%89%88%E6%9C%AC" /></p>

通过部署物料，在自己的 Kubernetes 集群中安装 VikingDB 和 OpenViking。开始前，请先完成[部署前检查](19-deployment-checklist.md)。Python、Docker 和开源 Helm 安装见[服务端部署](03-deployment.md)。

## 1. 准备物料与配置目录

解压包后，核对 `bin/ovadmin`、`viking-docs/` 与交付清单。该 ZIP 包含 CLI 和文档，**不等于全部运行镜像已到位**。带有 `vikinglist` 时，可按清单下载其他物料；已同步镜像的环境不需要重复下载。

以下命令在目标部署机执行。替换所有占位值；`ovadmin` 与业务访问用的 `ov` CLI 是两个工具。

```bash
export VIKING_HOME=/opt/viking-deploy
export CONFIG_DIR=/opt/viking-deploy/conf
export PATH="${VIKING_HOME}/bin:${PATH}"

ovadmin version --output json

# 仅物料清单下载场景：先核对预览
ovadmin material download --listfile "${VIKING_HOME}/vikinglist" \
  --output-dir "${VIKING_HOME}/materials" --dry-run
```

确认下载范围后，移除 `--dry-run` 下载物料，再按随包手册导入客户 Registry。隔离环境还须准备镜像导入工具、基础组件、模型服务，以及授权续期和遥测回传方案；下载 ZIP 不意味着系统可以离线运行。

## 2. 生成并编辑配置

```bash
ovadmin init config \
  --dir "${CONFIG_DIR}" \
  --profile cluster \
  --image-registry '<registry.example.com/team/viking>' \
  --image-pull-secret viking-registry-secret \
  --openviking-storage-class '<storage-class-name>'
```

生成后，先编辑配置再部署：

| 文件或对象 | 管理内容 |
| --- | --- |
| `ovadmin.conf` | 集群访问、配置目录、OpenViking 镜像、workspace 资源与存储 |
| `vdb.yaml` | VikingDB 组件镜像、依赖引用、调度、存储与观测配置 |
| ConfigMap Template | OpenViking 基础运行配置 |
| Secret Template | 模型密钥及其他敏感配置，使用 `ov.conf.secret` |
| `OpenVikingWorkspace` | workspace 的声明，包括存储和向量库覆盖值 |
| 生成的 `*.ovcli.conf` | 客户端 endpoint 与 API Key，按凭据保管 |

完整镜像前缀必须含仓库路径。Operator 镜像名以交付清单为准，本版为 `vikingdb_operator` 和 `openviking_operator`，不要改成带连字符的名称。镜像 tag 使用交付组合，不从示例中抄旧值。

核对业务与 Operator namespace、外部依赖 Secret / ConfigMap、节点标签和 StorageClass。按随包《基础组件配置要求》完成依赖初始化。执行配置预检，并在配置所指定的 namespace 初始化拉镜像 Secret：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" init secret --all-namespaces
```

## 3. 部署 VikingDB

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" setup apply \
  --module vikingdb --dir "${CONFIG_DIR}" --dry-run
```

核对预览中的 namespace、镜像、拉取 Secret、外部依赖、资源和调度。修正不一致项后再执行：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" setup apply \
  --module vikingdb --dir "${CONFIG_DIR}" --yes
```

如启用 License，首次 apply 可能因等待授权 Active 而退出。导入前须已有 `VikingDbCluster` CRD、目标 CR，且 Operator 已完成首次状态同步。此时按随包授权流程导入与当前集群绑定的文件，再使用同一配置重跑 apply：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license import --file '<license.vlic>'
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license status
```

未启用授权时跳过授权步骤。确认 VikingDB Ready 后再继续：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" cluster get vikingdb
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" doctor
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check smoketest --target vdb --p0
```

如果集群名不是 `vikingdb`，替换为实际名称。Smoke 会创建测试对象并执行写入，应在约定的验收环境运行。

## 4. 部署 OpenViking 与 Workspace

如果只交付 VikingDB，跳过本节。准备好随包版本的 ConfigMap Template 和模型 Secret Template 后，预览并安装 OpenViking Operator：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" setup apply \
  --module openviking --dir "${CONFIG_DIR}" --dry-run

# 核对预览后执行
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

Operator 安装成功不会自动完成 workspace 创建。同名 workspace 已存在时，按随包手册使用 `workspace update`。

配置合并顺序为：ConfigMap Template → Secret Template → Workspace CR 的 `spec.vectordb` / `spec.storage` 覆盖。最终配置写入 `<workspace-name>-ov-conf` Secret 并挂载到 `/app/ov.conf`。修改源模板或 CR，不要直接编辑生成的 Secret 或 Pod 内文件。模板变更后的生效方式见[升级与排障](21-private-operations.md)。

## 5. 连接并验收

```bash
export OV_CLIENT_CONF="${CONFIG_DIR}/${WORKSPACE_NAME}.ovcli.conf"
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace gen-conf "${WORKSPACE_NAME}" \
  --output "${OV_CLIENT_CONF}"
chmod 600 "${OV_CLIENT_CONF}"

ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check smoketest \
  --target openviking --p0 --openviking-conf "${OV_CLIENT_CONF}"
```

生成配置内含 Root API Key，仅用于初始化与管理。业务数据访问需 User / Admin Key；P0 冒烟会 provision 测试 User Key。不要将客户端配置提交到代码库或贴入日志。

客户端在集群内可用 `gen-conf --endpoint-type service`。集群外需可访问的入口，可用 `--endpoint '<openviking-endpoint>'` 指定；生成配置不会替你创建 Ingress、TLS 或负载均衡。已存在的输出文件需确认后才加 `--force` 覆盖。

验收记录应包含以下结果：

- 本次物料与运行版本一致，配置预览符合目标环境。
- `VikingDbCluster` 当前 generation 为 Ready；启用授权时 License 为 Active。
- 部署 OpenViking 时，`OpenVikingWorkspace` 为 Ready。
- `doctor` 通过，VikingDB P0 与 OpenViking P0 按交付范围分别通过。
- 从业务客户端验证入口、鉴权、导入、读取和检索。

VikingDB P0 不能替代 OpenViking P0；Pod Running 不能替代上述验收。上述检查也不替代容量、恢复或高可用验证。

## 随包文档索引

在解压后的 `viking-docs/` 中查看：`install.md`（入口）、`Viking部署手册.md`（完整流程）、`ovadmin使用手册.md`（参数）、`Viking模型要求.md`（模型模板）、`基础组件配置要求.md`（初始化）、`版本兼容性说明.md`（兼容范围）、`Viking升级说明.md`（升级）。后续维护见[升级与排障](21-private-operations.md)。
