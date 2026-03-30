# OpenViking API 自动化测试

本目录包含 OpenViking 的 API 集成测试套件。

## 目录结构

```
tests/api_test/
├── admin/              # 管理 API 测试
├── api/                # API 客户端实现
├── conftest.py         # pytest fixtures 和配置
├── filesystem/         # 文件系统 API 测试
├── health_check/       # 健康检查测试
├── pytest.ini          # pytest 配置
├── requirements.txt    # 测试依赖
├── resources/          # 资源管理 API 测试
├── retrieval/          # 检索 API 测试
├── scenarios/          # 场景级集成测试
├── services/           # 服务管理模块
├── sessions/           # 会话 API 测试
├── system/             # 系统 API 测试
└── tools/              # 工具模块
```

## 本地运行测试

### 前置条件

1. Python 3.10+
2. OpenViking Server 已启动（默认端口 1933）

### 安装依赖

```bash
cd tests/api_test
pip install -r requirements.txt
```

### 运行测试

#### 一键本地测试（推荐）

使用提供的脚本模拟完整的 CI 流水线流程：

```bash
cd tests/api_test
./local-test.sh
```

这个脚本会自动：
1. 检查 Python 版本
2. 安装 OpenViking
3. 安装测试依赖
4. 启动 OpenViking Server（自动找可用端口）
5. 运行所有 API 测试
6. 停止服务并清理

#### 手动运行测试

```bash
# 运行所有测试
python -m pytest . -v

# 运行特定模块测试
python -m pytest admin/ -v
python -m pytest filesystem/ -v
python -m pytest sessions/ -v

# 运行特定测试文件
python -m pytest health_check/test_server_health_check.py -v

# 生成 HTML 报告
python -m pytest . -v --html=api-test-report.html --self-contained-html
```

### 环境变量配置

测试通过环境变量配置，无需修改代码：

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `SERVER_HOST` | OpenViking Server 主机 | 127.0.0.1 |
| `SERVER_PORT` | OpenViking Server 端口 | 1933 |
| `OPENVIKING_API_KEY` | API 密钥 | test-root-api-key |
| `VLM_API_KEY` | VLM 模型密钥（可选） | - |
| `EMBEDDING_API_KEY` | Embedding 模型密钥（可选） | - |

示例：

```bash
export SERVER_PORT=1934
export VLM_API_KEY=your-vlm-key
export EMBEDDING_API_KEY=your-embedding-key
python -m pytest retrieval/ -v
```

## CI/CD 流水线

### 工作流文件

`.github/workflows/api_test.yml` - API 集成测试流水线

### 流水线特性

- ✅ 智能构建复用：只在依赖变更时重新构建
- ✅ 并发安全：同一 PR 自动取消旧的运行
- ✅ 动态端口：自动查找可用端口避免冲突
- ✅ Secrets 支持：安全传递 API 密钥

### 配置 GitHub Secrets

为了运行完整的检索测试，需要在仓库中配置以下 Secrets：

1. 进入仓库 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**
3. 添加以下 Secrets：

| Secret 名称 | 说明 |
|------------|------|
| `VLM_API_KEY` | VLM 模型 API 密钥 |
| `EMBEDDING_API_KEY` | Embedding 模型 API 密钥 |

## 测试覆盖范围

### 接口测试

| 模块 | 测试用例数 | 说明 |
|------|----------|------|
| admin | 6 | 账户、用户、角色、密钥管理 |
| filesystem | 10 | 文件系统操作 |
| health_check | 1 | 服务健康检查 |
| resources | 3 | 资源管理 |
| retrieval | 4 | 搜索和检索 |
| sessions | 6 | 会话管理 |
| system | 4 | 系统管理 |

## 注意事项

1. **不要提交敏感信息**：`.env` 和 `ov.conf` 已在 `.gitignore` 中
2. **检索测试需要密钥**：`retrieval/` 和部分 `scenarios/` 测试需要 VLM 和 Embedding API 密钥
3. **CI 与本地一致**：CI 流水线使用与本地相同的测试框架和配置

## 相关文档

- OpenViking API 文档：`docs/zh/api/`
- CI/CD 配置：`.github/workflows/api_test.yml`
