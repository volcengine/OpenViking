# 本地关键词检索（SQLite FTS5 sidecar + search-time BM25）实现计划

| 项目 | 信息 |
| --- | --- |
| 状态 | 已实现（MVP 落地，M1–M4 主体完成） |
| 目标版本 | v0.5 |
| 更新日期 | 2026-08-13 |
| 关联 | 上游 PR #1857（被关闭）、Issue #2850/#2900（远程 BM25 grep 回退）、#2144（grep 接入 VikingDB BM25） |

> **实现状态（2026-08-13）**：M1（grep local_then_fs + 引擎解析 + 配置）、M2（KeywordQueue/
> KeywordProcessor + 与 EmbeddingMsg 同生产点共发 + rm/mv/restore 一致性 + wait_processed）、
> M3（HybridKeywordRecaller RRF/weighted 融合 + find/search/HTTP hybrid 字段）、M4（observer
> /observer/keyword + 文档 + 单测）均已完成。落地代码见 `openviking/storage/keywordfs/`、
> `openviking/retrieve/hybrid_keyword.py`，测试见 `tests/storage/keywordfs/`、
> `tests/retrieve/test_hybrid_keyword.py`、`tests/service/test_observer_keyword.py`。
> 未实现（后续）：`ov reindex --mode keyword` 全量重建 CLI 接线、git.tuning 级观测指标。

> 本文是 B2「本地 BM25 / 关键词检索」的实现计划。方向遵循上游维护者在 PR #1857 中的明确指引：
> **“keyword recall comes from backend-owned search-time BM25 over a local SQLite FTS5 sidecar”，而不是预计算稀疏向量。**
> 对应 PR #1857 的关闭结论：本季度倾向更长期的检索层关键词方案，暂不合并 local_bm25（稀疏向量 + 重建）。

---

## 1. 背景与动机

### 1.1 问题

OpenViking 的 `grep` 与 `find`/`search` 目前的关键词能力依赖远程 VikingDB 的 BM25：

- `grep`：`engine="auto"` 时 `_resolve_grep_engine` 只有 `backend_type in ("volcengine", "vikingdb")` 才走 `_grep_vikingdb_then_fs`（远程 BM25 召回 + 本地精确匹配）；**本地后端（`local` 向量索引）直接退化为全量文件扫描** `_grep_fs`。
- `find`/`search`：`HierarchicalRetriever` 只做 dense 向量检索，**没有任何关键词召回**；对代码名、缩写、ticker、版本号这类“必须精确命中”的短查询，dense 检索不稳定（这也是 #1857 作者的核心痛点）。
- 远程 BM25 在 `local` 后端下不可用 → **纯本地部署没有可用的关键词召回，只能全量 grep 扫描**。

### 1.2 现状接线（已核实，main @ dcca2936）

| 位置 | 现状 |
|---|---|
| `viking_fs.grep()` | `engine: auto\|fs`；`auto` 解析为 `fs` 或 `vikingdb_then_fs` |
| `viking_fs._grep_vikingdb_then_fs()` | 远程 BM25 召回候选 URI → `_grep_in_files` 精确匹配（**最终匹配永远基于磁盘原文**） |
| `viking_fs._grep_fs()` | `_grep_with_agfs`（原生 grep）/ `_grep_encrypted`（解密扫描） |
| 向量异步管线 | `EmbeddingMsg` → `EmbeddingQueue` → `SemanticProcessor._vectorize_single_file/_vectorize_directory`；`wait_processed` 可等待 |
| 写入路径 | `viking_fs.write_file/write_file_bytes/rm/mv/move_file/mkdir`；snapshot restore 走 `_schedule_vector_rebuild` |
| 系统 SQLite 惯例 | `<workspace>/_system/queue/queue.db`、`<workspace>/_system/usage_audit/usage_audit.sqlite3`、`ingest/cursor_store.py`（SQLite 状态库范式） |
| 配置 | `OpenVikingConfig.grep: GrepConfig{engine, switch_to_remote_threshold}`、`retrieval: RetrievalConfig`；`ServerConfig` 不承载，走 ov.conf |
| CLI | `ov reindex`（`crates/ov_cli/src/commands/content.rs`） |

---

## 2. 目标与非目标

### 2.1 目标

1. 为**本地/离线部署**提供后端自有（backend-owned）的 search-time BM25 关键词召回，使 `grep` 不再退化为全量扫描。
2. 提供可选的 **hybrid 检索**：`find`/`search` 融合 dense + 关键词结果，解决短查询/精确 token 召回。
3. 与现有异步管线、权限模型、`wait_processed`、`ov reindex`、observer/metrics 一致，维护成本可接受。
4. 默认关闭（off-by-default），显式开启，符合项目一贯的隐私/最小侵入风格（同 `ingest`、`experimental_memory_switch`）。

### 2.2 非目标（避免踩 #1857 被否的坑）

- **不做**预计算稀疏向量 + 全文重建的方案（被维护者明确否定：BM25 stats 一致性 + reindex 复杂度）。
- **不替换**远程 VikingDB BM25：当 `vikingdb` 后端可用且数据量达到阈值时，仍优先远程（避免与现有能力重叠、造成用户选择困惑）。
- **不修改**向量索引格式与 `search_by_keywords` 远程接口。
- **不做**自动 commit hook / 强一致同步：关键词索引是**召回加速器**，非事实来源；缺失/过期时优雅回退 fs 扫描（与现有 `_grep_vikingdb_then_fs` 的 Step 2 回退语义一致）。

---

## 3. 总体架构

```text
写入路径 (write/rm/mv/restore/reindex/语义管线)
        │  发 KeywordMsg（与 EmbeddingMsg 同生产点，可独立等待）
        ▼
KeywordQueue (queuefs 持久化) ──► KeywordProcessor（异步）
                                      │  tokenize + upsert
                                      ▼
                  SQLite FTS5 sidecar   <workspace>/_system/keyword/<account_id>.sqlite3
                                      ▲
查询路径 grep / find / search          │  search-time bm25() 召回候选 URI
        └──► KeywordFS.lookup() ───────┘
                  │
                  ├── grep: 候选 URI → _grep_in_files 精确匹配（复用现有）
                  └── find/search(hybrid): 候选 URI + bm25 score → RRF 融合 dense
```

核心原则：

- **FTS5 负责存储与 search-time BM25**（`ORDER BY bm25(kf)` 由 SQLite 用索引内统计即时计算），无需维护 corpus stats 表。
- **FTS 只做召回**，最终匹配/排序决策仍走现有代码（grep 用 `_grep_in_files`；dense 用 `HierarchicalRetriever`）。
- **sidecar 是本地加速器**，天然支持回退：sidecar 缺失/未就绪 → `fs` 扫描或纯 dense。

---

## 4. 核心设计决策

| 决策 | 方案 | 备选被否原因 |
|---|---|---|
| 存储载体 | 每账号一个 SQLite FTS5 库，`<workspace>/_system/keyword/<account_id>.sqlite3` | 单一全局库无法隔离多租户重建；`_system` 已有 queue/usage_audit 先例 |
| 召回方式 | FTS5 内置 `bm25()`（search-time），不预计算稀疏向量 | #1857 的稀疏向量方案被维护者否决（stats 一致性 + 重建复杂度） |
| 索引内容 | 复用 `embedding.text_source`（默认 `content_only` 叶子原文）+ L1 overview（可选） | 与向量索引覆盖一致，避免两套内容源漂移 |
| 构建管线 | 独立 `KeywordMsg`/`KeywordQueue`/`KeywordProcessor`，**与 `EmbeddingMsg` 同生产点共发** | 独立队列使关键词不依赖 embedding 健康；同生产点保证覆盖一致 |
| 一致性 | 异步 + 回退：FTS 缺失/过期 → fs 扫描；`wait_processed` 可等待 | 强同步会侵入写链路，违反项目“索引异步重建”一贯模型 |
| CJK 分词 | Python 预归一化：默认拉丁按词、CJK 按字切分；`jieba` 可选增强（按字→按词） | FTS5 `unicode61` 原生不切 CJK（#1857 同款批评）；`trigram` 无位置信息、`bm25()` 排名弱 |
| 加密共存 | `keyword.respect_encryption=true` 时跳过索引 | 明文 sidecar 与加密保证冲突；`_grep_encrypted` 查询期解密扫描已覆盖加密场景 |
| 多副本 | sidecar 本地化，副本上 `keyword.enabled=false`（同 `enable_watch_scheduler` 只读副本开关） | 共享 s3 后端时本地 sidecar 无法随写者同步，加速器语义天然可降级 |

---

## 5. 数据模型与 Schema

文件：`<workspace>/_system/keyword/<account_id>.sqlite3`（`account_id` 需做白名单校验，防 `../`）。

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- 索引内容表（FTS5，外部内容表模式便于增量替换单行）
CREATE VIRTUAL TABLE kf USING fts5(
    account_id,            -- 索引列：用于 MATCH 租户限定
    scope,                 -- 索引列：context_type（memory/resource/skill），用于 MATCH 过滤
    uri,                   -- UNINDEXED：精确候选 URI
    content,               -- 索引列：归一化后的可检索文本
    tokenize='unicode61'
);

-- 状态/元数据（供增量同步、重建、健康检查）
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,          -- schema_version / tokenizer_version / built_at / last_seq
    value TEXT NOT NULL
);

-- 可选：索引内容到 URI 的映射（count、一致性核对用）
CREATE TABLE uri_map (
    rowid INTEGER PRIMARY KEY,       -- = kf.rowid
    uri TEXT NOT NULL,               -- 规范化 viking URI（含 owner 字段）
    level INTEGER NOT NULL,          -- 0/1/2
    context_type TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    byte_len INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(uri)
);
```

要点：

- **租户限定走 MATCH 列过滤**：`WHERE kf MATCH 'account_id: "acct" AND content: "foo"'`，让 FTS 内部扫描按账号收敛，而不是对全库结果做后置过滤。
- **scope/level/owner_user_id 不进 FTS 全文列**（`UNINDEXED`），只做候选后置过滤；必要时把 `context_type` 也提为索引列（见上 `scope`）。
- **单行替换**：外部内容表模式下，`INSERT INTO kf(kf, account_id, scope, uri, content) VALUES('delete', ...)` + `'rebuild'` 可精确替换单 URI，配合 `uri_map` 记录已建行，实现增量 upsert/delete。
- **bm25 即 search-time**：`SELECT uri FROM kf WHERE kf MATCH ? ORDER BY bm25(kf, 1.2, 0.75) LIMIT ?`。

---

## 6. 配置

扩展 `OpenVikingConfig`，新增顶层 `keyword` 段 + `grep.engine` 增补 `"local"`：

```jsonc
{
  "grep": {
    "engine": "auto",                 // auto | fs | local | vikingdb
    "switch_to_remote_threshold": 10000
  },
  "keyword": {
    "enabled": false,                 // off-by-default
    "tokenizer": "auto",              // auto | char | jieba
    "content_source": "content",      // content | summary | both（对齐 embedding.text_source）
    "max_doc_bytes": 65536,           // 跳过超大原文（对齐 #3006 的 verbatim 顾虑）
    "respect_encryption": true,       // encryption.enabled 时自动禁用索引
    "cjk_mode": "char"                // char | bigram（CJK 归一化粒度）
  },
  "retrieval": {
    "hybrid": {
      "enabled": false,               // find/search 是否融合关键词
      "fusion": "rrf",                // rrf | weighted
      "rrf_k": 60,
      "keyword_weight": 0.3,          // weighted 模式的权重
      "min_token_query_len": 2        // 低于该长度的查询跳过关键词（避免噪音）
    }
  }
}
```

引擎解析顺序（`_resolve_grep_engine` 扩展）：

```text
engine == "fs"            → fs
engine == "local"         → local_then_fs（要求 keyword.enabled 且 sidecar 就绪，否则 fs）
engine == "vikingdb"      → vikingdb_then_fs
engine == "auto":
   1. vikingdb 可用 && count >= switch_to_remote_threshold → vikingdb_then_fs
   2. keyword.enabled && sidecar 就绪                       → local_then_fs
   3. 否则                                                  → fs
```

---

## 7. 组件拆解（新增模块）

新增 `openviking/storage/keywordfs/`（对齐 `queuefs/` 命名与结构）：

| 文件 | 职责 |
|---|---|
| `keyword_fs.py` | `KeywordFS`：打开/建库/迁移、`upsert(uri, level, context_type, owner, text)`、`delete(uri)`、`move(old_uri,new_uri)`、`lookup(query, scope, exclude, limit) -> list[(uri, score)]`、`rebuild(scope)`、`health()` |
| `keyword_msg.py` | `KeywordMsg`：`{kind: upsert|delete|move, uri, old_uri?, level?, context_type?, owner?, text?}`（复用 `EmbeddingMsg` 的 dataclass 风格） |
| `keyword_queue.py` | `KeywordQueue(NamedQueue)` + `KeywordMsgConverter` |
| `keyword_processor.py` | `KeywordProcessor(DequeueHandlerBase)`：消费消息 → `KeywordFS` upsert/delete/move；失败重试入队（复用 `SemanticProcessor` 的重试模式） |
| `tokenizer.py` | `tokenize(text, cfg) -> str`：拉丁按词、CJK 按字/大词（`jieba` 可选）；归一化、小写化 |
| `config.py` | `KeywordConfig` / `HybridRetrievalConfig`（pydantic，`extra=forbid`） |
| `builder.py` | 全量重建：临时库建好 → 原子替换（`VACUUM INTO` + rename），避免半成品可见 |

改动点（存量文件，均为小侵入）：

| 文件 | 改动 |
|---|---|
| `openviking_cli/utils/config/open_viking_config.py` | 挂载 `keyword`、`retrieval.hybrid` 段 |
| `openviking_cli/utils/config/grep_config.py` | `GrepEngine` 增 `"local"`/`"vikingdb"` |
| `openviking/storage/viking_fs.py` | `_resolve_grep_engine` 增 `local_then_fs` 分支；`_grep_local_then_fs`；`rm/mv` 发 delete/move 消息；`write_file_bytes` 可选直发（内容写入场景） |
| `openviking/storage/queuefs/semantic_processor.py` | 在 `_vectorize_single_file/_vectorize_directory` 成功点**共发** `KeywordMsg`（复用已读取的 content/level/owner） |
| `openviking/service/reindex_executor.py` | reindex 时同时调度关键词重建；snapshot restore 的 `_schedule_vector_rebuild` 同步触发 `_schedule_keyword_rebuild` |
| `openviking/service/core.py` | 组装 `KeywordFS` + `KeywordProcessor`，注册队列与 `wait_processed` 等待 |
| `openviking/server/routers/system.py` | `wait_processed` 覆盖 keyword 队列；`consistency` 增加 keyword 核对项 |
| `openviking/server/routers/observer.py` | 新增 `observer_keyword`（sidecar 状态/行数/落后数） |
| `crates/ov_cli/src/commands/content.rs` | `ov reindex --mode keyword`；`ov doctor` 检查 keyword 健康 |

---

## 8. 索引构建与一致性

### 8.1 增量构建

1. `SemanticProcessor` 在 embedding 成功点共发 `KeywordMsg{kind=upsert, ...}`（内容来源与 `text_source` 一致）。
2. `KeywordProcessor` 消费 → `tokenize` → `KeywordFS.upsert`（`INSERT OR REPLACE` 单行）。
3. `viking_fs.rm/mv` 在向量删除点共发 `delete`/`move` 消息。
4. `snapshot restore` → `_schedule_keyword_rebuild(written, deleted)`（复用 `_schedule_vector_rebuild` 的精确路径调度）。
5. 服务重启后消息从持久化队列恢复（同 embedding 语义）。

### 8.2 全量重建

- 命令：`ov reindex <uri> --mode keyword --wait`，走 `reindex_executor`。
- 流程：临时库 `*.sqlite3.tmp` 边扫边写 → 完成 `VACUUM INTO` 目标库 → 原子 rename → 更新 `meta.built_at`/`tokenizer_version`。
- tokenizer 版本变更时自动触发全量重建（`meta.tokenizer_version` 比对）。

### 8.3 一致性语义

- **不强一致**：新写入到可召回存在异步窗口（与 embedding 一致）；`wait_processed` 可等待收敛。
- **回退保证正确性**：grep 的最终匹配始终基于磁盘原文（`_grep_in_files`）；FTS 召回为空 → 直接返回空（与远程 BM25 空召回语义一致，见 #2850 结论——索引确认无匹配）或按配置回退 fs 扫描。
- `content_transform`（projection）存在时强制走 `fs`（与现有远程路径一致，FTS 无法安全做投影）。

---

## 9. 检索接入

### 9.1 Phase 1 —— grep（低风险、独立合入）

`_grep_local_then_fs`（镜像 `_grep_vikingdb_then_fs`）：

```python
async def _grep_local_then_fs(self, uri, pattern, exclude_uri, case_insensitive, node_limit, level_limit, ctx):
    query = " ".join(kw.strip() for kw in pattern.split("|") if kw.strip())   # 同现有
    candidates = await self._keyword_fs.lookup(
        query, scope=uri, exclude=exclude_uri, limit=min(node_limit*5, 100000), ctx=ctx)
    if not candidates:
        return {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    return await self._grep_in_files(candidates, pattern, case_insensitive, node_limit, ctx)
```

- 复用 `_grep_in_files`（精确匹配），API/CLI 无变化。
- `engine=local` 显式开启；`auto` 在无 vikingdb 时落到 `local_then_fs`。

### 9.2 Phase 2 —— find/search hybrid（独立合入）

在 `HierarchicalRetriever` 前加轻量 `HybridKeywordRecaller`：

1. 满足 `retrieval.hybrid.enabled && len(query tokens) >= min_token_query_len` 时，`KeywordFS.lookup` 取候选 URI + bm25 score。
2. dense 结果与关键词候选用 **RRF** 融合（默认）：`score = Σ 1/(k + rank)`；`weighted` 模式用 `kw_norm = (bm25 - min)/(max - min)` 加权。
3. 重复 URI 去重合并（`max` 或 RRF 求和）。
4. 结果仍走现有 `_convert_to_matched_contexts` 与 rerank（关键词候选可选择性跳过 rerank 或一并参与，默认跳过以省成本）。
5. 暴露：`find(query, hybrid=True)` / `search(..., hybrid=True)`，CLI `ov find --hybrid`；HTTP 加 `hybrid` 字段（`extra` 逃生舱已支持 SDK 透传）。

---

## 10. 多租户与权限

- **存储隔离**：每账号独立 DB 文件。
- **查询隔离**：`KeywordFS.lookup` 强制 `account_id` 列过滤 + `viking_fs._ensure_access`（现有权限检查在调用前执行，不新增权限面）。
- **peer/owner 过滤**：候选按 `owner_user_id` 后置过滤，行为与 `actor_peer_id` 一致。

---

## 11. 安全与隐私

- 默认 `keyword.enabled=false`（显式开启）。
- `respect_encryption=true` 时，`encryption.enabled` 的部署自动禁用 FTS 索引（`_grep_encrypted` 已覆盖加密 grep）；文档明确该权衡。
- 超大原文用 `max_doc_bytes` 跳过（避免 verbatim 全文入库，呼应 #3006）。
- 明文只落本地 `_system/keyword/`，与 queue.db 同级；`ovpack` 导出**不包含** keyword 库（`legacy_migration` 已把 `_system` 视为非用户内容）。

---

## 12. 可观测性与运维

- **metrics**：`openviking_keyword_docs`（行数）、`openviking_keyword_queries_total`、`openviking_keyword_latency_seconds`、`openviking_keyword_rebuild_duration_seconds`（对齐 `metric-design.md` 边界）。
- **observer**：`/api/v1/observer/keyword` 返回 `{enabled, db_path, docs, last_built_at, queue_pending, degraded}`。
- **doctor**：`ov doctor` 检查 sidecar 可写、tokenizer 版本、行数与 `uri_map` 一致性。
- **consistency**：`/api/v1/system/consistency` 增加 keyword 核对（每 URI 有向量是否也有关键词行，反向亦然——仅告警不阻塞）。

---

## 13. 测试计划

| 层级 | 覆盖 |
|---|---|
| 单元 | `tokenizer`（拉丁/CJK/大小写/jieba 可选）；`KeywordFS`（upsert/delete/move/重建/原子替换）；`_resolve_grep_engine` 新分支；config 解析 |
| 集成 | `add_resource(wait=True)` → 无 vikingdb 时 `grep` 命中原文；`rm/mv` 后 FTS 一致；snapshot restore 触发关键词重建；`wait_processed` 收敛；加密部署自动禁用 |
| 检索质量 | 复用 `benchmark/RAG` 与 `benchmark/retrieval`：新增 keyword-only 与 hybrid 两组指标（recall@k、MRR）；构造代码名/缩写/版本号查询集 |
| 回退 | sidecar 缺失/损坏/过期 → fs 扫描；`content_transform` 强制 fs；vikingdb 可用时仍走远程 |
| 回归 | 现有 `tests/` 全部通过；`ov reindex --mode keyword` 幂等；多账号隔离 |

---

## 14. 里程碑与验收标准

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1 grep 落地** | `keywordfs` 基础库 + `tokenizer` + `_grep_local_then_fs` + 引擎解析 + 配置 | 本地部署 `grep "token"` 命中且不做全量扫描；`auto` 解析正确 |
| **M2 构建管线** | `KeywordQueue/Processor` + 语义管线共发 + `rm/mv/restore/reindex` 一致性 + `wait_processed` | `add_resource` 后 `wait_processed` 即可 grep；rm/mv/restore 后无脏行 |
| **M3 hybrid 检索** | `HybridKeywordRecaller` + RRF/weighted + API/CLI/SDK 透传 | `find("Version 2.4.1")` 召回改善（benchmark 对比）；默认关闭 |
| **M4 可观测与文档** | metrics/observer/doctor/consistency + `docs/en` 配置与指南 + README | `ov doctor` 绿；`/observer/keyword` 正常；中文+英文文档 |

每个里程碑独立可合入（PR 拆分建议：M1 → M2 → M3 → M4），便于社区 review 与灰度。

---

## 15. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 磁盘额外占用（索引副本） | `max_doc_bytes` + `content_source=content` 控制；与 VikingDB 远程索引等价（#1857 讨论已认可） |
| 加密与明文索引冲突 | `respect_encryption` 默认跳过；加密场景用查询期解密扫描 |
| 只读副本/共享后端 sidecar 过期 | sidecar 为加速器，回退 fs 扫描；副本默认 `keyword.enabled=false`（同 `enable_watch_scheduler` 先例） |
| CJK 召回精度（按字切分） | `jieba` 可选增强；hybrid 由 dense 补语义；bigram 模式可选 |
| 与远程 BM25 能力重叠 | 解析顺序保持远程优先；本地仅覆盖 vikingdb 不可用场景 |
| 异步窗口导致新写入暂不可召回 | 与 embedding 同语义；`wait_processed` 收敛；grep 最终匹配基于磁盘原文 |
| 超大语料 FTS 扫描 | `account_id`/`scope` 做 MATCH 列过滤收敛；重建用临时库原子替换 |

---

## 16. 参考

- PR #1857 `feat: add local_bm25 sparse provider for hybrid retrieval`（未合并）及其评论：维护者指定 SQLite FTS5 sidecar + search-time BM25 方向、`jieba` 可选依赖。
- Issue #2850 / PR #2900：远程 BM25 grep 空召回回退语义（本设计沿用“候选召回 + 磁盘精确匹配”）。
- PR #2144：grep 接入 VikingDB BM25 的既有实现（`_grep_vikingdb_then_fs` 的模板）。
- 现有代码：`viking_fs.grep/_resolve_grep_engine/_grep_vikingdb_then_fs/_grep_in_files`；`queuefs/embedding_*`、`semantic_processor._vectorize_*`；`ingest/cursor_store.py`、`observability/usage_audit/sqlite_store.py`（SQLite 范式）；`service/reindex_executor.py`、`_schedule_vector_rebuild`。
