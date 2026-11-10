---
description: 使用 Viking 私有交付包，从配置预览、VikingDB 部署到 OpenViking Workspace 与 P0 验收。
---

# 企业私有化部署

<p><VPButton text="联系我们获取部署物料" href="https://github.com/volcengine/OpenViking/blob/main/docs/repository/README_CN.md#%E5%95%86%E4%B8%9A%E7%89%88%E6%9C%AC" /></p>

通过部署物料，在自己的 Kubernetes 集群中安装 VikingDB 和 OpenViking。开始前，请先完成[部署前检查](19-deployment-checklist.md)。Python、Docker 和开源 Helm 安装见[服务端部署](03-deployment.md)。

## 1. 获取物料并同步镜像

部署物料需要先申请：在 [README 商业版本](https://github.com/volcengine/OpenViking/blob/main/docs/repository/README_CN.md#%E5%95%86%E4%B8%9A%E7%89%88%E6%9C%AC)登记邮箱，审核通过后，安装包下载链接和试用 License 会发到登记邮箱。

解压后核对 `bin/ovadmin`、`viking-docs/` 与交付清单。安装包只含 CLI 和文档，运行镜像要按 `vikinglist` 另外下载，再导入客户 Registry。镜像已同步到 Registry 时跳过本节。

以下命令在部署机执行。替换所有占位值；`ovadmin` 负责部署，业务访问用的是 `ov` CLI。

```bash
export VIKING_HOME=/opt/viking-deploy
export CONFIG_DIR=/opt/viking-deploy/conf
export MATERIAL_DIR=/opt/viking-deploy/materials
export IMAGE_REGISTRY='<registry.example.com/team/viking>'
export PATH="${VIKING_HOME}/bin:${PATH}"

ovadmin version --output json

# 下载镜像离线包到 ${MATERIAL_DIR}/repo
ovadmin material download --listfile "${VIKING_HOME}/vikinglist" \
  --output-dir "${MATERIAL_DIR}"

# 先登录 Registry（docker login 或 skopeo login），再导入并按清单检查
ovadmin material import-registry --repo-dir "${MATERIAL_DIR}/repo" \
  --registry "${IMAGE_REGISTRY}"
ovadmin material check-registry --listfile "${VIKING_HOME}/vikinglist" \
  --image-registry "${IMAGE_REGISTRY}"
```

`vikinglist` 里的下载地址带签名和有效期，返回 `HTTP 403` 时向交付方重新获取。部署机无法访问下载地址时，在联网机器下载后，把 `repo` 目录拷到部署机。部署机没有 Docker daemon 时，`skopeo login` 后给 `import-registry` 加 `--skopeo-bin "$(command -v skopeo)"`。Registry 中已有的同名 tag 默认跳过，可以放心重跑。`check-registry` 不再报告缺失镜像后继续。

导入时注意：

- Registry 不会自动建仓库时（如 Amazon ECR），先用 `import-registry --dry-run` 列出目标镜像，逐个创建仓库。
- `--crane-bin` 会把 `.tar.gz` 直接交给 `crane push`，而 crane 只接受未压缩的 tar，会报 `archive/tar: invalid tar header`。改用 `--skopeo-bin`，或先 `gunzip` 再用 `crane push <file>.tar <target>` 逐个推送。
- `import-registry` 遇到第一个失败就停止整批导入。修复原因后重跑即可，已导入的 tag 会跳过。

**初始化镜像不在 `vikinglist` 中。** 使用 Flyway 自动初始化基础组件（随包《基础组件配置要求》方式二）时，初始化 Job 默认引用下面三个镜像。它们是 Docker Hub 上的公开镜像，需要自行同步到 Registry 的对应路径：

| 用途 | 默认引用 | 公开来源 |
| --- | --- | --- |
| MySQL DDL | `${IMAGE_REGISTRY}/infra/apecloud/mysql:8.0.39` | `docker.io/apecloud/mysql:8.0.39` |
| Kafka Topic | `${IMAGE_REGISTRY}/infra/apecloud/kafka:3.8.1-debian-12-r2` | `docker.io/apecloud/kafka:3.8.1-debian-12-r2` |
| HBase 表、HDFS 目录 | `${IMAGE_REGISTRY}/infra/apecloud/hbase-hmaster:v2.5.6-1.0.0` | `docker.io/apecloud/hbase-hmaster:v2.5.6-1.0.0` |

```bash
crane copy docker.io/apecloud/mysql:8.0.39 "${IMAGE_REGISTRY}/infra/apecloud/mysql:8.0.39"
crane copy docker.io/apecloud/kafka:3.8.1-debian-12-r2 "${IMAGE_REGISTRY}/infra/apecloud/kafka:3.8.1-debian-12-r2"
crane copy docker.io/apecloud/hbase-hmaster:v2.5.6-1.0.0 "${IMAGE_REGISTRY}/infra/apecloud/hbase-hmaster:v2.5.6-1.0.0"
```

使用自备的客户端镜像时，在 `vdb.yaml` 的 `spec.global.flywayInitImages` 中配置 repository 和 tag。MySQL DDL 内置在 ovadmin 中，由 Flyway Job 执行，交付包里没有单独的 SQL 文件。

`setup apply --dry-run` 会逐个检查 Registry 中的镜像，业务镜像和初始化镜像都同步完成后才能预览完整计划。

隔离环境还要准备基础组件、模型服务，以及 License 续期和遥测回传方案；镜像到位不代表系统可以完全离线运行。

## 2. 生成并编辑配置

```bash
ovadmin init config \
  --dir "${CONFIG_DIR}" \
  --profile cluster \
  --image-registry "${IMAGE_REGISTRY}" \
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

给节点打调度标签：`cluster` 至少 2 个 online 节点和 1 个 offline 节点；`standalone` 把组件调度到 online 节点。

```bash
kubectl label node '<node-name>' nodeLevel=online --overwrite
kubectl label node '<offline-node-name>' nodeLevel=offline --overwrite
kubectl get nodes -L nodeLevel
```

生成的 `vdb.yaml` 还要按实际环境补充或修改下面几项：

| 项目 | 原因 | 做法 |
| --- | --- | --- |
| `spec.middleware.hdfs.configMapName` | `init config` 不生成此字段；缺少时 dry-run 也不会校验 HDFS ConfigMap | 手工加上 `hdfs: {configMapName: hadoop-config}`，名称与实际 ConfigMap 一致 |
| `spec.observability.oneAgent.enabled` | `cluster` profile 默认为 `true`，one-agent 的下游写入 `viking-infra` namespace；没有配套观测栈时 apply 报 `namespaces "viking-infra" not found` | 未部署配套观测栈时设为 `false` |
| 各组件 `replicas` 与 `resources` | 默认值按大规格节点估算，可能超出节点池容量 | 按节点池可分配容量调整，见[部署前检查](19-deployment-checklist.md) |
| `spec.tbase` 调度 | 本版 Operator 不把 `spec.tbase` 下的 `tolerations` 渲染到 tbase-api / tbase-scan；直接修改 Deployment 会被授权 webhook 拒绝 | 专用节点带污点时，用 `spec.tbase.nodeSelector` 把 tbase 调度到无污点、容量足够的节点 |

专用节点带污点时，其他组件在 `vdb.yaml` 中逐一配置 `tolerations` 即可生效。

HDFS 模型目录要让 `root` 可写（原因见[部署前检查](19-deployment-checklist.md)）。Flyway 默认以 `hadoop` 用户创建目录并设为 `755`，运行时 fermat 以 `root` 写入。目录创建后，用 HDFS 管理员身份添加 ACL：

```bash
hdfs dfs -setfacl -R -m user:root:rwx,default:user:root:rwx,default:user:hadoop:rwx /home/vikingdb_data
hdfs dfs -getfacl /home/vikingdb_data
```

HDFS 未开启 ACL 时，改用 `hdfs dfs -chown -R root /home/vikingdb_data` 或其他符合客户规范的授权方式。缺少写权限时，VikingDB 仍会 Ready，但索引一直停在 `INIT`。

然后核对业务与 Operator namespace、外部依赖 Secret / ConfigMap 和 StorageClass。按随包《基础组件配置要求》完成依赖初始化。执行配置预检，并在配置所指定的 namespace 初始化拉镜像 Secret：

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

如启用 License，首次 apply 可能因等待授权 Active 而退出。此时 `VikingDbCluster` CR 已创建，Operator 完成首次状态同步后，用当前集群生成指纹，交给授权签发方换取 `.vlic`，导入后用同一配置重跑 apply：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license fingerprint \
  --system-namespace viking-system --out fingerprint.json

# 取得与本集群 fingerprint 匹配的 .vlic 后
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license import \
  --system-namespace viking-system --file '<license.vlic>'
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license status
```

`.vlic` 必须由本集群的 `fingerprint.json` 签发，其他集群的指纹或改过的文件都会校验失败。

使用在线授权时，不需要 fingerprint 和 `.vlic`。同样等 CR 完成首次状态同步后，用一次性 activation code 激活，再用同一配置重跑 apply：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license activate \
  --system-namespace viking-system \
  --code '<activation-code>' \
  --endpoint 'https://<license-endpoint>' \
  --yes
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" license status vikingdb
```

activation code 只能成功使用一次，不要写入配置文件、脚本或日志。激活后 `license status` 的 Mode 为 `online`，续期和遥测由 Operator 自动完成，续期凭据保存在 `viking-system/viking-license-renewal`。离线和在线两种方式不能混用。等待授权时，CR 的原因文案固定为 `provide offline .vlic via import secret`，在线授权环境按本段激活即可。

未启用授权时跳过授权步骤。确认 VikingDB Ready 后再继续：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" cluster get vikingdb
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" doctor
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check smoketest --target vdb --p0
```

如果集群名不是 `vikingdb`，替换为实际名称。Smoke 会创建测试对象并执行写入，应在约定的验收环境运行。

## 4. 部署 OpenViking 与 Workspace

如果只交付 VikingDB，跳过本节。

先把模型配置写入 Secret Template，内容结构与 `ov.conf` 一致。下面是火山方舟的默认模型，Embedding 输出维度为 `1024`；换其他模型服务时，同时核对接口协议和维度，见[部署前检查](19-deployment-checklist.md#模型接入检查)。

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "<embedding-api-key>",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-lite-260428",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<vlm-api-key>"
  }
}
```

保存为 `ov.conf.secret` 后写入 Secret，然后删除本地明文文件：

```bash
kubectl -n vikingdb create secret generic openviking-secrets \
  --from-file=ov.conf.secret=ov.conf.secret \
  --dry-run=client -o yaml | kubectl apply -f -
```

再预览并安装 OpenViking Operator，创建 workspace：

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
  --conf-secret openviking-secrets \
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

生成配置把 Root API Key 写在 `api_key` 字段。较新的 `ov` CLI 执行 `--sudo` 管理命令时读取 `root_api_key` 字段，因此先在客户端配置中加一个值相同的 `root_api_key`，再注册业务用户，并把返回的 User Key 写回 `api_key`：

```bash
ov admin register-user default '<user-id>' --sudo
```

之后日常的 `ov` 命令使用 User Key，`root_api_key` 只留给管理命令。

客户端在集群内可用 `gen-conf --endpoint-type service`。集群外需可访问的入口，可用 `--endpoint '<openviking-endpoint>'` 指定；生成配置不会替你创建 Ingress、TLS 或负载均衡。已存在的输出文件需确认后才加 `--force` 覆盖。

以下条件全部满足即完成部署：

- 所有节点 `Ready`，业务 Pod 没有 `Pending`、`ImagePullBackOff` 或持续重启。
- `check-registry` 没有报告缺失镜像；物料版本与运行版本一致。
- `VikingDbCluster` 当前 generation 为 Ready；部署 OpenViking 时 `OpenVikingWorkspace` 为 Ready。
- 启用 License 时，`license status vikingdb` 的 State 为 `Active`，业务 namespace 中存在 `viking-license-verdict` Secret。
- `doctor` 通过，VikingDB P0 与 OpenViking P0 分别通过。
- 从业务客户端验证入口、鉴权、导入、读取和检索。

Pod Running 不能替代上述验收，上述检查也不替代容量、恢复或高可用验证。部署完成后，用生成的客户端配置按 [CLI 快速开始](../getting-started/02-quickstart.md)连接服务。

## 随包文档索引

在解压后的 `viking-docs/` 中查看：`install.md`（入口）、`Viking部署手册.md`（完整流程）、`ovadmin使用手册.md`（参数）、`Viking模型要求.md`（模型模板）、`基础组件配置要求.md`（初始化）、`版本兼容性说明.md`（兼容范围）、`Viking升级说明.md`（升级）。后续维护见[升级与排障](21-private-operations.md)。
