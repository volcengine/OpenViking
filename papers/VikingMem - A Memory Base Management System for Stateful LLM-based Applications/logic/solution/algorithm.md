# 算法与形式化

## 实体物化代数
- **Source**: §2.2.2。
- **Grounding**: 论文中明确打印的公式。

```text
entity := SELECT OP(event.content) FROM Events
WHERE filters(event) GROUP BY keys(event).
```

- `keys(event)`：将 events 分组成 entity instances，例如 per user、per user-assistant pair 或 per topic。
- `filters(event)`：约束 event eligibility，例如 time window。
- `OP`：选定算子，例如 `LLM_MERGE`、`TIME_COMPRESS`、`AVG` 或 `SUM`。

## Algorithm 1: EUA Patch-based Entity Update w/o LLM
- **Source**: §3.1 / Figure 4 附近的 Algorithm 1。
- **Grounding**: 论文中明确打印的伪代码。

```text
Input: Entity schema S; old entity E_old; field-wise patches {p_f}
Output: Updated entity E_new
1 E_new ← E_old
2 foreach field f in S do
3     (s, r) ← ParsePatch(p_f)       // s = SEARCH, r = REPLACE
4     if s = ∅ then
5         continue
6     (i, j) ← BestApproxSpan(E_old[f], s)  // min edit distance
7     E_new[f] ← E_old[f][0:i] || r || E_old[f][j:]
8 return E_new
```

论文说明 patch 形式为 `«« SEARCH ... ==== ... »» REPLACE`，approximate span search 使 patching 对轻微 LLM 字符串误差更鲁棒。

## 最终召回打分
- **Source**: §3.3。
- **Grounding**: 论文中明确打印的公式。

```text
S_final = (1 - w_time - w_busi) · S_origin + w_time · S_time + w_busi · S_busi
```

约束和定义：

- `S_origin`、`S_time`、`S_busi` 均 normalized to `[0, 1]`。
- `w_time, w_busi ∈ [0, 1]`。
- `w_time + w_busi ≤ 1`。
- `S_time` 在 user-configurable freshness tolerance window 内为 1；更旧 memories 按 fast-then-slow exponential curve 衰减。
- `S_busi` 可以是 type-level 或 instance-level。

## Token-level F1
- **Source**: §5.7, Eq. (1)。
- **Grounding**: 论文中明确打印的公式。

```text
F1 = 2 · P · R / (P + R)
```

其中 `P` 是 token-level precision，`R` 是 token-level recall。

## 复杂度分析
- **Entity update**: 论文未给出 `BestApproxSpan` 或 patch application 的渐进复杂度。
- **Retrieval/reranking**: 论文报告了观测 p50/p95 latency，但未给出渐进复杂度。
- **Extraction**: 论文指出 one-pass extraction 相比单独抽取 `k` 个 memory types 能减少重复 LLM 调用，但未给出除 token-cost comparison 外的形式化运行时表达式。
