# VikingBot Feedback Observability 验证说明

本文档说明如何验证本轮 VikingBot feedback observability 改动是否生效，覆盖文档跳转、`/metrics` 暴露、以及已有 targeted tests。

## 1. 验证范围

本轮改动主要包括：

- `docs/zh/concepts/12-metrics.md` 新增 Feedback 小节与 PromQL 示例
- `docs/en/concepts/12-metrics.md` 补充 feedback 的 PromQL / Grafana examples
- `docs/en/api/09-metrics.md` 与 `docs/zh/api/09-metrics.md` 补充 `/metrics` 中的 feedback observability 说明
- 上述文档与 `bot/docs/vikingbot-feedback-observability-design.md` 之间的交叉链接
- `openviking/metrics/collectors/feedback.py` 对 VikingBot feedback 指标的 Prometheus 导出
- `/metrics` 端点对 feedback 指标的端到端暴露

## 2. 文档验证

### 2.1 检查交叉链接

手工检查以下链接是否存在且相对路径正确：

- `docs/zh/concepts/12-metrics.md`
  - 指向 `../api/09-metrics.md`
  - 指向 `../../../bot/docs/vikingbot-feedback-observability-design.md`
- `docs/en/concepts/12-metrics.md`
  - 指向 `../api/09-metrics.md`
  - 指向 `../../../bot/docs/vikingbot-feedback-observability-design.md`
- `docs/zh/api/09-metrics.md`
  - 指向 `../concepts/12-metrics.md`
  - 指向 `../../../bot/docs/vikingbot-feedback-observability-design.md`
- `docs/en/api/09-metrics.md`
  - 指向 `../concepts/12-metrics.md`
  - 指向 `../../../bot/docs/vikingbot-feedback-observability-design.md`
- `bot/docs/vikingbot-feedback-observability-design.md`
  - 指向 `../../docs/zh/concepts/12-metrics.md`
  - 指向 `../../docs/zh/api/09-metrics.md`
  - 指向 `./vikingbot-feedback-observability-validation.md`

执行案例：

1. 打开 `docs/zh/concepts/12-metrics.md`，滚动到 `### Feedback` 小节末尾。
2. 点击文中的 `Metrics API` 链接。
3. 预期现象：编辑器或文档预览会跳转到 `docs/zh/api/09-metrics.md`。
4. 再回到 `docs/zh/api/09-metrics.md` 的 `## 相关文档` 区域，点击 `指标与 Metrics`。
5. 预期现象：能跳回 `docs/zh/concepts/12-metrics.md`。
6. 再点击 `VikingBot 问答效果反馈观测方案设计`。
7. 预期现象：能跳转到 `bot/docs/vikingbot-feedback-observability-design.md`。
8. 打开设计文档末尾的 `## 14. Related Docs`，点击 `VikingBot Feedback Observability 验证说明`。
9. 预期现象：能跳回当前验证文档 `bot/docs/vikingbot-feedback-observability-validation.md`。

预期结果：

- 上述文档跳转都能成功打开目标文件
- 没有出现相对路径错误或链接指向不存在文件
- 中英文 Metrics 文档与 bot 侧设计文档之间已经形成双向跳转

### 2.2 检查文档内容

确认以下内容已经出现：

- 中文 Metrics 概念文档存在 `### Feedback` 小节
- 英文 Metrics 概念文档的 Feedback 小节下存在 `PromQL / Grafana examples`
- 中英文 Metrics API 文档都说明 `/metrics` 已包含 VikingBot feedback observability 指标
- 设计文档末尾存在 `## 14. Related Docs`

### 2.3 自查 diff

建议在仓库根目录执行：

```bash
git diff -- docs/en/concepts/12-metrics.md docs/zh/concepts/12-metrics.md docs/en/api/09-metrics.md docs/zh/api/09-metrics.md bot/docs/vikingbot-feedback-observability-design.md bot/docs/vikingbot-feedback-observability-validation.md
```

预期结果：

- 只包含上述文档文件的增量修改
- 没有与当前任务无关的实现代码变更

执行案例：

```bash
git diff -- docs/en/concepts/12-metrics.md docs/zh/concepts/12-metrics.md docs/en/api/09-metrics.md docs/zh/api/09-metrics.md bot/docs/vikingbot-feedback-observability-design.md bot/docs/vikingbot-feedback-observability-validation.md
```

预期现象：

- diff 中可以看到 `Feedback` 文档补充、`Related Documentation` / `相关文档` 交叉链接、以及新增验证文档
- 不应看到 `openviking/metrics/`、`bot/vikingbot/`、`tests/` 等实现文件出现在这个命令输出里

一个合理的结果特征示例：

```text
+++ b/docs/zh/concepts/12-metrics.md
### Feedback
... PromQL / Grafana 示例 ...

+++ b/bot/docs/vikingbot-feedback-observability-validation.md
# VikingBot Feedback Observability 验证说明
```

## 3. 指标验证

### 3.1 本地查看 `/metrics`

启动 OpenViking 服务后，请求：

```bash
curl http://localhost:1933/metrics
```

在输出中搜索以下指标名：

- `openviking_feedback_events_total`
- `openviking_feedback_coverage`
- `openviking_feedback_one_turn_resolution_rate`
- `openviking_feedback_channel_coverage`

如果本地已有 bot session 数据，预期可以看到类似样本：

```text
openviking_feedback_events_total{valid="1"} 1.0
openviking_feedback_channel_events_total{channel="cli__default",valid="1"} 1.0
```

执行案例：

1. 启动本地 OpenViking 服务。
2. 在另一个终端执行：

```bash
curl http://localhost:1933/metrics
```

3. 在返回文本中搜索 `openviking_feedback_`。

预期现象：

- 返回内容是 Prometheus exposition 文本，不是 JSON
- 能看到以 `# HELP`、`# TYPE` 开头的指标说明
- 如果本地 bot session 中已有反馈数据，能看到至少一组 feedback summary 或 channel 指标

一个可接受的输出片段示例：

```text
# HELP openviking_feedback_events_total Explicit feedback event count
# TYPE openviking_feedback_events_total gauge
openviking_feedback_events_total{valid="1"} 1.0

# HELP openviking_feedback_channel_events_total Explicit feedback event count by channel
# TYPE openviking_feedback_channel_events_total gauge
openviking_feedback_channel_events_total{channel="cli__default",valid="1"} 1.0
```

如果你本地还没有 bot session 数据，可能出现的现象是：

- 指标存在，但数值为 `0`
- 或者当前没有反馈相关样本输出，因为 collector 没有扫描到可用 session 数据

这两种情况都不一定表示实现有问题，需要结合下面的测试案例一起判断。

### 3.2 检查 fallback 语义

如果 collector 在刷新失败后使用上一次成功快照，输出中可能出现：

```text
openviking_feedback_events_total{valid="0"} <value>
```

预期语义：

- `valid="1"` 表示本次 scrape 成功得到的新鲜快照
- `valid="0"` 表示 fallback 或 stale snapshot，需要结合告警规则处理

执行案例：

1. 先以正常状态请求一次 `/metrics`，确认存在 `valid="1"` 的 feedback 指标。
2. 如果本地环境方便模拟 collector 刷新失败，再次请求 `/metrics`。
3. 观察输出中是否出现 `valid="0"` 的 feedback 指标样本。

预期现象：

- 正常情况下，主要看到 `valid="1"`
- 发生 fallback 时，可能看到 `valid="0"` 对应的同名指标

输出示例：

```text
openviking_feedback_events_total{valid="0"} 3.0
```

结果解释：

- 这不表示指标名错误
- 也不表示 `/metrics` 本身挂掉了
- 它表示 collector 没拿到新的成功快照，正在暴露上一次可用结果

## 4. 测试验证

本轮相关改动推荐只跑 targeted tests。

### 4.1 Feedback collector 与 bootstrap

```bash
uv run pytest tests/metrics/collectors/test_feedback_collector.py tests/metrics/integration/test_bootstrap.py bot/tests/test_feedback_stats.py
```

预期结果：

- 反馈 collector 的 summary / channel gauges 测试通过
- bootstrap 已注册 `FeedbackCollector`
- bot 侧 feedback stats 聚合与展示测试通过

执行案例：

```bash
uv run pytest tests/metrics/collectors/test_feedback_collector.py tests/metrics/integration/test_bootstrap.py bot/tests/test_feedback_stats.py
```

预期现象：

- pytest 会显示收集到的用例数量
- 执行过程中不应出现 import error、config error、fixture 缺失等问题
- 结束时应显示全部通过

一个合理的通过结果示例：

```text
============================= test session starts =============================
collected 15 items

tests/metrics/collectors/test_feedback_collector.py .....
tests/metrics/integration/test_bootstrap.py ..
bot/tests/test_feedback_stats.py ........

============================== 15 passed in 1.23s =============================
```

### 4.2 `/metrics` 端到端验证

```bash
uv run pytest tests/server/test_prometheus_metrics.py
```

预期结果：

- `test_metrics_endpoint_exports_feedback_metrics` 通过
- 测试会验证 `/metrics` 响应中包含 feedback summary 与 per-channel 指标

执行案例：

```bash
uv run pytest tests/server/test_prometheus_metrics.py
```

预期现象：

- pytest 输出中应包含 `test_metrics_endpoint_exports_feedback_metrics`
- 该用例会构造临时 bot session 数据，再请求 `/metrics`
- 成功时不会要求你手工准备真实反馈数据

一个合理的通过结果示例：

```text
============================= test session starts =============================
collected 2 items

tests/server/test_prometheus_metrics.py ..

============================== 2 passed in 0.84s =============================
```

如果只关心这一个新增测试，也可以执行：

```bash
uv run pytest tests/server/test_prometheus_metrics.py -k feedback
```

预期现象：

- 只运行与 feedback 相关的 `/metrics` 用例
- 输出中应出现 `1 passed` 或与过滤后数量一致的通过结果

### 4.3 一组推荐命令

如果只想快速回归本轮 feedback observability 主路径，可执行：

```bash
uv run pytest tests/server/test_prometheus_metrics.py tests/metrics/collectors/test_feedback_collector.py tests/metrics/integration/test_bootstrap.py bot/tests/test_feedback_stats.py
```

预期现象：

- 这是本轮 feedback observability 主路径的最小回归集
- 如果实现和文档都一致，预期全部通过

一个合理的通过结果示例：

```text
============================= test session starts =============================
collected 16 items

bot/tests/test_feedback_stats.py ........
tests/metrics/collectors/test_feedback_collector.py .....
tests/metrics/integration/test_bootstrap.py ..
tests/server/test_prometheus_metrics.py .

============================== 16 passed in 1.56s =============================
```

如果结果不符合预期，可以按下面顺序排查：

1. 先看是否是文档路径或文件名写错
2. 再看本地是否使用了仓库要求的 `uv` / `.venv`
3. 再看 `/metrics` 测试是否因为本地配置、端口占用或临时目录权限失败
4. 如果只有 `valid="0"` 样本出现，优先检查 collector 是否没有成功读取 bot session 数据

## 5. 验证通过的判定标准

可以把本轮改动视为验证通过，当且仅当以下条件同时满足：

- 文档中的 Feedback 小节、PromQL 示例、API 说明都已出现
- 文档间交叉链接可正确跳转
- `/metrics` 可以看到 feedback 指标输出
- targeted tests 全部通过
- 没有引入与本任务无关的额外代码改动
