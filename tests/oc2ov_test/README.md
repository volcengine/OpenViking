# OpenClaw - OpenViking 端到端自动化测试

OpenClaw 和 OpenViking 端到端自动化测试框架，用于验证记忆读写、增删改查等场景。

## 📋 前置条件

在使用本项目之前，请确保已完成以下准备工作：

### 1. 安装 OpenClaw

确保已在本地安装 OpenClaw：

```bash
# 验证 OpenClaw 安装
openclaw --version
```

### 2. 安装 OpenViking 插件

确保已安装 OpenViking 插件并正确配置：

```bash
# 检查已安装的插件
openclaw plugins list
```

### 3. （可选）配置 OpenClaw HTTP 通信

**注意：本项目推荐使用 OpenClaw CLI 方式（默认），更加稳定可靠。**

如果需要使用 HTTP API 方式，请在 OpenClaw 的配置文件 `~/.openclaw/openclaw.json` 中添加或修改以下配置：

```json
{
  "gateway": {
    "http": {
      "endpoints": {
        "responses": {
          "enabled": true
        }
      }
    }
  }
}
```

配置后重启 OpenClaw Gateway：

```bash
openclaw gateway restart
```

### 4. 启动 OpenClaw 服务

确保 OpenClaw Gateway 正在运行：

```bash
# 检查服务状态
openclaw gateway status

# 如果未运行，启动服务
openclaw gateway
```

## 🚀 快速开始（推荐：使用 CLI 方式）

### 方法一：使用快速脚本（推荐）

```bash
# 1. 设置环境（自动创建虚拟环境并安装依赖）
./setup.sh

# 2. 运行测试（带报告生成，使用 CLI 方式）
./run.sh -r
```

### 方法二：手动设置

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 3. 安装依赖
pip install -r requirements.txt
pip install pytest-html

# 4. 运行测试（使用 CLI 方式，推荐）
pytest test_cli_pytest.py -v --html=reports/test_report.html --self-contained-html
```

## 📁 项目结构

```
oc2ov_test/
├── config/                 # 配置文件目录
│   ├── __init__.py
│   └── settings.py         # 项目配置
├── tests/                  # 测试用例目录
│   ├── __init__.py
│   ├── base_test.py        # 测试基类（HTTP 方式）
│   ├── base_cli_test.py    # 测试基类（CLI 方式，推荐）
│   ├── p0/                 # P0 质量保障类测试
│   ├── crud/               # CRUD 操作测试
│   └── complex/            # 复杂场景测试
├── utils/                  # 工具函数目录
│   ├── __init__.py
│   ├── openclaw_client.py  # OpenClaw HTTP 客户端封装
│   ├── openclaw_cli_client.py  # OpenClaw CLI 客户端封装（推荐）
│   ├── logger.py           # 日志工具
│   └── assertions.py       # 断言工具（关键词匹配、文本相似度）
├── logs/                   # 日志目录
├── reports/                # 测试报告目录
├── venv/                   # Python 虚拟环境（自动创建）
├── conftest.py             # Pytest 配置（报告美化）
├── test_cli_pytest.py      # Pytest 测试入口（CLI 方式，推荐）
├── test_pytest.py          # Pytest 测试入口（HTTP 方式）
├── test_cli_single.py      # 单个 CLI 请求测试
├── quick_test.py           # 快速测试脚本
├── run_tests.py            # 测试运行入口
├── setup.sh                # 快速环境设置脚本
├── run.sh                  # 快速测试运行脚本
├── requirements.txt        # Python 依赖
├── pyproject.toml          # 项目配置文件
├── README.md               # 项目说明
└── ASSERTIONS_GUIDE.md     # 断言使用详细指南
```

## ⚙️ 配置说明

配置文件位于 `config/settings.py`，主要配置项：

```python
OPENCLAW_CONFIG = {
    "url": "http://127.0.0.1:18789/v1/responses",
    "auth_token": "Bearer YOUR_AUTH_TOKEN_HERE",  # 请替换为您自己的认证token
    "agent_id": "main",
    "model": "YOUR_MODEL_NAME_HERE",  # 请替换为您自己的模型名称
    "timeout": 120
}

TEST_CONFIG = {
    "wait_time": 10,  # 等待记忆同步的时间（秒）
    "log_dir": os.path.join(BASE_DIR, "logs"),
    "report_dir": os.path.join(BASE_DIR, "reports")
}
```


## 🧪 运行测试

### 推荐：使用 CLI 方式（更稳定）

```bash
# 运行全部 CLI 测试（带报告）
pytest test_cli_pytest.py -v --html=reports/test_report_cli.html --self-contained-html

# 运行单个 CLI 测试
pytest test_cli_pytest.py::TestMemoryWriteGroupA::test_memory_write_basic_info -v

# 快速测试单个 CLI 请求
python test_cli_single.py
```

### 使用 HTTP 方式

```bash
# 运行全部 HTTP 测试
pytest test_pytest.py -v --html=reports/test_report.html --self-contained-html
```

### 使用快速脚本

```bash
# 查看帮助
./run.sh -h

# 运行全部测试（带报告）
./run.sh -r

# 仅运行 P0 级测试
./run.sh -p

# 仅运行 CRUD 操作测试
./run.sh -c

# 仅运行复杂场景测试
./run.sh -x

# 详细输出模式
./run.sh -v

# 组合使用：详细输出 + 生成报告
./run.sh -v -r
```

## 📊 查看测试报告

测试报告生成在 `reports/` 目录下，可以直接在浏览器中打开：

```bash
# macOS
open reports/test_report_cli.html

# Linux
xdg-open reports/test_report_cli.html

# Windows
start reports/test_report_cli.html
```

报告包含：
- 📊 环境信息（OpenClaw 版本、OpenViking 状态等）
- 📝 详细的中文测试描述
- 📈 测试执行结果和日志
- ✅ 通过/失败的测试用例统计

## ✅ 断言功能

项目提供了多种断言方式来验证 OpenClaw 的响应：

### 1. 关键词断言

```python
# 验证响应中包含所有关键词
self.assertKeywordsInResponse(
    response,
    ["小明", "30岁", "测试开发"],
    require_all=True
)
```

### 2. 任意关键词组断言

```python
# 验证响应中包含任意一组中的任意一个关键词
self.assertAnyKeywordInResponse(
    response,
    [["小明", "小红"], ["30", "25"]]
)
```

### 3. 文本相似度断言

```python
# 验证响应文本与期望文本的相似度
self.assertSimilarity(
    response,
    "你叫小明，今年30岁",
    min_similarity=0.7
)
```

**详细使用指南请查看：[ASSERTIONS_GUIDE.md](./ASSERTIONS_GUIDE.md)**

## 📝 测试用例说明

### P0 质量保障类测试

- `TestMemoryWriteGroupA` - 测试组A（小明）：基本记忆结构化写入验证
- `TestMemoryWriteGroupB` - 测试组B（小红）：多维度丰富信息写入

### CRUD 操作测试

- `TestMemoryRead` - 记忆读取验证
- `TestMemoryUpdate` - 记忆更新验证
- `TestMemoryDelete` - 记忆删除验证

### 复杂场景测试

- `TestComplexScenarioMultiUsers` - 多用户切换场景
- `TestComplexScenarioIncrementalInfo` - 增量信息添加
- `TestComplexScenarioSpecialCharacters` - 特殊字符和边界情况

## 🔧 扩展新测试用例

1. 在 `tests/` 相应目录下创建新的测试文件
2. 继承 `BaseOpenClawCLITest` 基类（推荐使用 CLI 方式）
3. 使用 `self.send_and_log()` 发送消息
4. 使用 `self.wait_for_sync()` 等待记忆同步
5. 使用断言方法验证响应
6. 在 `test_cli_pytest.py` 中添加到测试套件中

示例：

```python
from tests.base_cli_test import BaseOpenClawCLITest

class TestMyNewFeature(BaseOpenClawCLITest):
    """
    测试目标：我的新功能验证
    测试场景：描述测试场景
    """
    
    def test_something(self):
        """测试场景：具体场景描述"""
        self.logger.info("开始测试")
        self.send_and_log("我叫测试用户")
        self.wait_for_sync()
        
        # 验证响应
        response = self.send_and_log("我是谁")
        self.assertKeywordsInResponse(response, ["测试用户"])
```

## 📋 日志

测试运行日志保存在 `logs/test_run.log`

## ⚠️ 注意事项

1. **会话管理**：使用 CLI 方式时，每个测试类会使用独立的 `session-id`，避免会话冲突
2. **等待时间**：根据实际情况调整 `config/settings.py` 中的 `wait_time`，确保记忆同步完成
3. **超时设置**：CLI 客户端默认超时为 300 秒，可根据需要调整
4. **服务状态**：确保 OpenClaw Gateway 正常运行，测试前可使用 `openclaw gateway status` 检查
5. **会话锁定**：如果遇到 "session file locked" 错误，请检查是否有其他进程在使用相同的 session-id

## 🐛 故障排查

### 问题：每次发送消息后需要重启服务

**解决方案**：使用 CLI 方式代替 HTTP API 方式，CLI 方式支持 `--session-id` 参数，可以保持会话连续性。

### 问题：测试超时

**解决方案**：
1. 增加 `config/settings.py` 中的 `timeout` 值
2. 增加 `wait_time` 等待时间
3. 检查 OpenClaw 服务是否正常运行

### 问题：会话文件锁定

**解决方案**：
1. 检查是否有其他测试进程在运行
2. 尝试使用不同的 session-id
3. 重启 OpenClaw Gateway

## 🐍 虚拟环境说明

项目使用 Python 虚拟环境来隔离依赖：

- `venv/` - 虚拟环境目录（已添加到 .gitignore）
- `setup.sh` - 一键设置环境脚本
- `run.sh` - 一键运行测试脚本

建议始终在虚拟环境中运行测试，避免依赖冲突。
