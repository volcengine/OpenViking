# OpenViking Prompt 说明与自定义指南

本文介绍 OpenViking 当前的 prompt 模板体系，重点说明：

- 当前有哪些 prompt
- 它们分别用于哪个处理环节
- 它们会影响哪些对外能力或结果
- 模板文件的格式要求是什么
- 如何安全地自定义 prompt

本文只覆盖 `openviking/prompts/templates/` 下的模板，以及少量与模板加载有关的配置项。

## 总览

OpenViking 当前的 prompt 主要分为两类：

1. 普通 prompt 模板
   - 存放在 `openviking/prompts/templates/<category>/*.yaml`
   - 用于给模型下发任务，例如做图片理解、文档总结、记忆提取、检索意图分析等
2. memory schema 模板
   - 存放在 `openviking/prompts/templates/memory/*.yaml`
   - 用于定义某类记忆的字段、文件名模板、内容模板和目录规则

从使用角度看，这些模板主要服务于以下处理环节：

| 类别 | 代表模板 | 主要作用 | 生效环节 | 影响的对外能力 |
|------|----------|----------|----------|----------------|
| `vision` | `vision.image_understanding` | 图片、页面、表格理解 | 资源解析、扫描件理解 | 图片解析、PDF 页面理解、表格抽取结果 |
| `parsing` | `parsing.context_generation` | 文档结构划分与节点语义生成 | 资源导入与解析 | 文档章节结构、节点摘要、图像摘要 |
| `semantic` | `semantic.document_summary` | 文件与目录级摘要 | 语义索引构建 | 文件摘要、目录概览、后续检索质量 |
| `retrieval` | `retrieval.intent_analysis` | 检索意图分析与查询规划 | 检索前分析 | 搜索 query 规划、上下文召回方向 |
| `compression` | `compression.memory_extraction` | 记忆提取、合并、压缩、摘要 | session commit / memory 管线 | 长期记忆抽取、session 压缩、记忆合并结果 |
| `memory` | `profile` | 记忆类型定义 | 记忆落盘与更新 | 不同记忆类型的组织方式和最终内容 |
| `processing` | `processing.tool_chain_analysis` | 从交互或资源背景中提炼经验 | 后处理与经验沉淀 | 策略提炼、工具链经验、交互学习结果 |
| `indexing` | `indexing.relevance_scoring` | 评估候选内容相关性 | 检索与索引辅助 | 相关性打分质量 |
| `skill` | `skill.overview_generation` | 提炼 Skill 信息 | Skill 资源处理 | Skill 检索摘要 |
| `test` | `test.skill_test_generation` | 自动生成测试样例 | 测试与验证辅助 | Skill 测试样例生成 |

## Prompt 格式要求

### 普通 Prompt YAML

普通 prompt 模板通常包含以下字段：

```yaml
metadata:
  id: "semantic.document_summary"
  name: "Document Summary"
  description: "Generate summary for documentation files"
  version: "1.0.0"
  language: "en"
  category: "semantic"

variables:
  - name: "file_name"
    type: "string"
    description: "Input file name"
    required: true

template: |
  ...

output_schema:
  ...

llm_config:
  ...
```

字段含义：

- `metadata`
  - 描述模板身份与分类
  - 其中 `id` 通常与文件路径对应，例如 `semantic.document_summary`
- `variables`
  - 定义模板可接受的输入变量
  - 常见字段包括 `name`、`type`、`description`、`default`、`required`、`max_length`
- `template`
  - 真正发送给模型的 prompt 正文
  - 使用 Jinja2 变量渲染
- `output_schema`
  - 可选
  - 用于描述期望输出结构，方便调用方约束模型返回
- `llm_config`
  - 可选
  - 用于描述模型调用建议参数，不直接属于 prompt 正文

编写普通 prompt 时，建议遵守以下要求：

- `metadata.id` 与模板的类别和用途保持一致
- 变量名保持稳定，避免与调用方约定不一致
- `template` 中的占位变量应与 `variables` 定义一致
- 如果模板要求结构化输出，应明确写清字段、格式和约束
- 如果存在长度敏感输入，应通过 `max_length` 或上游截断控制 prompt 大小

### Memory Schema YAML

`memory/*.yaml` 不是普通 prompt 文本模板，而是记忆类型定义。下面是一个示意结构，用来说明常见字段；实际内置模板是否包含 `content_template`、目录是否带子目录，取决于具体 memory type。

```yaml
memory_type: "profile"
description: "User profile memory"
fields:
  - name: "content"
    type: "string"
    description: "Profile content"
    merge_op: "patch"
filename_template: "profile.md"
content_template: |
  ...
directory: "viking://user/{{ user_space }}/memories/..."
enabled: true
operation_mode: "upsert"
```

字段含义：

- `memory_type`
  - 该记忆类型的名称
- `description`
  - 对该类记忆的定义和提取要求
- `fields`
  - 该类记忆包含哪些字段
- `filename_template`
  - 生成文件名时使用的模板
- `content_template`
  - 落盘时使用的正文模板
- `directory`
  - 该类记忆写入的目录
- `enabled`
  - 是否启用该类记忆
- `operation_mode`
  - 该类记忆的更新模式，例如 `upsert`

编写 memory schema 时，建议重点关注：

- 字段粒度是否稳定
- 文件名模板是否可预测、可检索
- 目录规则是否符合预期检索范围
- 合并策略是否适合该类记忆

## 当前 Prompt 模板说明

下面按类别列出当前全部模板。每个条目都说明它用于哪个处理环节，以及主要影响哪类对外能力。

阅读这一节时，可以用一个简单规则：

- 普通 prompt 模板，重点看“作用”和“关键输入”
- memory schema，重点看“作用”和“关键字段”

### Compression

这一类 prompt 主要用于 session 压缩、记忆提取、记忆合并和字段压缩，是长期记忆质量的核心部分。

- `compression.dedup_decision`
  - 生效环节：记忆候选去重与决策阶段
  - 影响能力：长期记忆去重、创建或合并策略
  - 作用：决定新候选记忆应跳过、创建，还是与已有记忆合并
  - 关键输入：`candidate_content`、`candidate_abstract`、`candidate_overview`、`existing_memories`

- `compression.field_compress`
  - 生效环节：记忆字段压缩阶段
  - 影响能力：工具记忆等长字段内容的可控长度与可读性
  - 作用：在保留关键信息的前提下压缩字段内容
  - 关键输入：`field_name`、`content`、`max_length`

- `compression.memory_extraction`
  - 生效环节：会话压缩后的记忆提取阶段
  - 影响能力：长期记忆抽取质量、后续 recall 命中率
  - 作用：从会话摘要和最近消息中提取值得长期保存的记忆候选
  - 关键输入：`summary`、`recent_messages`、`user`、`feedback`、`output_language`

- `compression.memory_merge`
  - 生效环节：单条记忆合并阶段
  - 影响能力：已有记忆更新后的内容质量
  - 作用：将已有记忆与新信息合并成更完整的版本
  - 关键输入：`existing_content`、`new_content`、`category`、`output_language`

- `compression.memory_merge_bundle`
  - 生效环节：结构化记忆合并阶段
  - 影响能力：L0/L1/L2 三层记忆合并结果
  - 作用：一次性输出 abstract、overview、content 三层合并结果
  - 关键输入：`existing_abstract`、`existing_overview`、`existing_content`、`new_abstract`、`new_overview`、`new_content`、`category`、`output_language`

- `compression.structured_summary`
  - 生效环节：session archive 摘要生成阶段
  - 影响能力：历史会话压缩摘要、后续回顾和检索效果
  - 作用：为归档后的 session 生成结构化摘要
  - 关键输入：`latest_archive_overview`、`messages`

### Indexing

这一类 prompt 主要用于为检索或索引辅助流程做相关性判断。

- `indexing.relevance_scoring`
  - 生效环节：候选内容相关性评估阶段
  - 影响能力：检索结果排序、候选筛选质量
  - 作用：评估候选内容与用户查询之间的相关性
  - 关键输入：`query`、`candidate`

### Memory

这一类 YAML 定义不同记忆类型的结构，不是单次推理 prompt。它们共同决定用户记忆和 agent 记忆如何落盘、如何更新、如何被后续检索使用。

- `cases`
  - 生效环节：案例型记忆落盘与更新阶段
  - 影响能力：问题到解决方案的案例沉淀与复用
  - 作用：定义“遇到了什么问题、如何解决”的案例型记忆
  - 关键字段：`case_name`、`problem`、`solution`、`content`

- `entities`
  - 生效环节：实体型记忆落盘与更新阶段
  - 影响能力：人物、项目、组织、系统等实体信息的长期保存
  - 作用：定义命名实体及其属性信息的存储结构
  - 关键字段：`category`、`name`、`content`

- `events`
  - 生效环节：事件型记忆落盘与更新阶段
  - 影响能力：事件回顾、带时间线的信息保留、对话叙事记录
  - 作用：定义事件摘要、目标、时间范围等结构化事件记忆
  - 关键字段：`event_name`、`goal`、`summary`、`ranges`

- `identity`
  - 生效环节：agent identity 记忆落盘阶段
  - 影响能力：agent 身份设定的长期一致性
  - 作用：定义 agent 的名字、形象、风格、自我介绍等身份字段
  - 关键字段：`name`、`creature`、`vibe`、`emoji`、`avatar`

- `patterns`
  - 生效环节：模式型记忆落盘与更新阶段
  - 影响能力：可复用流程和方法的长期积累
  - 作用：定义“在什么情况下按什么流程处理”的模式记忆
  - 关键字段：`pattern_name`、`pattern_type`、`content`

- `preferences`
  - 生效环节：偏好型记忆落盘与更新阶段
  - 影响能力：用户偏好 recall 和后续个性化表现
  - 作用：定义不同主题下的用户偏好记忆
  - 关键字段：`user`、`topic`、`content`

- `profile`
  - 生效环节：用户 profile 记忆落盘与更新阶段
  - 影响能力：用户画像、工作背景、稳定属性的长期保存
  - 作用：定义“用户是谁”这一类稳定信息的存储结构
  - 关键字段：`content`

- `skills`
  - 生效环节：skill 使用记忆落盘与更新阶段
  - 影响能力：skill 使用统计、经验沉淀与推荐流程
  - 作用：定义 skill 使用次数、成功率、适用场景等信息
  - 关键字段：`skill_name`、`total_executions`、`success_count`、`fail_count`、`best_for`、`recommended_flow`

- `soul`
  - 生效环节：agent soul 记忆落盘阶段
  - 影响能力：agent 核心边界、连续性和长期人格稳定性
  - 作用：定义 agent 的核心真值、边界、风格和连续性
  - 关键字段：`core_truths`、`boundaries`、`vibe`、`continuity`

- `tools`
  - 生效环节：工具使用记忆落盘与更新阶段
  - 影响能力：工具使用经验、最佳参数、失败模式沉淀
  - 作用：定义工具调用统计和工具使用经验的存储结构
  - 关键字段：`tool_name`、`static_desc`、`call_count`、`success_time`、`when_to_use`、`optimal_params`

### Parsing

这一类 prompt 主要用于把原始资源内容转成适合检索和理解的结构化节点、章节、摘要或图像概述。

- `parsing.chapter_analysis`
  - 生效环节：长文档章节划分阶段
  - 影响能力：文档章节结构、页面组织效果
  - 作用：分析文档内容并划分合理的章节结构
  - 关键输入：`start_page`、`end_page`、`total_pages`、`content`

- `parsing.context_generation`
  - 生效环节：文档节点语义生成阶段
  - 影响能力：节点 abstract/overview 质量、后续检索匹配效果
  - 作用：为文本节点生成更短、更适合检索的语义标题、abstract 和 overview
  - 关键输入：`title`、`content`、`children_info`、`instruction`、`context_type`、`is_leaf`

- `parsing.image_summary`
  - 生效环节：图像节点摘要阶段
  - 影响能力：图片资源的语义概述和后续检索效果
  - 作用：为图像内容生成简洁摘要
  - 关键输入：`context`

- `parsing.semantic_grouping`
  - 生效环节：语义分组与切分阶段
  - 影响能力：文档节点粒度、内容块切分质量
  - 作用：根据语义决定内容应该合并还是拆分
  - 关键输入：`items`、`threshold`、`mode`

### Processing

这一类 prompt 主要用于从交互记录、工具链和资源背景中提炼策略或经验，不直接面向单次用户问答，而是面向后处理和知识沉淀。

- `processing.interaction_learning`
  - 生效环节：交互后经验提炼阶段
  - 影响能力：可复用交互经验、有效资源和成功 skill 的沉淀
  - 作用：从交互记录中抽取可复用经验
  - 关键输入：`interactions_summary`、`effective_resources`、`successful_skills`

- `processing.strategy_extraction`
  - 生效环节：资源添加后策略提炼阶段
  - 影响能力：资源背景意图的结构化提炼和后续复用
  - 作用：从资源添加原因、指令和抽象信息中提炼使用策略
  - 关键输入：`reason`、`instruction`、`abstract`

- `processing.tool_chain_analysis`
  - 生效环节：工具链分析阶段
  - 影响能力：工具组合模式识别、工具经验沉淀
  - 作用：分析工具调用链并识别有价值的使用模式
  - 关键输入：`tool_calls`

### Retrieval

这一类 prompt 主要用于检索前理解用户意图，决定 query plan 和上下文类型。

- `retrieval.intent_analysis`
  - 生效环节：检索前意图分析阶段
  - 影响能力：检索 query 规划、召回方向、不同 context 类型的搜索质量
  - 作用：结合压缩摘要、最近消息和当前消息生成检索计划
  - 关键输入：`compression_summary`、`recent_messages`、`current_message`、`context_type`、`target_abstract`

### Semantic

这一类 prompt 主要用于文件级和目录级摘要生成，是语义索引构建的重要部分。

- `semantic.code_ast_summary`
  - 生效环节：大型代码文件 AST 骨架总结阶段
  - 影响能力：代码文件摘要、代码检索和结构理解效果
  - 作用：基于 AST 骨架而不是完整源码生成代码摘要
  - 关键输入：`file_name`、`skeleton`、`output_language`

- `semantic.code_summary`
  - 生效环节：代码文件摘要阶段
  - 影响能力：代码文件语义索引、代码检索与理解结果
  - 作用：为代码文件生成结构、函数、类和关键逻辑摘要
  - 关键输入：`file_name`、`content`、`output_language`

- `semantic.document_summary`
  - 生效环节：文档文件摘要阶段
  - 影响能力：文档内容摘要、文档检索与概览效果
  - 作用：为 Markdown、Text、RST 等文档生成内容摘要
  - 关键输入：`file_name`、`content`、`output_language`

- `semantic.file_summary`
  - 生效环节：通用文件摘要阶段
  - 影响能力：目录索引与通用文件检索质量
  - 作用：为单个文件生成摘要，作为目录 abstract/overview 的上游输入
  - 关键输入：`file_name`、`content`、`output_language`

- `semantic.overview_generation`
  - 生效环节：目录级概览生成阶段
  - 影响能力：目录 overview、层级检索与导航体验
  - 作用：根据文件摘要和子目录 abstract 生成目录级 overview
  - 关键输入：`dir_name`、`file_summaries`、`children_abstracts`、`output_language`

### Skill

这一类 prompt 主要用于把 Skill 内容压缩成适合检索和复用的摘要。

- `skill.overview_generation`
  - 生效环节：Skill 内容处理阶段
  - 影响能力：Skill 检索摘要、Skill 发现效果
  - 作用：从 Skill 名称、描述和正文中抽取关键检索信息
  - 关键输入：`skill_name`、`skill_description`、`skill_content`

### Test

这一类 prompt 主要用于辅助生成测试样例。

- `test.skill_test_generation`
  - 生效环节：Skill 测试辅助阶段
  - 影响能力：Skill 场景测试设计与验证样例生成
  - 作用：根据多个 Skill 的名称和描述生成测试用例
  - 关键输入：`skills_info`

### Vision

这一类 prompt 主要用于图片、页面、表格和多模态文档分析，直接影响图片解析和扫描件理解结果。

- `vision.batch_filtering`
  - 生效环节：多图批量筛选阶段
  - 影响能力：多图文档理解中的图片保留与忽略策略
  - 作用：批量判断多张图片是否值得纳入文档理解
  - 关键输入：`document_title`、`image_count`、`images_info`

- `vision.image_filtering`
  - 生效环节：单图筛选阶段
  - 影响能力：图片是否进入后续理解流程
  - 作用：判断单张图片是否对文档理解有意义
  - 关键输入：`document_title`、`context`

- `vision.image_understanding`
  - 生效环节：图片理解阶段
  - 影响能力：图片解析结果、图片 abstract/overview/detail_text 质量
  - 作用：使用 VLM 对图片生成三层信息
  - 关键输入：`instruction`、`context`

- `vision.page_understanding`
  - 生效环节：扫描页理解阶段
  - 影响能力：扫描 PDF 页面理解与后续语义化结果
  - 作用：理解单页图片化文档内容
  - 关键输入：`instruction`、`page_num`

- `vision.page_understanding_batch`
  - 生效环节：多页批量理解阶段
  - 影响能力：批量扫描页理解效率与结果一致性
  - 作用：批量理解多页图片化文档内容
  - 关键输入：`page_count`、`instruction`

- `vision.table_understanding`
  - 生效环节：表格理解阶段
  - 影响能力：图片表格解析、表格摘要和结构理解
  - 作用：分析表格图片并生成三层信息
  - 关键输入：`instruction`、`context`

- `vision.unified_analysis`
  - 生效环节：多模态统一分析阶段
  - 影响能力：包含图片、表格和章节的复杂文档解析结果
  - 作用：批量分析文档中的图片、表格和章节信息
  - 关键输入：`title`、`instruction`、`reason`、`content_preview`、`image_count`、`images_section`、`table_count`、`tables_section`

## 如何自定义 Prompt

OpenViking 支持两种主要的自定义方式：

1. 覆盖普通 prompt 模板
2. 扩展 memory schema

在进入具体方法之前，可以先用下面的边界判断改动风险：

| 改动类型 | 风险级别 | 说明 |
|----------|----------|------|
| 改 prompt 措辞、补示例、调整语气 | 低 | 通常只影响模型表达方式，不改变调用方协议 |
| 改输出风格、改抽取偏好、改摘要粒度 | 中 | 会影响结果分布，需要重新验证目标能力 |
| 改变量名、改输出结构、改 memory 字段名 | 高 | 容易和调用方或解析逻辑不兼容 |
| 改 `directory`、`filename_template`、`merge_op` | 很高 | 会直接影响记忆存储位置、组织方式和更新行为 |

### 覆盖普通 Prompt 模板

适用场景：

- 想调整记忆提取偏好
- 想改变摘要风格
- 想让图片理解输出更细或更简
- 想调整检索意图分析的规划方式

可用配置：

- `prompts.templates_dir`
- 环境变量 `OPENVIKING_PROMPT_TEMPLATES_DIR`

加载优先级：

1. 显式传入的模板目录
2. 环境变量 `OPENVIKING_PROMPT_TEMPLATES_DIR`
3. `ov.conf` 中的 `prompts.templates_dir`
4. 内置模板目录 `openviking/prompts/templates/`

也就是说，普通 prompt 的自定义方式本质上是“优先从自定义目录查找，同路径未命中时再回退到内置模板”。

推荐做法：

1. 先复制内置模板目录中的目标文件
2. 保持相同的类别目录和文件名
3. 仅修改 prompt 正文或输出要求
4. 尽量不要修改已被调用方依赖的变量名

示例目录：

```text
custom-prompts/
├── compression/
│   └── memory_extraction.yaml
├── retrieval/
│   └── intent_analysis.yaml
└── semantic/
    └── document_summary.yaml
```

示例配置：

```json
{
  "prompts": {
    "templates_dir": "/path/to/custom-prompts"
  }
}
```

或者：

```bash
export OPENVIKING_PROMPT_TEMPLATES_DIR=/path/to/custom-prompts
```

影响面示例：

- 修改 `compression.memory_extraction`
  - 主要影响记忆抽取阶段
  - 最终影响长期记忆质量和后续 recall 结果
- 修改 `retrieval.intent_analysis`
  - 主要影响检索前 query plan
  - 最终影响搜索方向和召回效果
- 修改 `semantic.document_summary`
  - 主要影响文档摘要阶段
  - 最终影响文档索引和摘要结果

### 扩展 Memory Schema

适用场景：

- 想新增一类业务记忆
- 想调整某类记忆的字段结构
- 想改变记忆落盘目录或文件模板

可用配置：

- `memory.custom_templates_dir`

加载行为：

- 内置 memory schema 会先加载
- 如果配置了 `memory.custom_templates_dir`，再继续加载自定义目录中的 schema
- 因此，memory 自定义更接近“扩展和补充”，而不是完全替换整套内置模板

示例目录：

```text
custom-memory/
├── project_decisions.yaml
└── user_preferences_ext.yaml
```

示例配置：

```json
{
  "memory": {
    "custom_templates_dir": "/path/to/custom-memory"
  }
}
```

扩展 memory schema 时建议：

- 优先参考现有 `memory/*.yaml` 写法
- 先确定该类记忆是否真的需要独立类型
- 保持字段名清晰、可稳定更新
- 确保 `directory` 和 `filename_template` 易于检索和维护

影响面示例：

- 新增 `project_decisions`
  - 影响记忆落盘类型和后续搜索组织方式
- 修改 `preferences`
  - 影响用户偏好类记忆的组织方式和 recall 颗粒度
- 修改 `tools`
  - 影响工具经验沉淀和工具使用建议结果

### 自定义时的高风险改动

以下改动最容易破坏现有链路：

- 修改普通 prompt 的变量名
- 修改 prompt 的预期输出结构，但未同步调整调用方解析逻辑
- 修改 memory schema 的关键字段名
- 修改 `directory` 导致检索范围变化
- 修改 `filename_template` 导致历史文件组织方式变化
- 修改 `merge_op` 导致已有记忆更新策略变化

如果只是想优化效果，通常优先考虑这些低风险改法：

- 给 prompt 增加更明确的输出示例
- 强化“该保留什么、该忽略什么”的规则
- 调整摘要粒度或表达风格
- 只改某一类 prompt，而不是同时改多类

保守做法是：

1. 先复制现有模板
2. 尽量只改指令内容和表达方式
3. 结构字段最后再改
4. 每次只改一类 prompt，方便定位影响范围

## 验证与排查

修改 prompt 之后，建议按“模板是否命中”和“能力是否变化”两层来验证。

### 先验证模板是否命中

检查项：

- 自定义目录是否配置正确
- 文件路径是否与原模板保持相同相对路径
- YAML 格式是否有效
- 变量名是否和原模板一致

如果是普通 prompt，重点确认：

- 模板是否被正确加载
- 目标环节是否真的使用了该模板

如果是 memory schema，重点确认：

- 新 schema 是否被成功加载
- 目标记忆类型是否真的参与了提取和落盘

### 再验证对外结果是否变化

从使用者角度验证最有效：

- 如果改的是 `vision` 类模板，就重新解析图片、表格或扫描 PDF，看结果是否变化
- 如果改的是 `semantic` 或 `parsing` 类模板，就重新导入文档或文件，看摘要和结构是否变化
- 如果改的是 `retrieval` 类模板，就重新执行相关搜索，看 query 规划和召回效果是否变化
- 如果改的是 `compression` 类模板，就重新触发 session commit 或 memory 处理流程，看记忆抽取和合并结果是否变化
- 如果改的是 `memory` 类 schema，就检查最终落盘的记忆文件内容、目录和字段结构是否符合预期

### 常见排查思路

现象与优先检查项：

| 现象 | 优先检查 |
|------|----------|
| 修改后结果完全没变 | 自定义目录未生效，或文件路径不匹配 |
| 模型报缺少变量 | 模板变量名与调用方不一致 |
| 返回内容格式错乱 | prompt 输出格式改了，但下游解析还按旧结构处理 |
| 新 memory 类型没有出现 | `memory.custom_templates_dir` 未生效，或 schema 未被正确加载 |
| 检索结果变差 | `retrieval`、`semantic` 或 `compression` 类 prompt 改得过于激进 |

## 附录

### 模板目录

内置 prompt 模板目录：

```text
openviking/prompts/templates/
```

其中：

- `compression/`：压缩、提取、合并
- `indexing/`：相关性评估
- `memory/`：记忆类型定义
- `parsing/`：结构分析与语义节点生成
- `processing/`：经验与策略提炼
- `retrieval/`：检索意图分析
- `semantic/`：文件和目录摘要
- `skill/`：Skill 摘要
- `test/`：测试样例生成
- `vision/`：图片、页面、表格理解

### 关键配置项

与 prompt 自定义相关的配置主要有：

| 配置项 | 用途 |
|--------|------|
| `prompts.templates_dir` | 指定普通 prompt 模板覆盖目录 |
| `OPENVIKING_PROMPT_TEMPLATES_DIR` | 通过环境变量指定普通 prompt 模板覆盖目录 |
| `memory.custom_templates_dir` | 指定 custom memory schema 目录 |

### 选型建议

如果你的目标是：

- 改模型“怎么说、怎么提取、怎么总结”
  - 优先改普通 prompt 模板
- 改“记忆长什么样、存在哪里、怎么组织”
  - 优先改 memory schema

如果不确定该改哪一层，先问自己一句：

“我要改的是模型的指令，还是最终记忆文件的结构？”

这个问题通常足以帮助你区分应该改普通 prompt 还是 memory schema。
