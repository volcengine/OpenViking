# 团队与组织

## 项目概述

OpenViking 是面向 AI Agent 的开源上下文数据库，由字节跳动火山引擎 Viking 团队发起并维护。它用文件系统组织资源、记忆和技能，供 Agent 浏览、检索和按需读取。

## 团队介绍

### Viking 团队背景

Viking 团队主要开发向量检索、知识库和记忆管理产品。OpenViking 将这些领域的工程经验用于开源上下文数据库，与社区共同开发。

这些工作涉及三个相互关联的问题：如何从非结构化内容中提取可检索的信息，如何在大量候选内容中找到相关上下文，以及如何保留对后续任务有用的交互经验。OpenViking 对应提供[资源解析与提取](../concepts/06-extraction.md)、[上下文检索](../concepts/07-retrieval.md)和[会话与记忆管理](../concepts/08-session.md)。可沿这些入口了解实现和使用条件。

### 发展历程与技术演进

| 时间 | 主要工作 |
| --- | --- |
| 2019–2023 | VikingDB 在字节跳动内部用于向量检索 |
| 2024 | 在火山引擎提供 VikingDB、Viking 知识库和 Viking 记忆库 |
| 2025 | 扩展 AI 搜索、知识助手等应用 |
| 2025 年末 | 开源 [MineContext](https://github.com/volcengine/MineContext)，探索主动式上下文应用 |
| 2026 年初 | 开源 OpenViking |

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

开源治理委员会负责技术路线、版本与功能优先级、核心架构和兼容性评审、工程规范、贡献者协作，以及相关项目的集成。成员包括 Haojie Qin、Jiahui Zhou、Linggang Wang、Maojia Sheng、Yaohui Sun。

具体模块的协作入口和近期活跃评审者见[贡献指南](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING_CN.md)。

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
- **改代码或文档**：阅读[贡献指南](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING_CN.md)，提交实现、测试、文档或翻译。
- **开发集成**：为 Agent 工具或框架添加插件，参考[插件开发指南](../agent-integrations/18-plugin-development.md)。
- **分享经验**：在社区分享使用案例、排障过程，或帮助其他用户解决问题。

Issue 和 PR 需要提供的信息见[贡献指南](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING_CN.md)。

## 讨论与协作机制

[GitHub 仓库](https://github.com/volcengine/OpenViking) 保存代码、文档和评审记录。[GitHub Discussions](https://github.com/volcengine/OpenViking/discussions) 用于技术方案讨论和社区交流，群聊适合即时交流；需要跟踪的问题和方案请同步到 Issue 或 Pull Request，方便后续查阅和协作。
