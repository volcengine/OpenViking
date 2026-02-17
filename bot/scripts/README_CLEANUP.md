# VikingBot 一键清理脚本

## 使用方法

### 交互式清理（推荐）
```bash
python scripts/clean_vikingbot.py
```
这会逐个询问确认是否删除每个目录。

### 直接删除（不确认）
```bash
python scripts/clean_vikingbot.py --yes
# 或
python scripts/clean_vikingbot.py -y
```

### 预览模式（不实际删除）
```bash
python scripts/clean_vikingbot.py --dry-run
```
这会显示将要删除的内容，但不会实际删除。

## 清理内容

脚本会清理以下内容：

| 目录/文件 | 说明 |
|-----------|------|
| `sessions/` | 会话文件 |
| `workspace/` | 工作空间文件（会重新初始化） |
| `cron/` | 定时任务数据 |
| `sandboxes/` | 沙箱数据 |
| `bridge/` | Bridge数据 |

**保留**: 
- `config.json` 配置文件不会被删除
- `config copy*.json` 配置备份文件不会被自动删除（会显示但不删除）

## 清理后的效果

清理后：
- 配置文件 `config.json` 保留
- 下次运行 `vikingbot agent` 或 `vikingbot gateway` 时：
  - workspace目录会自动重新创建
  - 模板文件会从源码自动复制
  - 会话重新开始

## 示例

### 完整清理
```bash
# 先预览
python scripts/clean_vikingbot.py --dry-run

# 确认没问题后，直接清理
python scripts/clean_vikingbot.py --yes
```

### 选择性清理
```bash
# 交互式，只删除你想删的
python scripts/clean_vikingbot.py
# 对每个问题回答 y 或 n
```