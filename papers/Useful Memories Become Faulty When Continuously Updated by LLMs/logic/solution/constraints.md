# Useful Memories Become Faulty When Continuously Updated by LLMs

## Constraints

1. **文本抽象不是无损压缩。**  
   LLM 在把轨迹改写为 lesson 时会引入边界漂移与错误泛化。

2. **原始证据不能被完全替代。**  
   仅保留抽象 memory，可能会丢掉后续修正错误所需的细粒度上下文。

3. **更新组织方式显著影响最终 memory 质量。**  
   不同 schedule、不同 batch 组织方式、不同分组粒度都可能改变记忆结果。

4. **更强模型也不能天然避免该问题。**  
   公开摘录中涉及多个模型，说明 faulty memory 不是单一模型的偶然失误。

5. **记忆质量需要长期评测。**  
   若只看短期收益，会误把“早期提升”当成“稳定有效”。
