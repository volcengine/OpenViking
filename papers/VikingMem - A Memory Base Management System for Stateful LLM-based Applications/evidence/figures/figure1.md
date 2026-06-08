# Figure 1: Event/Entity schema 与 VikingMem 内置算子

- **Source**: Figure 1, §2.2.1
- **Caption**: "The definition schema of event, entity for memory extraction, and the built-in operators in VikingMem"
- **Screenshot**: figure1.png
- **Figure type**: diagram
- **Extraction method**: visual_description
- **Reading confidence**: medium

## Visual description
- **Components**:
  - 左侧 Event Schema：包含 `EventType`、`Description`、`Properties`；每个 property 包含 `PropertyName`、`PropertyType`、`Description`。
  - 右侧 Entity Schema：包含 `EntityType`、`Description`、`Properties`；每个 property 包含 `PropertyName`、`PropertyType`、`Description`、`AggregateExpression`、`IsPrimaryKey`。
  - `AggregateExpression` 进一步包含 `EventType`、`PropertyName`、`Op` 等字段，用于把 event 属性连接到 entity 更新。
  - 下方列出 built-in operators，包括统计类（SUM、MAX、AVG、COUNT 等）与 LLM-based/压缩类（LLM_MERGE、TIME_COMPRESS 等）。
- **Connections**: Event schema 定义抽取出的 episodic records；Entity schema 通过 AggregateExpression 引用 event type/property，并用 operator 更新实体属性。
- **Annotations**: 图强调 schema 是 memory extraction 与 entity evolution 的核心接口。
- **What it conveys**: VikingMem 的可泛化性来自 schema 约束的 event/entity 抽象和可复用 operator library。
