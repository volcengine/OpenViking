# OpenViking Usage Reporter / Sink 技术方案

## 1. 背景

OpenViking 在 session commit 时会收到完整的 session messages。Agent runtime 调用 tool 后，会在 session messages 中留下 tool parts。部分 tool parts 可以表达某个记忆文件被检索、读取、注入等行为。

当前需求是：OpenViking 需要从 session 中解析出这些使用行为，并把结构化数据回流到不同下游。

不同部署形态的下游不同：

- 火山商业化版本：回流到指定 Kafka。
- 私有化部署：回流到客户自己的 Kafka、Webhook、日志系统或数据库。
- 开源本地版本：可以不回流，或写本地 JSONL 方便调试。

因此 OpenViking 开源版不应该硬编码 Kafka，也不应该 import Kafka 依赖，而是提供一套通用的 Usage Reporter / Sink 扩展机制。

## 2. 业界做法

这种模式是开源基础设施里常见的设计。

OpenTelemetry Collector 把数据链路拆成 receivers、processors、exporters、pipelines。exporters 专门负责把数据发送到不同 backend 或 destination，例如 OTLP、Kafka、Prometheus、file 等。配置 exporter 本身不代表启用，需要在 pipeline 中声明。

参考：https://opentelemetry.io/docs/collector/configuration/

Vector 使用 sinks 概念，把 observability data 投递到不同目的地，例如 File、HTTP、Kafka、S3、ClickHouse、Prometheus remote write 等。

参考：https://vector.dev/docs/reference/configuration/sinks/

Fluent Bit 使用 Outputs 概念。官方定义里，Outputs 用来定义数据目的地，常见目的地包括远端服务、本地文件系统或标准接口，并且 Outputs 以 plugin 形式实现。

参考：https://docs.fluentbit.io/manual/data-pipeline/outputs

所以 OpenViking 采用“核心定义事件 + 插件式 Sink 输出”是合理的。它的核心价值是把“事件产生”和“事件去哪”解耦。

## 3. 设计目标

- OpenViking 内核只定义 UsageEvent 标准结构。
- OpenViking 内核负责从 session commit 中解析 UsageEvent。
- OpenViking 内核不绑定 Kafka、Webhook、火山内部服务。
- 开源版本不 import Kafka 依赖。
- 商业化和私有化部署可以通过自定义 Sink 接入自己的数据系统。
- Sink 失败默认不影响 session commit。
- 默认不上报完整 session，只上报解析后的结构化事件，并保留 session evidence 指针。

## 4. 总体架构

数据流：

```text
Agent runtime 调用 tool
-> session message 留下 tool part
-> client 上传 session 并 commit
-> OpenViking archive session
-> UsageExtractor 从 session messages 解析 UsageEvent
-> UsageReporter 分发 UsageEvent
-> UsageSink 写入目标系统
```

模块拆分：

```text
UsageExtractor：负责从 session 里解析事件
UsageEvent：标准结构化事件
UsageReporter：负责分发事件
UsageSink：负责写入不同下游
```

## 5. UsageEvent

UsageEvent 是 OpenViking 内核和外部 Sink 之间的稳定协议。

示例：

```json
{
  "schema_version": "v1",
  "event_type": "memory.injected",
  "memory_uri": "viking://user/test/memories/experiences/xxx.md",
  "memory_type": "experience",
  "account_id": "new",
  "user_id": "test",
  "session_id": "510bb5f9-4671-498e-adf4-27bb1b3691fe",
  "archive_uri": "viking://user/test/sessions/510bb5f9/history/archive_001",
  "task_id": "b174eb56-e7d4-4fee-98a6-c53c0ddf62ed",
  "occurred_at": "2026-07-09T12:00:00Z",
  "source": {
    "tool_name": "read_experience",
    "tool_status": "completed"
  },
  "evidence": {
    "message_index": 12,
    "part_index": 0,
    "tool_call_id": "call_xxx"
  },
  "attributes": {}
}
```

默认只上报结构化事件，不上报完整 session 内容。

`memory_uri` 必须属于当前 `UsageContext.user_id` 对应的规范 experience
目录。客户端传入的其他用户 URI 不生成 UsageEvent。

## 6. UsageSink 机制

OpenViking 开源包只定义 Sink 抽象：

```python
class UsageSink:
    async def write(self, events: list[UsageEvent], context: UsageContext) -> None:
        ...
```

可扩展 Sink：

```text
kafka
webhook
customer_custom_sink
```

Kafka 不进入开源主包。商业化部署通过动态加载方式接入。

配置示例：

```yaml
usage_reporter:
  enabled: true
  sinks:
    - type: custom
      class_path: volc_ov_usage.kafka_sink.KafkaUsageSink
      config:
        bootstrap_servers: kafka-1:9092,kafka-2:9092
        topic: ov_memory_usage
```

OpenViking 用 `importlib` 动态加载：

```python
import importlib

def load_class(class_path: str):
    module_name, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)
```

只有配置了该 Sink 时才 import 对应模块。开源版本不会 import Kafka，也不会安装 Kafka 依赖。

每个 Sink 的 `write()` 调用最多等待 5 秒。超时或异常只记录日志，不影响其他 Sink。Reporter 在应用生命周期内只创建一次，应用退出时调用 Sink 可选的 `close()` 方法。

## 7. 配置设计

默认关闭：

```yaml
usage_reporter:
  enabled: false
```

商业化 Kafka：

```yaml
usage_reporter:
  enabled: true
  sinks:
    - type: custom
      class_path: volc_ov_usage.kafka_sink.KafkaUsageSink
      config:
        bootstrap_servers: kafka-1:9092,kafka-2:9092
        topic: ov_memory_usage
```

私有化 Webhook：

```yaml
usage_reporter:
  enabled: true
  sinks:
    - type: custom
      class_path: customer_ov_usage.webhook_sink.WebhookUsageSink
      config:
        url: https://customer.example.com/openviking/usage
```

## 8. 对 OpenViking 的侵入

侵入点控制在 4 个地方。

1. 新增 config

增加 `usage_reporter` 配置段。

2. 新增数据模型

增加 `UsageEvent`、`UsageContext`。

3. session commit 增加 hook

在 session archive 成功后触发：

```text
archive session success
-> usage extractor
-> usage reporter
```

4. 新增 reporter/sink 模块

新增通用扩展点和 custom sink 动态加载能力。

不侵入的地方：

- 不改 `find/search` 语义。
- 不改 `read` 语义。
- 不把 Kafka 写死进 session commit。
- 不默认上传完整 session。
- 不强制写 MEMORY_FIELDS。
- 不强制写 search_tags。
- 不影响 snapshot。
- Sink 调用具有 5 秒超时边界，失败不会中断 phase2。

整体侵入属于低到中等，核心主链路只增加一个旁路 hook。

## 9. 可靠性策略

失败处理：

- Sink 成功：正常返回。
- Sink 失败：记录日志，不影响 commit。
- 多个 Sink：某个 Sink 失败不影响其他 Sink。
- 事件带幂等 key，方便下游去重。

幂等 key：

```text
schema_version
+ event_type
+ account_id
+ user_id
+ session_id
+ archive_uri
+ message_index
+ part_index
+ memory_uri
```

后续如果需要更高可靠性，可以增加本地 buffer / dead letter file，但不作为第一期必需能力。

## 10. 当前需求落地：记忆文件使用次数回流

### 10.1 使用次数定义

本需求里的“某个记忆文件被使用”，拆成两个事件。

`memory.recalled` 表示记忆文件被检索命中。

来源：

```text
tool_name == search_experience
tool_status == completed
tool_output.results[].uri 包含该 memory uri
```

`memory.injected` 表示记忆文件被读取并注入 Agent 上下文。

来源：

```text
tool_name == read_experience
tool_status == completed
tool_input.uri 或 tool_output.uri 指向该 memory uri
```

产品上如果只展示“使用次数”，默认使用 `memory.injected` 的计数。

### 10.2 当前需求的数据流

```text
Agent 调用 search_experience / read_experience
-> tool parts 留在 session messages
-> 用户 commit session
-> OpenViking archive session
-> MemoryUsageExtractor 解析 tool parts
-> 生成 memory.recalled / memory.injected events
-> UsageReporter 分发到配置的 Sink
```

### 10.3 MemoryUsageExtractor

本期内置一个 extractor：

```text
MemoryUsageExtractor
```

解析范围：

```text
parts[].type == "tool"
tool_status == "completed"
```

识别 tool：

```text
search_experience
read_experience
```

解析规则：

```text
search_experience:
  从 tool_output.results[].uri 生成 memory.recalled

read_experience:
  从 tool_input.uri 或 tool_output.uri 生成 memory.injected
```

事件的 `occurred_at` 优先取 tool part 所在 message 的 `created_at`，并统一
转换为 UTC ISO 8601；消息时间缺失或非法时才使用 commit 解析时间。

不处理：

- 普通 `find/search` API 调用。
- 普通 `read` API 调用。
- 非 completed tool part。
- 非官方 tool name。

### 10.4 商业化版本如何回流 Kafka

商业化版本不改 OpenViking 开源内核。

商业化部署额外提供包：

```text
volc_ov_usage/
  kafka_sink.py
```

实现：

```python
class KafkaUsageSink(UsageSink):
    async def write(self, events, context):
        ...
```

商业化镜像额外安装 Kafka 依赖，例如：

```text
confluent-kafka
```

OpenViking 配置：

```yaml
usage_reporter:
  enabled: true
  sinks:
    - type: custom
      class_path: volc_ov_usage.kafka_sink.KafkaUsageSink
      config:
        topic: ov_memory_usage
```

这样开源主包不 import Kafka，商业化版本通过 custom sink 注入 Kafka 能力。

### 10.5 私有化如何接入

私有化客户可以实现自己的 Sink：

```python
class CustomerUsageSink(UsageSink):
    async def write(self, events, context):
        ...
```

配置：

```yaml
usage_reporter:
  enabled: true
  sinks:
    - type: custom
      class_path: customer_ov_usage.CustomerUsageSink
```

客户可以写入：

- 自己的 Kafka。
- HTTP endpoint。
- 数据库。
- 日志系统。
- 本地文件。

OpenViking 不需要知道目标系统细节。

### 10.6 开源版本默认能力

开源版默认关闭 usage reporting，不内置具体 Sink。部署方通过 custom sink 接入自己的目标系统。

## 11. 当前需求不在内核里做的事

本方案只解决“事件如何回流出去”。

不在 OpenViking 内核中强行完成：

- 不直接计算控制台 7 天/30 天聚合。
- 不直接把统计写进 MEMORY_FIELDS。
- 不直接把统计写进 search_tags。
- 不直接修改 experience 文件。
- 不直接推完整 session。

聚合统计可以由 Kafka 消费端、私有化 Sink、控制台 BFF 或后续专门的 Usage Store 来做。

## 12. 工作项

1. 新增 `usage_reporter` config。
2. 新增 `UsageEvent` / `UsageContext` 模型。
3. 新增 `UsageExtractor` 抽象。
4. 实现 `MemoryUsageExtractor`。
5. 新增 `UsageSink` 抽象。
6. 实现 custom sink 动态加载。
7. 在 session archive 成功后挂接 UsageReporter。
8. 增加 Sink 超时、best-effort 失败处理和日志。
9. 增加 Reporter/Sink 生命周期关闭处理。
10. 增加单测：tool parts -> UsageEvent。
11. 增加集成测试：commit session -> custom test sink 写出事件。
12. 增加文档：商业化和私有化 custom sink 接入方式。
