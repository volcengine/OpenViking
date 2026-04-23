"""OpenAPI channel for HTTP-based chat API."""

import asyncio
import json
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
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    SessionInfo,
    SessionListResponse,
)
from vikingbot.config.schema import (
    BaseChannelConfig,
    BotChannelConfig,
    Config,
    SessionKey,
    requires_gateway_token,
)
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.utils.helpers import ensure_dir


class PendingResponse:
    """Tracks a pending response from the agent."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.final_content: Optional[str] = None
        self.response_id: Optional[str] = None
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


class OpenAPIChannelConfig(BaseChannelConfig):
    """Configuration for OpenAPI channel."""

    enabled: bool = True
    type: str = "cli"
    allow_from: list[str] = []
    max_concurrent_requests: int = 100
    _channel_id: str = "default"

    def channel_id(self) -> str:
        return self._channel_id


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
        global_config: Config | None = None,
    ):
        super().__init__(config, bus, workspace_path)
        self.config = config
        self._global_config = global_config
        # Regular OpenAPI pending and sessions
        self._pending: Dict[str, PendingResponse] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        # BotChannel pending and sessions - key is channel_id
        self._bot_pending: Dict[str, Dict[str, PendingResponse]] = {}
        self._bot_sessions: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # BotChannel configs - key is channel_id
        self._bot_configs: Dict[str, BotChannelConfig] = {}
        self._router: Optional[APIRouter] = None
        self._app = app  # External FastAPI app to register routes on
        self._server: Optional[asyncio.Task] = None  # Server task
        self._langfuse = LangfuseClient.get_instance()
        self._response_index: Dict[str, Dict[str, Any]] = {}
        self._feedback_lock = asyncio.Lock()
        self._response_lock = asyncio.Lock()
        self._outcome_lock = asyncio.Lock()

        storage_root = (
            self._global_config.bot_data_path if self._global_config is not None else workspace_path
        ) or Path.cwd()
        feedback_dir = ensure_dir(storage_root / "feedback")
        self._feedback_file = feedback_dir / "feedback.jsonl"
        self._responses_file = feedback_dir / "responses.jsonl"
        self._outcomes_file = feedback_dir / "outcomes.jsonl"

        # Load BotChannel configurations immediately in constructor
        # so that subscriptions are setup before ChannelManager starts
        self._load_bot_channels()
        self._load_response_index()

    def _index_response(self, msg: OutboundMessage) -> None:
        """Remember terminal responses so feedback can be linked later."""
        if not msg.response_id or not msg.response_completed:
            return

        self._response_index[msg.response_id] = {
            "response_id": msg.response_id,
            "session_id": msg.session_key.chat_id,
            "session_key": msg.response_completed.session_id,
            "channel": msg.session_key.channel_key(),
            "user_id": msg.response_completed.user_id if msg.response_completed else None,
            "event_type": msg.event_type.value,
            "timestamp": (
                msg.response_completed.timestamp.isoformat()
                if msg.response_completed
                else datetime.now().isoformat()
            ),
        }

    def _load_response_index(self) -> None:
        """Load persisted response metadata for future feedback lookups."""
        if not self._responses_file.exists():
            return

        try:
            with open(self._responses_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    response_id = data.get("response_id")
                    if not response_id:
                        continue
                    self._response_index[response_id] = data
        except Exception as e:
            logger.warning(f"Failed to load persisted response index: {e}")

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _classify_outcome(reask_within_window: bool) -> str:
        return "follow_up_needed" if reask_within_window else "unresolved"

    def _build_outcome_record(
        self,
        response_info: Dict[str, Any],
        *,
        user_id: str | None,
        current_message: str,
        current_timestamp: datetime,
        window_seconds: int = 900,
    ) -> Dict[str, Any] | None:
        response_id = response_info.get("response_id")
        if not response_id:
            return None

        response_timestamp = self._parse_timestamp(response_info.get("timestamp"))
        if response_timestamp is None:
            return None

        delta_seconds = max((current_timestamp - response_timestamp).total_seconds(), 0.0)
        reask_within_window = delta_seconds <= window_seconds

        return {
            "event_type": "response_outcome_evaluated",
            "response_id": response_id,
            "session_id": response_info.get("session_id"),
            "session_key": response_info.get("session_key"),
            "channel": response_info.get("channel"),
            "user_id": user_id or response_info.get("user_id"),
            "evaluated_at": current_timestamp.isoformat(),
            "evaluation_type": "follow_up_message",
            "follow_up_message": current_message,
            "follow_up_delay_seconds": round(delta_seconds, 3),
            "reask_within_window": reask_within_window,
            "one_turn_resolution": False,
            "outcome_label": self._classify_outcome(reask_within_window),
        }

    def _find_latest_response_for_session(
        self, session_id: str, channel: str | None = None
    ) -> Dict[str, Any] | None:
        latest: Dict[str, Any] | None = None
        latest_ts: datetime | None = None
        for response_info in self._response_index.values():
            if response_info.get("session_id") != session_id:
                continue
            if channel and response_info.get("channel") != channel:
                continue
            ts = self._parse_timestamp(response_info.get("timestamp"))
            if ts is None:
                continue
            if latest is None or latest_ts is None or ts > latest_ts:
                latest = response_info
                latest_ts = ts
        return latest

    async def _store_outcome(self, record: Dict[str, Any]) -> None:
        """Append an implicit outcome evaluation to the local JSONL store."""
        async with self._outcome_lock:
            with open(self._outcomes_file, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._sync_langfuse_outcome(record)

    async def _evaluate_follow_up_outcome(
        self,
        *,
        session_id: str,
        channel: str | None,
        user_id: str | None,
        current_message: str,
        current_timestamp: datetime,
    ) -> None:
        response_info = self._find_latest_response_for_session(session_id, channel)
        if not response_info:
            return
        outcome_record = self._build_outcome_record(
            response_info,
            user_id=user_id,
            current_message=current_message,
            current_timestamp=current_timestamp,
        )
        if not outcome_record:
            return
        await self._store_outcome(outcome_record)

    async def _store_response(self, msg: OutboundMessage) -> None:
        """Persist response metadata so feedback survives process restarts."""
        if not msg.response_id or not msg.response_completed:
            return

        record = {
            "event_type": "response_completed",
            "response_id": msg.response_completed.response_id,
            "session_id": msg.session_key.chat_id,
            "session_key": msg.response_completed.session_id,
            "channel": msg.response_completed.channel,
            "user_id": msg.response_completed.user_id,
            "token_usage": msg.response_completed.token_usage,
            "time_cost": msg.response_completed.time_cost,
            "iteration": msg.response_completed.iteration,
            "tools_used_names": msg.response_completed.tools_used_names,
            "timestamp": msg.response_completed.timestamp.isoformat(),
        }
        async with self._response_lock:
            with open(self._responses_file, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._sync_langfuse_response(record)

    async def _store_feedback(self, record: Dict[str, Any]) -> None:
        """Append a feedback record to the local JSONL store."""
        async with self._feedback_lock:
            with open(self._feedback_file, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._sync_langfuse_feedback(record)

    @staticmethod
    def _langfuse_session_id(record: Dict[str, Any]) -> str | None:
        return record.get("session_key") or record.get("session_id")

    def _sync_langfuse_response(self, record: Dict[str, Any]) -> None:
        if not self._langfuse.enabled:
            return

        metadata = {
            "event_type": record.get("event_type"),
            "response_id": record.get("response_id"),
            "session_id": record.get("session_id"),
            "channel": record.get("channel"),
            "token_usage": record.get("token_usage"),
            "time_cost": record.get("time_cost"),
            "iteration": record.get("iteration"),
            "tools_used_names": record.get("tools_used_names"),
            "timestamp": record.get("timestamp"),
        }
        self._langfuse.log_event(
            "response_completed",
            session_id=self._langfuse_session_id(record),
            user_id=record.get("user_id"),
            metadata=metadata,
        )

    def _sync_langfuse_feedback(self, record: Dict[str, Any]) -> None:
        if not self._langfuse.enabled:
            return

        metadata = {
            "event_type": record.get("event_type"),
            "feedback_id": record.get("feedback_id"),
            "response_id": record.get("response_id"),
            "session_id": record.get("session_id"),
            "channel": record.get("channel"),
            "rating": record.get("rating"),
            "comment": record.get("comment"),
            "timestamp": record.get("timestamp"),
        }
        self._langfuse.log_event(
            "feedback_submitted",
            session_id=self._langfuse_session_id(record),
            user_id=record.get("user_id"),
            metadata=metadata,
        )

        rating = record.get("rating")
        if rating in {"positive", "negative"}:
            self._langfuse.log_score(
                "user_feedback",
                1.0 if rating == "positive" else 0.0,
                session_id=self._langfuse_session_id(record),
                user_id=record.get("user_id"),
                comment=record.get("comment"),
                metadata={
                    "response_id": record.get("response_id"),
                    "feedback_id": record.get("feedback_id"),
                    "session_id": record.get("session_id"),
                    "channel": record.get("channel"),
                    "timestamp": record.get("timestamp"),
                },
            )

    def _sync_langfuse_outcome(self, record: Dict[str, Any]) -> None:
        if not self._langfuse.enabled:
            return

        metadata = {
            "event_type": record.get("event_type"),
            "response_id": record.get("response_id"),
            "session_id": record.get("session_id"),
            "channel": record.get("channel"),
            "evaluated_at": record.get("evaluated_at"),
            "evaluation_type": record.get("evaluation_type"),
            "follow_up_message": record.get("follow_up_message"),
            "follow_up_delay_seconds": record.get("follow_up_delay_seconds"),
            "reask_within_window": record.get("reask_within_window"),
            "one_turn_resolution": record.get("one_turn_resolution"),
            "outcome_label": record.get("outcome_label"),
        }
        self._langfuse.log_event(
            "response_outcome_evaluated",
            session_id=self._langfuse_session_id(record),
            user_id=record.get("user_id"),
            metadata=metadata,
        )
        self._langfuse.log_score(
            "reask_within_window",
            1.0 if record.get("reask_within_window") else 0.0,
            session_id=self._langfuse_session_id(record),
            user_id=record.get("user_id"),
            metadata={
                "response_id": record.get("response_id"),
                "session_id": record.get("session_id"),
                "channel": record.get("channel"),
                "evaluated_at": record.get("evaluated_at"),
                "outcome_label": record.get("outcome_label"),
            },
        )

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
        # Complete all bot pending responses
        for pending_dict in self._bot_pending.values():
            for pending in pending_dict.values():
                pending.set_final("")
        logger.info("OpenAPI channel stopped")

    def _load_bot_channels(self) -> None:
        """Load all BotChannel configurations from the global config."""
        if self._global_config is None:
            logger.warning("No global config provided, cannot load BotChannels")
            return

        # Get all channel configs
        channels_config = self._global_config.channels_config
        all_channel_configs = channels_config.get_all_channels()

        for ch_config in all_channel_configs:
            if isinstance(ch_config, BotChannelConfig) or (
                hasattr(ch_config, "type") and getattr(ch_config, "type", None) == "bot_api"
            ):
                if isinstance(ch_config, dict):
                    bot_config = BotChannelConfig(**ch_config)
                else:
                    bot_config = ch_config

                if not bot_config.enabled:
                    continue

                channel_id = bot_config.channel_id()
                self._bot_configs[channel_id] = bot_config
                # Initialize pending and sessions for this channel
                self._bot_pending[channel_id] = {}
                self._bot_sessions[channel_id] = {}
                logger.info(f"Loaded BotChannel config: {channel_id}")

        # Instead of subscribing per channel, we'll check session type in send()
        # This is simpler and avoids subscription timing issues

    async def send(self, msg: OutboundMessage) -> None:
        """
        Handle outbound messages - routes to pending responses.
        This is called by the message bus dispatcher.
        """
        # Check if this message is for a BotChannel
        if msg.session_key.type == "bot_api":
            channel_id = msg.session_key.channel_id
            session_id = msg.session_key.chat_id

            if channel_id not in self._bot_pending:
                logger.warning(f"Unknown BotChannel: {channel_id}")
                return

            pending = self._bot_pending[channel_id].get(session_id)
            if not pending:
                logger.warning(
                    f"No pending request for BotChannel {channel_id} session: {session_id}"
                )
                return

            if (
                msg.event_type == OutboundEventType.RESPONSE
                or msg.event_type == OutboundEventType.NO_REPLY
            ):
                await self._store_response(msg)
                self._index_response(msg)
                pending.response_id = msg.response_id
                await pending.add_event("response", msg.content or "")
                pending.set_final(msg.content or "")
                await pending.close_stream()
            elif msg.event_type == OutboundEventType.REASONING:
                await pending.add_event("reasoning", msg.content)
            elif msg.event_type == OutboundEventType.TOOL_CALL:
                await pending.add_event("tool_call", msg.content)
            elif msg.event_type == OutboundEventType.TOOL_RESULT:
                await pending.add_event("tool_result", msg.content)
            return

        # Handle as normal OpenAPIChannel message
        session_id = msg.session_key.chat_id
        pending = self._pending.get(session_id)

        if not pending:
            # No pending request for this session, ignore
            return

        if msg.event_type == OutboundEventType.RESPONSE:
            # Final response - add to stream first
            await self._store_response(msg)
            self._index_response(msg)
            pending.response_id = msg.response_id
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

        async def verify_api_key(
            x_gateway_token: Optional[str] = Header(None, alias="X-Gateway-Token")
        ) -> bool:
            """Verify API key for privileged HTTP chat/session routes."""
            gateway_token = ""
            gateway_host = "127.0.0.1"
            if channel._global_config is not None:
                gateway = getattr(channel._global_config, "gateway", None)
                gateway_host = getattr(gateway, "host", "127.0.0.1") or "127.0.0.1"
                gateway_token = getattr(gateway, "token", "") or ""
            if not gateway_token:
                if requires_gateway_token(gateway_host, gateway_token):
                    raise HTTPException(
                        status_code=503,
                        detail="OpenAPI gateway token is required when host is non-localhost",
                    )
                return True
            if not x_gateway_token:
                raise HTTPException(status_code=401, detail="X-Gateway-Token header required")
            # Use secrets.compare_digest for timing-safe comparison
            if not secrets.compare_digest(x_gateway_token, gateway_token):
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

        @router.post("/feedback", response_model=FeedbackResponse)
        async def submit_feedback(
            request: FeedbackRequest,
            authorized: bool = Depends(verify_api_key),
        ):
            """Submit explicit feedback for a prior response."""
            return await channel._handle_feedback(request)

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

        # ========== Bot Channel Routes ==========

        @router.post("/chat/channel", response_model=ChatResponse)
        async def chat_channel(
            request: ChatRequest,
            authorized: bool = Depends(verify_api_key),
        ):
            """Send a chat message to a specific bot channel and get a response."""
            channel_id = request.channel_id
            if not channel_id:
                raise HTTPException(status_code=400, detail="channel_id is required")
            if channel_id not in channel._bot_configs:
                raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

            return await channel._handle_bot_chat(channel_id, request)

        @router.post("/chat/channel/stream")
        async def chat_channel_stream(
            request: ChatRequest,
            authorized: bool = Depends(verify_api_key),
        ):
            """Send a chat message to a specific bot channel and get a streaming response."""
            channel_id = request.channel_id
            if not channel_id:
                raise HTTPException(status_code=400, detail="channel_id is required")
            if channel_id not in channel._bot_configs:
                raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

            if not request.stream:
                request.stream = True
            return await channel._handle_bot_chat_stream(channel_id, request)

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
        now = datetime.now()

        # Create session if new
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "user_id": user_id,
                "created_at": now,
                "last_active": now,
                "message_count": 0,
                "messages": [],
            }
        else:
            await self._evaluate_follow_up_outcome(
                session_id=session_id,
                channel=f"cli__{self.config.channel_id()}",
                user_id=user_id,
                current_message=request.message,
                current_timestamp=now,
            )

        # Update session activity
        self._sessions[session_id]["last_active"] = now
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
                response_id=pending.response_id,
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

    async def _handle_feedback(self, request: FeedbackRequest) -> FeedbackResponse:
        """Handle explicit user feedback for a response."""
        feedback_id = str(uuid.uuid4())
        now = datetime.now()
        response_info = self._response_index.get(request.response_id, {})
        record = {
            "event_type": "feedback_submitted",
            "feedback_id": feedback_id,
            "response_id": request.response_id,
            "rating": request.rating.value,
            "comment": request.comment,
            "session_id": request.session_id or response_info.get("session_id"),
            "session_key": response_info.get("session_key"),
            "channel": response_info.get("channel"),
            "user_id": request.user_id or response_info.get("user_id"),
            "timestamp": now.isoformat(),
        }
        await self._store_feedback(record)
        return FeedbackResponse(
            feedback_id=feedback_id,
            response_id=request.response_id,
            accepted=True,
            timestamp=now,
        )

    async def _handle_chat_stream(self, request: ChatRequest) -> StreamingResponse:
        """Handle a streaming chat request."""
        session_id = request.session_id or str(uuid.uuid4())
        user_id = request.user_id or "anonymous"
        now = datetime.now()

        # Create session if new
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "user_id": user_id,
                "created_at": now,
                "last_active": now,
                "message_count": 0,
                "messages": [],
            }
        else:
            await self._evaluate_follow_up_outcome(
                session_id=session_id,
                channel=f"cli__{self.config.channel_id()}",
                user_id=user_id,
                current_message=request.message,
                current_timestamp=now,
            )

        self._sessions[session_id]["last_active"] = now
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

    async def _handle_bot_chat(self, channel_id: str, request: ChatRequest) -> ChatResponse:
        """Handle a BotChannel chat request."""
        # Generate or use provided session ID
        session_id = request.session_id or str(uuid.uuid4())
        user_id = request.user_id or "anonymous"
        now = datetime.now()

        # Ensure channel has session storage
        if channel_id not in self._bot_sessions:
            self._bot_sessions[channel_id] = {}

        # Create session if new
        if session_id not in self._bot_sessions[channel_id]:
            self._bot_sessions[channel_id][session_id] = {
                "user_id": user_id,
                "created_at": now,
                "last_active": now,
                "message_count": 0,
                "messages": [],
            }
        else:
            await self._evaluate_follow_up_outcome(
                session_id=session_id,
                channel=f"bot_api__{channel_id}",
                user_id=user_id,
                current_message=request.message,
                current_timestamp=now,
            )

        # Update session activity
        self._bot_sessions[channel_id][session_id]["last_active"] = now
        self._bot_sessions[channel_id][session_id]["message_count"] += 1

        # Create pending response tracker
        pending = PendingResponse()
        self._bot_pending[channel_id][session_id] = pending

        try:
            # Build session key with bot_api type
            session_key = SessionKey(
                type="bot_api",
                channel_id=channel_id,
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
                need_reply=request.need_reply,
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
                response_id=pending.response_id,
                message=response_content,
                events=pending.events if pending.events else None,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error handling bot chat request for channel {channel_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
        finally:
            # Clean up pending
            if channel_id in self._bot_pending:
                self._bot_pending[channel_id].pop(session_id, None)

    async def _handle_bot_chat_stream(
        self, channel_id: str, request: ChatRequest
    ) -> StreamingResponse:
        """Handle a BotChannel streaming chat request."""
        session_id = request.session_id or str(uuid.uuid4())
        user_id = request.user_id or "anonymous"
        now = datetime.now()

        # Ensure channel has session storage
        if channel_id not in self._bot_sessions:
            self._bot_sessions[channel_id] = {}

        # Create session if new
        if session_id not in self._bot_sessions[channel_id]:
            self._bot_sessions[channel_id][session_id] = {
                "user_id": user_id,
                "created_at": now,
                "last_active": now,
                "message_count": 0,
                "messages": [],
            }
        else:
            await self._evaluate_follow_up_outcome(
                session_id=session_id,
                channel=f"bot_api__{channel_id}",
                user_id=user_id,
                current_message=request.message,
                current_timestamp=now,
            )

        self._bot_sessions[channel_id][session_id]["last_active"] = now
        self._bot_sessions[channel_id][session_id]["message_count"] += 1

        pending = PendingResponse()
        self._bot_pending[channel_id][session_id] = pending

        async def event_generator():
            try:
                # Build session key with bot_api type
                session_key = SessionKey(
                    type="bot_api",
                    channel_id=channel_id,
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
                logger.exception(f"Error in bot stream generator for channel {channel_id}: {e}")
                error_event = ChatStreamEvent(event=EventType.RESPONSE, data={"error": str(e)})
                yield f"data: {error_event.model_dump_json()}\n\n"
            finally:
                # Clean up pending
                if channel_id in self._bot_pending:
                    self._bot_pending[channel_id].pop(session_id, None)

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
        # Check for OpenAPI config
        if isinstance(ch_config, dict) and ch_config.get("type") == "openapi":
            openapi_config = OpenAPIChannelConfig(**ch_config)
            break
        elif hasattr(ch_config, "type") and getattr(ch_config, "type", None) == "openapi":
            openapi_config = ch_config
            break

    if openapi_config is None:
        # Create default config
        openapi_config = OpenAPIChannelConfig()

    # Create channel and get router - pass global config for BotChannel loading
    channel = OpenAPIChannel(
        config=openapi_config,
        bus=bus,
        workspace_path=config.workspace_path,
        global_config=config,
    )

    # Register channel's send method as subscriber for outbound messages
    # Subscribe to cli type
    bus.subscribe_outbound(
        f"cli__{openapi_config.channel_id()}",
        channel.send,
    )

    # Subscribe to all bot_api channels that were loaded
    for channel_id in channel._bot_configs.keys():
        bus.subscribe_outbound(
            f"bot_api__{channel_id}",
            channel.send,
        )
        logger.info(f"Subscribed to bot_api channel: {channel_id}")

    return channel.get_router()
