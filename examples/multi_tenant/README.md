# OpenViking Multi-Tenant 示例

演示 OpenViking 多租户管理功能：账户创建、用户注册、角色管理、Key 管理、数据访问。

## 架构

```
                        Admin API (ROOT key)
                       ┌─────────────────────────┐
                       │  Create/Delete Accounts  │
                       │  Register/Remove Users   │
                       │  Set Roles, Regen Keys   │
                       └────────┬────────────────┘
                                │
                                ▼
┌──────────┐  User Key   ┌──────────────────┐  Root Key   ┌──────────┐
│  Alice   │ ──────────► │  OpenViking      │ ◄────────── │  Admin   │
│  (ADMIN) │             │  Server          │             │  (ROOT)  │
└──────────┘             │                  │             └──────────┘
┌──────────┐  User Key   │  ov.conf:        │
│  Bob     │ ──────────► │  root_api_key    │
│  (USER)  │             └──────────────────┘
└──────────┘
```

## 认证体系

| Key 类型 | 创建方式 | 角色 | 能力 |
|----------|---------|------|------|
| Root Key | `ov.conf` 中配置 | ROOT | 全部操作 + Admin API |
| User Key | Admin API 创建 | ADMIN 或 USER | 按 account 访问 |

| 角色 | 作用域 | 能力 |
|------|--------|------|
| ROOT | 全局 | 全部操作 + 创建/删除 account、管理用户 |
| ADMIN | 所属 account | 常规操作 + 管理本 account 的用户 |
| USER | 所属 account | 常规操作（ls、read、find、sessions 等） |

## Quick Start

### 1. 配置 Server

复制配置文件并填入你的模型 API Key：

```bash
cp ov.conf.example ov.conf
# 编辑 ov.conf，填入 embedding 和 vlm 的 api_key
```

关键配置项——`root_api_key` 启用多租户认证：

```json
{
  "server": {
    "root_api_key": "my-root-key"
  }
}
```

不配置 `root_api_key` 时，认证禁用，所有请求以 ROOT 身份访问（开发模式）。

### 2. 启动 Server

```bash
# 方式一：指定配置文件
python -m openviking serve --config ./ov.conf

# 方式二：放到默认路径
cp ov.conf ~/.openviking/ov.conf
python -m openviking serve

# 验证
curl http://localhost:1933/health
# {"status": "ok"}
```

### 3. 运行示例

**Python SDK：**

```bash
# 安装依赖
uv sync

# 运行（使用默认参数）
uv run admin_workflow.py

# 自定义参数
uv run admin_workflow.py --url http://localhost:1933 --root-key my-root-key
```

**CLI：**

```bash
# 运行（使用默认参数）
bash admin_workflow.sh

# 自定义参数
ROOT_KEY=my-root-key SERVER=http://localhost:1933 bash admin_workflow.sh
```

## 示例流程

两个示例（Python SDK 和 CLI）覆盖完全相同的流程：

```
 1. Health Check              无需认证，验证服务可用
 2. Create Account            ROOT 创建 account "acme"，同时创建首个 admin "alice"
 3. Register User (ROOT)      ROOT 在 "acme" 下注册普通用户 "bob"
 4. Register User (ADMIN)     alice (ADMIN) 在 "acme" 下注册用户 "charlie"
 5. List Accounts             ROOT 列出所有 account
 6. List Users                列出 "acme" 下所有用户及角色
 7. Change Role               ROOT 将 bob 提升为 ADMIN
 8. Regenerate Key            为 charlie 重新生成 key，旧 key 立即失效
 9. Access Data               bob 使用 user key 访问数据
10. Remove User               删除 charlie，验证其 key 失效
11. Delete Account            删除 account "acme"，验证 alice 的 key 也失效
```

## CLI 命令参考

```bash
# Account 管理
openviking admin create-account <account_id> --admin <admin_user_id>
openviking admin list-accounts
openviking admin delete-account <account_id>

# User 管理
openviking admin register-user <account_id> <user_id> [--role user|admin]
openviking admin list-users <account_id>
openviking admin remove-user <account_id> <user_id>
openviking admin set-role <account_id> <user_id> <role>
openviking admin regenerate-key <account_id> <user_id>
```

## 文件说明

```
admin_workflow.py    Python SDK 示例（httpx 调用 Admin API + SyncHTTPClient 访问数据）
admin_workflow.sh    CLI 示例（openviking admin 命令，同等流程）
ov.conf.example      Server 配置文件模板（含 root_api_key）
pyproject.toml       项目依赖
README.md            本文件
```

## Admin API 参考

| 方法 | 端点 | 所需角色 | 说明 |
|------|------|---------|------|
| POST | `/api/v1/admin/accounts` | ROOT | 创建 account + 首个 admin |
| GET | `/api/v1/admin/accounts` | ROOT | 列出所有 account |
| DELETE | `/api/v1/admin/accounts/{id}` | ROOT | 删除 account |
| POST | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | 注册用户 |
| GET | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | 列出用户 |
| DELETE | `/api/v1/admin/accounts/{id}/users/{uid}` | ROOT, ADMIN | 移除用户 |
| PUT | `/api/v1/admin/accounts/{id}/users/{uid}/role` | ROOT | 修改用户角色 |
| POST | `/api/v1/admin/accounts/{id}/users/{uid}/key` | ROOT, ADMIN | 重新生成 user key |

## 相关文档

- [认证指南](../../docs/zh/guides/04-authentication.md) - 完整认证说明
- [配置指南](../../docs/zh/guides/01-configuration.md) - 配置文件参考
- [API 概览](../../docs/zh/api/01-overview.md) - 完整 API 参考
- [Server-Client 示例](../server_client/) - 基础 Server/Client 用法
