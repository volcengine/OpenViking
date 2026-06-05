# OpenViking 文件系统挂载模块

这个模块将 OpenViking 的虚拟文件系统挂载到本地文件系统路径，让用户可以像操作普通文件一样操作 OpenViking 上的数据。

这个模块只是一个实验功能，并没有被实际使用


## 功能特性

- **文件系统范式**: 将 OpenViking 的 `viking://` URI 映射到本地文件路径
- **多作用域支持**: 支持 resources、session、user 等多种作用域挂载
- **挂载管理**: 支持多个挂载点的生命周期管理
- **语义搜索**: 通过文件系统路径进行语义搜索
- **层级内容访问**: 支持 L0 (abstract)、L1 (overview)、L2 (details) 三层内容访问

## 快速开始

### 基本使用

```python
from vikingbot.openviking_mount import OpenVikingMount, MountConfig, MountScope
from pathlib import Path

# 创建挂载配置
config = MountConfig(
    mount_point=Path("./my_openviking_mount"),
    openviking_data_path=Path("./my_openviking_data"),
    scope=MountScope.RESOURCES,
    auto_init=True,
    read_only=False
)

# 使用上下文管理器
with OpenVikingMount(config) as mount:
    # 列出目录
    files = mount.list_dir(mount.config.mount_point)
    for f in files:
        print(f"{f.name} ({'目录' if f.is_dir else '文件'})")
    
    # 读取文件
    content = mount.read_file(mount.config.mount_point / "some_file.md")
    print(content)
    
    # 获取摘要和概览
    abstract = mount.get_abstract(mount.config.mount_point / "some_dir")
    overview = mount.get_overview(mount.config.mount_point / "some_dir")
    print(f"摘要: {abstract}")
    print(f"概览: {overview}")
    
    # 语义搜索
    results = mount.search("什么是 OpenViking")
    for r in results:
        print(f"{r.uri}")
```

### 使用挂载管理器

```python
from vikingbot.openviking_mount import OpenVikingMountManager, get_mount_manager
from pathlib import Path

# 获取全局管理器
manager = get_mount_manager()

# 创建资源挂载
mount = manager.create_resources_mount(
    mount_id="my_resources",
    openviking_data_path=Path("./ov_data")
)

# 为会话创建挂载
session_mount = manager.create_session_mount(
    session_id="session_123",
    openviking_data_path=Path("./ov_data")
)

# 列出所有挂载
mounts = manager.list_mounts()
for m in mounts:
    print(f"{m['id']} -> {m['mount_point']}")

# 获取挂载
mount = manager.get_mount("my_resources")

# 移除挂载
manager.remove_mount("my_resources", cleanup=True)
```

## 目录结构

```
vikingbot/openviking_mount/
├── __init__.py          # 模块入口，导出公共API
├── mount.py             # 核心挂载实现 (OpenVikingMount)
└── manager.py           # 挂载管理器 (OpenVikingMountManager)
```

## API 参考

### OpenVikingMount

主要的挂载类，提供文件系统操作。

#### 初始化参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `config` | `MountConfig` | 挂载配置对象 |

#### MountConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mount_point` | `Path` | 必填 | 挂载点路径 |
| `openviking_data_path` | `Path` | 必填 | OpenViking 数据存储路径 |
| `session_id` | `Optional[str]` | `None` | 会话 ID（session 作用域时需要） |
| `scope` | `MountScope` | `RESOURCES` | 挂载作用域 |
| `auto_init` | `bool` | `True` | 是否自动初始化 |
| `read_only` | `bool` | `False` | 是否只读模式 |

#### MountScope 枚举

| 值 | 说明 |
|----|------|
| `RESOURCES` | 只挂载资源目录 |
| `SESSION` | 只挂载会话目录 |
| `USER` | 只挂载用户目录 |
| `ALL` | 挂载所有作用域 |

#### 主要方法

| 方法 | 说明 |
|------|------|
| `initialize()` | 初始化 OpenViking 客户端 |
| `list_dir(path)` | 列出目录内容 |
| `read_file(path)` | 读取文件内容 |
| `write_file(path, content)` | 写入文件内容 |
| `mkdir(path)` | 创建目录 |
| `delete(path, recursive)` | 删除文件/目录 |
| `get_abstract(path)` | 获取 L0 摘要 |
| `get_overview(path)` | 获取 L1 概览 |
| `search(query, target_path)` | 语义搜索 |
| `add_resource(source_path, target_path)` | 添加资源 |
| `sync_to_disk(path)` | 同步到磁盘 |
| `close()` | 关闭挂载 |

### OpenVikingMountManager

挂载管理器，管理多个挂载点的生命周期。

#### 主要方法

| 方法 | 说明 |
|------|------|
| `create_mount(mount_id, ...)` | 创建新挂载 |
| `get_mount(mount_id)` | 获取挂载 |
| `list_mounts()` | 列出所有挂载 |
| `remove_mount(mount_id, cleanup)` | 移除挂载 |
| `remove_all(cleanup)` | 移除所有挂载 |
| `create_session_mount(session_id, ...)` | 为会话创建挂载 |
| `create_resources_mount(mount_id, ...)` | 创建资源挂载 |

### 全局函数

| 函数 | 说明 |
|------|------|
| `get_mount_manager(base_mount_dir)` | 获取全局挂载管理器单例 |

## 路径映射

OpenViking URI 到本地文件路径的映射规则：

```
OpenViking URI                    本地路径
-------------------               ------------------
viking://resources/foo     ->    {mount_point}/resources/foo
viking://session/bar       ->    {mount_point}/session/bar
viking://user/baz          ->    {mount_point}/user/baz
```

## 测试

运行测试：

```bash
cd /Users/bytedance/workspace/openviking/bot
.venv/bin/python test_openviking_mount.py
```

## 注意事项

1. **直接写入限制**: OpenViking 主要通过 `add_resource` 添加外部资源，直接文件写入需要特殊处理
2. **性能考虑**: 大量文件操作可能影响性能，建议批量处理
3. **数据同步**: `sync_to_disk` 是一个简化实现，生产环境可能需要更复杂的同步机制
4. **只读模式**: 设置 `read_only=True` 可以防止意外修改

## 下一步

- 集成到 vikingbot 的 SessionManager 中
- 添加 FUSE 支持实现真正的文件系统挂载
- 实现更完善的双向同步机制
- 添加更多测试用例
