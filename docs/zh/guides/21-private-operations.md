---
description: 私有交付的配置生效、升级验收、回滚准备和故障排查。
---

# 企业私有化部署：升级与排障

使用 ovadmin 和 Operator 维护[企业私有化部署](20-private-deployment.md)，包括配置更新、版本升级和故障排查。开源服务的容器更新见[服务端部署](03-deployment.md)，OpenViking 数据模型迁移见[迁移指南](../migration/01-user-peer-model.md)。交付包版本和 runtime 版本分别核对。

## 配置修改如何生效

| 修改内容 | 修改位置 | 生效路径 |
| --- | --- | --- |
| 模型地址、密钥、Embedding 配置 | Secret Template / ConfigMap Template | 重新渲染并滚动 workspace |
| workspace 存储与向量后端 | Workspace 声明及交付配置 | 按 `workspace update` 流程；先确认数据迁移影响 |
| VikingDB 镜像、调度、观测配置 | `vdb.yaml` / 对应 values | dry-run 后执行 `setup apply --module vikingdb` |
| 客户端入口 | 生成客户端配置时选择 endpoint | 重新生成并验证调用方的连接 |

模板更新后，在实际 namespace 执行：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace restart "${WORKSPACE_NAME}" \
  --namespace vikingdb --yes --wait
```

该命令会滚动工作负载，需安排变更窗口。不要修改生成的最终 Secret、Pod 文件或受 Operator 管理的 Deployment 来维持配置。替换 Embedding 模型或维度时，先确定索引重建方案。

## 升级检查单

1. **记录基线。** 保存当前配置目录、版本、CR 状态、workspace 清单和 License 状态。当前未 Ready 或授权异常时，先处理原因。
2. **确认恢复路径。** 备份 workspace 数据、外部依赖数据、配置、密钥和授权材料，并确认各自恢复步骤。配置副本不是数据备份；[OpenViking 快照](15-snapshot.md)也不能代替整套外部基础设施的备份。
3. **核对版本组合。** 记录 `ovadmin version --output json` 与 `version --cluster`。跨 `x.y` 升级按发布说明确认；单独替换某一产品镜像后，原交付组合的兼容结论不再自动成立。
4. **生成候选配置。** 在新目录运行本轮 `ovadmin init config`，迁移已确认的 namespace、Registry、Secret 引用、StorageClass、调度和资源值。不要覆盖当前配置目录或照搬旧版默认镜像。
5. **检查与预览。** 执行 `check`，分别对本次变更涉及的 VikingDB、OpenViking 执行 `setup apply --dry-run`；确认镜像已同步、依赖可达、授权匹配。
6. **执行与验收。** 按随包升级顺序变更，保留命令输出与事件。重复 CR Ready、License Active（如启用）、doctor 和各产品 P0 验收，再验证业务调用。

本轮默认 workspace requests / limits 均为 2 CPU / 4 GiB。升级候选配置要保留经过确认的资源规格，不能把默认值当成现有容量方案。

升级失败时暂停后续变更，保留失败状态、dry-run 与事件。依据预先确认的回滚方案，使用旧配置、旧交付物和必要的数据恢复。**降级镜像不保证恢复数据兼容性。** 回滚后重复完整验收，不直接编辑受管子资源绕开 Operator。

## 先收集状态，再修复

下面是读取状态的入口；替换配置目录、集群名与 workspace 名称：

```bash
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" version --cluster
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" check
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" cluster get vikingdb
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" workspace get "${WORKSPACE_NAME}"
ovadmin -c "${CONFIG_DIR}/ovadmin.conf" doctor
kubectl -n vikingdb get pods,pvc,jobs,svc -o wide
kubectl -n vikingdb get events --sort-by=.lastTimestamp
```

| 现象 | 优先检查 | 下一步 |
| --- | --- | --- |
| ImagePullBackOff / 镜像检查失败 | 完整前缀、交付 tag、仓库是否已同步、各 namespace 的拉取 Secret | 修正源配置或补齐镜像，再预览 |
| Pod Pending | 节点标签、污点、资源 requests、PVC 与节点亲和性 | 用 Pod / PVC 事件区分调度和存储问题 |
| PVC Pending / 无可用 StorageClass | 实际 StorageClass、kubeconfig context、列举存储类的 RBAC | 显式指定适用的存储类；不直接删除 PVC |
| License 非 Active | fingerprint、有效期、system namespace、CR 首次同步 | 按随包授权流程处理，再查状态 |
| `license checksum mismatch` | `.vlic` 是否由本集群 fingerprint 签发、文件是否被改动 | 不修改文件内容；用本集群 `fingerprint.json` 重新申请原始 `.vlic` |
| API Server 访问失败，但已提交资源 | 部署机到 API Server 的网络与 API 状态 | 恢复后用 `cluster get` / `doctor` 查最终状态，不立即重装 |
| workspace Ready，但导入或检索失败 | 模型鉴权、维度、API 路径、限流、向量服务、用户 Key | 运行 OpenViking P0 并读取对应失败阶段 |
| Root Key 可管理但数据访问失败 | 是否把 Root Key 用于数据面 | 改用 User / Admin Key |
| 模型配置更新后未变化 | 修改的是否为源模板；是否重渲染 | 使用 `workspace restart`，重新验证模型请求 |
| 集群内可访问，外部不可访问 | 客户端 endpoint 是否为 Service DNS、Ingress / LB / TLS | 配好外部入口，再生成对应客户端配置 |

## 交付给支持人员的信息

提供交付版本、发生时间、受影响操作、CR 条件、相关事件、doctor 与 P0 的失败阶段，以及脱敏后的配置差异。不要上传 kubeconfig 凭据、模型密钥、Root/User Key、授权文件、完整 Secret 或带签名下载链接。

默认启用采集组件不等于监控平台已可用。本版随包说明中，VictoriaMetrics / Grafana 由交付方案单独准备。需确认指标接收、Dashboard 加载、告警接收与保留周期。离线授权环境还需按授权策略安排续期与遥测回传。
