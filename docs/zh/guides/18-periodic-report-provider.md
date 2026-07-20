# 可选的周期报告归档 Provider

OpenViking 提供一个可选 Resource provider，用来归档其他产品已经完成选材和渲染的
报告。它可与 LoopX `periodic-report` 配合，但协议不绑定具体业务：研发周报、版本总结、
研究报告、运维报告和 issue-fix 周报都使用同一归档接口。

该 extension **默认关闭**。import 只表示 provider 可用，不会激活周报或归档 sink。
正式 sink 入口必须收到已验证的 LoopX `periodic_report_activation_v0`，其中 profile
必须已启用，并且绑定的正是这个 provider。

这样产品只有一份状态：LoopX 拥有 capability 激活和项目 profile；OpenViking 不再
另造一个可能与 LoopX 漂移的“周报开关”。

## Ownership 边界

Provider 只拥有三项能力：

- `report.archive.write`
- `report.archive.readback`
- `report.query`

它不决定何时触发报告、不负责选材、不生成 Markdown/HTML，也不发送飞书消息。归档失败
只影响可选 archive sink；主投递是否继续由上游产品策略决定。

## Resource 布局与回执

每份报告使用稳定路径：

```text
viking://resources/periodic-reports/{project_key}/{period_start}/{report_id}/
```

目录包含 `report.md`、`report.html.txt` 和 `manifest.json`。HTML 仍以 UTF-8 原文保存，
manifest 声明 `media_type=text/html`；这样无需放宽 OpenViking `content/write` 的安全扩展名
白名单。

`manifest.json` 是最后写入的 commit marker。确定性的 `bundle_digest` 和 `result_id`
绑定报告身份、元数据、Markdown 与 HTML：完全相同的重试为 no-op；部分写入后的重试只
补齐缺失文件；同一身份出现不同内容则 fail closed。每次成功归档都会精确回读 manifest
和两份正文。

## Python 用法

```python
from openviking import AsyncOpenViking
from openviking.extensions.periodic_report import (
    PeriodicReportArchiveProvider,
    PeriodicReportBundle,
)

client = AsyncOpenViking(path="./data")
provider = PeriodicReportArchiveProvider(client)

bundle = PeriodicReportBundle(
    project_key="my-project",
    profile_id="my_project_weekly",
    profile_version="v1",
    report_id="weekly-2026-07-20",
    period_start="2026-07-13",
    period_end="2026-07-19",
    generated_at="2026-07-20T09:00:00+08:00",
    markdown="# 周报\n\n- 完成 provider",
    html="<h1>周报</h1><ul><li>完成 provider</li></ul>",
    metadata={"trigger": "cadence"},
)

# LoopX inspect-profile 返回的原始 periodic_report_activation_v0。
activation_receipt = load_loopx_activation_receipt()
readiness = provider.readiness(
    activation_receipt=activation_receipt,
    sink_id="project_archive",
    bundle=bundle,
)
assert readiness["status"] == "ready"

result = await provider.archive_sink(
    bundle,
    sink_id="project_archive",
    idempotency_key="report_sink_0123456789abcdef",
    activation_receipt=activation_receipt,
)
assert result["readback_verified"]

history = await provider.query(project_key="my-project", limit=8)
```

## 可选 LoopX 绑定

`get_extension_manifest()` 返回 extension manifest，其中
`report.archive.write@v0` 与 `periodic_report_sink_v0` 对齐 LoopX 的版本锁定 sink
协议：

```json
{
  "schema_version": "periodic_report_sink_binding_v0",
  "sink_id": "project_archive",
  "sink_kind": "project_resource",
  "sink_role": "archive",
  "dependency_policy": "optional",
  "capability": {
    "capability_id": "report.archive.write",
    "capability_version": "v0"
  },
  "extension": {
    "extension_id": "openviking.periodic-report.archive",
    "extension_version": "0.1.0",
    "protocol": "periodic_report_sink_v0"
  }
}
```

把 LoopX activation receipt 传给 `provider.readiness(...)`，再将 provider receipt
交给 LoopX extension-readiness 评估。报告生成后，用同一 activation receipt、sink id
和 LoopX 派生的幂等键调用 `provider.archive_sink(...)`。capability 未启用、binding
缺失或禁用、版本不兼容时，都会在任何 Resource 写入前 fail closed。

LoopX 项目应先显式启用通用 periodic-report profile，再添加这个 optional binding。
OpenViking 缺失时，provider-neutral 生成结果仍然可用；可选归档只降级，不接管调度或群
消息投递。

## 体验模式

| 配置 | 体验 |
| --- | --- |
| capability 未启用、extension 已安装 | 不运行报告，也不允许归档写入 |
| capability 已启用 | 触发、证据归一化、Markdown/HTML 生成及已配置的主投递 |
| capability + optional OV extension | 在上述基础上增加历史归档、精确重试/回读和查询/可视化输入；OV 缺失只局部降级 |
| capability + required OV extension | durable 模式；归档未验证前，正式完成 fail closed |

最完整的团队体验，是在同一个 enabled profile 中同时配置飞书主投递 sink 和这个
OpenViking archive sink。群消息和历史报告共享同一报告身份，但两个 provider 都不会
获得调度权。
