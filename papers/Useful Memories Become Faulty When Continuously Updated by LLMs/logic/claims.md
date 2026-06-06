# Useful Memories Become Faulty When Continuously Updated by LLMs

## C01

**Statement**  
持续更新的 consolidated textual memory 会发生质量退化，且可能低于 no-memory baseline。

**Status**  
Supported.

**Falsification criteria**  
如果持续 consolidation 的 utility 始终单调不降，或至少不会跌破 no-memory baseline，则该主张不成立。

**Proof**  
论文摘要明确指出 memory utility 会先升后降，并可能 fall below the no-memory baseline。

---

## C02

**Statement**  
性能回退的重要原因在于 consolidation step 本身，而不只是原始经验质量。

**Status**  
Supported.

**Falsification criteria**  
如果相同 trajectories 在不同 update schedules 下仍稳定导出一致记忆，则该主张被削弱。

**Proof**  
论文摘录指出：the same trajectories yield qualitatively different memories under different update schedules；作者据此将 regression 归因到 consolidation 过程。

---

## C03

**Statement**  
保留 raw episodic trajectories 的策略，比持续重写 consolidated memory bank 更稳健。

**Status**  
Supported.

**Falsification criteria**  
如果 episodic-only 控制组系统性劣于 consolidators，则该主张不成立。

**Proof**  
公开材料指出：preserving raw episodic trajectories maintains better accuracy；episodic-only control remains competitive。

---

## C04

**Statement**  
在受控 ARC-AGI Stream 环境中，agent 默认更倾向保留原始 episodes，而不是频繁执行 consolidate。

**Status**  
Supported.

**Falsification criteria**  
如果 agent 在 Retain / Delete / Consolidate 三类动作中主要偏好 Consolidate，则该主张不成立。

**Proof**  
论文公开摘要与摘录都表明：在该环境下，agents preserve raw episodes by default。

---

## C05

**Statement**  
离线设定中，按任务家族分组后再做抽象（Static-Group），优于把所有经验混合后统一抽象（Static-All）以及流式增量更新（Stream Update）。

**Status**  
Supported by accessible sources.

**Falsification criteria**  
如果 Static-Group 在主要对比中不优，或仅偶然占优，则该主张不成立。

**Proof**  
项目页将 Static-Group 标为三种方案中最佳，并解释其优势来自“同任务家族的干净 batch 更利于抽取潜在结构”。

---

## C06

**Statement**  
即使从 ground-truth solutions 进行 consolidation，强模型仍可能出现显著性能回退。

**Status**  
Supported.

**Falsification criteria**  
如果 ground-truth consolidation 几乎不引入失败，则该主张不成立。

**Proof**  
可检索摘录指出：即使从 ground-truth solutions 做 consolidation，GPT-5.4 仍会在一组其原本可无记忆解决的 ARC-AGI 题目上出现明显失败。
