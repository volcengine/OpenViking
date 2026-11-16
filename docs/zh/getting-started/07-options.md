# 部署模式

<a id="使用方式"></a>

你可以自行运行 OpenViking，连接火山引擎托管服务，或申请企业私有化交付。选择时先看数据放在哪里、谁负责运行和需要怎样的支持。

已有服务地址与 API Key 的用户无需重新部署，直接进入 [CLI 入门](05-cli-setup.md)。想在本机跑一次完整问答，按[快速开始](02-quickstart.md)操作。

## 选择一条路径

| 方式 | 数据与运行环境 | 运维与支持 | 需要准备 |
| --- | --- | --- | --- |
| **开源自建** | 自己的本机、服务器或云环境，存储由自己配置 | 自行部署、升级、备份与配置访问控制；通过开源社区获取帮助 | 运行环境、模型访问凭据和存储 |
| **云服务** | 火山引擎托管的服务环境，区域与数据使用条件以服务说明为准 | 服务方运行基础设施；用户管理自己的接入、数据与授权 | 开通服务，获取连接信息和 API Key |
| **企业部署** | 按交付方案部署到自己的云账号、VPC 或离线环境 | 运维分工、技术支持及验收范围按实际交付约定 | 申请部署物料与 License，完成环境检查 |

“开源/商业”描述软件与支持方式，“自建/托管”描述部署责任。开源自建也可用于团队环境，选择时可以结合访问控制需求、部署条件和支持方式综合考虑。各版本的具体能力与限制见相应版本和服务说明。

## 开源自建

适合需要控制环境、自己调试或接入应用的用户。开源服务器采用 AGPLv3；本机体验不需要企业部署物料。按照[快速开始](02-quickstart.md)准备模型、启动服务和 VikingBot，再导入样例问答。

团队共享前，需要完善身份认证、持久化、备份和运维。Docker、开源 Helm 等路径见[服务端部署](../guides/03-deployment.md)；许可文本见 [LICENSE](https://github.com/volcengine/OpenViking/blob/main/LICENSE)。

## 云服务

火山引擎的 OpenViking Context 是基于开源 OpenViking 内核的托管服务。开通后，在 [OpenViking 控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing)获取 API Key，在 `ov config` 中选择 **OpenViking Service** 并保存连接，无需在本机启动数据服务。

套餐、额度、可用区域与支持范围见[官方服务说明](https://docs.volcengine.com/docs/84313/2374478)。VikingBot 和 Compile Runtime 的可用性取决于所选服务的配置；如需使用，可以向服务提供方了解支持情况。数据服务也支持通过自己的 Agent 接入。

## 企业部署

适合需要私有化交付、部署物料和官方支持的团队。通过[官方咨询入口](https://my.feishu.cn/share/base/form/shrcnMFqymCd9sq77sLk34Krxoc)确认方案，按实际交付要求申请物料与 License。

当前[企业部署指南](../guides/20-private-deployment.md)使用 Kubernetes 环境中的 VikingDB 和 OpenViking。准备部署物料时，可以按照[部署前检查](../guides/19-deployment-checklist.md)一并准备镜像、存储和依赖环境。

## 选好之后

取得 URL 和数据访问凭据后，进入 [CLI 入门](05-cli-setup.md)。如果想先验证完整本地体验，使用[快速开始](02-quickstart.md)。

依据：[项目商业版本说明](https://github.com/volcengine/OpenViking/blob/main/docs/repository/README_CN.md#商业版本)、[云服务产品介绍](https://docs.volcengine.com/docs/84313/2374478)、[企业部署指南](../guides/20-private-deployment.md)。核对日期：2026-10-27。
