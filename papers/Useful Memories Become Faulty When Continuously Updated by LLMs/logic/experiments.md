# Useful Memories Become Faulty When Continuously Updated by LLMs

## E01

**Verifies**  
C01

**Setup**  
在固定任务流中持续更新文本 memory bank，并在不同阶段评估 memory utility。

**Procedure**  
比较无记忆、早期 consolidation 和记忆长期更新后的效果差异。

**Expected outcome**  
若论文结论成立，性能不会单调上升，而会出现先升后降的退化轨迹。

---

## E02

**Verifies**  
C02

**Setup**  
固定原始 trajectories，仅改变 consolidation 的 update schedule 或记忆组织方式。

**Procedure**  
比较不同 schedule 导出的 memories 与对应下游表现。

**Expected outcome**  
如果 regression 来自 consolidation，本实验应观察到相同经验在不同 schedule 下得到 qualitatively different memories。

---

## E03

**Verifies**  
C03, C04

**Setup**  
在 ARC-AGI Stream 中允许 Retain / Delete / Consolidate 三类动作，并对比自动记忆管理、强制 consolidation、禁用 consolidation 的 episodic-only 控制。

**Procedure**  
让 agent 自主选择记忆动作，并评估长期表现与行为偏好。

**Expected outcome**  
如果论文成立，agent 会更偏向保留 raw episodes；episodic-only 控制组应表现出竞争力。

---

## E04

**Verifies**  
C05

**Setup**  
比较 Static-Group、Static-All、Stream Update 三种 consolidation 组织方式。

**Procedure**  
在相同经验池上分别构建 memory，并比较其下游效用。

**Expected outcome**  
若分组抽象更稳健，则 Static-Group 应优于混池统一抽象与流式增量更新。

---

## E05

**Verifies**  
C06

**Setup**  
从 ground-truth solutions 而非 noisy trajectories 出发构造 consolidated memory。

**Procedure**  
比较无记忆求解与依赖该 consolidated memory 的求解结果。

**Expected outcome**  
如果问题在 consolidation 而非原始数据噪声，则即便输入更干净的 solution 级 evidence，也仍可能出现显著回退。
