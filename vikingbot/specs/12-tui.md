# vikingbot TUI - 聊天式编程助手完整规范

## 概述

为 vikingbot 开发一个基于 Textual 的终端用户界面（TUI），提供类似 OpenCode 的交互式编程体验。用户可以通过 TUI 与 AI 助手进行对话，完成编程、代码生成、调试等任务。

## 核心目标

1. **现代化交互体验**: 提供流畅、响应迅速的终端界面
2. **实时对话**: 支持与 AI 助手的实时对话交互
3. **Markdown 渲染**: 正确渲染代码块、列表、链接等 Markdown 格式
4. **代码高亮**: 支持多种编程语言的语法高亮
5. **轻量级**: 保持 vikingbot 超轻量级的设计理念
6. **不修改现有 CLI**: 作为独立的 `tui` 命令添加到 CLI

## 技术栈

### 框架选择

**Textual (Python TUI Framework)**
- 现代化、功能丰富的 Python TUI 框架
- 基于 Elm 架构的响应式设计
- 内置丰富的组件库
- 优秀的异步支持
- 活跃的社区和文档

**为什么选择 Textual:**
- 项目已有 `rich` 依赖，Textual 与 Rich 兼容性好
- 提供开箱即用的组件（表格、输入框、滚动视图等）
- 支持 CSS 样式系统
- 良好的键盘事件处理
- 支持 Windows、macOS、Linux

### 依赖项

```toml
[project.dependencies]
"textual>=0.50.0"  # TUI 框架
"rich>=13.0.0"        # 已有，用于 Markdown 渲染
"pygments>=2.16.0"      # 代码语法高亮
```

## 项目结构

```
vikingbot/
└── tui/
    ├── __init__.py           # 模块初始化
    ├── app.py                # 主 TUI 应用程序
    ├── state.py              # 应用状态管理
    ├── screens/
    │   ├── __init__.py
    │   ├── chat.py          # 主聊天屏幕
    │   └── help.py          # 帮助屏幕
    ├── widgets/
    │   ├── __init__.py
    │   ├── message.py        # 消息显示组件
    │   ├── input.py         # 输入组件
    │   ├── thinking.py       # 思考状态指示器
    │   └── status_bar.py    # 状态栏组件
    └── styles/
        ├── __init__.py
        └── theme.py         # 主题定义
```

## 详细设计

### 1. 应用状态管理

```python
# vikingbot/tui/state.py
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
from datetime import datetime

class MessageRole(Enum):
    """消息角色枚举"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

@dataclass
class Message:
    """聊天消息"""
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tokens_used: Optional[int] = None

@dataclass
class TUIState:
    """TUI 应用状态"""
    # 消息历史
    messages: List[Message] = field(default_factory=list)
    
    # 会话信息
    session_id: str = "tui:default"
    
    # UI 状态
    is_thinking: bool = False
    thinking_message: str = "vikingbot is thinking..."
    
    # 输入状态
    input_text: str = ""
    input_history: List[str] = field(default_factory=list)
    history_index: int = -1
    
    # 错误状态
    last_error: Optional[str] = None
    
    # 统计信息
    total_tokens: int = 0
    message_count: int = 0
```

### 2. 主应用程序

```python
# vikingbot/tui/app.py
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer
from vikingbot.tui.screens.chat import ChatScreen
from vikingbot.tui.screens.help import HelpScreen

class NanobotTUI(App):
    """vikingbot TUI 主应用"""
    
    CSS = """
    Screen {
        background: $background;
        layout: vertical;
    }
    
    Header {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    
    Footer {
        background: $surface;
        color: $text;
    }
    """
    
    TITLE = "vikingbot TUI"
    SUB_TITLE = "Interactive AI Programming Assistant"
    
    def __init__(self, agent_loop, bus, config):
        super().__init__()
        self.agent_loop = agent_loop
        self.bus = bus
        self.config = config
    
    def on_mount(self) -> None:
        """应用挂载时初始化"""
        self.push_screen(ChatScreen())
    
    def show_help(self) -> None:
        """显示帮助屏幕"""
        self.push_screen(HelpScreen())
```

### 3. 聊天屏幕

```python
# vikingbot/tui/screens/chat.py
from textual.screen import Screen
from textual.containers import Vertical, Horizontal
from textual.widgets import Static
from vikingbot.tui.widgets.message import MessageList
from vikingbot.tui.widgets.input import ChatInput
from vikingbot.tui.widgets.thinking import ThinkingIndicator
from vikingbot.tui.widgets.status_bar import StatusBar

class ChatScreen(Screen):
    """主聊天屏幕"""
    
    CSS = """
    ChatScreen {
        layout: vertical;
    }
    
    #message_list {
        height: 1fr;
        dock: top;
    }
    
    #thinking_indicator {
        dock: top;
        height: 1;
    }
    
    #input_area {
        dock: bottom;
        height: 3;
    }
    
    #status_bar {
        dock: bottom;
        height: 1;
    }
    """
    
    def __init__(self):
        super().__init__()
        self.state = TUIState()
    
    def compose(self) -> ComposeResult:
        """构建 UI"""
        yield MessageList(id="message_list")
        yield ThinkingIndicator(id="thinking_indicator")
        yield ChatInput(id="input_area")
        yield StatusBar(id="status_bar")
    
    def on_mount(self) -> None:
        """屏幕挂载时初始化"""
        self.query_one(ThinkingIndicator).visible = False
    
    def add_message(self, role: MessageRole, content: str) -> None:
        """添加消息到界面"""
        message = Message(role=role, content=content)
        self.state.messages.append(message)
        self.state.message_count += 1
        
        message_list = self.query_one(MessageList)
        message_list.add_message(message)
        
        # 更新状态栏
        self._update_status_bar()
    
    def show_thinking(self, message: str = None) -> None:
        """显示思考状态"""
        self.state.is_thinking = True
        self.state.thinking_message = message or "vikingbot is thinking..."
        
        thinking_indicator = self.query_one(ThinkingIndicator)
        thinking_indicator.message = self.state.thinking_message
        thinking_indicator.visible = True
    
    def hide_thinking(self) -> None:
        """隐藏思考状态"""
        self.state.is_thinking = False
        thinking_indicator = self.query_one(ThinkingIndicator)
        thinking_indicator.visible = False
    
    async def send_message(self, text: str) -> None:
        """发送消息到 AI"""
        # 添加用户消息
        self.add_message(MessageRole.USER, content=text)
        
        # 添加到历史
        if text.strip():
            self.state.input_history.append(text.strip())
            self.state.history_index = len(self.state.input_history)
        
        # 显示思考状态
        self.show_thinking()
        
        try:
            # 发送到 agent
            response = await self.app.agent_loop.process_direct(
                text,
                session_id=self.state.session_id
            )
            
            # 隐藏思考状态
            self.hide_thinking()
            
            # 添加助手回复
            self.add_message(MessageRole.ASSISTANT, content=response)
            
        except Exception as e:
            self.hide_thinking()
            self.state.last_error = str(e)
            self._show_error(f"Error: {e}")
    
    def _update_status_bar(self) -> None:
        """更新状态栏"""
        status_bar = self.query_one(StatusBar)
        status_bar.update(
            session_id=self.state.session_id,
            message_count=self.state.message_count,
            is_thinking=self.state.is_thinking
        )
    
    def _show_error(self, message: str) -> None:
        """显示错误消息"""
        self.app.notify(message, severity="error")
```

### 4. 消息显示组件

```python
# vikingbot/tui/widgets/message.py
from textual.widgets import Static
from textual.containers import VerticalScroll
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound
from vikingbot.tui.state import Message, MessageRole

class MessageItem(Static):
    """单条消息显示"""
    
    def __init__(self, message: Message):
        super().__init__()
        self.message = message
    
    def render(self) -> str:
        """渲染消息"""
        if self.message.role == MessageRole.USER:
            return self._render_user_message()
        else:
            return self._render_assistant_message()
    
    def _render_user_message(self) -> str:
        """渲染用户消息"""
        content = self.message.content
        return f"[bold cyan]You:[/bold cyan] {content}"
    
    def _render_assistant_message(self) -> str:
        """渲染助手消息（支持 Markdown）"""
        content = self.message.content
        
        # 尝试检测代码块并高亮
        try:
            md = Markdown(content)
            return f"[bold green]🐈 vikingbot:[/bold green]\n{md}"
        except Exception:
            return f"[bold green]🐈 vikingbot:[/bold green] {content}"

class MessageList(VerticalScroll):
    """消息列表"""
    
    def __init__(self):
        super().__init__()
        self.can_focus = False
    
    def add_message(self, message: Message) -> None:
        """添加消息到列表"""
        message_item = MessageItem(message)
        self.mount(message_item)
        
        # 滚动到底部
        self.scroll_end(animate=False)
```

### 5. 输入组件

```python
# vikingbot/tui/widgets/input.py
from textual.widgets import TextArea
from textual.keys import Keys
from textual.message import Message

class ChatInput(TextArea):
    """聊天输入框"""
    
    def __init__(self):
        super().__init__(
            placeholder="Type your message here...",
            id="chat_input",
            max_lines=5,
        )
        self.history = []
        self.history_index = -1
    
    def on_key(self, event: Message) -> None:
        """处理键盘事件"""
        if event.key == Keys.Enter:
            # 发送消息
            if self.text.strip():
                self._submit_message()
        elif event.key == Keys.Up:
            # 历史记录上
            self._navigate_history(-1)
        elif event.key == Keys.Down:
            # 历史记录下
            self._navigate_history(1)
        elif event.key == Keys.ControlK:
            # 清空输入
            self.text = ""
        elif event.key == Keys.ControlC:
            # 复制选中文本（如果支持）
            pass
    
    def _submit_message(self) -> None:
        """提交消息"""
        text = self.text.strip()
        if not text:
            return
        
        # 添加到历史
        self.history.append(text)
        self.history_index = len(self.history)
        
        # 发送到父屏幕
        screen = self.app.screen
        if hasattr hasattr(screen, 'send_message'):
            self.app.run_worker(screen.send_message(text))
        
        # 清空输入
        self.text = ""
    
    def _navigate_history(self, direction: int) -> None:
        """导航历史记录"""
        if not self.history:
            return
        
        new_index = self.history_index + direction
        
        if 0 <= new_index < len(self.history):
            self.history_index = new_index
            self.text = self.history[new_index]
        elif new_index >= len(self.history):
            self.history_index = len(self.history)
            self.text = ""
```

### 6. 思考状态指示器

```python
# vikingbot/tui/widgets/thinking.py
from textual.widgets import Static
from textual.containers import Horizontal
from rich.spinner import Spinner

class ThinkingIndicator(Static):
    """思考状态指示器"""
    
    def __init__(self):
        super().__init__()
        self.message = "vikingbot is thinking..."
        self.visible = False
        self.spinner = Spinner("dots", text=self.message)
    
    def render(self) -> str:
        """渲染指示器"""
        if not self.visible:
            return ""
        return str(self.spinner)
```

### 7. 状态栏组件

```python
# vikingbot/tui/widgets/status_bar.py
from textual.widgets import Static

class StatusBar(Static):
    """状态栏"""
    
    def __init__(self):
        super().__init__()
        self.session_id = "tui:default"
        self.message_count = 0
        self.is_thinking = False
    
    def update(self, session_id: str, message_count: int, is_thinking: bool) -> None:
        """更新状态栏"""
        self.session_id = session_id
        self.message_count = message_count
        self.is_thinking = is_thinking
    
    def render(self) -> str:
        """渲染状态栏"""
        thinking = " [yellow]Thinking...[/yellow]" if self.is_thinking else ""
        return (
            f"[dim]Session: {self.session_id}[/dim] | "
            f"[cyan]Messages: {self.message_count}[/cyan]"
            f"{thinking}"
        )
```

### 8. 帮助屏幕

```python
# vikingbot/tui/screens/help.py
from textual.screen import Screen
from textual.widgets import Static

class HelpScreen(Screen):
    """帮助屏幕"""
    
    CSS = """
    HelpScreen {
        layout: vertical;
        padding: 1 2;
    }
    """
    
    def compose(self):
        help_text = """
[bold]vikingbot TUI Help[/bold]

[dim]Keyboard Shortcuts:[/dim]
  [cyan]Enter[/cyan]      - Send message
  [cyan]Ctrl+K[/cyan]     - Clear input
  [cyan]Ctrl+C[/cyan]     - Copy selection
  [cyan]Up/Down[/cyan]   - Navigate message history
  [cyan]Ctrl+Q[/cyan]     - Quit
  [cyan]Ctrl+H[/cyan]     - Show this help
  [cyan]Esc[/cyan]        - Return to chat

[dim]Features:[/dim]
  • Real-time AI conversation
  • Markdown rendering
  • Code syntax highlighting
  • Message history
  • Session persistence
        """
        yield Static(help_text)
```

### 9. 主题定义

```python
# vikingbot/tui/styles/theme.py
from textual.color import Color
from textual.theme import Theme

# 默认主题（深色）
DEFAULT_THEME = Theme({
    "primary": Color.parse("#00d4ff"),      # 蓝色
    "secondary": Color.parse("#6c757d"),    # 灰色
    "background": Color.parse("#1e1e2e"),  # 深色背景
    "surface": Color.parse("#2d2d2d"),      # 表面颜色
    "text": Color.parse("#e9ecef"),         # 文本颜色
    "success": Color.parse("#28a745"),      # 绿色
    "warning": Color.parse("#ffc107"),      # 黄色
    "error": Color.parse("#dc3545"),        # 红色
})

# 浅色主题
LIGHT_THEME = Theme({
    "primary": Color.parse("#007bff"),
    "secondary": Color.parse("#6c757d"),
    "background": Color.parse("#ffffff"),
    "surface": Color.parse("#f8f9fa"),
    "text": Color.parse("#212529"),
    "success": Color.parse("#28a745"),
    "warning": Color.parse("#ffc107"),
    "error": Color.parse("#dc3545"),
})
```

## CLI 集成

### 添加 TUI 命令

```python
# 在 vikingbot/cli/commands.py 中添加

@app.command()
def tui():
    """Launch vikingbot TUI interface."""
    from vikingbot.config.loader import load_config
    from vikingbot.bus.queue import MessageBus
    from vikingbot.agent.loop import AgentLoop
    from vikingbot.session.manager import SessionManager
    from vikingbot.tui.app import NanobotTUI
    
    config = load_config()
    bus = MessageBus()
    
    # 创建 provider
    provider = _make_provider(config)
    
    # 创建 session manager
    session_manager = SessionManager(config.workspace_path)
    
    # 创建 agent loop
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
    )
    
    # 启动 TUI
    app = NanobotTUI(
        agent_loop=agent_loop,
        bus=bus,
        config=config
    )
    app.run()
```

## 键盘快捷键

| 快捷键 | 功能 |
|---------|------|
| `Enter` | 发送消息 |
| `Ctrl+K` | 清空输入框 |
| `Ctrl+C` | 复制选中文本 |
| `Up/Down` | 浏览输入历史 |
| `Ctrl+Q` | 退出应用 |
| `Ctrl+H` | 显示帮助 |
| `Esc` | 返回聊天界面（从帮助屏幕） |
| `Ctrl+L` | 清除聊天历史 |
| `Ctrl+S` | 保存当前会话 |

## 功能特性

### 核心功能

1. **实时对话**
   - 发送消息并接收 AI 回复
   - 显示思考状态（spinner）
   - 错误处理和提示

2. **消息历史**
   - 保存所有对话消息
   - 支持输入历史导航
   - 会话持久化

3. **Markdown 渲染**
   - 标题、列表、链接
   - 代码块检测和渲染
   - 引用块支持

4. **代码高亮**
   - 自动检测编程语言
   - 支持 100+ 种语言
   - 语法着色

5. **会话管理**
   - 自动保存会话
   - 支持会话恢复
   - 会话 ID 显示

### 增强功能（可选）

1. **多行输入**
   - 支持 Ctrl+Enter 换行
   - Enter 发送消息
   - 最大行数限制

2. **代码复制**
   - 点击代码块复制
   - 快捷键复制
   - 复制成功提示

3. **搜索功能**
   - 在消息中搜索关键词
   - 高亮匹配结果
   - 快速导航

4. **主题切换**
   - 深色/浅色主题
   - 自定义颜色
   - 实时切换

5. **导出功能**
   - 导出为 Markdown
   - 导出为 JSON
   - 导出为 PDF

## 性能优化

1. **消息虚拟化**
   - 只渲染可见消息
   - 滚动时动态加载
   - 减少内存占用

2. **Markdown 缓存**
   - 缓存渲染结果
   - 避免重复解析
   - 提升响应速度

3. **异步渲染**
   - 使用 Textual 的 worker
   - 不阻塞主线程
   - 保持界面流畅

4. **延迟加载**
   - 代码块懒加载
   - 大消息分块渲染
   - 优先显示文本

## 测试策略

### 单元测试

```python
# tests/tui/test_state.py
import pytest
from vikingbot.tui.state import TUIState, Message, MessageRole

def test_state_initialization():
    """测试状态初始化"""
    state = TUIState()
    assert state.session_id == "tui:default"
    assert len(state.messages) == 0
    assert state.is_thinking == False

def test_add_message():
    """测试添加消息"""
    state = TUIState()
    message = Message(role=MessageRole.USER, content="Hello")
    state.messages.append(message)
    assert len(state.messages) == 1
    assert state.messages[0].content == "Hello"
```

### 集成测试

```python
# tests/tui/test_integration.py
import pytest
from unittest.mock import Mock
from vikingbot.tui.app import NanobotTUI

@pytest.mark.asyncio
async def test_send_message():
    """测试发送消息"""
    # 创建 mock agent
    mock_agent = Mock()
    mock_agent.process_direct = Mock(return_value="Test response")
    
    # 创建 TUI
    app = NanobotTUI(
        agent_loop=mock_agent,
        bus=Mock(),
        config=Mock()
    )
    
    # 发送消息
    await app.screen.send_message("Test message")
    
    # 验证
    mock_agent.process_direct.assert_called_once()
    assert len(app.screen.state.messages) == 2  # user + assistant
```

### 手动测试清单

- [ ] 启动 TUI: `vikingbot tui`
- [ ] 发送测试消息
- [ ] 验证 AI 回复显示
- [ ] 测试 Markdown 渲染
- [ ] 测试代码高亮
- [ ] 测试输入历史导航
- [ ] 测试 Ctrl+K 清空输入
- [ ] 测试帮助屏幕 (Ctrl+H)
- [ ] 测试退出 (Ctrl+Q)
- [ ] 验证会话保存
- [ ] 测试错误处理

## 用户体验设计

### 视觉设计

1. **清晰的层次结构**
   - Header: 应用标题和版本
   - Main: 聊天区域
   - Footer: 状态和快捷键提示

2. **颜色编码**
   - 用户消息: 青色
   - AI 消息: 绿色
   - 错误: 红色
   - 思考状态: 黄色

3. **动画效果**
   - 思考状态: 旋转 spinner
   - 消息出现: 淡入效果
   - 滚动: 平滑动画

### 交互设计

1. **直观的导航**
   - 键盘优先
   - 鼠标支持（可选）
   - 清晰的焦点指示

2. **即时反馈**
   - 输入时显示字符
   - 发送后清空输入
   - 错误时显示提示

3. **容错处理**
   - 网络错误重试
   - 无效输入提示
   - 优雅降级

## 未来扩展

1. **多会话支持**
   - 会话切换
   - 会话对比
   - 会话合并

2. **文件操作**
   - 拖拽上传
   - 文件预览
   - 附件支持

3. **高级编辑**
   - 代码块编辑
   - 实时协作
   - 版本控制

4. **插件系统**
   - 自定义组件
   - 第三方集成
   - 主题市场

## 参考资源

- [Textual Documentation](https://textual.textual.io/)
- [OpenCode TUI](https://github.com/anomalyco/opencode)
- [Bubble Tea](https://github.com/charmbracelet/bubbletea)
- [Rich Library](https://rich.readthedocs.io/)
- [Pygments](https://pygments.org/)
