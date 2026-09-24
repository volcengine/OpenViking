---
description: 区分托管、自建和私有交付，准备集群、镜像、存储、模型与验收条件。
---

# 部署前检查

先确定服务方式，再准备环境。已有服务地址和 API Key 的用户，从 [CLI 快速开始](../getting-started/02-quickstart.md)连接即可。

| 你的目标 | 选择 | 下一步 |
| --- | --- | --- |
| 使用服务，由平台管理基础设施 | 火山引擎托管 OpenViking | [服务端部署中的托管入口](03-deployment.md) |
| 自己运行开源服务 | Python / Docker / 开源 Helm chart | [服务端部署](03-deployment.md) |
| 已取得 VikingDB / OpenViking 私有交付包 | Kubernetes、Operator 与 ovadmin | [企业私有化部署](20-private-deployment.md) |

私有交付的组件、授权和配置机制与开源服务不同。下文中标注“私有交付”的要求不适用于所有 OpenViking 安装。

## 所有自建环境都要确认

| 项目 | 部署前应有的答案 | 参考 |
| --- | --- | --- |
| 工作负载 | 文档数量与大小、向量维度、并发请求、导入频率、保留周期 | [可观测性](05-observability.md) |
| 模型服务 | provider、模型名、API Base、密钥、输出维度、限流与超时；从运行环境可访问 | [配置指南](01-configuration.md) |
| 存储 | workspace 的持久化位置、容量、权限、备份位置和恢复方式 | [存储架构](../concepts/05-storage.md)、[快照](15-snapshot.md) |
| 网络与身份 | 客户端入口、TLS、DNS、反向代理、管理员与业务用户密钥 | [公网访问](12-public-access.md)、[认证](04-authentication.md) |
| 运维责任 | 谁接收告警、执行升级、保管密钥和恢复数据 | [可观测性与排障](05-observability.md) |

资源配置需要用业务样本验证。容器启动成功、单次健康检查通过，都不足以证明容量满足要求。

## 私有交付：版本与环境

部署前核对下面的环境与版本要求，并以所用部署包的兼容矩阵确认组件组合。

| 项目 | 部署要求 | 需要核对 |
| --- | --- | --- |
| OS / CPU | Linux，默认 x86_64 | arm64 需有对应镜像和二进制 |
| Kubernetes | 兼容矩阵声明 1.24+ | 实际版本、CRD、CNI、CSI 与 RBAC；未声明最大兼容版本 |
| Helm / kubectl | Helm 3.x，不支持 Helm 4；kubectl 与集群差异不超过一个 minor | 在部署机核对工具版本 |
| profile | `standalone` 用于演示和复现；`cluster` 用于集群验证 | 按环境选择 profile，并验证副本、调度与故障恢复 |
| 版本组合 | ovadmin、VikingDB、OpenViking runtime、Operator 属于同一交付组合 | 记录 `ovadmin version --output json`；不能用包名推断 runtime 版本 |

随包文档给出的集群规划参考为至少 3 台机器，每台 32 核、256 GiB 内存、2 TiB 本地盘。**这不是开源 OpenViking 的最低配置，也不是已验证的容量承诺。** 是否适合你的环境取决于索引规模、向量维度、QPS、构建并发、副本和基础组件部署位置。将基础组件与业务混布的三节点形态，仅作为研发、演示或 POC 的起点。

## 私有交付：基础设施清单

| 范围 | 确认内容 |
| --- | --- |
| 权限 | 部署 kubeconfig；创建 CRD 和 RBAC 的权限；Operator 管理目标 namespace 的权限；按随包权限表申请 |
| Namespace | 业务与 Operator 的 namespace，默认示例分别为 `vikingdb`、`viking-system`；Secret 的作用域 |
| 调度 | `cluster` 默认使用 `nodeLevel=online` / `nodeLevel=offline`；实际节点标签、污点和资源能满足调度 |
| Registry | 完整仓库前缀、交付镜像 tag、拉取凭据；部署机登录与集群拉取分别验证 |
| 存储 | 可用 StorageClass；分别配置 VikingDB 数据盘、OpenViking workspace PVC 与外部基础组件存储 |
| 网络 | Pod / Service DNS、API Server、中间件与模型访问；集群 DNS 后缀；入口域名和 TLS |
| 授权 | 启用授权时准备当前集群 fingerprint 对应的授权文件；确认有效期、续期与离线遥测安排 |

不要直接沿用生成配置中的 `local-path`、磁盘容量或节点标签。OpenViking workspace 默认 requests / limits 均为 2 CPU / 4 GiB，这是配置默认值，需按负载核对。

`cluster` 交付依赖客户提供或按交付方案准备的基础组件：

| 组件 | 随包推荐版本 | 随包声明的最低版本 / 条件 |
| --- | --- | --- |
| MySQL | 8.0 | 5.7+ |
| Redis | 6.2.x | 未声明最低版本 |
| Kafka | 3.x | 3.0+ |
| ZooKeeper | 3.7.2 | 未声明最低版本 |
| HDFS | 3.3.x | 高可用形态 |
| HBase | 2.5.x | 依赖 HDFS 和 ZooKeeper |

这张表记录本次交付口径，不代表持续更新的兼容认证。连接可达后，还要按随包《基础组件配置要求》核对数据库、Topic、命名空间、目录、权限和初始化结果。外部依赖升级后需重新验收。

## 模型接入检查

- Embedding 必须返回固定维度的 dense vector。核对模型实际输出、`embedding.dense.dimension` 和向量库维度一致。
- 确认 API Base 是否包含 `/v1` 或 `/api/v3`。文本与多模态 Embedding 的接口、请求体可能不同，不能仅凭“OpenAI 兼容”判断可用。
- VLM / LLM 用于理解、摘要和记忆抽取。图片任务需验证图片输入，启用工具调用的 Agent 需验证 tool calls。
- 从实际 Pod 网络测试鉴权、DNS、证书和超时；本机能访问不代表 Pod 能访问。
- 已有数据更换 Embedding 模型时，即使维度相同，也要评估向量语义变化和重建索引方案。

私有交付中，`OpenVikingWorkspace.spec.vectordb.dimension` 会覆盖模板中的存储维度，但不会同步修改 `embedding.dense.dimension`。两处必须分别核对。

准备完成后进入[企业私有化部署](20-private-deployment.md)。日常维护见[升级与排障](21-private-operations.md)。
