# Figure 2: VikingMem pipeline

- **Source**: Figure 2, §3
- **Caption**: "The pipeline of VikingMem"
- **Screenshot**: figure2.png
- **Figure type**: diagram
- **Extraction method**: visual_description
- **Reading confidence**: high

## Visual description
- **Components**:
  - Input Data Stream：包含 user profile（if need）、historical messages、session（N messages）。
  - Extract Module：包含 LLM 图标、system instruction、memory schema、fixed prompt cache、input messages。
  - Storage and Management：VikingDB-backed event/entity stores，包含 entity memory、event memories、old events、keyword graph、deduplication、memory compression based on timeline/TTL。
  - Retrieve Module：包含 multi-path retrieved memory、multi-vector-based rerank。
  - Reply side：query、short-term memory、updated profile（if need）、reply with memory、long-term memory extraction。
- **Connections**:
  - Session messages 进入 Extract Module，产生 event/entity 输出并写入 Storage and Management。
  - Storage and Management 对 event/entity 做 update、deduplication、compression，并维护 keyword graph。
  - Query 触发 Retrieve Module，通过 hybrid vector search 与 keyword graph multi-path recall 获取 long-term memory，再经 rerank 返回 reply-with-memory。
  - Short-term memory 与 long-term memory 同时进入回复流程。
- **Annotations**: 图中标注 dense+sparse、time/custom weights、keyword graph、TTL、MultiVector-based rerank 等关键设计。
- **What it conveys**: VikingMem 是从抽取、管理到检索重排的闭环 MBMS，而非单独的向量检索器。
