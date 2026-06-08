# Figure 4: Faster Entity Update w/o LLM

- **Source**: Figure 4, §3.1
- **Caption**: "Faster Entity Update w/o LLM"
- **Screenshot**: figure4.png
- **Figure type**: diagram
- **Extraction method**: visual_description
- **Reading confidence**: high

## Visual description
- **Components**:
  - 左侧传统 entity update：Event Prompt 进入 LLM，产生 Entity-related Events；Entity Prompt 与 Old Entities 进入 Operators 生成新实体，可能涉及额外 LLM 合成。
  - 右侧 patch-based update：Event Prompt 与 Entity Prompt 辅助 LLM 输出 Entity-related Patch；Operator(Patch) 与 Old Entities 结合更新实体。
- **Connections**: 图用箭头表示从传统 event-based entity update 转向 patch-based entity update。
- **Annotations**: 图强调 “w/o LLM”：实体维护由 patch operator 作为确定性后处理执行，避免额外 LLM 调用。
- **What it conveys**: EUA 的关键收益是把字符串实体更新从二次 LLM 合成转换为 SEARCH/REPLACE patch application。
