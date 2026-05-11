# ICLR 2026 论文分析报告：大模型时代的记忆系统

> 本报告分析了 38 篇 ICLR 2026 论文，涵盖 LLM Agent 记忆、持续学习、长上下文处理、知识推理等核心议题。

---

## 一、逐篇论文介绍

---

### 1. REMem: Reasoning with Episodic Memory in Language Agent

- **作者**：Yiheng Shu, Saisri Padmaja Jonnalagedda, Xiang Gao, Bernal Jiménez Gutiérrez, Weijian Qi et al.
- **关键词**：language agent, episodic memory, long-term memory
- **链接**：https://openreview.net/forum?id=fugnQxbvMm

**问题与动机**：人类擅长在时空上下文中记忆具体经验并在事件间进行推理，即情景记忆能力。然而，当前语言 Agent 的记忆主要是语义性的，无法有效地回顾和推理交互历史。现有工作要么忽视情景性，要么缺乏显式的事件建模，要么过度强调简单检索而非复杂推理。

**方法**：提出 REMem，一个构建和推理情景记忆的两阶段框架：(1) 离线索引阶段，将经验转化为混合记忆图，灵活连接时间感知的摘要（gists）和事实（facts）；(2) 在线推理阶段，使用配备精心策展工具的 Agent 检索器在记忆图上进行迭代检索。

**结果**：在四个情景记忆基准上，REMem 显著超越 Mem0 和 HippoRAG 2 等最先进记忆系统，在情景回忆和推理任务上分别提升 3.4% 和 13.4%。此外，REMem 对不可回答的问题表现出更鲁棒的拒绝行为。

---

### 2. Pretraining with Hierarchical Memories: Separating Long-Tail and Common Knowledge

- **作者**：Hadi Pouransari, David Grangier, C Thomas, Michael Kirchhof, Oncel Tuzel
- **关键词**：Large language models, pretraining, memory, long-tail knowledge, reasoning, forgetting
- **链接**：https://openreview.net/forum?id=XOu5z16cbY

**问题与动机**：现代语言模型的性能增益依赖参数规模扩大——更大模型存储更多世界知识和更好的推理能力。但将所有世界知识压缩到参数中既无必要（每次推理只用一小部分），对边缘设备也不实际（推理时内存和计算受限）。

**方法**：提出记忆增强架构和与现有硬件范式对齐的预训练策略。引入访问大规模分层参数化记忆库的小语言模型，记忆库编码世界知识。预训练学习将长尾知识存储在记忆参数中，小模型作为锚点捕获常见知识和通用推理能力。

**结果**：通过万亿 token 规模的实验，160M 参数模型配合 18M 参数的记忆（从 4.6B 记忆库检索）获得与 2 倍参数的常规模型相当的性能。研究了参数化记忆的最优类型和规模，扩展到 21B+ 参数，发现分层前馈记忆在 Transformer 架构中稳定工作，无论是预训练时添加还是后置添加。

---

### 3. CoMem: Compositional Concept-Graph Memory for Vision-Language Adaptation

- **作者**：Heng Zhou, Jing Tang, Jusheng Zhang, Yanshu Li, Canran Xiao et al.
- **关键词**：VLM, Vision Language Learning, Continual Learning
- **链接**：https://openreview.net/forum?id=xp7wDU9JBW

**问题与动机**：持续视觉-语言学习对多模态任务至关重要（图像-文本检索、视觉 QA、接地推理），但部署系统必须在严格隐私和记忆预算下从非平稳流中学习，朴素微调会导致遗忘和迁移能力下降。

**方法**：CoMem 将组合结构作为记忆和排练的基本单元：增量地将知识组织为紧凑的概念-关系图，在特征空间中通过条件化采样子图来排练。轻量级组合一致性目标保持部分-整体预测的协调，教师指导的、不确定性感知的过滤限制偏离流形的漂移。

**结果**：在跨域检索、结构化概念学习和持续多模态 VQA 任务上，CoMem 在匹配记忆和参数预算下实现 SOTA 保持与迁移性能。通过将结构作为记忆并在学习发生的特征空间中排练，CoMem 提供了无需原始样本的隐私友好范式。

---

### 4. Look Back to Reason Forward: Revisitable Memory for Long-Context LLM Agents (ReMemR1)

- **作者**：Yaorui Shi, Yuxin Chen, Siyuan Wang, Sihang Li, Hengxing Cai et al.
- **关键词**：LLM Agent, Reinforcement Learning, Long-Context LLM
- **链接**：https://openreview.net/forum?id=1cymflI2Lh

**问题与动机**：LLM 在长上下文 QA 中面临关键证据分散在百万 token 中的挑战。现有"边读边记忆"方法虽然可扩展，但存在三个问题：前向处理不可逆、通过覆盖导致信息丢失、RL 信号稀疏。

**方法**：提出 ReMemR1，一个记忆增强 Agent，具有回调增强记忆（callback-enhanced memory），允许从整个记忆历史中选择性检索，支持非线性推理和回溯早期证据。进一步提出 RLMLR（多层级奖励的强化学习），结合最终答案奖励与密集的步骤级信号来引导有效的记忆使用。

**结果**：在长文档 QA 上的实验显示相对现有记忆方法有显著提升，验证了 ReMemR1 作为长上下文推理 Agent 的有效方案。

---

### 5. AssoMem: Scalable Memory QA with Multi-Signal Associative Retrieval

- **作者**：Kai Zhang, Xinyuan Zhang, Ejaz Ahmed, Hongda Jiang, Caleb Kumar et al.
- **关键词**：memory-augmented LLM, scalable retrieval, memory question answering
- **链接**：https://openreview.net/forum?id=ZCjWUBwCwE

**问题与动机**：对于记忆增强 AI 助手的 QA 任务，从大规模记忆中准确回忆仍是核心挑战，尤其是在相似性密集场景中，现有方法主要依赖与查询的语义距离进行检索，容易漏掉语义不相似但重要的记忆。

**方法**：受人类联想式信息链接启发，提出 AssoMem 框架：构建联想记忆图，将对话话语锚定到自动提取的线索，提供对话上下文的丰富组织视图并促进重要性感知的排序。进一步融合多维度检索信号——相关性、重要性和时间对齐——使用自适应互信息（MI）驱动的融合策略。

**结果**：在三个基准和新增 MeetingQA 数据集上的广泛实验表明，AssoMem 一致超越 SOTA 基线，验证了其在上下文感知记忆回忆中的优越性。

---

### 6. HALO: A Memory-Efficient Hierarchical Algorithm for Large-scale Optimal Transport Problems

- **作者**：Wenzhou Xia, Ya-Nan Zhu, Jingwei Liang, Xiaoqun Zhang
- **关键词**：optimal transport, linear programming, multiscale framework, first-order methods
- **链接**：https://openreview.net/forum?id=CkOBcyntGd

**问题与动机**：大规模最优传输（OT）问题面临内存和可扩展性瓶颈，现有求解器难以处理高分辨率数据。

**方法**：提出 HALO，一种内存高效的层次化算法，核心是结合 OT 问题的层次化表示与并行友好的线性规划求解器，并集成主动剪枝技术进一步降低内存和计算成本。理论上建立了细化阶段的规模无关迭代复杂度上界。

**结果**：对 n=1024² 像素的图像实现 8.9 倍加速和 70.5% 内存降低；对 n=2^18 的 3D 点云实现 1.84 倍加速和 83.2% 内存降低，同时传输成本降低 24.9%。

---

### 7. Memory-Statistics Tradeoff in Continual Learning with Structural Regularization

- **作者**：Haoran Li, Jingfeng Wu, Vladimir Braverman
- **关键词**：continual learning, deep learning theory
- **链接**：https://openreview.net/forum?id=qfEqXJnlB4

**问题与动机**：持续学习中，如何在有限的记忆复杂度下实现良好的统计效率是基本理论问题。

**方法**：研究两个线性回归任务的持续学习问题，使用结构化正则化算法（基于前一任务 Hessian 的广义 l2 正则化）来缓解灾难性遗忘。建立该算法的联合过量风险上下界。

**结果**：揭示了记忆复杂度与统计效率之间的基本权衡——增加正则化向量数量改善过量风险但恶化记忆复杂度，反之亦然。理论表明无正则化的朴素持续学习遭受灾难性遗忘，而结构化正则化可达到与同时访问两个任务的联合训练相当的性能，突出了曲率感知正则化的关键作用。

---

### 8. RF-Mem: Evoking User Memory — Personalizing LLM via Recollection-Familiarity Adaptive Retrieval

- **作者**：Yingyi Zhang, Junyi Li, Wenlin Zhang, Pengyue Jia, Xianneng Li et al.
- **关键词**：Large Language Model, Memory Retrieval, Recollection-Familiarity Dual Process, Personalization
- **链接**：https://openreview.net/forum?id=f7p0F2X6XN

**问题与动机**：个性化 LLM 依赖记忆检索来融入用户历史、偏好和上下文。现有方法要么将用户所有过去记忆注入提示词（昂贵且不可扩展），要么简化为一次性相似性搜索（仅捕获表面匹配）。认知科学表明人类记忆通过双过程运作：Familiarity（快速粗略识别）和 Recollection（刻意链条式重构）。

**方法**：提出 RF-Mem，基于熟悉度不确定性引导的双路径记忆检索器。通过均值分数和熵衡量熟悉度信号——高熟悉度走快速 Top-K Familiarity 检索路径，低熟悉度激活 Recollection 路径。在 Recollection 路径中，系统聚类候选记忆并应用 alpha-mix 与查询在嵌入空间中迭代扩展证据，模拟刻意的上下文重构。

**结果**：在三个基准和不同语料规模上，RF-Mem 在固定预算和延迟约束下一致超越一次性检索和全上下文推理。可即插即用于 HyDE、Search-o1 等高级 RAG 管线。

---

### 9. BrowseNet: Graph-Based Associative Memory for Contextual Information Retrieval

- **作者**：Pavan Kumar S, Kiran Kumar Nakka, C Vamshi Krishna Reddy, Divyateja Pasupuleti, Prakhar Agarwal et al.
- **关键词**：retrieval augmented generation, graph-of-chunks, continual learning, large language models
- **链接**：https://openreview.net/forum?id=2q5CugVPoK

**问题与动机**：联想记忆系统在从大型文档集合中高效检索语义相关信息时面临挑战，特别是查询需要遍历概念间复杂关系的场景。传统 RAG 难以捕捉文本数据中的复杂联想模式和关系。

**方法**：提出 BrowseNet，将非结构化文本转换为"块图"（graph-of-chunks）表示——节点编码带有语义嵌入的文档块，边捕捉内容段之间的词汇关系。通过基于查询特征的动态子图遍历，模拟内容可寻址记忆系统。结合来自词汇关系的结构相似性和基于嵌入的语义相似性。

**结果**：在需要跨多信息源联想推理的公开数据集上，BrowseNet 在精确匹配分数上达到 SOTA，超越图基 RAG 和稠密检索方法，同时比 HippoRAG-2 成本降低约 33 倍。代码和数据已开源。

---

### 10. GraphPlanner: Graph Memory-Augmented Agentic Routing for Multi-Agent LLMs

- **作者**：Tao Feng, Haozhen Zhang, Zijie Lei, Peixuan Han, Jiaxuan You
- **关键词**：Multi-agent LLMs, Memory utilization, Heterogeneous agents, Graph
- **链接**：https://openreview.net/forum?id=ZdGB7MNQDT

**问题与动机**：LLM 路由在整合多样模型优势方面已取得成果，但要支持更真实的应用，路由必须扩展到 Agent 式 LLM 场景——需要任务规划、异构 Agent 间的多轮合作和记忆利用。

**方法**：提出 GraphPlanner，将工作流生成形式化为马尔可夫决策过程（MDP），每步同时选择 LLM 骨干和 Agent 角色（规划者、执行者、总结者）。使用异构图 GARNet 捕获查询、Agent 和响应之间的交互记忆，将历史记忆和工作流记忆整合到更丰富的状态表示中。整个管线通过强化学习优化。

**结果**：在 14 个多样 LLM 任务上评估：(1) 准确率提升最高 9.3%，GPU 成本从 186.26 GiB 降至 1.04 GiB；(2) 对未见任务和 LLM 具有强零样本能力；(3) 有效利用历史记忆，支持归纳和转导推理。

---

### 11. M3-Agent: A Multimodal Agent with Long-Term Memory

- **作者**：Lin Long, Yichen He, Wentao Ye, Yiyuan Pan, Yuan Lin et al.
- **关键词**：multimodal agent, long-term memory
- **链接**：https://openreview.net/forum?id=PMz29A7Muq

**问题与动机**：现有视觉-语言模型在离线视频任务上表现出色，但缺乏长期记忆能力——无法像人类一样从实时视觉和听觉输入中积累知识。

**方法**：M3-Agent 以实体为中心的多模态方式组织记忆，处理实时视觉和听觉输入来构建和更新情景记忆与语义记忆，逐步积累世界知识。给定指令后，自主执行多轮推理并检索相关记忆。同时开发 M3-Bench 基准，包含 100 个新录制的机器人视角视频和 920 个网络视频，标注测试人理解、通用知识提取和跨模态推理的 QA 对。

**结果**：通过强化学习训练的 M3-Agent 在 M3-Bench-robot、M3-Bench-web 和 VideoMME-long 上分别比使用 Gemini-1.5-pro 和 GPT-4o 的最强基线高出 6.7%、7.7% 和 5.3%。随着视频长度增加，M3-Agent 保持稳定的 token 用量和准确率，而基线 VLM 显著下降。来自 ByteDance Seed。

---

### 12. EMPO²: Exploratory Memory-Augmented LLM Agent via Hybrid On- and Off-Policy Optimization

- **作者**：Zeyuan Liu, Jeonghye Kim, Xufang Luo, Dongsheng Li, Yuqing Yang
- **关键词**：Reinforcement Learning, LLM Agent, Exploration
- **链接**：https://openreview.net/forum?id=UOzxviKVFO

**问题与动机**：探索仍是 RL 训练的 LLM Agent 的关键瓶颈。现有方法利用预训练知识，但在需要发现新状态的环境中失败。

**方法**：提出 EMPO²，混合 RL 框架，利用记忆进行探索，结合在线和离线策略更新。在线策略更新确保有记忆时的良好表现，离线策略更新确保无记忆时也有鲁棒性。

**结果**：在 ScienceWorld 和 WebShop 上分别比 GRPO 提升 128.6% 和 11.3%。在分布外测试中，EMPO² 展示出对新任务的卓越适应性——仅需少量有记忆的试错即可适应，无需参数更新。

---

### 13. MemGAS: From Single to Multi-Granularity — Toward Long-Term Memory Association and Selection of Conversational Agents

- **作者**：Derong Xu, Yi Wen, Pengyue Jia, Yingyi Zhang, Wenlin Zhang et al.
- **关键词**：Long-Term Memory, Agent, LLM, Multi-Granularity, Conversation
- **链接**：https://openreview.net/forum?id=i2yIvZARnG

**问题与动机**：用户与 Agent 的长期交互积累了大量对话记录，有限上下文窗口的 LLM 难以维持连贯的长期对话记忆。现有检索增强记忆系统依赖单一粒度的记忆分割和检索，无法捕获深层记忆连接，导致有用信息部分检索或噪声过多。

**方法**：MemGAS 基于多粒度记忆单元（会话/轮次/摘要/关键词），使用高斯混合模型（GMM）聚类并将新记忆与历史记忆关联。基于熵的路由器通过评估查询相关性分布来自适应选择最优粒度，平衡信息完整性和噪声。检索的记忆通过 LLM 过滤进一步精炼，并使用个性化 PageRank（PPR）排序。

**结果**：在四个长期记忆基准上，MemGAS 在问答和检索任务上均超越 SOTA 方法，在不同查询类型和 Top-K 设置下均表现优越。

---

### 14. TokMem: One-Token Procedural Memory for Large Language Models

- **作者**：Zijun Wu, Yongchang Hao, Lili Mou
- **关键词**：Procedural Memory, Memory tokens, Continual adaptation, Large language models
- **链接**：https://openreview.net/forum?id=RWjEf9PdiJ

**问题与动机**：LLM 通常通过提示词控制，但提示词每次查询都需重复处理且难以模块化复用。如何让 LLM 高效地记住可复用的任务过程？

**方法**：将每个可复用的任务过程编译为单个可训练记忆 token。每个 token 同时充当过程索引和生成控制信号，以常数级开销引导生成。骨干 LLM 保持冻结，过程知识完全存储在专用 token 嵌入中，因此可以持续添加新过程而不干扰已有过程。

**结果**：在两个设置上评估——1000 个 Super-NaturalInstructions 任务的原子召回和多步函数调用的组合召回。TokMem 一致超越检索增强的提示方法，同时避免重复的上下文开销，且以更少的可训练参数匹配或超越参数高效微调。

---

### 15. Forget Forgetting: Continual Learning in a World of Abundant Memory

- **作者**：Dongkyu Cho, Taesup Moon, Rumi Chunara, Kyunghyun Cho, Sungmin Cha
- **关键词**：continual learning, model merging, machine learning, large language models
- **链接**：https://openreview.net/forum?id=fvL8IIEPxG

**问题与动机**：持续学习传统上聚焦于最小化样本记忆，但这与 GPU 时间（而非存储）是现代系统主要瓶颈的现实不符。本文研究更实际的"中间地带"——记忆充足到可以缓解遗忘，但全量重训仍过于昂贵。

**方法**：提出 Weight Space Consolidation，轻量级方法结合：(1) 基于秩的参数重置以恢复可塑性，(2) 权重平均以增强稳定性。在"记忆充足"场景下，简单重放基线以远低于 SOTA 的 GPU 成本超越 SOTA 方法。

**结果**：在类别增量学习（图像分类器）和持续指令微调（LLM）上验证，超越强基线同时匹配重放的低计算成本。这些发现挑战了长期以来的 CL 假设，为样本记忆不再是限制因素的现实 CL 系统建立了新的高效基线。

---

### 16. FlowSearcher: Synthesizing Memory-Guided Agentic Workflows for Web Information Seeking

- **作者**：Keyi Xiang, Zeyu Feng, Zhuoyi Lin, Yueming Lyu, Shi Boyuan et al.
- **关键词**：Large Language Model Reasoning, Structured Planning, Agentic Workflow
- **链接**：https://openreview.net/forum?id=34v7DVz2l0

**问题与动机**：Web 搜索是深度研究 Agent 的基石，但现有系统依赖 ReAct 风格的线性工作流，无法适应多样查询类型和工具使用策略。

**方法**：将 Web 信息搜索形式化为记忆引导的 Agent 工作流合成。FlowSearcher 将查询分解为子目标，为每个子目标合成定制的 DAG 工作流图，动态调整工具使用的深度、顺序和组合。层次化记忆将过去的工作流整合为可复用的结构经验，检索来引导新查询的工作流编排和执行。

**结果**：无需监督训练或 RLHF，从被动工具调用转向经验条件化的工作流设计。在 GAIA、BrowseComp 和 GPQA 上，FlowSearcher 在相同模型骨干下一致匹配或超越经过 RLHF 训练的 Web Agent。

---

### 17. Multi-Agent Debate with Memory Masking (MAD-M²)

- **作者**：Hongduan Tian, Xiao Feng, Ziyuan Zhao, Xiangyu Zhu, Rolan Yan et al.
- **关键词**：multi-agent debate, memory selection, robustness
- **链接**：https://openreview.net/forum?id=EdTt8nMAMA

**问题与动机**：多 Agent 辩论（MAD）通过让多个 LLM Agent 访问先前辩论记忆来迭代改进推理，但存在错误记忆问题——Agent 容易受到先前辩论中错误记忆的影响。

**方法**：提供理论洞察——MAD 性能高度依赖先前辩论记忆的质量。提出 MAD-M²（Multi-Agent Debate with Memory Masking），在每个辩论轮次开始时屏蔽错误记忆，保留有信息量的记忆同时丢弃错误记忆。

**结果**：在主流数学和逻辑推理基准上的广泛实验表明，MAD-M² 能够识别错误记忆并在推理中超越标准 MAD，提高了多 Agent 辩论的鲁棒性。

---

### 18. GLoW: Dual-Scale World Memory for LLM Agents towards Hard-Exploration Problems

- **作者**：Minsoo Kim, Seung-won Hwang
- **关键词**：hard-exploration problems, world memory, LLM agents, text-based games
- **链接**：https://openreview.net/forum?id=bH5uHIVtTe

**问题与动机**：LLM Agent 在稀疏反馈的硬探索任务中仍受限——需要在几乎没有奖励信号的情况下进行持续探索。

**方法**：提出 GLoW，利用双尺度文本世界记忆：全局尺度维护高价值发现的轨迹前沿（trajectory frontier），局部尺度通过多路径优势反思（Multi-path Advantage Reflection）机制从试错中学习，推断基于优势的进展信号来引导探索。

**结果**：在 Jericho 文本游戏基准上实现 LLM 方法的新 SOTA。与 RL 方法相比，以 100-800 倍更少的环境交互达到可比性能。扩展到更强 LLM 时，在 6 个最难的 Jericho 游戏中的 4 个超越所有先前方法。

---

### 19. REM: Redirection for Erasing Memory — Towards a Universal Unlearning Method for Corrupted Data

- **作者**：Stefan Schoepf, Michael Curtis Mozer, Nicole Elyse Mitchell, Alexandra Brintrup, Georgios Kaissis et al.
- **关键词**：machine unlearning, corrupted data, data poisoning
- **链接**：https://openreview.net/forum?id=xG0mQ4Xsfm

**问题与动机**：机器遗忘方法针对特定任务设计，难以系统比较。需要统一框架来刻画和评估不同损坏数据遗忘任务。

**方法**：提出概念空间，用两个维度刻画视觉分类器中多样的损坏数据遗忘任务——发现率（遗忘时已知损坏数据的比例）和统计规律性（从随机样本到共享概念）。先前方法只针对部分空间，在其他区域可预测地失败。提出 REM：在遗忘时引入专用神经元，将损坏数据重定向到这些神经元，然后丢弃/停用它们。

**结果**：REM 在整个任务空间上表现强劲，而先前的 SOTA 方法在它们设计的区域之外失败。为机器遗忘提供了通用解决方案。

---

### 20. Memento: Toward an All-Day Proactive Assistant for Ultra-Long Streaming Video

- **作者**：Hongxiang Jiang, Zengrui Ge, Guo Chen, Qixiong Wang, Jile Jiao et al.
- **关键词**：Vision-Language Models, Online Ultra-Long Video Understanding, Dynamic Memory
- **链接**：https://openreview.net/forum?id=FtdbdoGbk3

**问题与动机**：现有模型通常局限于数十分钟的视频，无法实现全天前瞻式理解。它们在线维护长期上下文时受 token 积累和缺乏可扩展记忆机制的限制，无法完成如"提醒用户几小时前是否服药"这类需要长期推理的前瞻式任务。

**方法**：提出 Memento，首个面向超长流视频的前瞻式视觉-语言框架。引入动态记忆和查询相关记忆选择，实现稀疏记忆保留和高效检索，避免 token 增长。提出步感知记忆注意力（Step-Aware Memory Attention），将记忆访问与时间步对齐以实现稳定监督。构建 Memento-54K 和 MementoBench 数据集-基准套件。

**结果**：支持最长 7 小时的视频流理解，在文本、物体和动作任务上取得优越性能，为全天前瞻式视频助手铺平道路。

---

### 21. Sculptor: Empowering LLMs with Cognitive Agency via Active Context Management

- **作者**：Mo Li, L.H. Xu, Qitai Tan, Long Ma, Flood Sung et al.
- **关键词**：Large Language Models, Long Context, Active Context Management, Tool Use, Proactive Interference, Reinforcement Learning
- **链接**：https://openreview.net/forum?id=HPeiH7da0Z

**问题与动机**：LLM 在处理长上下文时因前摄干扰（proactive interference）导致性能下降——上下文早期部分的无关信息破坏推理和记忆回忆。大多数研究聚焦外部记忆系统，但忽视了 LLM 主动管理自身工作记忆的能力。

**方法**：提出 Sculptor，赋予 LLM 三类主动上下文管理（ACM）工具：(1) 上下文分片，(2) 摘要/隐藏/恢复，(3) 精确搜索。使 LLM 能够主动管理注意力和工作记忆，类似于人类选择性关注相关信息。即使无特定训练，也能利用 LLM 固有的工具调用和指令遵循能力提升性能。进一步引入动态上下文感知 RL 方法优化 Agent。

**结果**：在多样长上下文基准上显著提升性能。突出表明显式上下文控制策略（而非仅仅更大的 token 窗口）是长上下文鲁棒性的关键。

---

### 22. PM-KVQ: Progressive Mixed-precision KV Cache Quantization for Long-CoT LLMs

- **作者**：Tengxuan Liu, Shiyao Li, Jiayi Yang, Tianchen Zhao, Feng Zhou et al.
- **关键词**：KV Cache Quantization
- **链接**：https://openreview.net/forum?id=Vem6FQvRvq

**问题与动机**：长链式思维（CoT）推理产生大量 KV Cache 内存开销。现有 KV Cache 量化方法应用于长 CoT 时性能下降，原因有二：(1) 累积量化误差大——现有方法未能充分利用可用内存且每步直接量化；(2) 短上下文校准偏差——RoPE 使短上下文校准数据无法覆盖 Key Cache 中低频通道的分布。

**方法**：提出渐进式混合精度 KV Cache 量化（PM-KVQ）：(1) 设计渐进式量化策略逐步降低每块 KV Cache 的位宽，提出分块内存分配为更敏感的 Transformer 块分配更高位宽；(2) 提出位置插值校准策略，用短标定数据配合位置插值近似长上下文分布。

**结果**：在 7B-70B 长 CoT LLM 上，PM-KVQ 在相同内存预算下推理基准性能提升最高 8%，吞吐量提升 2.73-5.18 倍。

---

### 23. TNT: Improving Chunkwise Training for Test-Time Memorization

- **作者**：Zeman Li, Ali Behrouz, Yuan Deng, Peilin Zhong, Praneeth Kacham et al.
- **关键词**：Recurrent Neural Networks, Sequence Modeling
- **链接**：https://openreview.net/forum?id=rajioNWfRs

**问题与动机**：Titans/TTT 等 RNN 训练极慢、硬件利用率低。现有并行化方法在块大小超参数上面临根本冲突：大块提升速度但降低性能，必须做固定、次优的折中。

**方法**：提出 TNT，两阶段训练范式解耦训练效率与推理性能：第一阶段，效率导向的预训练使用分层记忆——全局模块处理大块获取长程上下文，多个并行局部模块处理细粒度细节。关键是通过周期性重置局部记忆状态打破顺序依赖，实现大规模上下文并行化。第二阶段，短暂微调仅将局部记忆模块适配到更小的高分辨率块大小。

**结果**：在 Titans 和 TTT 模型上评估，训练速度提升最高 17 倍，同时提升模型精度。消除了关键的可扩展性障碍，为开发表达性 RNN 并缩小与 Transformer 的差距奠定基础。

---

### 24. Learning Facts at Scale with Active Reading

- **作者**：Jessy Lin, Vincent-Pierre Berges, Xilun Chen, Wen-tau Yih, Gargi Ghosh et al.
- **关键词**：factuality, tail knowledge, synthetic data, synthetic continued pretraining
- **链接**：https://openreview.net/forum?id=mRi2cJDtIS

**问题与动机**：LLM 从参数化记忆中学习和回忆事实的能力不可靠，很大程度上取决于特定事实在训练数据中的流行度。从业者缺乏工具来确保模型可靠、一致地学习给定知识。

**方法**：提出 Active Reading，训练模型使用自生成学习策略"研读"给定材料。核心思想是让模型主动学习而非被动接受信息。

**结果**：8B 专家模型在 SimpleQA 上达 66%（比普通微调高 313%），FinanceBench 上达 26%（高 160%）。在预训练规模上，发布 WikiExpert-8B（在 1 万亿生成 token 上训练），在事实 QA 上超越数百亿参数的模型。

---

### 25. When Agents "Misremember" Collectively: Exploring the Mandela Effect in LLM-based Multi-Agent Systems

- **作者**：Naen Xu, Hengyu An, Shuo Shi, Jinghuai Zhang, Chunyi Zhou et al.
- **关键词**：LLM for Social Science, Mandela Effect, Multi-agent System, Cognitive Bias
- **链接**：https://openreview.net/forum?id=yIoMqDes7O

**问题与动机**：多 Agent 系统中 Agent 对集体认知偏见的易感性是被忽视的问题。曼德拉效应——群体因社会影响和内化错误信息而集体误记——限制了我们对多 Agent 系统中记忆偏差的理解，并引发错误信息传播的伦理担忧。

**方法**：提出 ManBench 基准，在四类易受曼德拉效应影响的任务类型上评估 Agent 行为，使用五种在 Agent 角色和记忆时间尺度上不同的交互协议。量化多个 LLM 的曼德拉效应，分析影响因素，提出缓解策略——提示级防御（认知锚定、来源审查）和模型级对齐防御。

**结果**：平均降低 74.40% 的曼德拉效应。为开发更具韧性和伦理对齐的协作多 Agent 系统提供了宝贵洞察。

---

### 26. XENON: Experience-based Knowledge Correction for Robust Planning in Minecraft

- **作者**：Seungjoon Lee, Suhwan Kim, Minhyeon Oh, Youngsik Yoon, Jungseul Ok
- **关键词**：LLM-guided exploration, hierarchical planning, LLM knowledge correction
- **链接**：https://openreview.net/forum?id=N22lDHYrXe

**问题与动机**：LLM 规划 Agent 在 Minecraft 等长视野环境中常以错误先验开始，且即使有反馈也无法通过提示纠正。LLM 固有的知识缺陷是规划失败的根源。

**方法**：提出 XENON，通过算法从经验中修正知识，实现对错误先验和稀疏二值反馈的鲁棒性。集成两个机制：自适应依赖图（Adaptive Dependency Graph）——从成功经验修正物品依赖关系；失败感知动作记忆（Failure-aware Action Memory）——从失败经验修正动作知识。

**结果**：在多个 Minecraft 基准上超越先前 Agent 的知识学习和长视野规划能力。仅用 7B 开源 LLM 即超越依赖更大私有模型的 Agent。

---

### 27. Graph-based Nearest Neighbors with Dynamic Updates via Random Walks

- **作者**：Nina Mishra, Yonatan Naamad, Tal Wagner, Lichen Zhang
- **关键词**：nearest neighbor search, graph, random walk
- **链接**：https://openreview.net/forum?id=l97Kacqdfk

**问题与动机**：HNSW（最广泛使用的图基 ANN 算法）支持插入但不支持高效删除。先前删除算法以增加查询延迟、降低召回率或延长删除时间为代价。

**方法**：提出基于随机游走的图 ANN 新理论框架。利用该框架分析保持命中时间统计（相比删除点之前的图）的随机删除方法，然后将其转化为确定性删除算法。

**结果**：通过大量实验表明，在查询延迟、召回率、删除时间和内存使用之间实现更好的权衡。对需要动态更新的 RAG 系统和 LLM 应用具有重要意义。

---

### 28. Learning From the Past with Cascading Eligibility Traces

- **作者**：Tokiniaina Raharison Ralambomihanta, Ivan Anokhin, Roman Pogodin, Samira Ebrahimi Kahou, Jonathan Cornford et al.
- **关键词**：biological credit assignment, eligibility traces, synaptic plasticity, computational neuroscience
- **链接**：https://openreview.net/forum?id=yQ7ssakeKM

**问题与动机**：动物常在显著延迟后收到错误和奖励信息。标准指数衰减资格迹在延迟期间混合事件，导致任何显著延迟后的信用分配信号出现问题。

**方法**：展示由状态空间模型形成的资格迹——受级联生化反应启发——可在任意延迟下提供时间精确的记忆来处理信用分配。级联资格迹（CETs）在行为时间尺度（秒到分钟）上工作，也可处理极慢的逆行信号（如逆行轴突信号中发现的）。

**结果**：CETs 为建模突触可塑性提供了优秀基础，连接了计算神经科学与深度学习中的信用分配问题。

---

### 29. ACE: Agentic Context Engineering for Self-Improving Language Models

- **作者**：Qizheng Zhang, Changran Hu, Shubhangi Upasani, Boyuan Ma, Fenglu Hong et al.
- **关键词**：LLM Agents, Context Engineering, Continual Learning, Agent Memory, Test-Time Scaling, Self-Improving LLMs
- **链接**：https://openreview.net/forum?id=eC4ygDs02R

**问题与动机**：LLM 应用（如 Agent 和领域推理）越来越依赖上下文适应——通过指令、策略或证据修改输入，而非权重更新。先前方法存在简明性偏差（丢失领域洞察）和上下文坍塌（迭代重写侵蚀细节）问题。

**方法**：ACE 将上下文视为可演化的"剧本"，通过生成、反思和策展的模块化过程积累、精炼和组织策略。通过结构化增量更新防止上下文坍塌，保留详细知识并与长上下文模型协同扩展。既可离线优化（如系统提示），也可在线优化（如 Agent 记忆）。

**结果**：在 Agent 基准上 +10.6%，金融领域 +8.6%，显著降低适应延迟和 rollout 成本。无需标注监督，仅需自然执行反馈即可适应。在 AppWorld 排行榜上，ACE 使用更小的开源模型匹配顶级生产 Agent 的总体平均分，并在更难的 test-challenge 分割上超越它。

---

### 30. KRLM: Knowledge Reasoning Language Model — Unifying Knowledge and Language for Inductive Knowledge Graph Reasoning

- **作者**：Xingrui Zhuo, Jiapu Wang, Gongqing Wu, Zhongyuan Wang, Jichen Zhang et al.
- **关键词**：Inductive Knowledge Graph Reasoning, Large Language Model, Knowledge Graph Foundation Model
- **链接**：https://openreview.net/forum?id=2g8EmFwNTB

**问题与动机**：归纳知识图谱推理（KGR）需要在包含未知实体和关系的开放域 KG 中发现事实。LLM 的内在知识可能被稀疏 KG 上下文压过，导致 LLM 知识扭曲（对模型推理造成不可逆损害），且现有方法仍难以完全约束生成幻觉。

**方法**：提出 KRLM，在 KGR 过程中实现 LLM 知识与 KG 上下文的统一协调。设计 KRL 指令格式和 KRL 分词器对齐 LLM 知识与 KG 表示；提出 KRL 注意力层通过动态知识记忆机制协调 LLM 内在知识与额外 KG 上下文；提出结构感知的下一实体预测器，严格约束推理结果在可信知识域内。

**结果**：在 25 个真实世界归纳 KGR 数据集上，KRLM 在零样本推理和微调场景中均显著优于现有方法。

---

### 31. DataMind: Scaling Generalist Data-Analytic Agents

- **作者**：Shuofei Qiao, Yanqiu Zhao, Zhisong Qiu, Xiaobin Wang, Jintian Zhang et al.
- **关键词**：Data Analysis, LLM Agents, Agent Training
- **链接**：https://openreview.net/forum?id=5PxFqpIYWC

**问题与动机**：数据分析 Agent 是自动科学发现的关键催化剂，但现有方法严重依赖私有模型的提示工程，开源模型在多样格式、大规模数据文件和长视野多步推理方面挣扎。

**方法**：DataMind 解决三个关键挑战：(1) 数据不足——细粒度任务分类和递归从易到难任务组合增加合成查询的多样性和难度；(2) 训练策略不当——知识增强轨迹采样+模型/规则过滤；(3) 不稳定的多轮 rollout——动态可调训练目标结合 SFT 和 RL 损失，加上内存节省且稳定的代码多轮 rollout 框架。策划 DataMind-12K 高质量轨迹集。

**结果**：DataMind-14B 在多个数据分析基准上以平均 71.16% 达到 SOTA，超越 DeepSeek-V3.1 和 GPT-5。DataMind-7B 以 68.10% 在所有开源模型中排名第一。

---

### 32. Test-Time Training Done Right (LaCT)

- **作者**：Tianyuan Zhang, Sai Bi, Yicong Hong, Kai Zhang, Fujun Luan et al.
- **关键词**：Test-Time Training, Sequence Model, Long Context Model
- **链接**：https://openreview.net/forum?id=Tb9qAxT3xv

**问题与动机**：测试时训练（TTT）通过在推理时适应模型部分权重（快权重）来建模上下文依赖。现有 TTT 方法在长序列数据处理上效果不佳——FLOPs 利用率极低（常低于 5%），因为刻意使用小在线迷你批次（每 16 或 64 个 token 更新快权重），且细粒度块因果依赖使其难以处理非一维有序序列。

**方法**：提出 LaCT（Large Chunk Test-Time Training），走相反方向——使用 2K 到 1M token 的极大块更新。GPU 利用率提升数个量级，且便于非线性状态大小扩展（可达模型参数的 40%），无需复杂易错的自定义 kernel。还允许集成复杂优化器如 Muon 用于在线记忆更新。

**结果**：验证覆盖多种模态和任务——图像集的新视角合成、语言模型、自回归视频扩散。可扩展到 14B 参数、56K token 序列。最长序列实验中，以超过 100 万上下文长度进行新视角合成。

---

### 33. MemER: Scaling up Memory for Robotic Control via Experience Retrieval

- **作者**：Ajay Sridhar, Jennifer Pan, Satvik Sharma, Chelsea Finn
- **关键词**：Robot Learning, Memory, Vision-Language-Action Models
- **链接**：https://openreview.net/forum?id=1dH4ARGdwD

**问题与动机**：人类依赖记忆执行任务，但如何赋予机器人策略同样的能力？直接以长观测历史为条件计算昂贵且在协变量偏移下脆弱，而不加区分地子采样历史会导致无关或冗余信息。

**方法**：提出层次化策略框架——高层策略训练来从过去经验中选择和跟踪任务相关关键帧，使用选中的关键帧和最近帧生成文本指令供低层策略执行。与现有视觉-语言-动作（VLA）模型兼容，使系统能高效推理长视野依赖。

**结果**：微调 Qwen2.5-VL-7B-Instruct 和 π₀.₅ 作为高低层策略，使用补充最少语言标注的演示。MemER 在三个需要分钟级记忆的真实世界长视野机器人操作任务上超越先前方法。

---

### 34. LightMem: Lightweight and Efficient Memory-Augmented Generation

- **作者**：Jizhan Fang, Xinle Deng, Haoming Xu, Ziyan Jiang, Yuqi Tang et al.
- **关键词**：large language model, LLM memory
- **链接**：https://openreview.net/forum?id=dyJ0GWpjJB

**问题与动机**：LLM 在动态复杂环境中难以有效利用历史交互信息。记忆系统使 LLM 超越无状态交互，但现有系统常带来显著的时间和计算开销。

**方法**：受 Atkinson-Shiffrin 人类记忆模型启发，LightMem 将记忆组织为三个互补阶段：(1) 认知启发的感知记忆——通过轻量压缩快速过滤无关信息，按主题分组；(2) 主题感知短期记忆——整合主题分组，组织和摘要内容以实现更结构化访问；(3) 长期记忆——睡眠时更新采用离线过程，将巩固与在线推理解耦。

**结果**：在 LongMemEval 上使用 GPT 和 Qwen 骨干，LightMem 准确率提升最高 10.9%，同时 token 用量减少最高 117 倍，API 调用减少最高 159 倍，运行时间减少超 12 倍。

---

### 35. Improving Code Localization with Repository Memory

- **作者**：Boshi Wang, Weijian Xu, Yunsheng Li, Xuemei Gao, Yujia Xie et al.
- **关键词**：Code Localization, Large Language Models, Agent Memory
- **链接**：https://openreview.net/forum?id=8yjWLJy2eX

**问题与动机**：代码定位是仓库级软件工程任务（如 bug 修复）的基本挑战。现有方法为语言 Agent 配备获取仓库信息的工具/接口，但忽视了关键的记忆方面——每个实例都从头处理，假设没有先前的仓库知识。相比之下，人类开发者自然构建长期仓库记忆（如关键模块功能和 bug 类型与修复位置的关联）。

**方法**：利用仓库的 commit 历史——记录代码库演化的丰富但未被充分利用的资源——为 Agent 增加记忆。引入工具允许 Agent 从非参数化记忆中检索：近期历史提交和关联 issue，以及通过 commit 模式识别的活跃代码区域功能摘要。

**结果**：在 SWE-bench-verified 和更新的 SWE-bench-live 基准上显著提升 LocAgent（SOTA 定位框架）。推动开发能够积累和利用过去经验的 Agent，更贴近人类开发者的专业能力。

---

### 36. MemGen: Weaving Generative Latent Memory for Self-Evolving Agents

- **作者**：Guibin Zhang, Muxin Fu, Shuicheng Yan
- **关键词**：Agent Memory, Latent Reasoning, LLM Agent
- **链接**：https://openreview.net/forum?id=vI56m4Iu4e

**问题与动机**：参数化记忆强行调整模型参数，检索式记忆将经验外化到结构化数据库，两者都无法捕获人类认知中推理与记忆的流动交织。

**方法**：提出 MemGen，动态生成式记忆框架，赋予 Agent 类人的认知能力。包含记忆触发器（监控 Agent 推理状态决定显式记忆调用）和记忆编织器（以 Agent 当前状态为刺激，构建潜在 token 序列作为机器原生记忆来丰富推理）。实现推理中回忆和增强潜在记忆的紧密交织循环。

**结果**：在 8 个基准上超越 ExpeL 和 AWM 等领先外部记忆系统最多 38.22%，超越 GRPO 最多 13.44%，展现强跨域泛化能力。更重要的是，无显式监督下，MemGen 自发涌现出类人记忆功能——规划记忆、过程记忆和工作记忆，暗示了向更自然化机器认知的涌现轨迹。

---

### 37. ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory

- **作者**：Siru Ouyang, Jun Yan, I-Hung Hsu, Yanfei Chen, Ke Jiang et al.
- **关键词**：LLM Agents, Memory Mechanism, Reasoning, Test-Time Scaling
- **链接**：https://openreview.net/forum?id=jL7fwchScm

**问题与动机**：LLM Agent 在持续的真实世界角色中自然遇到连续任务流，但关键局限是未能从积累的交互历史中学习，被迫丢弃有价值的洞察并重复过去的错误。

**方法**：提出 ReasoningBank，从 Agent 自判的成功和失败经验中提炼可泛化的推理策略。测试时 Agent 从 ReasoningBank 检索相关记忆来指导交互，然后整合新的学习，随时间变得更强大。进一步提出记忆感知测试时缩放（MaTTS）——通过分配更多计算到每个任务，Agent 生成丰富多样的经验，为合成更高质量记忆提供丰富对比信号。更好的记忆反过来引导更有效的缩放，建立记忆与测试时缩放之间的强大协同。

**结果**：在 Web 浏览和软件工程基准上，ReasoningBank 一致超越存储原始轨迹或仅存储成功任务经验的现有记忆机制，同时提升效果和效率；MaTTS 进一步放大这些增益。建立记忆驱动的经验缩放作为新缩放维度，使 Agent 自发涌现新行为。

---

### 38. MemoryAgentBench: Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions

- **作者**：Yuanzhe Hu, Yu Wang, Julian McAuley
- **关键词**：LLM Agents, Agents with Memory, Memory Agents Benchmark, Evaluation for Memory
- **链接**：https://openreview.net/forum?id=DT7JyQC3MR

**问题与动机**：现有 LLM Agent 基准主要评估推理、规划和执行能力，而记忆——Agent 如何记忆、更新和检索长期信息——因缺乏基准而评估不足。现有基准依赖有限上下文长度或针对静态长上下文设置（如书籍 QA），不反映记忆 Agent 增量积累信息的交互、多轮本质，且没有基准覆盖全部四项核心能力。

**方法**：基于记忆科学和认知科学经典理论，识别记忆 Agent 四项核心能力：准确检索、测试时学习、长程理解、选择性遗忘。构建 MemoryAgentBench，将现有长上下文数据集转化为多轮格式，新增构造数据集，模拟记忆 Agent 的增量信息处理特征。

**结果**：评估多种记忆 Agent（从简单上下文/RAG 系统到带外部记忆模块和工具集成的高级 Agent），揭示当前方法无法同时掌握四项核心能力，凸显了对全面记忆机制进一步研究的需求。

---

## 二、总体分析

### 1. 研究主题集中度：记忆是 ICLR 2026 的核心议题

38 篇论文中，**超过 30 篇直接涉及"记忆"**，覆盖了从理论到应用、从模型架构到 Agent 系统的完整谱系。这反映了一个明确的趋势：大模型研究正从"如何让模型更大"转向"如何让模型记住"——记忆是实现持续学习、个性化、长期交互和自主进化的关键瓶颈。

### 2. 六大技术方向

| 方向 | 代表论文 | 核心问题 |
|------|----------|----------|
| **Agent 记忆** | REMem, ReMemR1, MemGAS, EMPO², MemGen, ReasoningBank | Agent 如何存储、检索和利用交互经验 |
| **图结构记忆** | BrowseNet, GraphPlanner, CoMem, AssoMem | 用图结构组织记忆实体与关系 |
| **记忆检索** | RF-Mem, LightMem, AssoMem | 如何从大规模记忆中高效准确地检索 |
| **持续学习** | Paper 7, Paper 15, CoMem, TokMem | 如何在学到新知识时不遗忘旧知识 |
| **长上下文效率** | PM-KVQ, Memento, Sculptor, TNT, LaCT | 如何在超长上下文下保持效率 |
| **记忆评估** | MemoryAgentBench, ManBench | 如何系统评估记忆系统的能力 |

### 3. 关键技术趋势

**趋势一：从"存储"到"推理+记忆"融合**

早期记忆系统（如 RAG）主要关注存储和检索，而 2026 年的论文普遍强调记忆与推理的深度耦合。REMem 的情景推理、ReMemR1 的回调增强推理、ReasoningBank 的推理策略提炼、MemGen 的推理-记忆交织——都表明记忆不再是被动仓库，而是推理过程的有机组成。特别是 MemGen 展示了推理和记忆的紧密交织循环，ReasoningBank 证明了从推理经验中提炼可泛化策略的价值。

**趋势二：图结构成为记忆组织的主导范式**

BrowseNet 的块图、GraphPlanner 的异构图、CoMem 的概念图、AssoMem 的联想图——图结构因其天然的关系建模能力，已成为组织记忆的事实标准。相比扁平的向量存储，图结构能更好地捕捉实体间关系和多跳推理路径。GraphPlanner 甚至将图记忆与强化学习结合，在路由决策中显式利用历史交互模式。

**趋势三：认知科学启发的记忆分层**

多篇论文借鉴认知科学的记忆分类：情景记忆 vs 语义记忆（REMem、M3-Agent）、Recollection vs Familiarity 双过程（RF-Mem）、Atkinson-Shiffrin 三阶段模型（LightMem）、规划/过程/工作记忆（MemGen）。这种跨学科借鉴不再是表面比喻，而是深入到算法设计层面。RF-Mem 基于不确定性信号在两条检索路径间动态切换，LightMem 的三阶段架构直接映射人类记忆的信息流模型，MemGen 的三类记忆功能则是自发涌现而非显式设计。

**趋势四：Test-Time 记忆与适应**

TNT 和 LaCT 代表了一个重要方向——将记忆机制嵌入模型的前向传播过程中（测试时训练/记忆化）。LaCT 用极大块更新（2K-1M tokens）替代 TTT 的迷你批次，GPU 利用率提升数个量级，非线性状态大小可扩展至模型参数的 40%。这不同于外部记忆库，而是在模型内部实现动态记忆更新，与线性注意力和 RNN 复兴的浪潮紧密相连。

**趋势五：从被动记忆到主动记忆管理**

Sculptor 的主动上下文管理、ACE 的上下文工程、Memento 的前瞻式感知——这些工作强调 Agent 应该主动决定"记住什么、遗忘什么"，而非被动地存储所有信息。Sculptor 让 LLM 使用工具主动雕塑内部工作记忆（分片、摘要/隐藏/恢复、精确搜索），ACE 将上下文视为可演化的"剧本"而非静态指令。这与人类认知的注意力和选择性编码高度一致。

**趋势六：记忆的可塑性与遗忘**

Paper 15 提出"记忆充足时代"的可塑性挑战——当存储不再是瓶颈，核心问题从"如何不遗忘"转向"如何保持学习新知识的能力"。REM 研究机器遗忘，提出概念空间统一刻画不同遗忘任务。MemoryAgentBench 将选择性遗忘列为核心能力。遗忘不再是需要避免的问题，而是需要精确控制的能力。这在隐私合规（GDPR）和知识更新场景中至关重要。

### 4. 评估与基准的成熟

MemoryAgentBench（四项核心能力评估）、ManBench（曼德拉效应）、M3-Bench（多模态长视频）、MementoBench（超长视频流）等基准的出现，标志着记忆研究正从"方法创新"走向"系统评估"阶段。特别是 MemoryAgentBench 揭示的"现有方法无法同时掌握准确检索、测试时学习、长程理解和选择性遗忘"这一发现，为未来研究指明了方向。ManBench 则首次系统研究了多 Agent 系统中的集体记忆偏差，开启了记忆可靠性的新研究线。

### 5. 应用场景的多元化

记忆系统已覆盖广泛场景：

| 应用场景 | 论文 | 关键洞察 |
|----------|------|----------|
| Minecraft 规划 | XENON | 从失败经验修正动作知识，7B 超越大模型 |
| 机器人控制 | MemER | 层次化策略+关键帧检索，分钟级记忆 |
| 代码定位 | Repository Memory | commit 历史构建仓库记忆 |
| Web 搜索 | FlowSearcher | 记忆引导的工作流合成 |
| 数据分析 | DataMind | 14B 超越 GPT-5 |
| 视频理解 | Memento | 7 小时超长视频的前瞻式助手 |
| 个性化对话 | RF-Mem, MemGAS | 双过程检索/多粒度关联 |
| 知识图谱推理 | KRLM | 统一 LLM 知识与 KG 上下文 |

### 6. 待解决的关键挑战

1. **记忆的可扩展性**：随着交互历史增长，记忆检索的准确性和效率如何保持？AssoMem 和 LightMem 分别从多信号融合和三阶段压缩角度做了尝试，但大规模（百万级交互）下的表现仍是开放问题。

2. **多粒度记忆的统一**：MemGAS 提出多粒度关联，但从 token 级（TokMem、LaCT）到会话级（MemGAS）到策略级（ACE、ReasoningBank）的统一框架尚不存在。

3. **记忆的可解释性**：Agent 基于什么检索到某条记忆？决策路径是否可审计？当前工作普遍缺乏对此的深入探讨。

4. **记忆安全与隐私**：REM 展示了通用遗忘方法，但如何在不损害模型性能的前提下精确遗忘特定信息仍是难题。

5. **记忆的跨任务迁移**：在一个任务中积累的记忆能否帮助新任务？EMPO² 的 OOD 适应和 GraphPlanner 的零样本泛化给出了初步答案，但通用迁移机制仍待建立。

6. **内隐与外显记忆的统一**：参数化记忆（TokMem、预训练记忆库 Paper 2）与外部记忆（RAG、图数据库如 BrowseNet/AssoMem）如何协同？MemGen 的潜在记忆是连接两者的初步尝试，但统一框架尚未形成。

---

## 三、总结

ICLR 2026 的这 38 篇论文共同描绘了一幅图景：**大模型的下一个核心挑战是记忆**。这不仅是技术问题——如何高效存储和检索——更是认知问题——如何像人类一样在时间中积累经验、修正知识、并在此基础上推理和行动。从情景记忆到过程记忆，从图结构到潜在空间，从被动检索到主动管理，这些工作正在构建一个日益完整的 AI 记忆体系。

三个特别值得关注的信号：(1) **MemGen 的自发涌现**——无需显式监督即涌现出规划/过程/工作记忆三类功能，暗示正确的记忆架构可能自然催生类人认知结构；(2) **LaCT 的极大块更新**——打破 TTT 的计算瓶颈，使内部记忆机制可扩展到 14B 模型和百万 token 序列；(3) **MemoryAgentBench 的四能力缺失发现**——现有方法无法同时掌握准确检索、测试时学习、长程理解和选择性遗忘，为未来研究划定了明确目标。

未来的突破可能来自三个交汇点：(1) 认知科学与深度学习的深度结合，(2) 内隐记忆（参数化）与外显记忆（外部存储）的统一框架，(3) 记忆驱动的自主进化——让 Agent 不仅"记住"，更能基于记忆"变得更好"。
