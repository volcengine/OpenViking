# Resource import configuration research

本记录仅使用当前仓库内的服务端源码、测试与官方文档，供 Web Studio 的资源导入引导实现使用。

## 可实施结论

- 飞书“服务端应用凭证”没有单独的启用开关。服务端读取 `ov.conf` 中的飞书配置，缺失时回退到 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`；两者均缺失才在导入时失败。Web Studio 可以链接已有配置文档，但按钮应称为“查看服务端配置文档”，不应称为“完整开启教程”。
- TOS 是 Connector-only 来源。必须启用外部 Connector，并把 `tos` 放入 `connector.allowed_add_types`；Connector 的提交接口、任务查询接口也必须配置成完整 URL。
- 当前仓库没有发布 TOS Connector 部署、端点获取或 TOS 凭证配置教程，也没有可对应的中英文公开 docs 路由。Web Studio 不应为 TOS 链接无关的 S3/TOS 存储文档，也不应猜测端点或凭证。若产品需要“如何开启”链接，应先补齐 Connector/TOS 官方文档。

## 飞书 / Lark

### 准确配置与生效方式

服务端配置字段定义在 `FeishuConfig`：

- `app_id`
- `app_secret`
- `domain`，默认 `https://open.feishu.cn`
- `max_rows_per_sheet`，默认 `1000`
- `max_records_per_table`，默认 `1000`
- `download_images`，默认 `true`
- `request_timeout`，默认 `30`

证据：`openviking_cli/utils/config/parser_config.py:621-644`。

应用凭证读取顺序是 `config.app_id` / `config.app_secret` 优先，环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 兜底；只有两处都缺失时才报错。代码中不存在飞书专用的布尔启用开关。证据：`openviking/parse/accessors/feishu_accessor.py:431-462`、`openviking/resource/feishu_watch_auth.py:47-63`。

推荐与现有完整配置指南保持一致，在 `ov.conf` 使用：

```json
{
  "feishu": {
    "app_id": "YOUR_APP_ID",
    "app_secret": "YOUR_APP_SECRET",
    "domain": "https://open.feishu.cn"
  }
}
```

仓库也兼容把配置放在 `parsers.feishu`；加载器会将嵌套的 `parsers` 配置展开，且继续兼容根级同名配置。证据：`openviking_cli/utils/config/open_viking_config.py:397-437,467-471`。现有配置指南使用根级 `feishu`：`docs/zh/guides/01-configuration.md:794-820`、`docs/en/guides/01-configuration.md:825-851`。

Lark 国际版必须把 `domain` 设置为 `https://open.larksuite.com`。证据：`docs/zh/guides/01-configuration.md:810-820`、`docs/en/guides/01-configuration.md:841-851`。

服务启动时加载并初始化配置，因此修改配置后需要重启服务。证据：`openviking/server/bootstrap.py:202-218`。

### 配置文件位置

服务端配置加载顺序为：显式 `--config`、`OPENVIKING_CONFIG_FILE`、`~/.openviking/ov.conf`、`/etc/openviking/ov.conf`。证据：`openviking_cli/utils/config/open_viking_config.py:502-510`、`openviking_cli/utils/config/config_loader.py:23-64`。

### 支持范围与 Token 模式

飞书 Accessor 明确支持 `docx`、`wiki`、`sheets` 和 `base` URL。证据：`openviking/parse/accessors/feishu_accessor.py:116-130`。

- 应用凭证导入由服务端使用 `app_id` / `app_secret` 获取 app/tenant token。
- 一次性用户 Token 导入可以仅传 `feishu_access_token`，且服务端不保存该 Token。
- 用户 Token 定时同步还要传 `feishu_refresh_token`，并要求服务端配置签发该 Token 的同一个飞书应用凭证。

证据：`docs/zh/api/02-resources.md:196-200`、`docs/en/api/02-resources.md:202-206`。

### 已有官方文档链接

- 中文配置：`https://docs.openviking.ai/zh/guides/01-configuration#feishu`
- 英文配置：`https://docs.openviking.ai/en/guides/01-configuration#feishu`
- 中文资源行为：`https://docs.openviking.ai/zh/api/02-resources`
- 英文资源行为：`https://docs.openviking.ai/en/api/02-resources`

路由由 Markdown 路径生成并启用 clean URL；中英文导航也显式注册了对应 guides/API 路径。证据：`docs/.vitepress/config.ts:83-86,691-706,852-856,918-944`。`docs.openviking.ai` 是仓库 README 给出的官方文档域名：`README_CN.md:13`、`README.md:13`。

现有配置文档只覆盖字段和 Lark domain，没有覆盖飞书开放平台从创建应用到权限申请、发布、授权具体文档的完整流程。仓库源码只明确指出图片下载需要特定权限（`openviking/parse/accessors/feishu_accessor.py:822`），因此 Web Studio 不应自行补写完整权限清单。

## TOS Connector

### 准确配置

Connector 配置是 `ov.conf` 的根级 `connector` 段：

```json
{
  "connector": {
    "enable": true,
    "connector": "YOUR_DOC_ADD_ENDPOINT",
    "tracker": "YOUR_TASK_INFO_ENDPOINT",
    "timeout_seconds": 3600,
    "poll_interval_ms": 5000,
    "allowed_add_types": ["tos"]
  }
}
```

字段及默认值：

- `enable`: 默认 `false`。
- `connector`: 外部 Connector 的 doc/add 完整端点 URL；启用时必填。
- `tracker`: 外部 Connector 的 task/info 完整端点 URL；启用时必填。
- `timeout_seconds`: 默认 `3600`，必须大于零。
- `poll_interval_ms`: 默认 `5000`，必须大于零。
- `allowed_add_types`: 默认 `["tos"]`。

证据：`openviking_cli/utils/config/open_viking_config.py:59-86,249-252`；解析样例与校验测试见 `tests/unit/test_connector_config.py:10-64`。

TOS URI 必须是 `tos://<bucket>/<path>`。TOS 没有标准解析管线，是 Connector-only 类型；当 Connector 未开启或 `tos` 不在 `allowed_add_types` 中时，服务端明确拒绝，而不是降级。证据：`openviking/connector/routing.py:71-83`、`openviking/connector/delegate.py:122-161,387-395`。

Connector 客户端把 `connector` 用作 doc/add POST URL、把 `tracker` 用作 task/info POST URL，并使用请求 API key 作为 Bearer 鉴权。证据：`openviking/connector/client.py:42-55,57-117`。

OpenViking 对 TOS 只把去掉 `tos://` 后的路径作为 `tos_path` 交给外部 Connector；TOS 的受支持参数和凭证参数集合在 OpenViking 侧均为空。这意味着 TOS 凭证与外部 Connector 的部署方式不由本仓库这层 UI/接口配置，不能从 OpenViking 源码推断。证据：`openviking/connector/routing.py:19-41`、`openviking/connector/delegate.py:387-395`。

### 文档现状

当前中英文配置指南及资源 API 文档均没有 `connector`、`allowed_add_types`、`add_type` 或 `tos://` 的配置说明；仓库中出现的 TOS 文档主要是 S3-compatible 存储、快照后端或安装镜像，与资源导入 Connector 不是同一能力。因此当前不存在可由 Web Studio 安全链接的 TOS Connector 开启文档路由。

在官方 Connector/TOS 文档补齐前，建议 UI 只展示基于源码可确认的信息：

> 需要服务端启用 TOS Connector。请在 `ov.conf` 的 `connector` 配置中启用服务、填写 Connector 端点，并允许 `tos` 类型；修改后重启服务。

不要在 Web Studio 中预填或推测 Connector endpoint，也不要链接 `guides/15-snapshot`、云部署 TOS 存储等无关页面。
