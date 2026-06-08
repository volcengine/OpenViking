# 公式与伪代码证据

## 实体物化代数
- **Source**: §2.2.2
- **Statement**: 论文将 event 与 entity 的关系表达为参数化查询。

```text
entity := SELECT OP(event.content) FROM Events
WHERE filters(event) GROUP BY keys(event).
```

## Algorithm 1: EUA
- **Source**: Algorithm 1, §3.1
- **Statement**: Patch-based Entity Update without an additional LLM。

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

## 召回最终打分
- **Source**: §3.3
- **Statement**: 候选 memory 的 final score 由原始检索分、时间分和业务分加权组成。

```text
S_final = (1 - w_time - w_busi) · S_origin + w_time · S_time + w_busi · S_busi
```

其中 `w_time, w_busi ∈ [0, 1]` 且 `w_time + w_busi ≤ 1`。

## F1-score
- **Source**: Eq. (1), §5.7
- **Statement**: token-level precision 与 recall 的调和平均。

```text
F1 = 2 · P · R / (P + R)
```
