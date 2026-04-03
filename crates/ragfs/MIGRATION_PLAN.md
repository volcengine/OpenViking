# RAGFS Migration Plan
# Rust 实现的 AGFS 重构计划

**文档版本**: v1.0
**创建日期**: 2026-04-03
**目标**: 将 third_party/agfs (Go 实现迁移到 crates/ragfs (Rust 实现)) 以支持后续扩展
**策略**: 允许 Go/Rust 双实现并存，支持自由切换和回退
**致谢**: 谢谢 c44pt0r 的 AGFS 项目贡献了开源的 Go 实现，为本计划提供基础，我们会持续保持对原项目的 License 引用。

---

## 目录

1. [概述](#概述)
2. [架构设计](#架构设计)
3. [迁移阶段](#迁移阶段)
4. [纯 Rust 插件系统设计](#纯rust-插件系统设计)
5. [Go/Rust 切换机制](#go-rust-切换机制)
6. [技术选型](#技术选型)
7. [风险评估](#风险评估)
8. [里程碑](#里程碑)

---

## 概述

### 背景

当前 AGFS 完全使用 Go 实现，包含约 33,000 行代码、14 种内置插件。随着 OpenViking 项目引入 Rust 生态（ov_cli），使用 Rust 重写 AGFS 可以：

- 提升性能（无 GC，编译时优化）
- 增强安全性（内存安全保证）
- 统一技术栈（与 ov_cli 一致，移除项目对 Golang 的依赖）
- 简化部署（无需编译 Go 代码）

### 设计原则

1. **渐进式迁移**: Go 和 Rust 版本可同时存在，独立运行
2. **API 兼容性**: HTTP API 和 Python SDK 保持完全兼容
3. **纯 Rust 插件**: 使用 trait 定义插件系统，不依赖 C ABI
4. **可观测性**: 完善的日志、指标和追踪支持，文件目录结构尽量与 Go 实现保持一致
5. **测试驱动**: 每个模块都有对应的测试用例，确保功能一致

### 项目结构

```
crates/ragfs/
├── Cargo.toml              # Workspace 配置
├── MIGRATION_PLAN.md       # 本文档
├── src/
│   ├── lib.rs             # 库入口，导出公共 API
│   ├── core/              # 核心文件系统抽象
│   │   ├── mod.rs
│   │   ├── filesystem.rs  # FileSystem trait
│   │   ├── mountable.rs   # MountableFS 实现
│   │   ├── handle.rs      # 文件句柄管理
│   │   └── errors.rs     # 错误类型定义
│   ├── server/            # HTTP 服务器
│   │   ├── mod.rs
│   │   ├── main.rs       # 服务器入口
│   │   ├── config.rs     # 配置管理
│   │   ├── handlers.rs   # HTTP 处理器
│   │   └── middleware.rs # 中间件
│   ├── shell/             # 交互式 shell
│   │   ├── mod.rs
│   │   └── main.rs
│   └── plugins/           # 内置插件
│       ├── mod.rs
│       ├── memfs/
│       ├── kvfs/
│       ├── queuefs/
│       ├── s3fs/
│       ├── sqlfs/
│       └── ...
└── tests/                # 集成测试
```

---

## 架构设计

### 核心组件

```
┌─────────────────────────────────────────────────────────────┐
│                    HTTP Client / SDK                      │
└────────────────────────┬────────────────────────────────────┘
                     │ HTTP/JSON API
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                    HTTP Server                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │  Router (axum)                                 │   │
│  │  - /api/v1/files                               │   │
│  │  - /api/v1/directories                         │   │
│  │  - /api/v1/mounts                              │   │
│  │  - /api/v1/plugins                              │   │
│  └────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                    MountableFS                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │  Radix Trie (路径路由)                          │   │
│  │  /memfs    -> MemFS Plugin                      │   │
│  │  /kvfs     -> KVFS Plugin                       │   │
│  │  /queuefs  -> QueueFS Plugin                    │   │
│  └────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
      ┌──────┐   ┌──────┐   ┌──────┐
      │MemFS │   │KVFS  │   │QueueFS│
      └──────┘   └──────┘   └──────┘
```

### 核心数据结构

#### FileSystem Trait

```rust
/// 文件系统抽象接口
#[async_trait]
pub trait FileSystem: Send + Sync {
    /// 创建空文件
    async fn create(&self, path: &str) -> Result<()>;

    /// 创建目录
    async fn mkdir(&self, path: &str, mode: u32) -> Result<()>;

    /// 删除文件
    async fn remove(&self, path: &str) -> Result<()>;

    /// 递归删除
    async fn remove_all(&self, path: &str) -> Result<()>;

    /// 读取文件内容
    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>>;

    /// 写入文件
    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64>;

    /// 列出目录
    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>>;

    /// 获取文件信息
    async fn stat(&self, path: &str) -> Result<FileInfo>;

    /// 重命名
    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()>;

    /// 修改权限
    async fn chmod(&self, path: &str, mode: u32) -> Result<()>;
}
```

#### ServicePlugin Trait

```rust
/// 服务插件接口
#[async_trait]
pub trait ServicePlugin: Send + Sync {
    /// 插件名称
    fn name(&self) -> &str;

    /// 验证配置
    async fn validate(&self, config: &PluginConfig) -> Result<()>;

    /// 初始化插件
    async fn initialize(&self, config: PluginConfig) -> Result<Box<dyn FileSystem>>;

    /// 获取文档
    fn get_readme(&self) -> &str;

    /// 获取配置参数定义
    fn get_config_params(&self) -> Vec<ConfigParameter>;

    /// 关闭插件
    async fn shutdown(&self) -> Result<()>;
}
```

---

## 迁移阶段

### 阶段 1: 基础设施 (2-3 周) ✅ 已完成

**目标**: 建立项目骨架和核心抽象

#### 任务清单

- [x] 创建 Cargo.toml 和项目结构
- [x] 定义 `FileSystem` trait (`src/core/filesystem.rs`)
- [x] 定义 `ServicePlugin` trait (`src/core/plugin.rs`)
- [x] 定义错误类型 (`src/core/errors.rs`)
- [x] 定义文件信息结构 (`src/core/types.rs`)
- [x] 创建配置模块 (`src/server/config.rs`)
- [x] 编写基础单元测试

#### 验收标准

- ✅ 可以编译 `ragfs` 库
- ✅ 所有 trait 有清晰的文档和测试
- ✅ 错误处理完善

---

### 阶段 2: MountableFS 核心实现 (2 周) ✅ 已完成

**目标**: 实现核心的挂载文件系统

#### 任务清单

- [x] 实现 Radix Trie 路由 (使用 `radix_trie` crate)
- [x] 实现 `MountableFS` 结构体
- [x] 实现插件注册机制
- [x] 实现路径解析和路由 (`find_mount`)
- [x] 实现所有 `FileSystem` 方法的委托
- [x] 实现并发安全 (使用 `Arc<RwLock<>>`)
- [x] 编写并发测试

#### 对应 Go 代码

- `third_party/agfs/agfs-server/pkg/mountablefs/mountablefs.go` (967 行)

#### 验收标准

- ✅ 可以动态挂载/卸载插件
- [x] 高并发场景下无数据竞争
- [x] 所有文件系统操作正确委托到挂载点

---

### 阶段 3: HTTP 服务器 (2 周) ✅ 已完成

**目标**: 实现与 Go 版本兼容的 HTTP API

#### 任务清单

- [x] 实现 axum 路由
- [x] 实现文件操作处理器 (`/api/v1/files`)
- [x] 实现目录操作处理器 (`/api/v1/directories`)
- [x] 实现挂载管理处理器 (`/api/v1/mount`, `/api/v1/unmount`)
- [x] 实现健康检查 (`/api/v1/health`)
- [x] 实现日志中间件 (tower TraceLayer)
- [ ] 实现指标收集
- [x] 添加 CORS 支持

#### API 兼容性

必须与 Go 版本保持完全兼容的端点：

```
GET    /api/v1/files?path=...           # 读取文件
PUT    /api/v1/files?path=...           # 写入文件
POST   /api/v1/files?path=...           # 创建文件
DELETE /api/v1/files?path=...           # 删除文件
GET    /api/v1/stat?path=...            # 获取元数据

GET    /api/v1/directories?path=...     # 列出目录
POST   /api/v1/directories?path=...     # 创建目录

GET    /api/v1/mounts                   # 列出挂载点
POST   /api/v1/mount                   # 挂载插件
POST   /api/v1/unmount                 # 卸载插件

GET    /api/v1/health                  # 健康检查
```

#### 验收标准

- ✅ 所有 API 端点可访问
- ✅ API 响应格式与 Go 版本一致
- ✅ 可以使用现有的 Python SDK 和 agfs-shell 连接

---

### 阶段 4: 基础插件 - MemFS (1 周) ✅ 已完成

**目标**: 实现最简单的内存文件系统

#### 任务清单

- [x] 实现 `MemFSPlugin` 结构体
- [x] 实现内存存储 (使用 `Arc<RwLock<HashMap<>>>`)
- [x] 实现所有文件系统操作
- [x] 编写完整的测试用例
- [ ] 添加性能基准测试

#### 对应 Go 代码

- `third_party/agfs/agfs-server/pkg/plugins/memfs/memfs.go`
- `third_party/agfs/agfs-server/pkg/plugins/memfs/memoryfs.go`

#### 验收标准

- ✅ 可以创建/读取/写入/删除文件
- ✅ 可以列出目录
- ✅ 数据存储在内存中，重启后丢失
- [ ] 性能与 Go 版本相当或更好

---

### 阶段 5: 基础插件 - KVFS (1 周) ✅ 已完成

**目标**:**: 实现键值存储文件系统

#### 任务清单

- [x] 实现 `KVFSPlugin` 结构体
- [x] 实现键值存储后端 (内存)
- [x] 实现文件名到键的映射
- [ ] 实现持久化（可选）

#### 对应 Go 代码

- `third_party/agfs/agfs-server/pkg/plugins/kvfs/`

#### 验收标准

- ✅ 写入操作将文件内容作为值存储
- ✅ 读取操作返回对应键的值
- ✅ 列出目录返回所有键

---

### 阶段 6: 基础插件 - QueueFS (1-2 周) ✅ 已完成

**目标**: 实现消息队列文件系统

#### 任务清单

- [x] 实现 `QueueFSPlugin` 结构体
- [x] 实现队列后端 (内存 VecDeque)
- [x] 实现控制文件:
  - `enqueue`: 写入消息
  - `dequeue`: 读取并移除消息
  - `peek`: 查看但不移除
  - `size`: 获取队列长度
  - `clear`: 清空队列
- [x] 实现消息 ID 生成 (UUID)
- [x] 编写并发测试 (多生产者/多消费者)

#### 对应 Go 代码

- `third_party/agfs/agfs-server/pkg/plugins/queuefs/`

#### 验收标准

- ✅ 多个写入者可以并发入队
- ✅ 多个读取者可以并发出队
- ✅ 消息不丢失、不重复
- ✅ 性能满足实际使用需求

---

### 阶段 7: 高级插件 🔄 进行中

**目标**: 实现生产环境必需的插件

#### S3FS ✅ 已完成

- [x] 集成 AWS SDK for Rust (`aws-sdk-s3`, feature-gated under `s3`)
- [x] 实现文件上传/下载 (get_object, put_object, get_object_range)
- [x] 实现目录列举 (list_objects with prefix/delimiter, pagination)
- [x] 支持大文件分片上传 (batch delete 1000 per call)
- [x] S3Client wrapper (client.rs): 全面支持 AWS S3/MinIO/LocalStack/TOS
- [x] Dual-layer LRU cache (cache.rs): ListDirCache (30s TTL) + StatCache (60s TTL, 5x capacity)
- [x] S3FileSystem: 完整 FileSystem trait 实现
- [x] S3FSPlugin: 13 个配置参数, validate, readme
- [x] 3 种 Directory Marker Modes: none/empty/nonempty (TOS 兼容)
- [x] Feature-gated: `cargo build --features s3` (不影响无 S3 需求的构建)
- [x] 9 个单元测试 (cache + path normalization + plugin validate)

#### SQLFS ✅ 已完成

- [x] 使用 `rusqlite`
- [x] 支持 SQLite (MySQL/TiDB 预留接口)
- [x] 实现文件元数据存储
- [x] 实现文件数据存储 (数据库 BLOB)
- [x] LRU 缓存 (目录列表)
- [x] Mutex<Connection> 线程安全
- [x] 17 个单元测试

#### ProxyFS

- [ ] 实现对远程 AGFS 的代理
- [ ] 实现请求转发
- [ ] 实现连接池

#### 其他插件

- [ ] HTTPFS (提供文件下载服务)
- [ ] StreamFS (流式数据)
- [ ] HeartbeatFS (心跳监控)
- [ ] LocalFS (本地文件系统挂载)

---

### 阶段 8: 配置系统 ⏳ 部分完成

**目标**: 支持与 Go 版本兼容的 YAML 配置

#### 任务清单

- [x] 定义配置结构体 (使用 `serde`)
- [x] 实现配置文件加载 (`config.yaml`)
- [x] 支持环境变量覆盖
- [x] 实现配置验证 (基础)
- [ ] 提供示例配置文件
- [ ] 支持从 YAML 配置自动挂载插件

#### 配置格式 (兼容 Go 版本)

```yaml
server:
  address: ":8080"
  log_level: "info"

plugins:
  memfs:
    enabled: true
    path: "/memfs"

  kvfs:
    enabled: true
    path: "/kvfs"

  queuefs:
    enabled: true
    path: "/queuefs"
```

---

### 阶段 9: Shell 客户端 (2 周)

**目标**: 实现交互式 shell (可选)

#### 任务清单

- [ ] 实现 REPL (使用 `rustyline`)
- [ ] 实现内置命令 (`ls`, `cat`, `echo`, `mkdir`, `rm`, 等)
- [ ] 实现命令补全
- [ ] 支持脚本执行
- [ ] 添加颜色输出

---

### 阶段 10: 测试与优化 (2-3 周)

**目标**: 完善测试覆盖和性能优化

#### 任务清单

- [ ] 编写集成测试 (端到端)
- [ ] 性能基准测试 (与 Go 版本对比)
- [ ] 压力测试 (高并发场景)
- [ ] 内存泄漏检测
- [ ] 代码覆盖率 > 80%
- [ ] 文档完善

---

## 纯 Rust 插件系统设计

### 设计理念

1. **类型安全**: 使用 trait 确保编译时类型检查
2. **零抽象成本**: 没有虚函数调用开销 (通过 monomorphization)
3. **异步优先**: 所有操作都是异步的
4. **易于测试**: 插件可以 mock 和单元测试

### 插件接口

```rust
/// 插件配置参数元数据
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigParameter {
    pub name: String,
    pub r#type: String,  // "string", "int", "bool", "string_list"
    pub required: bool,
    pub default: Option<String>,
    pub description: String,
}

/// 插件配置值
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginConfig {
    pub name: String,
    pub mount_path: String,
    pub params: HashMap<String, ConfigValue>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum ConfigValue {
    String(String),
    Int(i64),
    Bool(bool),
    StringList(Vec<String>),
}

/// 服务插件 trait
#[async_trait]
pub trait ServicePlugin: Send + Sync + 'static {
    /// 插件唯一名称
    fn name(&self) -> &str;

    /// 插件版本
    fn version(&self) -> &str {
        "0.1.0"
    }

    /// 插件描述
    fn description(&self) -> &str {
        ""
    }

    /// 获取 README 文档
    fn readme(&self) -> &str;

    /// 验证配置
    async fn validate(&self, config: &PluginConfig) -> Result<()>;

    /// 初始化插件，返回文件系统实例
    async fn initialize(
        &self,
        config: PluginConfig
    ) -> Result<Box<dyn FileSystem>>;

    /// 关闭插件
    async fn shutdown(&self) -> Result<()>;

    /// 获取支持的配置参数
    fn config_params(&self) -> &[ConfigParameter];
}

/// 插件注册表
pub struct PluginRegistry {
    plugins: HashMap<String, Box<dyn ServicePlugin>>,
}

impl PluginRegistry {
    pub fn new() -> Self {
        Self {
            plugins: HashMap::new(),
        }
    }

    /// 注册插件
    pub fn register<P: ServicePlugin + 'static>(&mut self, plugin: P) {
        let name = plugin.name().to_string();
        self.plugins.insert(name, Box::new(plugin));
    }

    /// 获取插件
    pub fn get(&self, name: &str) -> Option<&dyn ServicePlugin> {
        self.plugins.get(name).map(|p| p.as_ref())
    }

    /// 列出所有插件
    pub fn list(&self) -> Vec<&str> {
        self.plugins.keys().map(|s| s.as_str()).collect()
    }
}
```

### 插件实现示例: MemFS

```rust
use crate::core::{FileSystem, ServicePlugin, PluginConfig, ConfigParameter};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

/// MemFS 插件
pub struct MemFSPlugin;

impl ServicePlugin for MemFSPlugin {
    fn name(&self) -> &str {
        "memfs"
    }

    fn readme(&self) -> &str {
        r#"MemFS - In-memory File System

A simple file system that stores data in memory. All data is lost
when the server restarts.

Usage:
  echo "hello" > /memfs/test.txt
  cat /memfs/test.txt
"#
    }

    async fn validate(&self, config: &PluginConfig) -> Result<()> {
        // MemFS 没有必需的配置参数
        Ok(())
    }

    async fn initialize(
        &self,
        _config: PluginConfig
    ) -> Result<Box<dyn FileSystem>> {
        Ok(Box::new(MemFileSystem::new()))
    }

    async fn shutdown(&self) -> Result<()> {
        Ok(())
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &[]
    }
}

/// MemFS 实现文件系统
struct MemFileSystem {
    files: Arc<RwLock<HashMap<String, Vec<u8>>>>,
}

impl MemFileSystem {
    fn new() -> Self {
        Self {
            files: Arc::new(RwLock::new(HashMap::new())),
        }
    }
}

#[async_trait]
impl FileSystem for MemFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let mut files = self.files.write().await;
        files.insert(path.to_string(), vec![]);
        Ok(())
    }

    async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
        let files = self.files.read().await;
        files.get(path)
            .cloned()
            .ok_or_else(|| Error::NotFound(path.to_string()))
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let mut files = self.files.write().await;
        let file = files.entry(path.to_string()).or_insert_with(Vec::new);

        let offset = offset as usize;
        if offset >= file.len() {
            file.resize(offset, 0);
        }

        let write_end = offset + data.len();
        file[offset..write_end].copy_from_slice(data);
        Ok(data.len() as u64)
    }

    // ... 其他方法实现
}
```

---

## Go/Rust 切换机制

### 策略

Go 和 Rust 版本作为独立进程运行，通过以下方式切换：

1. **环境变量控制**
   ```bash
   export AGFS_IMPL=rust  # 使用 Rust 版本
   export AGFS_IMPL=go    # 使用 Go 版本 (默认)
   ```

2. **统一入口脚本**
   ```bash
   # scripts/agfs-server
   if [[ "$AGFS_IMPL" == "rust" ]]; then
       cargo run --bin ragfs-server -- "$@"
   else
       go run ./third_party/agfs/agfs-server/cmd/server/main.go "$@"
   fi
   ```

3. **Makefile 目标**
   ```makefile
   # 运行 Go 版本
   run-agfs-go:
       cd third_party/agfs/agfs-server && make run

   # 运行 Rust 版本
   run-agfs-rust:
       cargo run --bin ragfs-server

   # 运行 (根据 AGFS_IMPL 环境变量)
   run-agfs:
       @echo "Running AGFS (impl=$(AGFS_IMPL))..."
       @if [ "$(AGFS_IMPL)" = "rust" ]; then \
           cargo run --bin ragfs-server; \
       else \
           cd third_party/agfs/agfs-server && make run; \
       fi
   ```

### 兼容性保证

#### 1. API 兼容

两个版本必须实现完全相同的 HTTP API，包括：
- 请求格式
- 响应格式
- 错误码
- 超时行为

#### 2. 配置兼容

使用相同的 `config.yaml` 格式，Rust 版本可以读取 Go 版本的配置。

#### 3. SDK 兼容

Python SDK 应该能够无缝连接到任一版本。

### 回退机制

如果 Rust 版本出现问题，可以通过以下方式快速回退：

```bash
# 立即切换回 Go 版本
export AGFS_IMPL=go

# 重启服务
systemctl restart agfs
```

### A/B 测试

支持同时运行两个版本进行对比：

```bash
# 在不同端口运行
cargo run --bin ragfs-server -- --port 8081
cd third_party/agfs/agfs-server && go run ./cmd/server --port 8080

# 运行对比测试
./scripts/compare_servers.sh http://localhost:8080 http://localhost:8081
```

---

## 技术选型

### 运行时与网络

| 组件 | Go 实现 | Rust 选择 | 理由 |
|------|---------|-----------|------|
| HTTP 服务器 | net/http | axum | 基于 tower 生态，类型安全，性能优秀 |
| 异步运行时 | goroutines | tokio | 最成熟，生态完善 |
| HTTP 客户端 | net/http | reqwest/hyper | 与 tokio 集成良好 |

### 数据结构

| 组件 | Go 实现 | Rust 选择 | 理由 |
|------|---------|-----------|------|
| 并发控制 | sync.RWMutex | tokio::sync::RwLock | 异步安全 |
| 路径路由 | go-immutable-radix | radix_trie | 无锁读取，性能好 |
| UUID | google/uuid | uuid (serde) | 功能完整 |

### 序列化与配置

| 组件 | Go 实现 | Rust 选择 | 理由 |
|------|---------|-----------|------|
| JSON | encoding/json | serde_json | serde 生态，编译时检查 |
| YAML | gopkg.in/yaml.v3 | serde_yaml | 基于 serde |
| TOML | - | toml (可选) | 配置文件可选格式 |

### 数据库

| 组件 | Go 实现 | Rust 选择 | 理由 |
|------|---------|-----------|------|
| SQLite | mattn/go-sqlite3 | rusqlite | 纯 Rust，无 CGO |
| SQL 通用 | - | sqlx | 编译时查询检查 |

### 云服务

| 组件 | Go 实现 | Rust 选择 | 理由 |
|------|---------|-----------|------|
| AWS SDK | aws-sdk-go-v2 | aws-sdk-rust | 官方支持，性能好 |
| S3 | aws-sdk-go-v2/service/s3 | aws-s3 | 简化的 S3 客户端 |

### 日志与追踪

| 组件 | Go 实现 | Rust 选择 | 理由 |
|------|---------|-----------|------|
| 日志 | logrus | tracing | 结构化日志，与 tokio 集成 |
| 追踪 | - | tracing-opentelemetry | OpenTelemetry 支持 |

### 开发工具

| 组件 | Go 实现 | Rust 选择 | 理由 |
|------|---------|-----------|------|
| CLI 解析 | flag | clap | 功能强大，derive 宏 |
| 测试 | testing | built-in + criterion | 内置测试 + 性能基准 |
| 格式化 | gofmt | rustfmt | 官方工具 |
| Linting | golangci-lint | clippy | 编译器内建 |

---

## 风险评估

### 高风险

1. **插件系统设计变更**
   - 风险: 从 C ABI 改为 Rust trait，外部插件需要重写
   - 缓解: 提供 Go 版本插件作为参考，提供迁移指南

2. **性能回归**
   - 风险: 初始实现可能性能不如 Go 版本
   - 缓解: 并行开发，进行性能对比和优化

3. **API 不兼容**
   - 风险: 实现细节差异导致行为不同
   - 缓解: 使用相同的测试套件测试两个版本

### 中风险

1. **异步编程复杂度**
   - 风险: Tokio 异步模型比 goroutine 更复杂
   - 缓解: 团队培训，使用成熟的模式

2. **学习曲线**
   - 风险: 团队成员不熟悉 Rust
   - 缓解: 提供培训，结对编程

3. **依赖更新**
   - 风险: Rust 生态快速变化，API 可能变动
   - 缓解: 使用稳定版本，定期更新

### 低风险

1. **测试覆盖**
   - 风险: 重写时遗漏测试
   - 缓解: 测试驱动开发，代码评审

2. **文档缺失**
   - 风险: 代码复杂但文档不完善
   - 缓解: 要求所有公共 API 有文档

---

## 里程碑

### Milestone 0.1: MVP (4 周) ✅ 已完成

**目标**: 可以运行的最小可用版本

- [x] 基础设施和核心 trait
- [x] MountableFS 实现
- [x] HTTP 服务器
- [x] MemFS 插件
- [x] API 兼容性验证

**验收**: 可以运行 Rust 版本服务器，与 Python SDK 交互

**完成情况**:
- `core/` 模块完整: filesystem.rs, mountable.rs, plugin.rs, errors.rs, types.rs
- MountableFS 支持动态 mount/unmount，路径路由，并发安全
- HTTP 服务器 (axum): 完整 REST API (files/directories/mounts/health)
- MemFS 插件: 完整文件系统操作 + 6 个测试
- 所有 62 个单元测试通过

---

### Milestone 0.2: 基础插件完整 (2 周) ✅ 已完成

**目标**: 完成所有基础插件

- [x] KVFS — 内存键值存储，支持嵌套 key，6 个测试
- [x] QueueFS — 基于控制文件的消息队列 (enqueue/dequeue/peek/size/clear)，UUID 消息 ID，并发安全，8 个测试
- [x] 基础配置系统 — CLI args (clap) + YAML 配置文件加载 + 环境变量

**验收**: 可以使用所有基础插件功能 ✅

---

### Milestone 0.3: 生产就绪 (4 周) 🔄 进行中

**目标**: 可以在生产环境使用

- [x] SQLFS — SQLite 后端，Mutex<Connection> 线程安全，LRU 缓存，5MB 文件限制，17 个测试
- [x] S3FS — AWS SDK for Rust, S3/MinIO/TOS 兼容, dual-layer cache, feature-gated, 9 个测试
- [ ] 完善的日志和指标
- [ ] 完整的测试覆盖
- [ ] 文档完善

**当前进展**:
- SQLFS 已完成并通过所有测试 (backend.rs + cache.rs + mod.rs)
- S3FS 已完成: client.rs + cache.rs + mod.rs, feature-gated under `s3`
- SQLFSPlugin 和 S3FSPlugin 已注册到 server/main.rs
- 全部 71 个单元测试通过 (含 s3 feature)
- 下一步: 完善日志/指标、测试覆盖、文档

**验收**: 可以在生产环境部署并切换

---

### Milestone 1.0: 功能完整 (8 周) 🔄 进行中

**目标**: 功能与 Go 版本对等

- [x] 提供 Python wrapper (ragfs-python)，用于 OpenViking 内联集成
- [ ] 支持切换和功能回滚，将默认实现切换为 Rust 版本

**当前进展**:
- ragfs-python crate 已完成 (crates/ragfs-python/): PyO3 native binding
- RAGFSBindingClient 类，API 兼容 Go AGFSBindingClient
- 支持所有核心操作: ls/read/write/create/mkdir/rm/stat/mv/chmod/touch
- 支持 mount/unmount/mounts 插件管理
- 所有内置插件可用: memfs, kvfs, queuefs, sqlfs
- maturin develop 构建集成
- openviking/pyagfs/__init__.py 已更新: Rust 优先 -> Go fallback
- Python 端到端测试全部通过 (memfs + sqlfs + kvfs + queuefs)

---

## 参考资源

### Go 源代码

- Server: `third_party/agfs/agfs-server/`
- SDK: `third_party/agfs/agfs-sdk/`
- Shell: `third_party/agfs/agfs-shell/`
- FUSE: `third_party/agfs/agfs-fuse/`

### Rust 生态

- axum: https://docs.rs/axum/latest/axum/
- tokio: https://tokio.rs/
- sqlx: https://docs.rs/sqlx/latest/sqlx/
- aws-sdk-rust: https://github.com/awslabs/aws-sdk-rust

### 相关项目

- Riker: https://github.com/riker-rs/riker (Actor 模型)
- async-std: https://async.rs/ (替代 tokio 的选择)

---

## 更新日志

| 日期 | 版本 | 变更内容 |
|------|------|---------|
| 2026-04-03 | v1.0 | 初始计划创建 |
| 2026-04-03 | v1.1 | 标注 Milestone 0.1/0.2 完成，阶段 1-6 完成；SQLFS 修复 18 个编译错误并通过所有测试；开始 Milestone 0.3 |
| 2026-04-03 | v1.2 | S3FS 完成并通过 MinIO 端到端验证；ragfs-python PyO3 binding 完成 (Milestone 1.0 开始) |

---

## 贡献

本计划是动态文档，随着项目进展持续更新。更新时请：

1. 在更新日志中记录变更
2. 更新相关章节
3. 同步到团队

---

## 联系方式

如有问题或建议，请联系 OpenViking 团队。
