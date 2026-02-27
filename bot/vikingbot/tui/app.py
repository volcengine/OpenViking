"""Main TUI application using Textual framework."""

import asyncio
from typing import Optional

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Static, Input, Button, RichLog
from textual.binding import Binding
from textual.reactive import reactive

from vikingbot.config.schema import SessionKey
from vikingbot.tui.state import TUIState, MessageRole, Message, ThinkingStep, ThinkingStepType
from vikingbot import __logo__


class ThinkingPanel(Vertical):
    """思考过程面板"""

    def __init__(self, state: TUIState) -> None:
        super().__init__()
        self.state = state
        self.thinking_log = RichLog(id="thinking-log", markup=True, wrap=True, auto_scroll=True)
        self.title = Static("[bold yellow]🧠 Thinking Process[/bold yellow]", id="thinking-title")

    def compose(self) -> ComposeResult:
        yield self.title
        yield self.thinking_log

    def add_step(self, step: ThinkingStep) -> None:
        """添加思考步骤"""
        if step.step_type == ThinkingStepType.ITERATION:
            self.thinking_log.write(f"[dim]━━━ {step.content} ━━━[/dim]")
        elif step.step_type == ThinkingStepType.REASONING:
            self.thinking_log.write(f"[cyan]💭 Reasoning:[/cyan] {step.content}")
        elif step.step_type == ThinkingStepType.TOOL_CALL:
            self.thinking_log.write(f"[magenta]🔧 Tool:[/magenta] {step.content}")
        elif step.step_type == ThinkingStepType.TOOL_RESULT:
            self.thinking_log.write(f"[green]✓ Result:[/green] {step.content}")

    def clear(self) -> None:
        """清空思考过程"""
        self.thinking_log.clear()


class MessageList(RichLog):
    """消息列表组件，显示聊天消息"""

    def add_message(self, message: Message) -> None:
        """添加消息到列表"""
        if message.role == MessageRole.USER:
            self.write(f"[bold cyan]You:[/bold cyan] {message.content}")
        elif message.role == MessageRole.ASSISTANT:
            self.write(f"[bold green]🐈 vikingbot:[/bold green]")
            self.write(message.content)
        elif message.role == MessageRole.SYSTEM:
            self.write(f"[dim]{message.content}[/dim]")
        self.write("")


class ChatInput(Horizontal):
    """聊天输入框组件"""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Type your message here...", id="chat-input")
        yield Button("Send", variant="primary", id="send-button")


class ThinkingIndicator(Static):
    """思考状态指示器"""

    is_thinking = reactive(False)

    def render(self) -> str:
        if self.is_thinking:
            return "[dim]vikingbot is thinking...[/dim]"
        return ""


class StatusBar(Static):
    """状态栏显示会话信息"""

    def __init__(self, state: TUIState) -> None:
        super().__init__()
        self.state = state

    def render(self) -> str:
        status = f"Messages: {self.state.message_count}"
        if self.state.total_tokens > 0:
            status += f" | Tokens: {self.state.total_tokens}"
        if self.state.last_error:
            status += f" | [red]Error: {self.state.last_error}[/red]"
        status += " | [F2] Toggle Thinking | [F3] Clear Thinking"
        return status


class ChatScreen(Horizontal):
    """聊天主屏幕（左右分栏布局）"""

    def __init__(self, state: TUIState) -> None:
        super().__init__()
        self.state = state
        self.message_list = MessageList(id="message-list", markup=True, wrap=True)
        self.thinking_panel = ThinkingPanel(state)
        self.thinking_indicator = ThinkingIndicator(id="thinking-indicator")
        self.status_bar = StatusBar(state)

    def compose(self) -> ComposeResult:
        # 左侧：聊天区域
        with Vertical(id="left-panel"):
            yield self.message_list
            yield self.thinking_indicator
            yield ChatInput(id="chat-input-container")

        # 右侧：思考过程面板
        with Vertical(id="right-panel"):
            yield self.thinking_panel

        yield self.status_bar

    def on_mount(self) -> None:
        """挂载时初始化消息列表"""
        for message in self.state.messages:
            self.message_list.add_message(message)
        # 根据状态显示/隐藏思考面板
        self._update_thinking_panel_visibility()

    def _update_thinking_panel_visibility(self) -> None:
        """更新思考面板可见性"""
        right_panel = self.query_one("#right-panel", Vertical)
        right_panel.display = self.state.show_thinking_panel

    def toggle_thinking_panel(self) -> None:
        """切换思考面板显示/隐藏"""
        self.state.show_thinking_panel = not self.state.show_thinking_panel
        self._update_thinking_panel_visibility()
        self.status_bar.refresh()

    def update_thinking(self, is_thinking: bool) -> None:
        """更新思考状态"""
        self.thinking_indicator.is_thinking = is_thinking

    def add_message(self, message: Message) -> None:
        """添加消息并更新状态"""
        self.state.messages.append(message)
        self.message_list.add_message(message)
        self.state.message_count = len(self.state.messages)
        self.status_bar.refresh()

    def add_thinking_step(self, step: ThinkingStep) -> None:
        """添加思考步骤"""
        self.state.current_thinking_steps.append(step)
        self.thinking_panel.add_step(step)

    def clear_thinking(self) -> None:
        """清空思考过程"""
        self.state.current_thinking_steps.clear()
        self.thinking_panel.clear()


class NanobotTUI(App):
    """vikingbot Textual TUI 主应用"""

    CSS_PATH = "styles/tui.css"
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("escape", "quit", "Quit", show=True),
        Binding("up", "history_up", "Previous message", show=True),
        Binding("down", "history_down", "Next message", show=True),
        Binding("ctrl+l", "clear", "Clear chat", show=True),
        Binding("f2", "toggle_thinking", "Toggle thinking panel", show=True),
        Binding("f3", "clear_thinking", "Clear thinking", show=True),
    ]

    def __init__(self, agent_loop, bus, config) -> None:
        super().__init__()
        self.agent_loop = agent_loop
        self.bus = bus
        self.config = config
        self.state = TUIState()
        self.chat_screen: Optional[ChatScreen] = None

        # 设置思考回调
        self.state.thinking_callback = self._on_thinking_step

    def _on_thinking_step(self, step) -> None:
        """思考步骤回调（处理来自 agent loop 的回调）"""
        # 转换 step 类型（来自 loop.py 的简化版本）
        from vikingbot.agent.loop import ThinkingStepType as LoopThinkingStepType

        converted_type_map = {
            LoopThinkingStepType.REASONING: ThinkingStepType.REASONING,
            LoopThinkingStepType.TOOL_CALL: ThinkingStepType.TOOL_CALL,
            LoopThinkingStepType.TOOL_RESULT: ThinkingStepType.TOOL_RESULT,
            LoopThinkingStepType.ITERATION: ThinkingStepType.ITERATION,
        }

        converted_step = ThinkingStep(
            step_type=converted_type_map.get(step.step_type, ThinkingStepType.REASONING),
            content=step.content,
            timestamp=step.timestamp,
            metadata=step.metadata or {},
        )

        if self.chat_screen:
            self.chat_screen.add_thinking_step(converted_step)

    def compose(self) -> ComposeResult:
        """创建应用布局"""
        yield Header()
        self.chat_screen = ChatScreen(self.state)
        yield self.chat_screen
        yield Footer()

    def on_mount(self) -> None:
        """应用挂载时显示欢迎信息"""
        self.title = "🐈 vikingbot TUI"
        self.sub_title = "Interactive AI Programming Assistant"

        # 添加欢迎消息
        if self.chat_screen:
            welcome_msg = Message(
                role=MessageRole.SYSTEM,
                content=f"{__logo__} Welcome to vikingbot TUI! Type your message below.",
            )
            self.chat_screen.add_message(welcome_msg)

        # 延迟聚焦到输入框，确保组件已完全挂载
        self.call_later(self._focus_input)

    def _focus_input(self) -> None:
        """设置焦点到输入框"""
        try:
            input_widget = self.query_one("#chat-input", Input)
            self.set_focus(input_widget)
        except Exception:
            pass

    @on(Input.Submitted, "#chat-input")
    @on(Button.Pressed, "#send-button")
    async def on_message_sent(self) -> None:
        """处理消息发送"""
        if not self.chat_screen:
            return

        input_widget = self.query_one("#chat-input", Input)
        message_text = input_widget.value.strip()

        if not message_text:
            return

        # 检查退出命令
        if self._is_exit_command(message_text):
            await self.action_quit()
            return

        # 清空输入框
        input_widget.value = ""

        # 让输入框失去焦点，避免光标一直闪烁
        self.set_focus(None)

        # 清空当前思考过程
        self.chat_screen.clear_thinking()

        # 添加用户消息
        user_message = Message(role=MessageRole.USER, content=message_text)
        self.chat_screen.add_message(user_message)
        self.state.input_history.append(message_text)
        self.state.history_index = len(self.state.input_history)

        # 显示思考状态
        self.chat_screen.update_thinking(True)

        original_callback = None
        try:
            # 设置 agent loop 的回调
            original_callback = getattr(self.agent_loop, "thinking_callback", None)
            self.agent_loop.thinking_callback = self._on_thinking_step

            # 处理消息
            response = await self.agent_loop.process_direct(
                message_text, session_key=self.state.session_key
            )

            # 恢复原回调
            self.agent_loop.thinking_callback = original_callback

            # 添加助手回复
            assistant_message = Message(role=MessageRole.ASSISTANT, content=response)
            self.chat_screen.add_message(assistant_message)

            # 更新令牌计数（简化）
            self.state.total_tokens += len(response) // 4

            # 重新聚焦到输入框
            self.set_focus(input_widget)

        except Exception as e:
            # 恢复原回调
            if hasattr(self.agent_loop, "thinking_callback"):
                self.agent_loop.thinking_callback = original_callback
            # 显示错误
            error_msg = Message(role=MessageRole.SYSTEM, content=f"[red]Error: {e}[/red]")
            self.chat_screen.add_message(error_msg)
            self.state.last_error = str(e)
            # 重新聚焦到输入框
            self.set_focus(input_widget)
        finally:
            # 隐藏思考状态
            self.chat_screen.update_thinking(False)
            self.chat_screen.status_bar.refresh()

    def action_history_up(self) -> None:
        """上一条历史消息"""
        if self.state.input_history:
            input_widget = self.query_one("#chat-input", Input)
            if self.state.history_index > 0:
                self.state.history_index -= 1
                input_widget.value = self.state.input_history[self.state.history_index]
                input_widget.cursor_position = len(input_widget.value)

    def action_history_down(self) -> None:
        """下一条历史消息"""
        if self.state.input_history:
            input_widget = self.query_one("#chat-input", Input)
            if self.state.history_index < len(self.state.input_history) - 1:
                self.state.history_index += 1
                input_widget.value = self.state.input_history[self.state.history_index]
                input_widget.cursor_position = len(input_widget.value)
            elif self.state.history_index == len(self.state.input_history) - 1:
                self.state.history_index = len(self.state.input_history)
                input_widget.value = ""

    def action_clear(self) -> None:
        """清空聊天并开始新会话"""
        self.state.messages.clear()
        self.state.message_count = 0
        self.state.total_tokens = 0
        self.state.last_error = None

        # 生成新的 session ID
        import uuid

        self.state.session_key = SessionKey(
            type="tui", channel_id="default", chat_id="uuid.uuid4().hex[:8]"
        )

        # 清空思考过程
        if self.chat_screen:
            self.chat_screen.clear_thinking()
            self.chat_screen.message_list.clear()
            welcome_msg = Message(
                role=MessageRole.SYSTEM,
                content=f"{__logo__} Chat cleared. New session started (Session: {self.state.session_key}).",
            )
            self.chat_screen.add_message(welcome_msg)

    def action_toggle_thinking(self) -> None:
        """切换思考面板"""
        if self.chat_screen:
            self.chat_screen.toggle_thinking_panel()

    def action_clear_thinking(self) -> None:
        """清空思考过程"""
        if self.chat_screen:
            self.chat_screen.clear_thinking()

    def _is_exit_command(self, command: str) -> bool:
        """检查是否为退出命令"""
        return command.lower().strip() in {"exit", "quit", "/exit", "/quit", ":q"}


async def run_tui(agent_loop, bus, config) -> None:
    """运行 TUI 应用"""
    app = NanobotTUI(agent_loop, bus, config)
    await app.run_async()
