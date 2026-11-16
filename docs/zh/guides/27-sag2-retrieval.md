# SAG2 词法检索与多跳扩展

SAG2 是 OpenViking 的可选本地检索后端，使用 SQLite records 和 FTS5/BM25，不需要 Embedding 模型。默认后端仍为 `local`，已有向量集合不会自动迁移，也不会在故障时切换后端或混合召回。

## 启用

```json
{
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "backend": "sag2",
      "sag2": {
        "candidate_k": 50,
        "seed_k": 10,
        "expansion_term_limit": 20,
        "protected_k": 5,
        "max_hops": 1,
        "max_context_tokens": 32000,
        "total_timeout_s": 30
      }
    }
  }
}
```

数据库位于 `<workspace>/sag2/<collection>.sqlite3`。首版只支持单个 OV 服务实例使用该 workspace，不支持多副本共同写入、NFS 或对象存储上的 SQLite。

`max_judgments` 默认按 `B + H * (B + K + F)` 计算，B50/H1 为 115，B100/H2 为 330。它是每个 TypedQuery 的评分预算，不是保证执行完全部跳数。下一轮完整评分池会超出预算时，返回上一轮完整结果；不会截断候选池、补做最终复评或返回部分评分。父 `search()` 含多个 TypedQuery 时，各子查询分别计算预算。

## 检索模式

| 公共入口 | 行为 |
| --- | --- |
| `find()`、任意 `search_type="keywords"` | 单轮 BM25，不调用模型 |
| `search()` semantic QUICK | 单轮 BM25，不调用模型 |
| semantic THINKING，H=0 | BM25 加一轮 JEV 必要证据评分 |
| semantic THINKING，level 仅为 L0/L1 | 最多执行 `max_hops` 次词项扩展 |
| semantic THINKING，level=None 或包含 L2 | 保留请求层级，只执行 H=0 |

QUICK 对外分数为 `-bm25`，越高越好，不能与向量分或 JEV 分跨后端比较。旧向量 `score_threshold` 需要重新校准。THINKING 只暴露 `[0,1]` 的 JEV 分。

THINKING 需要现有 JEV Noul 配置：

```json
{
  "rerank": {
    "provider": "jev",
    "api_key": "${TYPESAFE_API_KEY}",
    "mode": "noul",
    "threshold": 0.1
  }
}
```

未配置可用 reranker 时，自动模式使用 QUICK。其他 reranker 或 JEV Choice 在自动模式下也使用 QUICK；显式请求 SAG2 THINKING 则直接报错。模型错误、响应缺失、候选正文超预算和索引版本冲突均使本次 TypedQuery 失败，不回向量分、不截断正文、不返回部分成功。

## 数据与权限语义

- L0/L1/L2 都复用 OV 现有写入队列、record id、FieldPatch、merge、rename/copy/delete 和完成确认；只跳过向量推理，不跳过摘要生成。
- 初召回、每次扩展、标题和别名修复复用同一个 account/ACL/target/context/level/tags/filter scope。ROOT 查询也绑定请求 account。
- 同 URI 的 L0/L1 在证据链内是不同 record，最终返回前才按 URI 保留最高分记录。
- SAG2 扩展统计采用固定算法的 collection+account N/DF 口径；标题与别名只使用当前 canonical scope。
- 索引保存 tokenizer、jieba 版本、schema 和 record chunk policy。版本不匹配时要求重建，不读取不兼容索引。
- 每个检索相关写事务推进 account epoch。多轮等待期间正文、ACL 或标量变化会触发 `IndexChanged`，调用方可重试，OV 不自动重复计费的 JEV 请求。
- 精确 grep 使用现有 filesystem 引擎。SAG2 的检索分词会移除 stopword 和标点，不能作为精确 grep 的完整候选集。

## 迁移与边界

从其他后端迁移时，使用独立 workspace 做蓝绿重建，由 OVPack/事实内容重新生成 records 和 FTS。不要原地修改现有 workspace 的后端配置，不要要求 dense snapshot 或 `--include-vectors`。目标接受新写后如需回滚，必须先停写并反向迁移增量，不能直接切回旧端点。

当前实现覆盖 P0–P3 功能闭环。10 万记录性能、长稳、真实 JEV 服务质量、完整迁移灰度和回滚演练属于后续 P4/P5 验收，不能由单元测试或 mock JEV 结果代替。
