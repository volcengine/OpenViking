# Ark 4.0 Adapter

## 配置只维护一份

Adapter 和 KubeVPN 启停脚本默认读取同目录的 `adapter_config.local.json`，不依赖当前工作目录。`--config` 仅用于明确指定其他配置，通常不用传。模板见 `adapter_config.example.json`。

OpenViking 自己的模型、存储和 API key 仍放在 OV 配置文件中，由 `memory_proxy.openviking_config_file` 引用，不把密钥复制进 Adapter 配置。

| 配置位置 | 作用 |
| --- | --- |
| `service` | Adapter 地址和数据集标识；运行并发跟随 runner |
| `platform` | 自进化平台网关、API key、项目 ID、请求来源 |
| `training_task.workflow_id` | 新版使用 `ark_viking_external_training` |
| `training_task.agent_id` | 执行 Agent，当前 `ark` |
| `training_task.lane_key` | 执行路由，当前 `evolving` |
| `training_task.rollout_resource_id` | 使用哪个 Agent 并发资源池；申请量跟随 runner 的 `--concurrency` |
| `training_task.viking_experiment_sets` | 训练集、评测集，以及固定的题目版本 |
| `rollout` | 等待超时、Memory 开关、回调 target、AgentMD |
| `memory_proxy` | 本地 OpenViking 地址、配置文件路径和回调日志 |
| `kubevpn` | kubeconfig 和 namespace；端口、deployment 从 service 和 target 推导 |

请求来源只写 `platform.vaka_request_source`，当前为 `ark-lx`。自动补到创建 Task 的 `agent.execution.values.request_source`、平台 HTTP Header 和 rollout 的 `x-vaka-request-source`，以及已有 `x-tt-sandbox` 的 `env.VAKA_REQUEST_SOURCE`。旧 flat `task_body` 使用 `agent_execution.values.request_source`。重复填写且不一致会报错，不会静默覆盖。

执行泳道和并发资源是两件事，在 `training_task` 中分别指定：

```json
"agent_id": "ark",
"lane_key": "evolving",
"rollout_resource_id": "rm_lane_lane_057cf57ea34b4e6"
```

Viking 创建 Task 使用 `training-task-request.v2`：`agent_id` 写入 `agent.agent_id`，`lane_key` 写入 `scheduling.lane_key`。并发资源、一个 Memory 身份及其他必需资源写入 `scheduling.resource_requests`；并发申请量跟随 runner 的 `--concurrency`。不再发送 `agent.lane_id`、`agent.lane_concurrency` 或 `agent.resources`。

创建后检查 `GET /inspect/training/tasks/{task_id}` 返回的 `params.agent_lane_key` 为 `evolving`。选中名称带 evolving 的并发资源，不代表执行自动走 evolving；不传 `scheduling.lane_key` 时平台默认走 main。已有 Task 不会被改写。

依据[接入文档第 6.3 节（修订版 371）](https://bytedance.larkoffice.com/wiki/X5huw7Orqiuxxzks19TcU1UknRf#doxcnVum4me9Yitp5rTlD9mtvJm)，`scheduling.lane_key` 可以与统一资源申请同时使用；互斥的是旧 `agent` 泳道字段与统一资源申请，不是平台要求冲突。

2026-09-08 此前三次单题尝试均在创建 Task 时失败：flat 请求误入 CaseHub 校验；V2 请求混用旧泳道字段与统一资源申请；删除统一申请后又缺少 Memory 身份。三次都没有 Task、rollout 或得分。此次改为上述统一调度方式，新测试结果需以实际请求和轨迹为准。

Adapter 不再配置或发送 `x-tt-backend`，平台根据 `scheduling.lane_key` 把它补到 Agent 的 `extra_headers`。旧 `training_task.agent_lane_id`、`agent_lane_key` 配置会报错。现在的 `lane_key` 对应文档明确支持的请求体字段，不是本地 HTTP Header。泳道不控制 KubeVPN。

2026-09-08 修复后单题实测：Task `task_ecad091cb8a0` 正常完成，Viking `2644` 的实际 `extra_headers.x-tt-backend=evolving`；Eval `372/V1` 的 `172174` 得分 `0.75`，无执行错误。本地收到 search 并返回 200，但 Agent 命中 0 条：已有 Case/Experience 仍为 draft，被默认 Agent 召回策略过滤。本次只 Eval，没有 Train 或晋升 Memory。

`openviking_target` 只需写在 `rollout.runtime_params.memory`。Memory Proxy 和 KubeVPN 复用它。本地身份默认为 `default/default`，平台身份由平台分配，不能在 Viking rollout 中手工覆盖。

`idempotency_namespace` 不用配置，使用代码默认的 `openviking-ark4`。防重复请求标识由代码生成，不需要每次实验手动更换。

Viking 模式无需手填平台请求体、合约版本、摘要、OpenViking 版本、Memory 身份资源 ID、并发和训练轮数。执行泳道与并发资源池需要明确选择，不能从彼此推断。Authorization、Cookie、`x-tt-env` 等受保护 Header 不能通过 rollout 覆盖。

## Task 与实验集

每执行一次 `run_batch_train_eval`，Adapter 创建一个新 Task；这次 Train、Commit、Eval 共用它。Adapter 启动本身不创建 Task。

`training_task` 只需要：

```json
{
  "workflow_id": "ark_viking_external_training",
  "agent_id": "ark",
  "lane_key": "evolving",
  "rollout_resource_id": "rm_lane_lane_057cf57ea34b4e6",
  "viking_experiment_sets": [
    {"experiment_set_id": 371, "version": "V1", "role": "train"},
    {"experiment_set_id": 372, "version": "V1", "role": "eval"}
  ]
}
```

Adapter 创建 Task 时把配置转换成 V2 请求，并补齐平台参数；按 phase 获取平台冻结后的 Case。runner 的 `--train-split train --eval-split dev` 分别选 Train/Eval；`--train-index`、`--eval-index` 可选题。runner 的 `--dataset` 必须匹配 `service.dataset`，不是 Viking Set ID。

创建 Task 前自动处理：

- 查询平台当前执行合约、默认模型和默认 OpenViking 版本；不按版本号猜“最新”。当前平台默认 `v0.4.12.dev3`，与原先固定的 `dev4` 不同，且不影响本地 OV 代码版本。
- 按 workflow 查唯一的有效实验和 Memory 目标；查询独立泳道列表，校验 `lane_key` 属于指定 Agent 且可用。
- 校验所选并发资源属于指定 Agent；申请一个 Memory 身份，并按资源元数据补齐其他必需资源。当前平台的 `task_manager_executor` 自动申请量为 `1`，也会带上；它是资源名称，不需要配置 Task Manager URL。无法确定申请量时直接报错，不猜值。
- `--concurrency` 同时控制本地 rollout 并发、平台 rollout workers、评测 workers 和并发资源申请量。泳道只负责路由，不承载 slot。
- runner 的 epochs、train trials、eval trials 控制本地实际执行，Adapter 保留原值。平台 `memory.train_epochs/train_trials/eval_trials` 按接口规定固定填 `1`，不拿本地轮数覆盖；填 `1` 不会自动启动 Train。
- `rollout.runtime_params.task_timeout` 同时用于平台 case timeout，不重复配置。
- 请求格式和 Viking 评测器由 Adapter 按当前支持的接口协议填写；Agent 使用配置中的 `agent_id`。

平台查询失败或结果不唯一时，直接报错，不猜 ID、不回退到旧版本。Adapter 运行期间可在 `/admin/platform-runs` 查看：`training_plan` 是本地实际轮数，`resolved_task_request` 是发给平台的请求，两者含义不同；重启后这些内存记录会清空。

只评测已有 Memory：runner 使用 `--epochs 0 --skip-baseline-eval --trials 1`，保留原 OV 配置与数据目录；不执行 Train 或 Commit。平台仍要求创建 Task 时配置 train/eval 两种实验集，不代表必须执行 Train。纯 Eval 的平台完成信号是否能正常收尾，需以实际运行结果为准。

题目 `version` 保留：它决定使用哪版题，不能自动换成最新。可选 `task_name` 只改任务名称前缀；需要指定模型时填 `model_ep`；同 workflow 有多个实验时填 `experiment_id`。通常均不用填。

确需接续已有 Task 时，`training_task` 只填 `workflow_id` 和 `existing_task_id`，此时不重新查询或修改冻结参数。旧 `task_body` 仅保留兼容，不能与 `viking_experiment_sets` 混用。嵌套 `agent` 的 V2 请求体必须声明 `schema_version: "training-task-request.v2"`；不写版本时只能使用 flat 字段（如 `agent_execution`），不能混搭。自定义 flat 请求不推荐用于 Viking，当前平台会要求 CaseHub dataset。

更新代码和配置后需重启 Adapter，并使用更新后的 runner；旧 runner 未发送训练计划时，新配置会明确报错。

## 启动与停止

先按 OV 配置启动本地 OpenViking，再分别运行：

```bash
bash benchmark/ark4-0/start_adapter.sh
bash benchmark/ark4-0/start_kubevpn_proxy.sh
```

检查当前本地配置对应的服务（端口以 `service.port` 为准）：

```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS http://127.0.0.1:8765/admin/platform-runs
curl -fsS http://127.0.0.1:8765/admin/memory-proxy
python3 benchmark/ark4-0/kubevpn_proxy.py status
```

停止 Adapter：对应终端按 Ctrl+C。停止本机 KubeVPN 代理：

```bash
bash benchmark/ark4-0/stop_kubevpn_proxy.sh
```

`service.admin_token` 是可选的本地管理接口口令，仍有用途；KubeVPN 的状态文件用于安全停止自己启动的进程，也仍有用途。无需手工填写时使用默认值。

## 链路与结果

`runner → Adapter → 自进化平台 → Viking 执行和评分`；训练轨迹返回后，runner 向配置指定的 OpenViking Commit。远端 Memory 通过 target 对应的 KubeVPN 代理访问本地。

runner 全部完成且 Commit 无错误后，Adapter 才发送 `external-training-completed`。平台显示完成不代表 Memory 召回已验证，仍需检查实际轨迹和 Memory 回调日志。

当前单题的旧结果保留在 `.local/ark-viking-smoke-20260907/local-ov/`。此次配置整理不代表远端泳道/召回权限或 KubeVPN 网络问题已修复，也不会自动启动新实验。

## 兼容与测试

旧的 `ov_external_training` / CaseHub 调用仍有代码和测试覆盖，保留支持。该模式继续使用 runner 的 `--casehub-dataset-id`、`--casehub-case-id` 和旧 Task 字段，不要与 Viking 模式混用。

```bash
.venv/bin/python -m pytest -q --no-cov benchmark/ark4-0/tests
ruff check benchmark/ark4-0
```
