"""OpenAPI channel for HTTP-based chat API."""

import asyncio
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from vikingbot.bus.events import InboundMessage, OutboundEventType, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.channels.openapi_models import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    EventType,
    HealthResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    SessionInfo,
    SessionListResponse,
)
from vikingbot.config.schema import BaseChannelConfig, Config, SessionKey


class OpenAPIChannelConfig(BaseChannelConfig):
    """Configuration for OpenAPI channel."""

    enabled: bool = True
    type: str = "cli"
    api_key: str = ""  # If empty, no auth required
    allow_from: list[str] = []
    max_concurrent_requests: int = 100
    _channel_id: str = "default"

    def channel_id(self) -> str:
        return self._channel_id


class PendingResponse:
    """Tracks a pending response from the agent."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.final_content: Optional[str] = None
        self.event = asyncio.Event()
        self.stream_queue: asyncio.Queue[Optional[ChatStreamEvent]] = asyncio.Queue()

    async def add_event(self, event_type: str, data: Any):
        """Add an event to the response."""
        event = {"type": event_type, "data": data, "timestamp": datetime.now().isoformat()}
        self.events.append(event)
        await self.stream_queue.put(ChatStreamEvent(event=EventType(event_type), data=data))

    def set_final(self, content: str):
        """Set the final response content."""
        self.final_content = content
        self.event.set()

    async def close_stream(self):
        """Close the stream queue."""
        await self.stream_queue.put(None)


class OpenAPIChannel(BaseChannel):
    """
    OpenAPI channel exposing HTTP endpoints for chat API.
    This channel works differently from others - it doesn't subscribe
    to outbound messages directly but uses request-response pattern.
    """

    name: str = "openapi"

    def __init__(
        self,
        config: OpenAPIChannelConfig,
        bus: MessageBus,
        workspace_path: Path | None = None,
        app: "FastAPI | None" = None,
    ):
        super().__init__(config, bus, workspace_path)
        self.config = config
        self._pending: Dict[str, PendingResponse] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._router: Optional[APIRouter] = None
        self._app = app  # External FastAPI app to register routes on
        self._server: Optional[asyncio.Task] = None  # Server task

    async def start(self) -> None:
        """Start the channel - register routes to external FastAPI app if provided."""
        self._running = True

        # Register routes to external FastAPI app
        if self._app is not None:
            self._setup_routes()

        logger.info("OpenAPI channel started")

    async def stop(self) -> None:
        """Stop the channel."""
        self._running = False
        # Complete all pending responses
        for pending in self._pending.values():
            pending.set_final("")
        logger.info("OpenAPI channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """
        Handle outbound messages - routes to pending responses.
        This is called by the message bus dispatcher.
        """
        session_id = msg.session_key.chat_id
        pending = self._pending.get(session_id)

        if not pending:
            # No pending request for this session, ignore
            return

        if msg.event_type == OutboundEventType.RESPONSE:
            # Final response - add to stream first
            await pending.add_event("response", msg.content or "")
            pending.set_final(msg.content or "")
            await pending.close_stream()
        elif msg.event_type == OutboundEventType.REASONING:
            await pending.add_event("reasoning", msg.content)
        elif msg.event_type == OutboundEventType.TOOL_CALL:
            await pending.add_event("tool_call", msg.content)
        elif msg.event_type == OutboundEventType.TOOL_RESULT:
            await pending.add_event("tool_result", msg.content)

    def get_router(self) -> APIRouter:
        """Get or create the FastAPI router."""
        if self._router is None:
            self._router = self._create_router()
        return self._router

    def _create_router(self) -> APIRouter:
        """Create the FastAPI router with all routes."""
        router = APIRouter()
        channel = self  # Capture for closures

        async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> bool:
            """Verify API key if configured."""
            if not channel.config.api_key:
                return True  # No auth required
            if not x_api_key:
                raise HTTPException(status_code=401, detail="X-API-Key header required")
            # Use secrets.compare_digest for timing-safe comparison
            if not secrets.compare_digest(x_api_key, channel.config.api_key):
                raise HTTPException(status_code=403, detail="Invalid API key")
            return True

        @router.get("/health", response_model=HealthResponse)
        async def health_check():
            """Health check endpoint."""
            from vikingbot import __version__

            return HealthResponse(
                status="healthy" if channel._running else "unhealthy",
                version=__version__,
            )

        @router.post("/chat", response_model=ChatResponse)
        async def chat(
            request: ChatRequest,
            authorized: bool = Depends(verify_api_key),
        ):
            """Send a chat message and get a response."""
            return await channel._handle_chat(request)

        @router.post("/chat/stream")
        async def chat_stream(
            request: ChatRequest,
            authorized: bool = Depends(verify_api_key),
        ):
            """Send a chat message and get a streaming response."""
            if not request.stream:
                request.stream = True
            return await channel._handle_chat_stream(request)

        @router.get("/sessions", response_model=SessionListResponse)
        async def list_sessions(
            authorized: bool = Depends(verify_api_key),
        ):
            """List all sessions."""
            sessions = []
            for session_id, session_data in channel._sessions.items():
                sessions.append(
                    SessionInfo(
                        id=session_id,
                        created_at=session_data.get("created_at", datetime.now()),
                        last_active=session_data.get("last_active", datetime.now()),
                        message_count=session_data.get("message_count", 0),
                    )
                )
            return SessionListResponse(sessions=sessions, total=len(sessions))

        @router.post("/sessions", response_model=SessionCreateResponse)
        async def create_session(
            request: SessionCreateRequest,
            authorized: bool = Depends(verify_api_key),
        ):
            """Create a new session."""
            session_id = str(uuid.uuid4())
            now = datetime.now()
            channel._sessions[session_id] = {
                "user_id": request.user_id,
                "created_at": now,
                "last_active": now,
                "message_count": 0,
                "metadata": request.metadata or {},
            }
            return SessionCreateResponse(session_id=session_id, created_at=now)

        @router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
        async def get_session(
            session_id: str,
            authorized: bool = Depends(verify_api_key),
        ):
            """Get session details."""
            if session_id not in channel._sessions:
                raise HTTPException(status_code=404, detail="Session not found")

            session_data = channel._sessions[session_id]
            info = SessionInfo(
                id=session_id,
                created_at=session_data.get("created_at", datetime.now()),
                last_active=session_data.get("last_active", datetime.now()),
                message_count=session_data.get("message_count", 0),
            )
            # Get messages from session manager if available
            messages = session_data.get("messages", [])
            return SessionDetailResponse(session=info, messages=messages)

        @router.delete("/sessions/{session_id}")
        async def delete_session(
            session_id: str,
            authorized: bool = Depends(verify_api_key),
        ):
            """Delete a session."""
            if session_id not in channel._sessions:
                raise HTTPException(status_code=404, detail="Session not found")

            del channel._sessions[session_id]
            return {"deleted": True}

        return router

    def _setup_routes(self) -> None:
        """Setup routes on the external FastAPI app."""
        if self._app is None:
            logger.warning("No external FastAPI app provided, cannot setup routes")
            return

        # Get the router and include it at root path
        # Note: openviking-server adds its own /bot/v1 prefix when proxying
        router = self.get_router()
        self._app.include_router(router, prefix="/bot/v1")
        logger.info("OpenAPI routes registered at root path")

    async def _handle_chat(self, request: ChatRequest) -> ChatResponse:
        """Handle a chat request."""
        # Generate or use provided session ID
        session_id = request.session_id or str(uuid.uuid4())
        user_id = request.user_id or "anonymous"

        # Create session if new
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "user_id": user_id,
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "message_count": 0,
                "messages": [],
            }

        # Update session activity
        self._sessions[session_id]["last_active"] = datetime.now()
        self._sessions[session_id]["message_count"] += 1

        # Create pending response tracker
        pending = PendingResponse()
        self._pending[session_id] = pending

        try:
            # Build session key
            session_key = SessionKey(
                type="cli",
                channel_id=self.config.channel_id(),
                chat_id=session_id,
            )

            # Build content with context if provided
            content = request.message
            if request.context:
                # Context is handled separately by session manager
                pass

            # Create and publish inbound message
            msg = InboundMessage(
                session_key=session_key,
                sender_id=user_id,
                content=content,
            )

            await self.bus.publish_inbound(msg)

            # Wait for response with timeout
            try:
                await asyncio.wait_for(pending.event.wait(), timeout=300.0)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Request timeout")

            # Build response
            response_content = pending.final_content or ""

            return ChatResponse(
                session_id=session_id,
                message=response_content,
                events=pending.events if pending.events else None,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error handling chat request: {e}")
            raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
        finally:
            # Clean up pending
            self._pending.pop(session_id, None)

    async def _handle_chat_stream(self, request: ChatRequest) -> StreamingResponse:
        """Handle a streaming chat request."""
        session_id = request.session_id or str(uuid.uuid4())
        user_id = request.user_id or "anonymous"

        # Create session if new
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "user_id": user_id,
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "message_count": 0,
                "messages": [],
            }

        self._sessions[session_id]["last_active"] = datetime.now()
        self._sessions[session_id]["message_count"] += 1

        pending = PendingResponse()
        self._pending[session_id] = pending

        async def event_generator():
            try:
                # Build session key and send message
                session_key = SessionKey(
                    type="cli",
                    channel_id=self.config.channel_id(),
                    chat_id=session_id,
                )

                msg = InboundMessage(
                    session_key=session_key,
                    sender_id=user_id,
                    content=request.message,
                )

                await self.bus.publish_inbound(msg)

                # Stream events as they arrive
                while True:
                    try:
                        event = await asyncio.wait_for(pending.stream_queue.get(), timeout=300.0)
                        if event is None:
                            break
                        yield f"data: {event.model_dump_json()}\n\n"
                    except asyncio.TimeoutError:
                        yield f"data: {ChatStreamEvent(event=EventType.RESPONSE, data={'error': 'timeout'}).model_dump_json()}\n\n"
                        break

            except Exception as e:
                logger.exception(f"Error in stream generator: {e}")
                error_event = ChatStreamEvent(event=EventType.RESPONSE, data={"error": str(e)})
                yield f"data: {error_event.model_dump_json()}\n\n"
            finally:
                self._pending.pop(session_id, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )


def get_openapi_router(bus: MessageBus, config: Config) -> APIRouter:
    """
    Create and return the OpenAPI router for mounting in FastAPI.

    This factory function creates an OpenAPIChannel and returns its router.
    The router should be mounted in the main FastAPI app.
    """
    # Find OpenAPI config from channels
    openapi_config = None
    for ch_config in config.channels:
        if isinstance(ch_config, dict) and ch_config.get("type") == "openapi":
            openapi_config = OpenAPIChannelConfig(**ch_config)
            break
        elif hasattr(ch_config, "type") and getattr(ch_config, "type", None) == "openapi":
            openapi_config = ch_config
            break

    if openapi_config is None:
        # Create default config
        openapi_config = OpenAPIChannelConfig()

    # Create channel and get router
    channel = OpenAPIChannel(
        config=openapi_config,
        bus=bus,
        workspace_path=config.workspace_path,
    )

    # Register channel's send method as subscriber for outbound messages
    bus.subscribe_outbound(
        f"cli__{openapi_config.channel_id()}",
        channel.send,
    )

    return channel.get_router()
