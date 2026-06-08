# Figure 3: VikingMem 的抽取范式

- **Source**: Figure 3, §3.1
- **Caption**: "The extract paradigm in VikingMem"
- **Screenshot**: figure3.png
- **Figure type**: diagram
- **Extraction method**: visual_description
- **Reading confidence**: high

## Visual description
- **Components**:
  - 上半部分 Previous Method：用户为 Memory Type 1、2、...、N 分别定义 prompt；同一 Input Data 多次进入 LLM，分别输出不同类型 memories。
  - 下半部分 Our Method：用户定义 Event Schemas 与 Entity Schemas；schema 生成 Event Prompt 与 Entity Prompt；Input Data 一次进入 LLM 输出 Entity-related Events 与 Other Events；Entity-related Events 与 Old Entities 通过 Operators 生成 New Entities。
- **Connections**:
  - Previous Method 中多条路径分别从 input data 到 LLM 再到 memory type 输出，表示多次处理同一输入。
  - Our Method 中 input data 只进入一次 LLM，event/entity schema 和 operators 共同完成抽取与实体更新。
- **Annotations**: 图中用虚线区分 User Defined Prompt 与 User Defined Schema；下方方法将 prompt-per-type 改为 schema-per-system。
- **What it conveys**: VikingMem 通过 schema-driven one-pass extraction 降低重复 LLM 调用和 prompt engineering 成本。
