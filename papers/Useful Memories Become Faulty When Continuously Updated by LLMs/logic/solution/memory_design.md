# Useful Memories Become Faulty When Continuously Updated by LLMs

## Design principle 1: Treat raw episodes as first-class evidence

不要把 episodic traces 仅看作临时缓存；在当前设定下，它们是防止错误抽象持续放大的关键锚点。

## Design principle 2: Gate consolidation instead of firing it by default

consolidation 应是有条件触发的动作，而不是每次交互后的默认流程。

## Design principle 3: Separate heterogeneous task families before abstraction

如果不同任务家族的经验混在一起统一抽象，更容易形成过度泛化。按组处理再抽象更稳健。

## Design principle 4: Keep episodic and abstract stores both retrievable

更稳健的 memory stack 不是二选一，而是让抽象经验与原始证据并存，在检索阶段共同发挥作用。

## Design principle 5: Evaluate memory systems by long-horizon stability

memory 方法应当重点评测长期稳定性，而不是只比较初期 few-shot 增益。
