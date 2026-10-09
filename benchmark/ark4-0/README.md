# Ark：直接接入 Viking

旧自进化网关已移除，不再创建 `/inspect/training/tasks`、等待 OV_WAIT 或发送完成信号。

## 现在怎么跑

`run_batch_train_eval → 本地 adapter → Viking 实验 → Agent + 评判 → 拉取结果和轨迹 → OpenViking commit`

- 每次启动 runner 新建一个本地 run；每个页批次、epoch、trial 对应新的 Viking 实验。
- 一个实验包含多道题，不是每道题创建一个实验。Viking 的 `run_times` 固定为 1，多轮由 runner 控制。
- `--concurrency` 设置单个实验总并发及未单独配置的组并发；`viking.group_concurrency` 可按组覆盖。当前 G1 跟随 runner，G2～G4 各为 20。`service.max_active_tasks` 控制本 Adapter 同时运行的平台 Task 数（默认 1，最多 2，跨 run 共用）；设为 2 后两个实验可以同时运行，合计负载也会增加，不改变单个实验的并发。
- 只跑 Eval：`--epochs 0 --skip-baseline-eval`。不会 Train，也不会 commit。
- Memory 是否启用由下面的 `sandbox_config` 控制，`--loader-mode none` **不代表关闭远端 Memory**。

## 唯一配置文件

`benchmark/ark4-0/adapter_config.local.json`。启动脚本默认找自己同目录的这个文件，`--config` 可省略。

| 字段 | 用途 |
| --- | --- |
| service.port | 本地 adapter 端口，默认 8765 |
| viking.base_url | Viking 地址：`https://viking-exp.byted.org` |
| viking.api_token | Viking API Token。不是旧网关 API Key |
| viking.template_task_id | 已有实验的编排模板，当前 3327；只复制算子、分组、资源，不复用实验 |
| viking.group_concurrency | 可选，按模板的 group key 指定题目并发，例如 `{"group_2":20,"group_3":20,"group_4":20}`；未配置的组跟随 `--concurrency`，组名不存在则报错 |
| service.max_active_tasks | 平台 Task 同时运行上限，1 或 2；占满时等待，某个 Task 结束后再发起下一个。修改后重启 Adapter 生效，不修改或取消已创建的 Task。 |
| viking.train / viking.eval | 分别指定 `experiment_set_id`、`version`；可加 `row_ids` 选题 |
| viking.sandbox_config | 发给 Agent 算子的完整运行配置，包含 headers 和 Memory |
| viking.score_column | 读取哪个评分列，默认 `answer_score`，保留原始 0～1 分数 |
| viking.task_time_limit_seconds | 单个 Viking 实验总时限，默认 14400 秒 |
| viking.group_timeout_seconds | 按组覆盖一次尝试的超时；当前 `{"group_1":1800}` 表示 G1「Vaka调用」30分钟，其他组保留模板设置。不是 shell 工具超时，也不是每轮对话各30分钟；修改后重启 Adapter，对新建 run 生效 |
| memory_proxy | 保留原来的本地 OpenViking 地址、配置文件路径、鉴权读取位置 |
| kubevpn | 保留原来的 kubeconfig、namespace |

没有配置的可选项使用代码默认值。旧 `platform`、`training_task`、`rollout` 配置会报错，不会悄悄继续走旧接口。

平台 HTTP 请求只带 `Authorization: Bearer <Viking Token>`。
Agent 的 header 放在 `viking.sandbox_config.extra_headers`，例如：

```json
{
  "x-tt-backend": "evolving",
  "x-vaka-request-source": "ark-lx",
  "x-tt-sandbox": "{\"multi-agents-md-id\":\"stg-default-agentmd-20260901\",\"env\":{\"VAKA_REQUEST_SOURCE\":\"ark-lx\"}}"
}
```

Memory 放在 `viking.sandbox_config.extra_payload.extra_data.extra.memory`。
跑空 Memory baseline 时设置 `enabled: false`。
访问本地 Memory 时设置 `enabled: true`、`mode: read_only` 和 `openviking_target: evolving-dutao`，并开启 KubeVPN。
当前迁移配置使用独立本地身份 `default/default`，不会复制模板里的 `memory_case_*` 身份；远端是否接受这个身份仍需真实联调确认。

## 启动和停止

在仓库根目录执行：

```bash
# 1. 先填配置里的 Viking API Token，再启动 adapter（前台；Ctrl+C 停止）
bash benchmark/ark4-0/start_adapter.sh

# 2. 需要读本地 Memory 时，在另一个终端开启 KubeVPN
bash benchmark/ark4-0/start_kubevpn_proxy.sh

# 3. 只测一道题（下面是 Viking 行 ID，不是 data.case_id）
.venv/bin/python -m openviking.session.train.run_batch_train_eval \
  --dataset ark4-0 --domain ark \
  --config /Users/bytedance/.openviking/ov-eval-ark.conf \
  --benchmark-service-url http://127.0.0.1:8765 \
  --epochs 0 --trials 1 --concurrency 36 --skip-baseline-eval \
  --viking-eval-set-id 300 --viking-eval-version V11 \
  --viking-eval-row-id 175403

# 4. 关闭 KubeVPN
bash benchmark/ark4-0/stop_kubevpn_proxy.sh
```

runner 的 `--config` 是 OpenViking 配置，与 adapter 配置不同；请使用和 `memory_proxy.openviking_config_file` 相同的文件/服务，避免 Train 写入与 Agent 读取的不是同一个目录。

不传 `--viking-*-*` 时使用 adapter 配置；传入时覆盖本次 run，不修改配置文件。
Train 可用 `--viking-train-set-id`、`--viking-train-version`、可重复的 `--viking-train-row-id`。
Eval 同理。省略行 ID 表示使用该集合该版本的全部行。
不再使用 `--casehub-*`。

## 查结果、恢复与取消

- 最终 runner 报告里的 `benchmark_task_ids` 是本次 Viking 实验 ID。
- `GET /v1/runs/{run_id}` 查看每批实验 ID、行 ID、状态。
- 原始结果、轨迹、提交记录保存在脚本同目录 `.adapter-state/`；含完整请求数据，不要提交到 Git。
- 重启 adapter 会复用本地记录，runner 继续查询即可。**重新启动 runner 是一个新 run**。
- 创建请求超时时，按唯一实验名查回原实验；查不到或有重名时停在待核对状态，不自动再创建。查询执行状态的 `error` 会给出待核对名称。
- 需要取消远端实验：`POST /v1/runs/{run_id}/cancel`。只关 adapter 或 runner 不等于取消 Viking 实验。
- 模板含告警、资源预检失败或资源池容量低于请求并发时，拒绝提交，不自动创建/扩容资源池。

## 失败如何处理

- Agent 已成功，但评判失败：原始结果和完整轨迹仍保存，评分为“无有效评分”，不是 0。
- 报告中 `invalid_evaluation_count` 单列失败数量；平均分只统计有效评分。
- 无有效评分、轨迹缺失、存在无法区分的重试分支：跳过 commit，避免生成错误 Memory；报告保留跳过数量。
- 轨迹支持两种格式：`session_trace.messages` 会话，以及节点式工作流。节点式只提取原始问题、真实工具调用及返回、最终回答，不导入系统提示词；工具结果或最终回答缺失时仍跳过 commit。读取结果不会自动 commit。
- 多轮对话从逐题日志补齐每轮轨迹；只读取 `session_trace.messages`，不把内部模型完整提示词当作 Agent 对话。
- 大轨迹不会静默截断；超过内容接口限制时走签名下载，最高 128 MiB，失败会明确记录。

## 本地测试

```bash
.venv/bin/pytest --no-cov -q benchmark/ark4-0/tests
```

本地模拟通过不等于远端联调成功。真实验证需要有效 Viking API Token，以及模板资源和本地 Memory 路由可用。
