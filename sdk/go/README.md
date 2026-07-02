# OpenViking Go SDK

The Go SDK is an HTTP client for a running OpenViking server. It lives in the
main OpenViking repository as an independent Go module.

```bash
go get github.com/volcengine/OpenViking/sdk/go
```

## Client Setup

```go
package main

import (
	"context"
	"fmt"
	"log"
	"time"

	openviking "github.com/volcengine/OpenViking/sdk/go"
)

func main() {
	ctx := context.Background()

	client, err := openviking.NewClient(openviking.Config{
		BaseURL: "http://localhost:1933",
		APIKey:  "your-key",
		Timeout: 120 * time.Second,
	})
	if err != nil {
		log.Fatal(err)
	}
	defer client.CloseIdleConnections()

	ok, err := client.Health(ctx)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("healthy:", ok)
}
```

The client sends the same identity headers as the Python HTTP client:

| Config field | HTTP header |
|--------------|-------------|
| `APIKey` | `X-API-Key` |
| `Account` | `X-OpenViking-Account` |
| `User` | `X-OpenViking-User` |
| `ActorPeerID` | `X-OpenViking-Actor-Peer` |

For the common `api_key` deployment mode, `APIKey` is enough because the server
derives account and user identity from the key. Set `Account` and `User` only
for trusted deployments or gateways where the upstream explicitly forwards
tenant identity through OpenViking headers.

This SDK is HTTP-only. It does not implement Python embedded mode or legacy
`agent_id` compatibility.

## Common Operations

```go
// Add a local file or remote URL. Local files/directories are uploaded first.
resource, err := client.AddResource(ctx, "./docs/readme.md", &openviking.AddResourceOptions{
	To:   "viking://resources/docs",
	Wait: true,
})

// Read and update content.
content, err := client.Read(ctx, "viking://resources/docs/readme.md", 0, -1)
updated, err := client.Write(ctx, "viking://resources/docs/readme.md", content+"\n\nUpdated.", &openviking.WriteOptions{
	Mode: "replace",
	Wait: true,
})

// Find relevant context.
results, err := client.Find(ctx, "how do I configure auth?", &openviking.FindOptions{
	TargetURI:   "viking://resources/docs",
	Limit:       10,
	ContextType: []string{"resource"},
})
for _, item := range results.Resources {
	fmt.Println(item.URI, item.Score)
}

// Work with sessions.
session, err := client.CreateSession(ctx, &openviking.CreateSessionOptions{
	SessionID: "demo-session",
})
_, err = client.AddMessage(ctx, "demo-session", "user", openviking.AddMessageOptions{
	Content: openviking.String("remember this deployment decision"),
})
commit, err := client.CommitSession(ctx, "demo-session", &openviking.CommitSessionOptions{
	KeepRecentCount: 2,
})

_, _, _ = resource, updated, session
_ = commit
```

## API Coverage

The Go SDK v1 intentionally follows the Python HTTP client surface.

Implemented:

| Area | Go methods |
|------|------------|
| Resource and skill import | `AddResource`, `AddSkill`, `WaitProcessed` |
| Skill management | `ListSkills`, `FindSkills`, `ValidateSkill`, `GetSkill`, `UpdateSkill`, `DeleteSkill` |
| Watch management | `ListWatches`, `GetWatch`, `UpdateWatch`, `DeleteWatch`, `TriggerWatch` |
| Filesystem and content | `List`, `Tree`, `Stat`, `Attrs`, `Mkdir`, `Remove`, `Move`, `Read`, `Abstract`, `Overview`, `Write`, `SetTags`, `Reindex` |
| Retrieval | `Find`, `Search`, `Grep`, `Glob` |
| Sessions and tasks | `CreateSession`, `ListSessions`, `GetSession`, `SessionExists`, `GetSessionContext`, `GetSessionArchive`, `DeleteSession`, `AddMessage`, `BatchAddMessages`, `CommitSession`, `GetTask`, `ListTasks` |
| Packs | `ExportOVPack`, `BackupOVPack`, `ImportOVPack`, `RestoreOVPack` |
| System and observer | `Health`, `CheckConsistency`, `GetStatus`, `IsHealthy`, `QueueStatus`, `VikingDBStatus`, `ModelsStatus` |
| Admin | `AdminCreateAccount`, `AdminCreateAccountWithOptions`, `AdminListAccounts`, `AdminDeleteAccount`, `AdminRegisterUser`, `AdminRegisterUserWithOptions`, `AdminListUsers`, `AdminRemoveUser`, `AdminSetRole`, `AdminRegenerateKey`, `AdminRegenerateKeyWithOptions`, `AdminMigrate` |

Not implemented in Go SDK v1:

| Area | Reason |
|------|--------|
| Python embedded mode | Go SDK is HTTP-only. |
| Legacy `agent_id` compatibility | New SDKs use `ActorPeerID` only. |
| Privacy config routes | Server-only management surface today; not in Python HTTP client. |
| Metrics endpoint | Prometheus text scrape endpoint, not a JSON SDK API. |
| Console/debug/backend-sync/session tool-result endpoints | Operational or server-only endpoints outside Python HTTP client parity. |

## Admin User Config

Use the options variants when creating users with initial server-side user
config. Ordinary add calls do not need SDK defaults; omit `To` / `TargetURI`
and let the server resolve user and deployment defaults.

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

When `Seed` is set, the returned API key is derived from
`sha256(user_id + "\0" + seed)`; omit it for random key generation.
Use `nil` to omit `Seed`; set `Seed` to a string pointer to send it, including
an empty string that the server rejects.

## Files, Directories, and Packs

`AddResource` and `AddSkill` accept local files and directories. Directory
uploads are zipped by the SDK, symlinks are skipped, and the resulting archive
is uploaded to `/api/v1/resources/temp_upload` before the final API call.

```go
_, err := client.AddSkill(ctx, "./skills/search-web", &openviking.AddSkillOptions{
	Wait: true,
})

skills, err := client.ListSkills(ctx, nil)
found, err := client.FindSkills(ctx, "search the web", &openviking.FindSkillsOptions{
	Limit: 5,
})
_, _ = skills, found

exported, err := client.ExportOVPack(ctx, "viking://resources/docs", "./backups", nil)
restored, err := client.RestoreOVPack(ctx, exported, &openviking.ImportPackOptions{
	OnConflict: "overwrite",
})

_, _ = exported, restored
```

## Watch Management

`AddResource` creates a watch when `WatchInterval` is greater than zero.
The dedicated watch methods manage existing tasks:

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

## Error Handling

OpenViking API errors return `*openviking.Error`.

```go
_, err := client.Read(ctx, "viking://resources/missing.md", 0, -1)
if openviking.IsCode(err, "NOT_FOUND") {
	fmt.Println("missing resource")
}

var apiErr *openviking.Error
if errors.As(err, &apiErr) {
	fmt.Println(apiErr.Code, apiErr.StatusCode, apiErr.Details)
}
```

## Test

```bash
cd sdk/go
go test ./...
```

## Smoke Test Against a Server

Edit the constants at the top of `examples/basic_usage/main.go`, then run:

```bash
cd sdk/go
go run ./examples/basic_usage
```

The script creates a temporary Markdown file, imports it as an OpenViking
resource, reads and updates it, runs semantic retrieval, exercises watch and
skill management APIs, then creates a multi-message session, commits it,
polls the memory extraction task, and searches both user and peer-scoped
memories.
