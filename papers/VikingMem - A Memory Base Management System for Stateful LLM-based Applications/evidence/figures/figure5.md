# Figure 5: Agent Memory 用例

- **Source**: Figure 5, §4.2
- **Caption**: "Use case for Agent Memory"
- **Screenshot**: figure5.png
- **Figure type**: qualitative_sample
- **Extraction method**: visual_description
- **Reading confidence**: high

## Visual description
- **Shows**:
  - 左侧 Event Instance：`event_id: 25111513141`，`event_content` 包含 tool 为 "Drawing Helper"、description 为用于绘制 characters/landscapes 等、`is_success: True`、use_situation、token_usage、time_cost_sec、feedback、thoughts；`event_time: "2025-11-15 13:14:35"`。
  - 右侧 Entity Instance：`entity_id: 2509102214`，`entity_content` 包含 tool_name、description、tool_call_times、success_rate、avg_token_usage、avg_time_cost_sec、suitable_for、failure_cases、suggestions；`last_updated: "2025-11-15 13:14:35"`。
  - 中间箭头标注 `Evolve`，表示 event instance 演化更新 entity instance。
- **Demonstrates**: Agent 工具调用日志可被抽取为事件，并持续物化为工具画像/经验实体，用于后续 agent 选择工具和避免失败。
- **Supports**: C01, C02
