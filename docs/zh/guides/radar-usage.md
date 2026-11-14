# GitHub PR Radar 使用说明

Radar 随源码 Watch 定期采集 GitHub 仓库已合并的 PR，生成中文摘要和历史报告。

## 1. 准备

OpenViking 服务已启动，模型已配置。在运行服务的机器上，使用服务所属系统用户登录 GitHub CLI：

```bash
gh auth login
gh auth status
```

已有登录可直接使用。Radar 直接调用 `gh api`，凭证完全由 `gh` 管理。

下面命令中的 `<host>`、`<port>`、`<owner>`、`<repo>` 请替换为实际值。如果 OpenViking 服务开启了鉴权，请按部署方式为请求添加鉴权头。

## 2. 开启 Radar

导入仓库时，在 `args.pr_radar` 中开启 Radar：

```bash
curl --fail-with-body -sS -X POST 'http://<host>:<port>/api/v1/resources' \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "path": "https://github.com/<owner>/<repo>",
  "processing_mode": "vectors_only",
  "watch_interval": 60,
  "wait": false,
  "args": {
    "pr_radar": {
      "enabled": true,
      "digest_interval_minutes": 1440,
      "backfill_days": 7
    }
  }
}
JSON
```

这个示例每 **60 分钟采集一次**，后续按 **24 小时窗口出报**，首次回溯 **7 天**。这些周期是示例值，可以自行调整。

| 参数 | 含义 |
| --- | --- |
| `watch_interval` | Watch 触发间隔，单位分钟；开启 Radar 时必须大于 0 |
| `processing_mode: vectors_only` | 源码只做向量处理，不关闭 Radar 的模型摘要 |
| `pr_radar.enabled` | 设置为 `true` 开启 Radar |
| `pr_radar.digest_interval_minutes` | 报告窗口长度，单位分钟；开启时必填，范围 1–10080 |
| `pr_radar.backfill_days` | 首次回溯天数，默认 7，范围 1–90 |
| `pr_radar.summary_enabled` | 模型摘要开关，默认 `true`，一般不必填写 |

保存返回结果中的 `watch_id`，后续用它管理 Watch 和定位报告。返回的 `task_id` 是本次导入任务 ID，与 `watch_id` 不同；请求受理不代表导入或报告已完成。

## 3. 查看报告

报告固定写到 Watch 所属用户目录：

```text
viking://user/<user_id>/resources/radar/<watch_id>/
├── latest.md       最近一份成功发布的报告
└── reports/        历史报告
```

使用创建 Watch 的用户身份读取，把 `<user_id>` 和 `<watch_id>` 替换为实际值：

```bash
curl --fail-with-body -sS --get 'http://<host>:<port>/api/v1/content/read' \
  --data-urlencode 'uri=viking://user/<user_id>/resources/radar/<watch_id>/latest.md'
```

报告正文位于响应的 `result` 字段。

- 首次回溯采集完整且有已合并 PR 时，生成首份报告。
- 后续窗口到期且采集完整后出报；PR 按合并时间归属窗口。
- 空窗口不生成报告，`latest.md` 保持不变。
- 每次 Watch 触发都会运行 Radar，源码 HEAD 没有变化也会采集。
- 模型失败时可以降级为事实模板报告，实际生成模式会标在报告中。

## 4. 管理 Watch

查看 Watch 状态和所属用户：

```bash
curl -sS 'http://<host>:<port>/api/v1/watches/<watch_id>'
```

立即触发一次：

```bash
curl --fail-with-body -sS -X POST \
  'http://<host>:<port>/api/v1/watches/<watch_id>/trigger'
```

手动触发会提交执行，不会强制提前关闭尚未到期的报告窗口。

暂停：

```bash
curl --fail-with-body -sS -X PATCH \
  'http://<host>:<port>/api/v1/watches/<watch_id>' \
  -H 'Content-Type: application/json' \
  --data '{"is_active": false}'
```

恢复时将 `false` 改为 `true`。暂停和恢复作用于整个 Watch，包括源码刷新与 Radar。

删除 Watch：

```bash
curl --fail-with-body -sS -X DELETE \
  'http://<host>:<port>/api/v1/watches/<watch_id>'
```

删除 Watch 会停止后续执行，已有报告保留。

## 5. 报告没有生成怎么办

先确认 Watch 处于启用状态，并检查服务日志中的 `Radar failed`。Radar 错误不会改写源码 Watch 的执行结果，因此不能只凭 Watch 的 `last_error` 判断 Radar 是否成功。

如果是 GitHub 访问失败，在服务运行环境检查 `gh auth status` 和仓库访问权限。采集尚未完成、窗口尚未到期或窗口内没有已合并 PR，也可能暂时没有新报告。

Radar 失败会记录错误，由后续 Watch 触发重试；源码 Watch 继续执行。
