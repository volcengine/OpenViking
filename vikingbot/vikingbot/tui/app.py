"""Main TUI application using Textual framework."""

import asyncio
from typing import Optional

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Static, Input, Button, RichLog
from textual.binding import Binding
from textual.reactive import reactive

from vikingbot.tui.state import TUIState, MessageRole, Message
from vikingbot import __logo__


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
        self.write("")  # 空行分隔


class ChatInput(Container):
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
        return status


class ChatScreen(Container):
    """聊天主屏幕"""
    
    def __init__(self, state: TUIState) -> None:
        super().__init__()
        self.state = state
        self.message_list = MessageList(id="message-list", markup=True, wrap=True)
        self.thinking_indicator = ThinkingIndicator(id="thinking-indicator")
        self.status_bar = StatusBar(state)
    
    def compose(self) -> ComposeResult:
        yield Vertical(
            self.message_list,
            self.thinking_indicator,
            id="chat-area"
        )
        yield ChatInput(id="chat-input-container")
        yield self.status_bar
    
    def on_mount(self) -> None:
        """挂载时初始化消息列表"""
        for message in self.state.messages:
            self.message_list.add_message(message)
    
    def update_thinking(self, is_thinking: bool) -> None:
        """更新思考状态"""
        self.thinking_indicator.is_thinking = is_thinking
    
    def add_message(self, message: Message) -> None:
        """添加消息并更新状态"""
        self.state.messages.append(message)
        self.message_list.add_message(message)
        self.state.message_count = len(self.state.messages)
        self.status_bar.refresh()


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
    ]
    
    def __init__(self, agent_loop, bus, config) -> None:
        super().__init__()
        self.agent_loop = agent_loop
        self.bus = bus
        self.config = config
        self.state = TUIState()
        self.chat_screen: Optional[ChatScreen] = None
    
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
        welcome_msg = Message(
            role=MessageRole.SYSTEM,
            content=f"{__logo__} Welcome to vikingbot TUI! Type your message below."
        )
        self.chat_screen.add_message(welcome_msg)
        
        # 聚焦到输入框
        self.set_focus(self.query_one("#chat-input", Input))
    
    @on(Input.Submitted, "#chat-input")
    @on(Button.Pressed, "#send-button")
    async def on_message_sent(self) -> None:
        """处理消息发送"""
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
        
        # 添加用户消息
        user_message = Message(role=MessageRole.USER, content=message_text)
        self.chat_screen.add_message(user_message)
        self.state.input_history.append(message_text)
        self.state.history_index = len(self.state.input_history)
        
        # 显示思考状态
        self.chat_screen.update_thinking(True)
        
        try:
            # 处理消息
            response = await self.agent_loop.process_direct(
                message_text,
                session_key=self.state.session_id
            )
            
            # 添加助手回复
            assistant_message = Message(role=MessageRole.ASSISTANT, content=response)
            self.chat_screen.add_message(assistant_message)
            
            # 更新令牌计数（简化）
            self.state.total_tokens += len(response) // 4  # 近似值
            
        except Exception as e:
            # 显示错误
            error_msg = Message(
                role=MessageRole.SYSTEM,
                content=f"[red]Error: {e}[/red]"
            )
            self.chat_screen.add_message(error_msg)
            self.state.last_error = str(e)
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
        """清空聊天"""
        self.state.messages.clear()
        self.state.message_count = 0
        self.state.total_tokens = 0
        self.state.last_error = None
        
        # 重新初始化消息列表
        self.chat_screen.message_list.clear()
        welcome_msg = Message(
            role=MessageRole.SYSTEM,
            content=f"{__logo__} Chat cleared. New session started."
        )
        self.chat_screen.add_message(welcome_msg)
    
    def _is_exit_command(self, command: str) -> bool:
        """检查是否为退出命令"""
        return command.lower().strip() in {"exit", "quit", "/exit", "/quit", ":q"}


async def run_tui(agent_loop, bus, config) -> None:
    """运行 TUI 应用"""
    app = NanobotTUI(agent_loop, bus, config)
    await app.run_async()
