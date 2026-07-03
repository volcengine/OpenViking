# OpenViking Go SDK

Go SDK 是面向 OpenViking Server 的 HTTP 客户端，作为独立 Go module 放在主仓库 `sdk/go` 下。

```bash
go get github.com/volcengine/OpenViking/sdk/go
```

## 初始化

```go
client, err := openviking.NewClient(openviking.Config{
    BaseURL: "http://localhost:1933",
    APIKey:  "your-key",
    Timeout: 120 * time.Second,
})
if err != nil {
    log.Fatal(err)
}
defer client.CloseIdleConnections()
```

Go SDK 发送的身份请求头与 Python HTTP client 一致：

| Config 字段 | HTTP Header |
|-------------|-------------|
| `APIKey` | `X-API-Key` |
| `Account` | `X-OpenViking-Account` |
| `User` | `X-OpenViking-User` |
| `ActorPeerID` | `X-OpenViking-Actor-Peer` |

普通 `api_key` 部署下只需要设置 `APIKey`，服务端会从 API key 推导 account/user 身份。只有在 trusted 部署或网关显式透传租户身份时，才需要设置 `Account` 和 `User`。

Go SDK 仅支持 HTTP 模式，不支持 Python embedded 模式，也不保留旧 `agent_id` 兼容路径。

## 已实现接口

| 模块 | Go 方法 |
|------|---------|
| 资源和技能导入 | `AddResource`, `AddSkill`, `WaitProcessed` |
| 技能管理 | `ListSkills`, `FindSkills`, `ValidateSkill`, `GetSkill`, `UpdateSkill`, `DeleteSkill` |
| Watch 管理 | `ListWatches`, `GetWatch`, `UpdateWatch`, `DeleteWatch`, `TriggerWatch` |
| 文件系统和内容 | `List`, `Tree`, `Stat`, `Attrs`, `Mkdir`, `Remove`, `Move`, `Read`, `Abstract`, `Overview`, `Write`, `SetTags`, `Reindex` |
| 检索 | `Find`, `Search`, `Grep`, `Glob` |
| 会话和任务 | `CreateSession`, `ListSessions`, `GetSession`, `SessionExists`, `GetSessionContext`, `GetSessionArchive`, `DeleteSession`, `AddMessage`, `BatchAddMessages`, `CommitSession`, `GetTask`, `ListTasks` |
| OVPack | `ExportOVPack`, `BackupOVPack`, `ImportOVPack`, `RestoreOVPack` |
| 系统和 observer | `Health`, `CheckConsistency`, `GetStatus`, `IsHealthy`, `QueueStatus`, `VikingDBStatus`, `ModelsStatus` |
| 管理接口 | `AdminCreateAccount`, `AdminCreateAccountWithOptions`, `AdminListAccounts`, `AdminDeleteAccount`, `AdminRegisterUser`, `AdminRegisterUserWithOptions`, `AdminListUsers`, `AdminRemoveUser`, `AdminSetRole`, `AdminRegenerateKey`, `AdminRegenerateKeyWithOptions`, `AdminMigrate` |

## 暂未实现接口

Go SDK v1 的边界是对齐当前 Python HTTP client，不覆盖所有 server 路由。

| 模块 | 原因 |
|------|------|
| Python embedded 模式 | Go SDK 是纯 HTTP SDK。 |
| 旧 `agent_id` 兼容 | 新 SDK 只使用 `ActorPeerID`。 |
| Privacy config 路由 | 当前属于 server-only 管理面，Python HTTP client 未公开。 |
| Metrics endpoint | Prometheus 文本抓取端点，不是标准 JSON SDK API。 |
| Console/debug/backend-sync/session tool-result 等端点 | 属于运维或 server-only 能力，未纳入 Python HTTP client parity。 |

## 管理用户配置

创建用户时如需写入初始服务端用户配置，使用 options 版本。普通 add 调用不需要 SDK 侧默认值；省略 `To` / `TargetURI`，让服务端解析用户和部署默认值。

```go
seed := "alice-seed"
_, err := client.AdminRegisterUserWithOptions(ctx, "acme", "alice", "user", &openviking.AdminRegisterUserOptions{
    Seed: &seed,
    UserConfig: map[string]any{
        "add_targets": map[string]any{
            "resource_uri": "viking://user/resources/project-a",
            "skill_uri":    "viking://user/skills",
        },
    },
})

newSeed := "alice-new-seed"
_, err = client.AdminRegenerateKeyWithOptions(ctx, "acme", "alice", &openviking.AdminRegenerateKeyOptions{
    Seed: &newSeed,
})
```

传入 `Seed` 时，返回的 API Key 会基于 `sha256(user_id + "\0" + seed)` 生成；省略时仍使用随机生成逻辑。
使用 `nil` 表示不传 `Seed`；传入字符串指针表示显式发送 seed，包括会被服务端拒绝的空字符串。

## 技能和 Watch 示例

```go
_, err := client.AddSkill(ctx, "./skills/search-web", &openviking.AddSkillOptions{
    Wait: true,
})

skills, err := client.ListSkills(ctx, nil)
found, err := client.FindSkills(ctx, "search the web", &openviking.FindSkillsOptions{
    Limit: 5,
})
_, _ = skills, found
```

`AddResource` 在 `WatchInterval > 0` 时会创建 watch；已有任务可用专用 watch 方法管理：

```go
watches, err := client.ListWatches(ctx, &openviking.ListWatchesOptions{
    ActiveOnly: true,
})
updated, err := client.UpdateWatch(ctx, openviking.UpdateWatchOptions{
    ToURI:         "viking://resources/docs",
    WatchInterval: openviking.Float64(30),
    IsActive:      openviking.Bool(true),
})
triggered, err := client.TriggerWatch(ctx, openviking.WatchRef{
    ToURI: "viking://resources/docs",
})
_, _, _ = watches, updated, triggered
```

## 验证脚本

编辑 `examples/basic_usage/main.go` 顶部常量：

```go
const (
    baseURL = "http://localhost:1933"
    apiKey  = "your-key"
)
```

运行：

```bash
cd sdk/go
go run ./examples/basic_usage
```

脚本会创建一个临时 Markdown 文件，导入为 OpenViking resource，读取并更新内容，执行语义检索，验证 watch 和 skill 管理接口，然后创建多消息 session、commit、轮询记忆抽取任务，并检索用户记忆和 peer-scoped 记忆。

## 测试

```bash
cd sdk/go
go test ./...
```
