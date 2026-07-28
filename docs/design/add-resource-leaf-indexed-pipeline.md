# add-resource Embedding-first 与 `leaf_indexed` 流程

## 本次改动的核心变化

```text
调用方
  |
  +--> add-resource(wait=false)
  |       |
  |       +--> 返回 task_id
  |
  +--> getTask(task_id)
          |
          +--> queued
          +--> processing_queue
          +--> leaf_indexed       叶子已经可检索
          +--> completed/failed   完整任务结束
```

原流程必须等叶子 summary、目录 overview 和所有 Embedding 全部完成，调用方才能确认任务成功。

现在增加 `leaf_indexed` 中间阶段，并让默认 `content_only` 文本叶子先做 Embedding、再生成 summary。

## 主处理链路

```text
ResourceService.add_resource
  |
  +--> AddResource Queue
          |
          +--> ResourceProcessor 完成解析和资源落盘
          |
          +--> Semantic Queue
                  |
                  v
          SemanticDagExecutor
                  |
                  +--> 发现本次需要更新的叶子
                  |
                  +--> 根据文本源策略选择处理方式
                          |
                          +----------------------------------------------------+
                          |                                                    |
                          v                                                    v
                 Embedding-first                                      Summary-first
                 content_only 文本                                    summary_first
                          |                                            summary_only
                          |                                            图片/音频等
                          |                                                    |
                          +--> 原文 Embedding                                  +--> LLM summary
                          |                                                    |
                          +--> LLM summary                                     +--> Embedding
                          |                                                    |
                          +--------------------------+-------------------------+
                                                     |
                                                     v
                                      等待全部必要叶子 Embedding 完成
                                                     |
                                  +------------------+------------------+
                                  |                                     |
                            有叶子错误                              全部成功
                                  |                                     |
                                  v                                     v
                           最终进入 failed                    stage=leaf_indexed
                                                                        |
                                                                        | 叶子已经可检索
                                                                        | 调用方可提前返回
                                                                        v
                                                         继续后台完整语义处理
                                                                        |
                                       +--------------------------------+------------------+
                                       |                                                   |
                                       v                                                   v
                         回填叶子 summary 元数据                               生成目录 overview
                         operation=metadata_patch                                       |
                                       |                                                  +--> 子目录 L0/L1 Embedding
                                       |                                                  +--> 父目录 overview
                                       |                                                  +--> 根目录 L0/L1 Embedding
                                       |                                                   |
                                       +-------------------------+-------------------------+
                                                                 |
                                                                 v
                                               等待完整 Embedding tracker 收敛
                                                                 |
                                                    +------------+------------+
                                                    |                         |
                                                  有错误                    无错误
                                                    |                         |
                                                    v                         v
                                            stage=failed              stage=completed
```

## `leaf_indexed` 的准确语义

```text
stage=leaf_indexed
  |
  +--> 本次新增或修改、且需要向量化的所有叶子 Embedding 已成功
  +--> 叶子已经可以参与检索
  |
  +-- 不保证叶子 summary/abstract 已回填
  +-- 不保证子目录 L0/L1 已完成
  +-- 不保证根目录 L0/L1 已完成
```

`leaf_indexed` 是 `running` 状态下的持久化里程碑。后续的 `processing_queue` 更新不会把它覆盖；任务最终只会进入 `completed` 或 `failed`。

## Metadata patch

```text
Embedding-first 叶子
  |
  +--> 初始 Embedding 使用原文
  |       +--> dense/sparse vector = 原文
  |       +--> full text = 原文
  |       +--> abstract = 空
  |
  +--> LLM summary 完成
          |
          +--> 先登记 metadata patch
          +--> 等全部叶子 Embedding 完成
          +--> 先发布 leaf_indexed
          +--> 再投递 metadata patch
                  |
                  +--> 仅 partial update abstract
                  +--> 不重新调用 embedder
                  +--> 不覆盖原有 vector 和 full text
```

## 调用方使用方式

```text
只需要叶子可检索：
    add-resource(wait=false)
      -> getTask 轮询
      -> stage == leaf_indexed 时即可返回

需要完整目录语义：
    add-resource(wait=false)
      -> getTask 轮询
      -> status == completed 时返回

使用 wait=true：
    保持原有行为，等待完整任务结束；
    不会在 leaf_indexed 提前返回。
```
