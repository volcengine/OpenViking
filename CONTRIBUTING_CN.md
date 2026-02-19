# 贡献指南

感谢你对 OpenViking 感兴趣！我们欢迎各种形式的贡献：

- 报告 Bug
- 提交功能请求
- 改进文档
- 贡献代码

---

## 开发环境设置

### 前置要求

- **Python**: 3.10+
- **Go**: 1.25.1+ (构建 AGFS 组件需要)
- **C++ 编译器**: GCC 9+ 或 Clang 11+ (构建核心扩展需要，必须支持 C++17)
- **CMake**: 3.12+

### 1. Fork 并克隆

```bash
git clone https://github.com/YOUR_USERNAME/openviking.git
cd openviking
```

### 2. 安装依赖

我们推荐使用 `uv` 进行 Python 环境管理：

```bash
# 安装 uv (如果尚未安装)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 同步依赖并创建虚拟环境
uv sync --all-extras
source .venv/bin/activate  # Linux/macOS
# 或者 .venv\Scripts\activate  # Windows
```

### 3. 配置环境

创建配置文件 `~/.openviking/ov.conf`：

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_key": "your-api-key",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

设置环境变量：

```bash
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf
```

### 4. 验证安装

```python
import asyncio
import openviking as ov

async def main():
    client = ov.AsyncOpenViking(path="./test_data")
    await client.initialize()
    print("OpenViking initialized successfully!")
    await client.close()

asyncio.run(main())
```

---

## 项目结构

```
openviking/
├── pyproject.toml        # 项目配置
├── third_party/          # 第三方依赖
│   └── agfs/             # AGFS 文件系统
│
├── openviking/           # Python SDK
│   ├── async_client.py   # AsyncOpenViking 客户端
│   ├── sync_client.py    # SyncOpenViking 客户端
│   │
│   ├── core/             # 核心数据模型
│   │   ├── context.py    # 上下文基类
│   │   └── directories.py # 目录定义
│   │
│   ├── parse/            # 资源解析器
│   │   ├── parsers/      # 解析器实现
│   │   ├── tree_builder.py
│   │   └── registry.py
│   │
│   ├── retrieve/         # 检索系统
│   │   ├── retriever.py  # 主检索器
│   │   ├── reranker.py   # 重排序
│   │   └── intent_analyzer.py
│   │
│   ├── session/          # 会话管理
│   │   ├── session.py    # 会话核心
│   │   └── compressor.py # 压缩
│   │
│   ├── storage/          # 存储层
│   │   ├── viking_fs.py  # VikingFS
│   │   └── vectordb/     # 向量数据库
│   │
│   ├── utils/            # 工具类
│   │   └── config/       # 配置
│   │
│   └── prompts/          # 提示词模板
│
├── tests/                # 测试套件
└── docs/                 # 文档
    ├── en/               # 英文文档
    └── zh/               # 中文文档
```

---

## 代码风格

我们使用以下工具来保持代码一致性：

| 工具 | 用途 | 配置 |
|------|---------|--------|
| **Ruff** | Linting, 格式化, 导入排序 | `pyproject.toml` |
| **mypy** | 类型检查 | `pyproject.toml` |

### 自动检查（推荐）

我们使用 [pre-commit](https://pre-commit.com/) 在每次提交前自动运行这些检查。这确保您的代码无需手动努力即可符合标准。

1. **安装 pre-commit**：
   ```bash
   pip install pre-commit
   ```

2. **安装 git hooks**：
   ```bash
   pre-commit install
   ```

现在，当您运行 `git commit` 时，`ruff`（检查和格式化）将自动运行。如果任何检查失败，它可能会自动修复文件。您只需添加更改并再次提交即可。

### 运行检查

```bash
# 格式化代码
ruff format openviking/

# Lint 检查
ruff check openviking/

# 类型检查
mypy openviking/
```

### 风格指南

1. **行宽**：100 字符
2. **缩进**：4 个空格
3. **字符串**：推荐使用双引号
4. **类型提示**：鼓励但不强制
5. **Docstrings**：公共 API 必须包含（最多 1-2 行）

---

## 测试

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_parser.py

# 运行并生成覆盖率报告
pytest --cov=openviking --cov-report=term-missing

# 详细输出模式运行
pytest -v
```

### 编写测试

将测试文件放在 `tests/` 目录下，命名为 `test_*.py`：

```python
# tests/test_client.py
import pytest
from openviking import AsyncOpenViking

class TestAsyncOpenViking:
    @pytest.mark.asyncio
    async def test_initialize(self, tmp_path):
        client = AsyncOpenViking(path=str(tmp_path / "data"))
        await client.initialize()
        assert client._viking_fs is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_add_resource(self, tmp_path):
        client = AsyncOpenViking(path=str(tmp_path / "data"))
        await client.initialize()

        result = await client.add_resource(
            "./test.md",
            reason="test document"
        )
        assert result["status"] == "success"
        assert "root_uri" in result

        await client.close()
```

---

## 贡献流程

### 1. 创建分支

```bash
git checkout main
git pull origin main
git checkout -b feature/your-feature-name
```

分支命名规范：
- `feature/xxx` - 新功能
- `fix/xxx` - Bug 修复
- `docs/xxx` - 文档更新
- `refactor/xxx` - 代码重构

### 2. 修改代码

- 遵循代码风格指南
- 为新功能添加测试
- 根据需要更新文档

### 3. 提交更改

```bash
git add .
git commit -m "feat: add new parser for xlsx files"
```

### 4. 推送并创建 PR

```bash
git push origin feature/your-feature-name
```

然后在 GitHub 上创建一个 Pull Request。

---

## 提交规范

我们遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 类型

| 类型 | 描述 |
|------|-------------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档 |
| `style` | 代码风格（不影响逻辑） |
| `refactor` | 代码重构 |
| `perf` | 性能优化 |
| `test` | 测试 |
| `chore` | 构建/工具 |

### 示例

```bash
# 新功能
git commit -m "feat(parser): add support for xlsx files"

# Bug 修复
git commit -m "fix(retrieval): fix score calculation in rerank"

# 文档
git commit -m "docs: update quick start guide"

# 重构
git commit -m "refactor(storage): simplify interface methods"
```

---

## Pull Request 指南

### PR 标题

使用与提交消息相同的格式。

### PR 描述模板

```markdown
## Summary

简要描述更改及其目的。

## Type of Change

- [ ] New feature (feat)
- [ ] Bug fix (fix)
- [ ] Documentation (docs)
- [ ] Refactoring (refactor)
- [ ] Other

## Testing

描述如何测试这些更改：
- [ ] Unit tests pass
- [ ] Manual testing completed

## Related Issues

- Fixes #123
- Related to #456

## Checklist

- [ ] Code follows project style guidelines
- [ ] Tests added for new functionality
- [ ] Documentation updated (if needed)
- [ ] All tests pass
```

---

## CI/CD 工作流

我们使用 **GitHub Actions** 进行持续集成和持续部署。我们的工作流设计为模块化和分层的。

### 1. 自动工作流

| 事件 | 工作流 | 描述 |
|-------|----------|-------------|
| **Pull Request** | `pr.yml` | 运行 **Lint** (Ruff, Mypy) 和 **Test Lite** (Linux + Python 3.10 上的集成测试)。为贡献者提供快速反馈。(显示为 **01. Pull Request Checks**) |
| **Push to Main** | `ci.yml` | 运行 **Test Full** (所有操作系统：Linux/Win/Mac，所有 Py版本：3.10-3.13) 和 **CodeQL** (安全扫描)。确保主分支稳定性。(显示为 **02. Main Branch Checks**) |
| **Release Published** | `release.yml` | 当您在 GitHub 上创建 Release 时触发。自动构建源码包和 wheel 包，基于 Git Tag 确定版本号，并发布到 **PyPI**。(显示为 **03. Release**) |
| **Weekly Cron** | `schedule.yml` | 每周日运行 **CodeQL** 安全扫描。(显示为 **04. Weekly Security Scan**) |

### 2. 手动触发工作流

维护者可以从 "Actions" 选项卡手动触发以下工作流，以执行特定任务或调试问题。

#### A. 代码检查 (`11. _Lint Checks`)
运行代码风格检查 (Ruff) 和类型检查 (Mypy) 。无需参数。

> **提示**：建议在本地安装 [pre-commit](https://pre-commit.com/) 以在提交前自动运行这些检查（详见上文[自动检查](#自动检查推荐)章节）。

#### B. 简易测试 (`12. _Test Suite (Lite)`)
运行快速集成测试，支持自定义矩阵配置。

*   **Inputs**:
    *   `os_json`: 操作系统列表的 JSON 字符串数组 (例如 `["ubuntu-latest"]`)。
    *   `python_json`: Python 版本列表的 JSON 字符串数组 (例如 `["3.10"]`)。

#### C. 完整测试 (`13. _Test Suite (Full)`)
在所有支持的平台 (Linux/Mac/Win) 和 Python 版本 (3.10-3.13) 上运行完整的测试套件。手动触发时支持自定义矩阵配置。

*   **Inputs**:
    *   `os_json`: 操作系统列表 (默认: `["ubuntu-latest", "macos-latest", "windows-latest"]`)。
    *   `python_json`: Python 版本列表 (默认: `["3.10", "3.11", "3.12", "3.13"]`)。

#### D. 安全扫描 (`14. _CodeQL Scan`)
运行 CodeQL 安全分析。无需参数。

#### E. 构建发行版 (`15. _Build Distribution`)
仅构建 Python wheel 包，不发布。

*   **Inputs**:
    *   `os_json`: 操作系统列表 (默认: `["ubuntu-latest", "macos-latest", "macos-15-intel", "windows-latest"]`)。
    *   `python_json`: Python 版本列表 (默认: `["3.10", "3.11", "3.12", "3.13"]`)。
    *   `build_sdist`: 是否构建源码包 (默认: `true`)。
    *   `build_wheels`: 是否构建 Wheel 包 (默认: `true`)。

#### F. 发布发行版 (`16. _Publish Distribution`)
将已构建的包（需要提供构建运行 ID）发布到 PyPI。

*   **Inputs**:
    *   `target`: 选择发布目标 (`testpypi`, `pypi`, `both`)。
    *   `build_run_id`: 构建 Workflow 的 Run ID (必需，从构建运行的 URL 中获取)。

#### G. 手动发布 (`03. Release`)
一站式构建并发布（包含构建和发布步骤）。

> **版本号与 Tag 规范**：
> 本项目使用 `setuptools_scm` 自动从 Git Tag 提取版本号。
> *   **Tag 命名规范**：必须遵循 `vX.Y.Z` 格式（例如 `v0.1.0`, `v1.2.3`）。Tag 必须是符合语义化版本规范的。
> *   **Release 构建**：当触发 Release 事件时，版本号直接对应 Git Tag（例如 `v0.1.0` -> `0.1.0`）。
> *   **手动构建/非 Tag 构建**：版本号会包含距离上一个 Tag 的提交次数（例如 `0.1.1.dev3`）。
> *   **确认版本号**：发布任务完成后，您可以在 Workflow 运行详情页的 **Summary** 页面顶部（**Notifications** 区域）直接看到发布的版本号（例如 `Successfully published to PyPI with version: 0.1.8`）。您也可以在日志或 **Artifacts** 产物文件名中确认。

*   **Inputs**:
    *   `target`: 选择发布目标。
        *   `none`: 仅构建工件（不发布）。用于验证构建能力。
        *   `testpypi`: 发布到 TestPyPI。用于 Beta 测试。
        *   `pypi`: 发布到官方 PyPI。
        *   `both`: 发布到两者。
    *   `os_json`: 构建平台 (默认包含所有)。
    *   `python_json`: Python 版本 (默认包含所有)。
    *   `build_sdist`: 是否构建源码包 (默认: `true`)。
    *   `build_wheels`: 是否构建 Wheel 包 (默认: `true`)。

> **发布注意事项**：
> *   **测试优先**：强烈建议在发布到正式 PyPI 之前，先发布到 **TestPyPI** 进行验证。请注意，PyPI 和 TestPyPI 是两个完全独立的环境，账号和包数据互不相通。
> *   **版本不可覆盖**：PyPI 和 TestPyPI 均**不允许覆盖**已发布的同名同版本包。如果您需要重新发布，必须升级版本号（例如打一个新的 Tag 或产生新的 dev 版本）。如果尝试发布已存在的版本，工作流将会失败。

---

## Issue 指南

### Bug 报告

请提供：

1. **环境**
   - Python 版本
   - OpenViking 版本
   - 操作系统

2. **复现步骤**
   - 详细步骤
   - 代码片段

3. **预期与实际行为**

4. **错误日志**（如果有）

### 功能请求

请描述：

1. **问题**：您试图解决什么问题？
2. **解决方案**：您建议什么解决方案？
3. **替代方案**：您是否考虑过其他方法？

---

## 文档

文档采用 Markdown 格式，位于 `docs/` 目录下：

- `docs/en/` - 英文文档
- `docs/zh/` - 中文文档

### 文档指南

1. 代码示例必须可运行
2. 保持文档与代码同步
3. 使用清晰、简洁的语言

---

## 行为准则

参与本项目即表示您同意：

1. **尊重**：保持友好和专业的态度
2. **包容**：欢迎来自不同背景的贡献者
3. **建设性**：提供有帮助的反馈
4. **专注**：保持讨论集中在技术层面

---

## 获取帮助

如果您有问题：

- [GitHub Issues](https://github.com/volcengine/openviking/issues)
- [Discussions](https://github.com/volcengine/openviking/discussions)

---

感谢您的贡献！
