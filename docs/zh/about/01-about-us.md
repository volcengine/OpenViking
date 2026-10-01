# 团队与组织

## 项目概述

OpenViking 是面向 AI Agent 的开源上下文数据库，由字节跳动火山引擎 Viking 团队发起并维护。它用文件系统组织资源、记忆和技能，供 Agent 浏览、检索和按需读取。

## 团队介绍

### Viking 团队背景

Viking 团队主要开发向量检索、知识库和记忆管理产品。团队有数十名工程师，覆盖分布式系统、机器学习、数据工程和 AI 算法，在上下文工程上有商业化落地经验。OpenViking 将这些领域的工程经验用于开源上下文数据库，与社区共同开发。

#### 核心技术能力

**大规模向量检索系统**
- 支撑亿级向量数据的实时检索与相似度计算
- 具备毫秒级响应能力，满足高并发业务场景需求
- 支持混合检索策略，结合语义相似性与关键词匹配

**多模态内容理解引擎**
- 支持文本、图像、音频、视频等多种数据类型的智能解析
- 实现跨模态语义关联与内容理解
- 提供统一的内容抽象与语义表示

**分布式系统架构设计**
- 具备构建高可用、可扩展分布式系统的丰富实践经验
- 支持弹性伸缩与故障自动恢复机制
- 实现数据一致性与系统性能的最佳平衡

这些工作涉及三个相互关联的问题：如何从非结构化内容中提取可检索的信息，如何在大量候选内容中找到相关上下文，以及如何保留对后续任务有用的交互经验。OpenViking 对应提供[资源解析与提取](../concepts/06-extraction.md)、[上下文检索](../concepts/07-retrieval.md)和[会话与记忆管理](../concepts/08-session.md)。可沿这些入口了解实现和使用条件。

### 发展历程与技术演进

| 时间阶段 | 里程碑事件 | 技术突破与产业影响 |
|----------|-----------|-------------------|
| **2019-2023** | VikingDB 向量数据库在字节跳动内部大规模应用 | 支撑了公司内部多个核心产品的非结构化信息检索需求，积累了大规模向量检索的工程实践经验，验证了向量数据库在真实业务场景中的技术价值 |
| **2024年** | 推出面向开发者的产品矩阵：VikingDB、Viking知识库、Viking记忆库 | 在火山引擎公有云平台正式提供服务，已成功支撑数千家企业客户开发 AI 原生应用，标志着上下文工程技术从内部工具向商业化产品的成功转型 |
| **2025年** | 扩展至 AI 搜索、知识助手等上层应用产品 | 构建了从基础设施到应用层的完整产品矩阵，进一步验证了上下文工程技术在不同业务场景中的商业价值，形成了技术到产品的完整闭环 |
| **2025年末** | 开源 [MineContext](https://github.com/volcengine/MineContext) 项目 | 探索主动式服务的 AI 应用模式，验证个人上下文工程的技术理念，为 OpenViking 的开源积累了社区运营经验 |
| **2026年初** | 开源 OpenViking 项目 | 推出全新设计的上下文数据库架构，为全球 AI Agent 生态系统提供开源基础设施，标志着 Viking 团队从商业产品提供商向开源技术贡献者的战略转变 |

### 学术合作与产学研结合

OpenViking 自启动起就与高校和研究机构合作，共同探索面向 AI Agent 的上下文数据库设计与工程实践，让研究工作贴近实际应用需求。

我们诚挚感谢以下学者的宝贵贡献与技术指导，共同发起了 OpenViking 项目：

- 中国人民大学信息学院副教授孙亚辉老师
- 浙江大学软件学院教授高云君老师，研究员朱轶凡、葛丛丛老师
- 上海交通大学人工智能学院副教授，无问芯穹联合创始人兼首席科学家戴国浩老师

我们与学术界的合作模式包括：

- **联合研究项目**：共同开展上下文工程的前沿研究
- **技术研讨会**：定期组织学术交流与技术方案评审
- **人才培养**：为研究生提供实践平台与研究课题
- **成果转化**：将学术研究成果转化为工程实践

### 研究论文

以下论文来自上述合作，其中部分核心机制已集成到 OpenViking。

- **VikingMem: A Memory Base Management System for Stateful LLM-based Applications**<br>
  Jiajie Fu, Junwen Chen, Mengzhao Wang, Aoxiang He, Maojia Sheng, Xiangyu Ke, Yifan Zhu, and Yunjun Gao. arXiv:2605.29640, 2026。已在 VLDB 2026 演讲。<br>
  以事件驱动长期记忆的提取、更新与整合，服务有状态 Agent。[arXiv](https://arxiv.org/abs/2605.29640) · [PDF](https://arxiv.org/pdf/2605.29640)
- **Directory-Aware Query and Maintenance in Vector Databases**<br>
  Mengzhao Wang, Zheng Gong, Jingpei Hu, Jiajie Fu, Maojia Sheng, Junwen Chen, and Yifan Zhu. arXiv:2606.16903, 2026。已被 ICDE 接收。<br>
  目录范围检索的形式化基础与索引设计（TrieHI），OpenViking 用它在向量排序前确定目录检索范围。[arXiv](https://arxiv.org/abs/2606.16903) · [PDF](https://arxiv.org/pdf/2606.16903)
- **VikingRAG: Accurate and Token-efficient Retrieval-augmented Generation over Structured Documents**<br>
  Peiyuan Gao, Gaoyuan Zhang, Haojie Qin, Yahui Sun, Qianyi Zhang, Yunhao Zhang, Zeyu Wang, and Wei Lu. arXiv:2609.11390, 2026。投递中。<br>
  将语义检索与文档结构结合，按证据缺口展开相关目录片段。[arXiv](https://arxiv.org/abs/2609.11390) · [PDF](https://arxiv.org/pdf/2609.11390)

## 开源组织建设

### 项目发展阶段

项目围绕上下文存储与检索、Agent 集成和部署能力持续迭代。已实现的能力与后续方向见[路线图](03-roadmap.md)，已发布的变更见[更新日志](02-changelog.md)。

### 治理架构与决策机制

开源治理委员会负责技术路线、版本与功能优先级、核心架构和兼容性评审、工程规范、贡献者协作，以及相关项目的集成。成员包括 Maojia Sheng（[@MaojiaSheng](https://github.com/MaojiaSheng)）、Haojie Qin（[@qin-ctx](https://github.com/qin-ctx)）、Jiahui Zhou（[@zhoujh01](https://github.com/zhoujh01)）、Zhiheng Liu（[@ZaynJarvis](https://github.com/ZaynJarvis)）。符合条件的社区贡献者可以通过后续的提名与选举程序加入委员会。

具体模块的协作入口和近期活跃评审者见[贡献指南](https://github.com/volcengine/OpenViking/blob/main/docs/repository/CONTRIBUTING_CN.md)。

功能建议和问题在 [GitHub Issues](https://github.com/volcengine/OpenViking/issues) 讨论，代码与文档变更通过 Pull Request 评审。涉及公开接口、数据存储、权限边界或跨模块架构的改动，请先说明当前行为、目标行为、请求或配置示例，以及兼容性影响，再开始实现。

## 社区参与

### 加入社区

#### 飞书群

扫描二维码加入飞书群，交流使用问题和开发方案：

![飞书扫码加群](../../images/lark-group-qrcode.png)

需要先安装[飞书客户端](https://www.feishu.cn/)。

#### 微信群

扫描二维码添加小助手，备注“OpenViking”，申请加入交流群：

![微信扫码加群](../../images/wechat-group-qrcode.png)

也可以加入 [Discord](https://discord.com/invite/eHvx8E9XF3)，或在 [X](https://x.com/openvikingai) 查看项目动态。

### 参与方式

- **报告问题或提建议**：在 [Issues](https://github.com/volcengine/OpenViking/issues) 提供场景、版本和复现步骤。
- **改代码或文档**：阅读[贡献指南](https://github.com/volcengine/OpenViking/blob/main/docs/repository/CONTRIBUTING_CN.md)，提交实现、测试、文档或翻译。
- **开发集成**：为 Agent 工具或框架添加插件，参考[插件开发指南](../agent-integrations/18-plugin-development.md)。
- **分享经验**：在社区分享使用案例、排障过程，或帮助其他用户解决问题。

Issue 和 PR 需要提供的信息见[贡献指南](https://github.com/volcengine/OpenViking/blob/main/docs/repository/CONTRIBUTING_CN.md)。

## 讨论与协作机制

[GitHub 仓库](https://github.com/volcengine/OpenViking) 保存代码、文档和评审记录。[GitHub Discussions](https://github.com/volcengine/OpenViking/discussions) 用于技术方案讨论和社区交流，群聊适合即时交流；需要跟踪的问题和方案请同步到 Issue 或 Pull Request，方便后续查阅和协作。

希望开展技术合作或生态集成的研究机构和企业，可以在 GitHub Discussions 发起讨论，或通过飞书群联系团队。
