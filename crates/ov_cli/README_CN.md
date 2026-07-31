# OpenViking CLI

[OpenViking](https://github.com/volcengine/OpenViking) 的命令行客户端。OpenViking 是面向 Agent 的上下文数据库。

本目录构建原生 `ov` 二进制。你可以用它配置 OpenViking 连接、导入资源、浏览 `viking://` 路径、检索上下文、查看服务状态、管理 session，以及执行管理员操作。

English documentation: [README.md](README.md).

## 安装

### 通过 npm 安装

```bash
npm i -g @openviking/cli
```

npm 包会安装适配 macOS、Linux 或 Windows 的平台二进制。

### 从源码安装

```bash
# OpenViking 要求 Rust >= 1.91.1。
cargo install --path crates/ov_cli
```

如果你正在本 crate 内开发：

```bash
cd crates/ov_cli
cargo install --path .
```

## 配置

推荐使用交互式配置管理器：

```bash
ov config
```

`ov config` 可以新增、编辑、删除、校验和切换配置。CLI 会把当前 active 客户端配置写到 `~/.openviking/ovcli.conf`。命名配置会保存为 `~/.openviking/ovcli.conf.<name>`，`ov config switch <name>` 会把选中的命名配置复制为 active 配置。

较新的 CLI 版本在非交互式 shell 中运行大多数命令前，需要先保存显示语言：

```bash
ov language en
# 或
ov language zh-CN
```

脚本和 Agent 场景建议使用确定性的配置命令，并通过 stdin 或已有环境变量传递密钥：

```bash
# OpenViking Service
printf '%s' "$OPENVIKING_API_KEY" | \
  ov config add ov-service --name prod --api-key-stdin --activate -o json

# 本地无鉴权自定义服务
ov config add custom --name local --url http://127.0.0.1:1933 --activate -o json

# 使用 user API key 的远程自定义服务
printf '%s' "$OPENVIKING_API_KEY" | \
  ov config add custom --name remote --url https://ov.example.com --api-key-stdin --activate -o json
```

验证 active 配置：

```bash
ov config show
ov config list -o json
ov config validate
ov health
ov status
```

`ov config show` 会隐藏密钥。除非你明确知道 `~/.openviking/ovcli.conf` 可能包含 API Key，否则不要直接打印原始配置文件。

### 手动配置文件

仍然支持手动编辑配置。一个最小的自定义服务配置示例如下：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-api-key",
  "account": "acme",
  "user": "alice"
}
```

使用普通 user API key 时，`account` 和 `user` 通常可以省略，因为服务端可以从 key 推导身份。使用 `trusted` 鉴权或租户级操作时，建议显式配置它们。仅 root key 的配置必须显式配置 `account` 和 `user`，因为 root key 本身不包含租户身份。

更完整的配置说明见 [docs/zh/getting-started/05-cli-setup.md](../../docs/zh/getting-started/05-cli-setup.md)。

## 快速开始

```bash
# 检查连接
ov health
ov status

# 添加资源并等待处理完成
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/docs/en/about/01-about-us.md --wait

# 浏览上下文
ov ls viking://resources
ov tree viking://resources -L 2
ov read viking://resources/...

# 检索上下文
ov find "what is openviking"
ov grep "openviking" --uri viking://resources
```

当前安装版本的准确命令面以 `ov --help` 和 `ov <command> --help` 为准。

## 命令分组

### 资源管理

- `add-resource` - 导入本地文件、目录、URL、Git 仓库和支持的文档源。
- `add-skill` - 从目录、`SKILL.md` 或原始内容添加 skill。
- `skills` - 列出、检索、查看、更新、删除和校验已安装 skills。
- `export` / `import` - 以 `.ovpack` 格式导出或导入上下文。
- `backup` / `restore` - 把公共 OpenViking scope 备份或恢复为 restore-only `.ovpack`。

### 文件系统

- `ls` - 列出目录内容。
- `tree` - 显示目录树。
- `mkdir` - 创建目录。
- `rm` - 删除资源或目录。
- `mv` - 移动或重命名资源。
- `stat` - 查看资源元数据。
- `attrs` - 获取逻辑扩展属性。
- `get` - 下载文件到本地路径。

### 内容访问

- `read` - 读取 L2 全量内容。
- `abstract` - 读取 L0 摘要。
- `overview` - 读取 L1 概览。
- `write` - 替换、追加或创建文本内容。

### 搜索

- `find` - 语义检索。
- `search` - 上下文感知检索，实验特性。
- `grep` - 内容模式搜索。
- `glob` - 文件 glob 搜索。

### Session 与记忆

- `session new` - 创建 session。
- `session list` - 列出 sessions。
- `session get` - 查看 session 详情。
- `session get-session-context` - 获取合并后的 session 上下文。
- `session add-message` / `session add-messages` - 向 session 添加消息。
- `session commit` - 归档消息并抽取记忆。
- `add-memory` - 一次性创建 session、添加消息并提交，实验特性。

### 交互式工具

- `tui` - 交互式文件浏览器。
- `chat` - 与 vikingbot agent 对话。

### 状态与可观测性

- `health` - 快速健康检查。
- `status` - 聚合服务组件状态。
- `wait` - 等待异步处理队列完成。
- `task status` / `task list` - 跟踪异步任务。
- `task watch` - 管理自动刷新 watch 任务。
- `observer queue` - 队列状态。
- `observer vikingdb` - VikingDB 状态。
- `observer models` - VLM、embedding 和 rerank 模型状态。
- `observer retrieval` - 检索质量指标。
- `observer fs` - 文件系统操作指标。
- `observer system` - 整体系统状态。

### 配置

- `config` - 交互式配置管理器。
- `config show` - 显示 active 配置并隐藏密钥。
- `config validate` - 校验 active 配置。
- `config list` - 列出命名配置。
- `config switch` - 切换 active 配置。
- `config add` - 非交互式新增命名配置。
- `config edit` - 非交互式编辑命名配置。
- `config delete` - 删除命名配置。
- `language` / `lang` - 选择 CLI 显示语言（`en` 或 `zh-CN`）。
- `version` - 显示 CLI 版本。

### 工作区快照

- `snapshot commit` - 创建工作区快照。
- `snapshot restore` - 把路径或工作区恢复到历史快照。
- `snapshot show` - 显示 commit 元数据或 blob 内容。
- `snapshot log` - 查看快照历史。
- `snapshot ignore-get` / `snapshot ignore-set` / `snapshot ignore-delete` - 管理 account `.ovgitignore`。

### 关系与隐私

- `relations` - 列出资源关系，实验特性。
- `link` - 创建关系链接，实验特性。
- `unlink` - 删除关系链接，实验特性。
- `privacy` - 管理隐私配置分类、目标、版本和 active 配置。

### 管理员命令

需要 `root_api_key` 的命令使用 `--sudo`。

- `admin create-account` - 创建 account 和首个 admin 用户。
- `admin list-accounts` - 列出 accounts，仅 ROOT。
- `admin delete-account` - 删除 account，仅 ROOT。
- `admin register-user` - 注册用户。
- `admin list-users` - 列出 account 内用户。
- `admin remove-user` - 移除用户。
- `admin set-role` - 修改用户角色，仅 ROOT。
- `admin regenerate-key` - 轮转用户 API key。
- `admin migrate` - 迁移 legacy agent/session 数据，仅 ROOT。
- `system` - 管理类系统工具命令。
- `reindex` - 为 URI 重建语义和向量产物。

## 输出格式

默认输出是面向人的表格或卡片渲染。脚本中建议使用 JSON：

```bash
ov -o json ls viking://resources
ov -o json config list
```

部分帮助文本也可能展示长参数 `--output json`。`-o json` 是测试和自动化示例中常用的紧凑写法。

## 示例

```bash
# 添加 URL 并等待处理完成
ov add-resource https://example.com/docs --wait --timeout 60

# 添加本地目录并过滤文件
ov add-resource ./dir \
  --wait --timeout 600 \
  --ignore-dirs "node_modules,dist" \
  --include "*.md,*.py" \
  --exclude "*.tmp,*.log"

# 导入到可预测的父路径
ov add-resource ./docs -p "viking://resources/docs/{calendar:today}" --wait

# 带过滤条件的搜索
ov find "API authentication" --threshold 0.7 --limit 5
ov find "authentication" --uri viking://resources/project --level 0,1

# 递归列目录
ov ls viking://resources --recursive

# 临时通过 CLI 参数覆盖身份
ov --account acme --user alice ls viking://

# 使用 root API key 执行管理员命令
ov --sudo admin create-account acme --admin alice --seed alice-seed
ov admin register-user acme bob --role user --seed bob-seed
ov admin regenerate-key acme bob --seed bob-new-seed

# Glob 搜索
ov glob "**/*.md" --uri viking://resources

# Session 工作流
SESSION=$(ov -o json session new | jq -r '.result.session_id')
ov session add-message --session-id "$SESSION" --role user --content "Hello"
ov session commit --session-id "$SESSION"

# Watch 任务管理
ov add-resource https://example.com/docs --to viking://resources/docs --watch-interval 60
ov task watch ls
ov task watch trigger viking://resources/docs
```

## 开发

```bash
# 构建
cargo build --release

# 使用刚构建出的精确二进制做 smoke
target/release/ov --version
target/release/ov -o json health

# 运行测试
cargo test

# 本地安装
cargo install --path .
```

驱动外部 e2e harness 时，请显式指向 `target/release/ov`，避免误用 `PATH` 中已经安装的旧版 `ov`。
