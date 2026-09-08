# Ark 4.0 Adapter

## 配置只维护一份

Adapter 和 KubeVPN 启停脚本默认读取同目录的 `adapter_config.local.json`，不依赖当前工作目录。`--config` 仅用于明确指定其他配置，通常不用传。模板见 `adapter_config.example.json`。

OpenViking 自己的模型、存储和 API key 仍放在 OV 配置文件中，由 `memory_proxy.openviking_config_file` 引用，不把密钥复制进 Adapter 配置。

| 配置位置 | 作用 |
| --- | --- |
| `service` | Adapter 地址和数据集标识；运行并发跟随 runner |
| `platform` | 自进化平台网关、API key、项目 ID、请求来源 |
| `training_task.workflow_id` | 新版使用 `ark_viking_external_training` |
| `training_task.lane_key` | 要使用的泳道，当前 `agentmemory`；资源 ID 自动查询 |
| `training_task.viking_experiment_sets` | 训练集、评测集，以及固定的题目版本 |
| `rollout` | 等待超时、Memory 开关、回调 target、AgentMD |
| `memory_proxy` | 本地 OpenViking 地址、配置文件路径和回调日志 |
| `kubevpn` | kubeconfig 和 namespace；端口、deployment 从 service 和 target 推导 |

请求来源只写 `platform.vaka_request_source`，当前为 `agentmemory`。自动补到 Task 的 `agent.execution.values.request_source`、rollout 的 `x-vaka-request-source` 和已有 `x-tt-sandbox` 的 `env.VAKA_REQUEST_SOURCE`。重复填写且不一致会报错，不会静默覆盖。

`openviking_target` 只需写在 `rollout.runtime_params.memory`。Memory Proxy 和 KubeVPN 复用它。本地身份默认为 `default/default`，平台身份由平台分配，不能在 Viking rollout 中手工覆盖。

`idempotency_namespace` 不用配置，使用代码默认的 `openviking-ark4`。防重复请求标识由代码生成，不需要每次实验手动更换。

Viking 模式无需手填平台请求体、合约版本、摘要、OpenViking 版本、资源 ID、并发和训练轮数。不能手工覆盖受保护的泳道 Header。

## Task 与实验集

每执行一次 `run_batch_train_eval`，Adapter 创建一个新 Task；这次 Train、Commit、Eval 共用它。Adapter 启动本身不创建 Task。

`training_task` 只需要：

```json
{
  "workflow_id": "ark_viking_external_training",
  "lane_key": "agentmemory",
  "viking_experiment_sets": [
    {"experiment_set_id": 371, "version": "V1", "role": "train"},
    {"experiment_set_id": 372, "version": "V1", "role": "eval"}
  ]
}
```

Adapter 创建 Task 时把这段配置传给平台；按 phase 获取平台冻结后的 Case。runner 的 `--train-split train --eval-split dev` 分别选 Train/Eval；`--train-index`、`--eval-index` 可选题。runner 的 `--dataset` 必须匹配 `service.dataset`，不是 Viking Set ID。

创建 Task 前自动处理：

- 查询平台当前执行合约、默认模型和默认 OpenViking 版本；不按版本号猜“最新”。当前平台默认 `v0.4.12.dev3`，与原先固定的 `dev4` 不同，且不影响本地 OV 代码版本。
- 按 workflow 查唯一的有效实验和 Memory 目标；按泳道查询资源 ID，补齐平台必需资源。
- `--concurrency` 同时控制本地 rollout 并发、平台 rollout workers、评测 workers 和泳道 slot。
- runner 的 epochs、train trials、eval trials 控制本地实际执行，Adapter 保留原值。平台 `memory.train_epochs/train_trials/eval_trials` 按接口规定固定填 `1`，不拿本地轮数覆盖；填 `1` 不会自动启动 Train。
- `rollout.runtime_params.task_timeout` 同时用于平台 case timeout，不重复配置。
- 请求格式、Ark Agent、Viking 评测器由 Adapter 按当前支持的接口协议填写。

平台查询失败或结果不唯一时，直接报错，不猜 ID、不回退到旧版本。Adapter 运行期间可在 `/admin/platform-runs` 查看：`training_plan` 是本地实际轮数，`resolved_task_request` 是发给平台的请求，两者含义不同；重启后这些内存记录会清空。自动获取资源 ID 不代表平台实际泳道绑定问题已经修复。

只评测已有 Memory：runner 使用 `--epochs 0 --skip-baseline-eval --trials 1`，保留原 OV 配置与数据目录；不执行 Train 或 Commit。平台仍要求创建 Task 时配置 train/eval 两种实验集，不代表必须执行 Train。纯 Eval 的平台完成信号是否能正常收尾，需以实际运行结果为准。

题目 `version` 保留：它决定使用哪版题，不能自动换成最新。可选 `task_name` 只改任务名称前缀；需要指定模型时填 `model_ep`；同 workflow 有多个实验时填 `experiment_id`。通常均不用填。

确需接续已有 Task 时，`training_task` 只填 `workflow_id` 和 `existing_task_id`，此时不重新查询或修改冻结参数。旧 `task_body` 仅保留兼容，不能与新配置混用。

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
