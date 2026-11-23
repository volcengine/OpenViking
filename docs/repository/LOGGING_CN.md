# 开发者日志规范

按记录用途选择级别。规则适用于 OpenViking 自有代码，第三方依赖保留原有级别语义。日志改动不新增审计接口或存储。

配置见[配置指南](../zh/guides/01-configuration.md)，Python 输出实现见 [logger.py](../../openviking_cli/utils/logger.py)，安全字段工具见 [log_safety.py](../../openviking/observability/log_safety.py)。

## 1. 选择级别

| 级别 | 用途 | 示例 |
| --- | --- | --- |
| `CRITICAL` | 进程无法继续 | 启动致命失败、进程状态不可恢复 |
| `ERROR` | 需要介入的最终失败 | 请求或任务失败、重试耗尽、数据丢失风险、安全防护失效 |
| `WARNING` | 仅用于安全审计 | 认证失败、权限或成员变更、密钥生命周期、敏感配置与数据操作 |
| `INFO` | 自愈、状态变化与预期拒绝 | 重试退避、兜底、熔断切换、启动关闭、迁移、批次汇总、4xx 拒绝 |
| `DEBUG` | 业务过程与排障明细 | 决策依据、中间结果、缓存行为、成功操作细节 |

- 单次重试记 `INFO`，最终失败只记一条 `ERROR`。在实际执行兜底的边界记录所用方案和结果；结果不完整时标记 `degraded=true`。
- 模型回复成功不能兜住对话或记忆写入失败。恢复若依赖客户端后续轮次重发历史，仍记 `ERROR`。
- 预期的 4xx 响应记 `INFO`，不带堆栈；认证失败或越权拒绝另留一条安全审计 `WARNING`。
- `api_key` 模式下，ROOT 身份的每次调用都必须留审计 `WARNING`，含只读调用，禁止采样。dev/trusted 模式、未认证路由和进程内构造的 ROOT 上下文不适用此规则。
- 观测/指标失败不得影响业务：首次 `ERROR`，后续 `INFO`。普通重试、兜底、慢调用和脏数据跳过不使用审计 `WARNING`。

## 2. 写有用、适量的日志

- 最终结果由决定处理方式的边界记录一次；中间层传播异常或提供 `DEBUG` 明细。
- 使用稳定英文模板，小写开头、不加句号。模块由日志头标识，避免手写模块前缀和没有信息量的“开始/完成”。
- Python 使用 `get_logger(__name__)` 和惰性参数：`logger.info("retry attempt=%d", attempt)`。不通过 f-string、字符串拼接、`%` 插值或 `.format()` 构造消息。其他运行时使用自身的结构化/惰性接口。
- 需要检索、聚合的维度放在 `extra`，使用统一字段名，如 `operation`、`http_route`、`http_status_code`、`duration_ms`、`error_code`、`error_type`、`total`、`failed`、`skipped`。
- 循环与批次降级使用一条带计数的 `INFO` 汇总，加 `DEBUG` 明细；高频过程信息记 `DEBUG`。昂贵的日志专用计算先检查 `isEnabledFor()`，简单计数和 hash 可直接计算。
- 消息不超过 2 KB，字段值不超过 1 KB。截断保留有效 UTF-8，并标记 `truncated=true`、`original_length`。

## 3. 保证内容安全

消息、`extra`、Trace 属性、审计记录和子进程诊断遵守同一规则。格式化或凭证模式过滤不能保证任意文本安全。

| 内容 | 规则 |
| --- | --- |
| 可信身份/关联 ID、任务 ID/状态、固定操作/错误码、异常类型、数量、耗时、文件类型 | 使用已校验 ID、固定枚举或基础类型；未明确允许的字段默认禁止。 |
| 路由与入口元数据 | 路由只用模板，不输出真实路径或查询串。IP 来自校验后的连接或可信代理信息；User-Agent 只保留白名单产品/版本摘要，最多 256 字节。不安全的值不输出。 |
| 路径、URI、资源定位 | 使用 `safe_path_for_log()` / `safe_uri_for_log()` 或 `short_hash()`。仅可信且不敏感的名称可原样输出；用户控制或租户路径必须转换。 |
| 异常 | 优先记录 `type(exc).__name__` 和稳定 `error_code`。不输出原始异常文本或对象；`sanitize_error()`、`sanitize_public_http_error()` 仅提供部分保护。 |
| 凭证、Privacy 明文、`peer_id`、绝对路径、提示词、消息、文档/记忆/Skill 正文、模型输入输出、工具参数/结果、完整请求响应体 | 禁止输出，改用数量、长度、类型或 hash。URI 中的 peer 段同样受限。 |
| 完整对象与任务 `meta` | 不用 `%s`、`%r`、`asdict()`、`model_dump()` 整体输出。选取安全字段，排除任意元数据值；`repr=False` 不能保护序列化。 |

- 密钥、Token、Cookie、签名头、预签名 URL、连接串凭证及可重建密钥的片段禁止输出。显式启用的密钥诊断展示可通过 `mask_api_key()` 仅显示末四位。
- 堆栈仅限显式本地排障时以 `ERROR` 输出，且须先清理异常消息、源码和路径；`exc_info=True` 不能绕过这一要求。
- 字段允许输出不代表可以把原始内容拼入消息。即使在 `DEBUG` 或启用 `log_payloads` 时，也优先使用安全摘要。

```python
from openviking.observability.log_safety import safe_uri_for_log
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
logger.info(
    "resource read failed; using cached result error_type=%s",
    type(exc).__name__,
    extra={"target_id": safe_uri_for_log(uri), "error_type": type(exc).__name__, "degraded": True},
)
```

## 4. 使用统一输出

```text
2026-11-13T22:56:01.028Z INFO openviking.server.bootstrap pid=452394 request_id=- trace_id=- message="server started"
```

- 顺序为 `ts level logger pid request_id trace_id message`，其后追加结构化字段。时间取事件发生时刻，使用 UTC、固定毫秒精度；PID 和模块/组件名属于事件来源。
- 级别名统一为 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`；映射 `TRACE→DEBUG`、`WARN→WARNING`、`FATAL→CRITICAL`，不改变第三方事件语义。Rust 没有原生 `CRITICAL`，将其阈值映射到 `ERROR` 无法只筛选致命事件。
- 每条事件占一行 UTF-8，不带 ANSI 颜色、Rich 布局、自动换行或进度条。消息和字符串按 JSON 规则引用、转义；数字和布尔值保留类型。缺失 ID 在文本中为 `-`，结构化记录中为 `null`；控制字符必须转义。
- 关联 ID 由统一适配层注入。业务异常元数据不得用 provider request ID 覆盖请求/后台任务的 `request_id`。
- server 入口统一管理 root、Uvicorn、MCP、APScheduler 和原生输出的 handler、阈值与格式。业务模块不安装 `basicConfig`、独立 handler 或 loguru sink。仅支持统一格式；worker 初始化和配置重载不得重复输出；原生设置需要重启时须明确说明。
- SDK 导入不接管宿主 root logger。合并到服务日志的子进程输出保留自身 PID/组件名并遵守同一格式；未适配的输出写入有明确来源的独立文件。
- **bootstrap 进度、依赖检查、启动结果和失败直接 `print(..., flush=True)`**，不受日志阈值或文件输出影响。失败写 stderr，其余状态写 stdout，内容仍遵守第 3 节。
- 交互式 CLI 的结果/进度及协议输出使用各自展示方式；诊断信息不得污染机器可读 stdout 或 stdio 协议。

## 5. 安全审计要求

审计覆盖账号/成员、ACL 与权限、密钥生命周期和身份绑定、认证/越权失败、敏感配置与数据写入、删除/导出，以及第 1 节约定的 ROOT 调用。

- 执行后记录一次真实成功或失败，禁止采样。审计副本同时按 `logger=openviking.audit` 和已登记的 `audit.*` 事件识别，不能只按 `WARNING` 路由。
- 完整记录包含 `ts`、`event`、`action`、`result`、`actor_id`、`actor_role`、`actor_type`、`account_id`、`target_type`、`target_id`、`request_id`、`trace_id`、`source_ip`、`user_agent`、`error_code`。`action` 取事件名末段，`result` 为 `success`/`failure`，主体类型为 `user`/`admin`/`root`/`service`。
- 未知身份、缺失上下文、成功时的 `error_code` 使用 `null`，不把请求中自报的值当已认证身份。ROOT 记录还含路由模板、HTTP 方法/状态，高风险操作标记 `sensitive=true`；root API key 使用 `actor_id=root-key`，不虚构用户或账号。
- 日志副本和可查询的持久审计事件使用同一组安全字段，并核实已持久化；best-effort 事件发布或 Usage/Audit HTTP 记录不等于安全事件已落库。审计设施失败记安全的 `ERROR`，不回滚业务操作。

## 6. 合入前检查

- 级别符合实际结果；重试、兜底和批次降级可见；最终失败只记录一次。
- 消息、字段、异常和嵌套对象不含禁止值，ID 来自可信上下文。
- 对修改的 Python 日志运行 Ruff `G` 检查；它检查调用写法，不判断级别语义或内容安全。
- 复用现有回归测试，必要时调整日志断言；避免新增测试文件和 Mock 调用测试。失败路径可用临时输出捕获验证。
- 初始化/原生适配改动须检查实际输出：UTC 与 Asia/Shanghai、重定向/非 TTY、重复初始化/重载、多 worker、不同阈值。验证时间、级别、字段、单行格式、重复记录和上下文隔离；bootstrap print 单独验收。
