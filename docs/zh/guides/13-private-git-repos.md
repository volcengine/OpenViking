# 私有 Git 仓库

默认情况下，`ov add-resource <url>` 支持 GitHub 和 GitLab 的**公开**仓库。
若要访问**私有**仓库，需要提供个人访问令牌（PAT）。
OpenViking 支持三种提供令牌的方式，按优先级从高到低依次判断。

## 令牌解析顺序

| 优先级 | 来源 | 适用范围 |
|--------|------|---------|
| 1（最高）| CLI `--token` 参数 | 单次命令 |
| 2 | `GITHUB_TOKEN` / `GITLAB_TOKEN` 环境变量 | 该 Host 下的所有仓库 |
| 3（最低）| `~/.openviking/ovcli.conf` 中的 `git_credentials` | 该 Host 下的所有仓库 |

## 方式一 — 命令行直接传入令牌

```bash
ov add-resource https://github.com/my-org/private-repo --token ghp_xxxxxxxxxxxx
```

`--token` 参数由 Python CLI 包装器在 Rust 二进制运行前处理，令牌不会出现在
进程列表或日志中。

## 方式二 — 环境变量

在 Shell 会话（或 CI/CD 环境）中导出令牌：

```bash
# GitHub
export GITHUB_TOKEN=ghp_xxxxxxxxxxxx
ov add-resource https://github.com/my-org/private-repo

# GitLab
export GITLAB_TOKEN=glpat-xxxxxxxxxxxx
ov add-resource https://gitlab.com/my-group/private-project
```

对于 `github.com` 和 `gitlab.com`，系统会自动读取对应的环境变量。
自托管实例请使用方式三配置凭据。

## 方式三 — 在 `ovcli.conf` 中持久化存储凭据

使用交互式向导一次性保存令牌：

```bash
ov configure git-credentials
```

系统将提示输入主机名和令牌：

```
Host (e.g. github.com): github.com
Token: ghp_xxxxxxxxxxxx
Credentials saved to ~/.openviking/ovcli.conf
```

保存后，该 Host 下的所有 `ov add-resource` 命令将自动使用该令牌。

### 手动编辑配置

也可以直接编辑 `~/.openviking/ovcli.conf`：

```jsonc
{
  // ... 其他配置 ...
  "git_credentials": {
    "github.com": "ghp_xxxxxxxxxxxx",
    "gitlab.com": "glpat-xxxxxxxxxxxx",
    "gitlab.example.com": "glpat-xxxxxxxxxxxx"
  }
}
```

键名为纯主机名，不含端口、路径或协议前缀。

## 自托管实例

自托管的 GitHub Enterprise 和 GitLab 均受支持，使用实际主机名作为键名：

```bash
ov configure git-credentials
# Host: git.corp.example.com
# Token: <your-PAT>

ov add-resource https://git.corp.example.com/team/repo
```

## 创建个人访问令牌

### GitHub

1. 进入 **Settings → Developer settings → Personal access tokens → Tokens (classic)**。
2. 点击 **Generate new token (classic)**。
3. 勾选 `repo` 范围（`add-resource` 仅需读权限）。
4. 复制令牌 — 令牌仅在创建时显示一次。

GitHub 细粒度令牌（Fine-grained tokens）同样支持，授予目标仓库的 **Contents: Read** 权限即可。

### GitLab

1. 进入 **User Settings → Access Tokens**。
2. 点击 **Add new token**。
3. 勾选 `read_repository` 范围。
4. 复制令牌。

## 安全说明

- 存储在 `ovcli.conf` 中的令牌为明文，建议设置合适的文件权限：`chmod 600 ~/.openviking/ovcli.conf`。
- `--token` 参数在 Rust 二进制运行前从参数列表中移除，不会出现在 `ps` 输出中。
- 令牌不会通过任何 API 请求字段发送到 OpenViking 服务端；它仅在克隆或下载归档期间临时注入仓库 URL。
- 克隆完成后，OpenViking 会对存储在元数据中的远程 URL 进行脱敏处理，令牌不会持久化到上下文数据库中。

## 相关文档

- [配置参考](01-configuration.md) — 完整的 `ovcli.conf` 字段说明
- [部署指南](03-deployment.md) — 在 CI/CD 中运行 OpenViking
- [数据加密](08-encryption.md) — 静态数据加密
