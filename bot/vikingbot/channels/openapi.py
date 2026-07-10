"""OpenAPI channel for HTTP-based chat API."""

import asyncio
import hashlib
import ipaddress
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
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
    OpenVikingConnection,
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
)
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.observability.outcome import evaluate_response_outcome, should_update_outcome
from vikingbot.session.manager import SessionManager

DEFAULT_OPENVIKING_AGENT_ID = "web-playground"
DEFAULT_NAMESPACE_POLICY = {
    "isolate_user_scope_by_agent": False,
    "isolate_agent_scope_by_user": False,
}
OPENVIKING_AUTH_TIMEOUT_SECONDS = 5.0
OPENVIKING_PROXY_TIMEOUT_SECONDS = 300.0
OPENVIKING_UPSTREAM_NOT_CONFIGURED_DETAIL = (
    "VikingBot gateway proxy is active, but no available OpenViking server is configured"
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class GatewayRequestAuth:
    """Resolved gateway-level auth facts for one HTTP request."""

    gateway_token_configured: bool
    gateway_token_valid: bool
    loopback_request: bool
    forwarded_connection_trusted: bool = False

    @property
    def can_trust_forwarded_connection(self) -> bool:
        return self.forwarded_connection_trusted


class PendingResponse:
    """Tracks a pending response from the agent."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.final_content: Optional[str] = None
        self.response_id: Optional[str] = None
        self.relevant_memories: Optional[str] = None
        self.token_usage: Dict[str, int] = {}
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

    def set_response_id(self, response_id: str | None):
        """Track the response ID for the final assistant response."""
        self.response_id = response_id

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
        self._gateway_router: Optional[APIRouter] = None
        self._app = app  # External FastAPI app to register routes on
        self._server: Optional[asyncio.Task] = None  # Server task
        self._session_manager = SessionManager(self._resolve_bot_data_path())

        # Load BotChannel configurations immediately in constructor
        # so that subscriptions are setup before ChannelManager starts
        self._load_bot_channels()

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

        # Some tests and lightweight callers only provide gateway settings.
        channels_config = getattr(self._global_config, "channels_config", None)
        if channels_config is None:
            return

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
                pending.set_response_id(msg.response_id)
                pending.relevant_memories = (msg.metadata or {}).get("relevant_memories")
                pending.token_usage = msg.token_usage
                await pending.add_event(
                    "response",
                    {"content": msg.content or "", "response_id": msg.response_id},
                )
                pending.set_final(msg.content or "")
                await pending.close_stream()
            elif msg.event_type == OutboundEventType.REASONING:
                await pending.add_event("reasoning", msg.content)
            elif msg.event_type == OutboundEventType.CONTENT_DELTA:
                await pending.add_event("content_delta", msg.content)
            elif msg.event_type == OutboundEventType.REASONING_DELTA:
                await pending.add_event("reasoning_delta", msg.content)
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
            pending.set_response_id(msg.response_id)
            pending.token_usage = msg.token_usage
            pending.relevant_memories = (msg.metadata or {}).get("relevant_memories")
            await pending.add_event(
                "response",
                {"content": msg.content or "", "response_id": msg.response_id},
            )
            pending.set_final(msg.content or "")
            await pending.close_stream()
        elif msg.event_type == OutboundEventType.REASONING:
            await pending.add_event("reasoning", msg.content)
        elif msg.event_type == OutboundEventType.CONTENT_DELTA:
            await pending.add_event("content_delta", msg.content)
        elif msg.event_type == OutboundEventType.REASONING_DELTA:
            await pending.add_event("reasoning_delta", msg.content)
        elif msg.event_type == OutboundEventType.TOOL_CALL:
            await pending.add_event("tool_call", msg.content)
        elif msg.event_type == OutboundEventType.TOOL_RESULT:
            await pending.add_event("tool_result", msg.content)

    def get_router(self) -> APIRouter:
        """Get or create the FastAPI router."""
        if self._router is None:
            self._router = self._create_router()
        return self._router

    def get_gateway_router(self) -> APIRouter:
        """Get or create root-level gateway routes."""
        if self._gateway_router is None:
            self._gateway_router = self._create_gateway_router()
        return self._gateway_router

    def _resolve_bot_data_path(self) -> Path:
        """Resolve the bot data path used for session persistence."""
        if self._global_config is not None and hasattr(self._global_config, "bot_data_path"):
            return Path(self._global_config.bot_data_path)
        if self.workspace_path is not None:
            return (
                self.workspace_path.parent
                if self.workspace_path.name == "workspace"
                else self.workspace_path
            )
        return Path("~/.openviking/data/bot").expanduser()

    def _create_router(self) -> APIRouter:
        """Create the FastAPI router with all routes."""
        router = APIRouter()
        channel = self  # Capture for closures

        async def verify_gateway_request(
            http_request: Request,
            x_gateway_token: Optional[str] = Header(None, alias="X-Gateway-Token"),
        ) -> GatewayRequestAuth:
            """Verify gateway access and resolve caller OpenViking identity when needed."""
            return await channel._verify_gateway_request(http_request, x_gateway_token)

        @router.get("/health", response_model=HealthResponse)
        async def health_check(
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Health check endpoint."""
            from vikingbot import __version__

            return HealthResponse(
                status="healthy" if channel._running else "unhealthy",
                version=__version__,
            )

        @router.post("/chat", response_model=ChatResponse)
        async def chat(
            request: ChatRequest,
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Send a chat message and get a response."""
            await channel._prepare_chat_request(http_request, request, auth)
            return await channel._handle_chat(request)

        @router.post("/chat/stream")
        async def chat_stream(
            request: ChatRequest,
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Send a chat message and get a streaming response."""
            if not request.stream:
                request.stream = True
            await channel._prepare_chat_request(http_request, request, auth)
            return await channel._handle_chat_stream(request)

        @router.post("/feedback", response_model=FeedbackResponse)
        async def submit_feedback(
            request: FeedbackRequest,
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Submit explicit user feedback for a prior assistant response."""
            if request.openviking_connection is not None:
                if not auth.can_trust_forwarded_connection:
                    raise HTTPException(
                        status_code=403,
                        detail=("openviking_connection is only accepted from trusted server proxy"),
                    )
                await channel._assert_runtime_upstream_auth_mode(
                    channel._identity_headers_from_connection(request.openviking_connection)
                )
                request._principal_scope = channel._connection_principal_scope(
                    request.openviking_connection
                )
            else:
                request._principal_scope = await channel._resolve_request_principal(
                    http_request,
                    auth,
                )
            return await channel._handle_feedback(request)

        @router.get("/sessions", response_model=SessionListResponse)
        async def list_sessions(
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """List all sessions."""
            scope = await channel._resolve_request_principal(http_request, auth)
            sessions = []
            for session_data in channel._sessions.values():
                if session_data.get("principal_scope") != scope:
                    continue
                sessions.append(
                    SessionInfo(
                        id=session_data["session_id"],
                        created_at=session_data.get("created_at", datetime.now()),
                        last_active=session_data.get("last_active", datetime.now()),
                        message_count=session_data.get("message_count", 0),
                    )
                )
            return SessionListResponse(sessions=sessions, total=len(sessions))

        @router.post("/sessions", response_model=SessionCreateResponse)
        async def create_session(
            request: SessionCreateRequest,
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Create a new session."""
            scope = await channel._resolve_request_principal(http_request, auth)
            session_id = str(uuid.uuid4())
            storage_key = channel._scoped_session_id(scope, session_id)
            now = datetime.now()
            channel._sessions[storage_key] = {
                "session_id": session_id,
                "principal_scope": scope,
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
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Get session details."""
            scope = await channel._resolve_request_principal(http_request, auth)
            storage_key = channel._scoped_session_id(scope, session_id)
            if storage_key not in channel._sessions:
                raise HTTPException(status_code=404, detail="Session not found")

            session_data = channel._sessions[storage_key]
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
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Delete a session."""
            scope = await channel._resolve_request_principal(http_request, auth)
            storage_key = channel._scoped_session_id(scope, session_id)
            if storage_key not in channel._sessions:
                raise HTTPException(status_code=404, detail="Session not found")

            del channel._sessions[storage_key]
            return {"deleted": True}

        # ========== Bot Channel Routes ==========

        @router.post("/chat/channel", response_model=ChatResponse)
        async def chat_channel(
            request: ChatRequest,
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Send a chat message to a specific bot channel and get a response."""
            channel_id = request.channel_id
            if not channel_id:
                raise HTTPException(status_code=400, detail="channel_id is required")
            if channel_id not in channel._bot_configs:
                raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

            await channel._prepare_chat_request(http_request, request, auth)
            return await channel._handle_bot_chat(channel_id, request)

        @router.post("/chat/channel/stream")
        async def chat_channel_stream(
            request: ChatRequest,
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            """Send a chat message to a specific bot channel and get a streaming response."""
            channel_id = request.channel_id
            if not channel_id:
                raise HTTPException(status_code=400, detail="channel_id is required")
            if channel_id not in channel._bot_configs:
                raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

            if not request.stream:
                request.stream = True
            await channel._prepare_chat_request(http_request, request, auth)
            return await channel._handle_bot_chat_stream(channel_id, request)

        return router

    def _create_gateway_router(self) -> APIRouter:
        """Create root-level gateway routes for health and OpenViking proxy."""
        router = APIRouter()
        channel = self

        async def verify_gateway_request(
            http_request: Request,
            x_gateway_token: Optional[str] = Header(None, alias="X-Gateway-Token"),
        ) -> GatewayRequestAuth:
            return await channel._verify_gateway_request(http_request, x_gateway_token)

        @router.get("/health")
        async def gateway_health(
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            return await channel._gateway_health(http_request)

        @router.api_route(
            "/api/v1/{path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        )
        async def proxy_openviking_api(
            path: str,
            http_request: Request,
            auth: GatewayRequestAuth = Depends(verify_gateway_request),
        ):
            return await channel._proxy_openviking_request(http_request, path)

        return router

    def _gateway_host(self) -> str:
        gateway = getattr(self._global_config, "gateway", None) if self._global_config else None
        return str(getattr(gateway, "host", "127.0.0.1") or "127.0.0.1").strip()

    def _gateway_token(self) -> str:
        gateway = getattr(self._global_config, "gateway", None) if self._global_config else None
        return str(getattr(gateway, "token", "") or "").strip()

    def _ov_server_config(self) -> Any | None:
        if self._global_config is None:
            return None
        return getattr(self._global_config, "ov_server", None)

    def _ov_server_url(self) -> str:
        ov_server = self._ov_server_config()
        return str(getattr(ov_server, "server_url", "") or "").strip().rstrip("/")

    def _ov_server_auth_mode(self) -> str:
        ov_server = self._ov_server_config()
        if ov_server is None or not self._ov_server_url():
            return ""
        effective = str(getattr(ov_server, "effective_auth_mode", "") or "").strip().lower()
        if effective in {"api_key", "trusted", "dev"}:
            return effective
        api_key_type = str(getattr(ov_server, "api_key_type", "") or "").strip().lower()
        if api_key_type == "root":
            return "trusted"
        return "api_key"

    def _ov_server_source(self) -> str:
        ov_server = self._ov_server_config()
        getter = getattr(ov_server, "get_config_source", None)
        source = getter() if callable(getter) else getattr(ov_server, "_source", "none")
        source = str(source or "none").strip().lower()
        return source if source in {"explicit", "inherited", "none"} else "none"

    def _ov_server_api_key_source(self) -> str:
        ov_server = self._ov_server_config()
        getter = getattr(ov_server, "get_api_key_source", None)
        source = getter() if callable(getter) else getattr(ov_server, "_api_key_source", "none")
        source = str(source or "none").strip().lower()
        allowed = {"bot.ov_server.api_key", "server.root_api_key", "none"}
        return source if source in allowed else "none"

    def _ov_server_is_server_managed(self) -> bool:
        ov_server = self._ov_server_config()
        getter = getattr(ov_server, "is_server_managed", None)
        if callable(getter):
            return bool(getter())
        return bool(getattr(ov_server, "_server_managed", False))

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        host = str(host or "").strip().lower()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if host in {"localhost", "127.0.0.1", "::1"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @classmethod
    def _is_loopback_url(cls, url: str) -> bool:
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            return False
        return cls._is_loopback_host(host)

    @classmethod
    def _is_loopback_request(cls, request: Request) -> bool:
        client = getattr(request, "client", None)
        return cls._is_loopback_host(getattr(client, "host", "") or "")

    def _is_gateway_localhost(self) -> bool:
        return self._is_loopback_host(self._gateway_host())

    def _is_safe_dev_boundary(self) -> bool:
        return self._is_gateway_localhost() and self._is_loopback_url(self._ov_server_url())

    @staticmethod
    def _has_openviking_auth_headers(request: Request) -> bool:
        return bool(
            request.headers.get("X-API-Key")
            or request.headers.get("x-api-key")
            or request.headers.get("Authorization")
            or request.headers.get("authorization")
        )

    @staticmethod
    def _extract_api_key(request: Request) -> str:
        api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        if api_key:
            return api_key.strip()
        authorization = request.headers.get("Authorization") or request.headers.get("authorization")
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return ""

    @staticmethod
    def _identity_headers_from_request(request: Request) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name in (
            "X-API-Key",
            "Authorization",
            "X-OpenViking-Account",
            "X-OpenViking-User",
            "X-OpenViking-Actor-Peer",
        ):
            value = request.headers.get(name)
            if value:
                headers[name] = value
        return headers

    async def _request_upstream_health_with_headers(
        self,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        server_url = self._ov_server_url()
        if not server_url:
            raise HTTPException(
                status_code=503,
                detail=OPENVIKING_UPSTREAM_NOT_CONFIGURED_DETAIL,
            )
        try:
            async with httpx.AsyncClient(
                timeout=OPENVIKING_AUTH_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                response = await client.get(
                    f"{server_url}/health",
                    headers=headers or {},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OpenViking upstream health check failed: {exc.__class__.__name__}",
            ) from exc

        if response.status_code in {401, 403}:
            raise HTTPException(status_code=401, detail="Invalid OpenViking credentials")
        if not 200 <= response.status_code < 300:
            raise HTTPException(
                status_code=502,
                detail=f"OpenViking upstream health check failed: HTTP {response.status_code}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="OpenViking upstream health check returned non-JSON response",
            ) from exc
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=502,
                detail="OpenViking upstream health check returned invalid response",
            )
        return data

    async def _request_upstream_health(self, request: Request) -> dict[str, Any]:
        return await self._request_upstream_health_with_headers(
            self._identity_headers_from_request(request)
        )

    def _runtime_probe_headers(self) -> dict[str, str]:
        ov_server = self._ov_server_config()
        if ov_server is None:
            return {}
        headers: dict[str, str] = {}
        api_key = str(getattr(ov_server, "api_key", "") or "").strip()
        if api_key:
            headers["X-API-Key"] = api_key
        if self._ov_server_auth_mode() == "trusted":
            account = str(getattr(ov_server, "account_id", "") or "").strip()
            user = str(getattr(ov_server, "admin_user_id", "") or "").strip()
            if account:
                headers["X-OpenViking-Account"] = account
            if user:
                headers["X-OpenViking-User"] = user
        return headers

    @staticmethod
    def _identity_headers_from_connection(connection: Any) -> dict[str, str]:
        if isinstance(connection, OpenVikingConnection):
            values = connection.model_dump(exclude_none=True)
        elif isinstance(connection, dict):
            values = connection
        else:
            return {}
        headers: dict[str, str] = {}
        mapping = {
            "api_key": "X-API-Key",
            "account_id": "X-OpenViking-Account",
            "user_id": "X-OpenViking-User",
            "actor_peer_id": "X-OpenViking-Actor-Peer",
        }
        for field, header in mapping.items():
            value = str(values.get(field) or "").strip()
            if value:
                headers[header] = value
        return headers

    def _assert_runtime_health_mode(self, health: dict[str, Any]) -> None:
        expected_auth_mode = self._ov_server_auth_mode()
        if not expected_auth_mode:
            return
        actual_auth_mode = str(health.get("auth_mode") or "").strip().lower()
        if not actual_auth_mode:
            raise HTTPException(
                status_code=503,
                detail="OpenViking upstream health response did not include auth_mode",
            )

        if actual_auth_mode == "dev" and not self._is_safe_dev_boundary():
            raise HTTPException(
                status_code=403,
                detail=(
                    "OpenViking server auth_mode changed to dev, but dev auth can only "
                    "be used when gateway and OpenViking server are localhost"
                ),
            )
        if actual_auth_mode != expected_auth_mode:
            raise HTTPException(
                status_code=503,
                detail=(
                    "OpenViking server auth_mode changed after gateway startup: "
                    f"gateway expects {expected_auth_mode}, server reports {actual_auth_mode}. "
                    "Restart VikingBot gateway or update ov.conf."
                ),
            )

    async def _assert_runtime_upstream_auth_mode(
        self,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self._ov_server_auth_mode():
            return {}
        health = await self._request_upstream_health_with_headers(
            self._runtime_probe_headers() if headers is None else headers
        )
        self._assert_runtime_health_mode(health)
        return health

    def _build_openviking_connection(
        self,
        *,
        api_key: str = "",
        account_id: str = "",
        user_id: str = "",
        role: str = "",
        api_key_type: str = "user",
        actor_peer_id: str = "",
    ) -> dict[str, Any]:
        connection: dict[str, Any] = {
            "account_id": account_id,
            "user_id": user_id,
            "agent_id": DEFAULT_OPENVIKING_AGENT_ID,
            "role": role,
            "api_key_type": api_key_type,
            "server_url": self._ov_server_url(),
            "namespace_policy": dict(DEFAULT_NAMESPACE_POLICY),
        }
        if api_key:
            connection["api_key"] = api_key
        if actor_peer_id:
            connection["actor_peer_id"] = actor_peer_id
        return {key: value for key, value in connection.items() if value not in ("", None)}

    def _resolve_api_key_connection(
        self,
        request: Request,
        health: dict[str, Any],
    ) -> dict[str, Any]:
        role = str(health.get("role") or "").strip().lower()
        account_id = str(health.get("account_id") or "").strip()
        user_id = str(health.get("user_id") or "").strip()
        if role not in {"user", "admin"} or not account_id or not user_id:
            raise HTTPException(
                status_code=401,
                detail="OpenViking credentials did not resolve to a usable User/Admin identity",
            )
        return self._build_openviking_connection(
            api_key=self._extract_api_key(request),
            account_id=account_id,
            user_id=user_id,
            role=role,
            api_key_type="user",
            actor_peer_id=request.headers.get("X-OpenViking-Actor-Peer", ""),
        )

    async def _resolve_trusted_connection(self, request: Request) -> dict[str, Any] | None:
        if not self._ov_server_url():
            return None
        api_key = self._extract_api_key(request)
        if not api_key:
            raise HTTPException(status_code=401, detail="OpenViking API key header required")
        account_id = str(request.headers.get("X-OpenViking-Account") or "").strip()
        user_id = str(request.headers.get("X-OpenViking-User") or "").strip()
        if not account_id or not user_id:
            missing = []
            if not account_id:
                missing.append("X-OpenViking-Account")
            if not user_id:
                missing.append("X-OpenViking-User")
            raise HTTPException(
                status_code=401,
                detail=(
                    "Trusted OpenViking chat requires "
                    + " and ".join(missing)
                    + ". Configure account/user in ovcli.conf or pass the headers."
                ),
            )
        health = await self._request_upstream_health(request)
        self._assert_runtime_health_mode(health)
        resolved_account_id = str(health.get("account_id") or "").strip()
        resolved_user_id = str(health.get("user_id") or "").strip()
        role = str(health.get("role") or "").strip().lower()
        if (
            not resolved_account_id
            or not resolved_user_id
            or resolved_account_id != account_id
            or resolved_user_id != user_id
        ):
            raise HTTPException(status_code=401, detail="Invalid OpenViking credentials")
        return self._build_openviking_connection(
            api_key=api_key,
            account_id=account_id,
            user_id=user_id,
            role=role or "user",
            api_key_type="root",
            actor_peer_id=request.headers.get("X-OpenViking-Actor-Peer", ""),
        )

    async def _verify_gateway_request(
        self,
        request: Request,
        x_gateway_token: Optional[str],
    ) -> GatewayRequestAuth:
        gateway_token = self._gateway_token()
        token_configured = bool(gateway_token)
        token_valid = False

        gateway_is_localhost = self._is_gateway_localhost()
        gateway_challenge_headers = {"X-VikingBot-Gateway": "true"}
        if not gateway_is_localhost:
            if not token_configured:
                raise HTTPException(
                    status_code=503,
                    detail="OpenAPI gateway token is required when host is non-localhost",
                    headers=gateway_challenge_headers,
                )
            if x_gateway_token:
                token_valid = secrets.compare_digest(x_gateway_token, gateway_token)
                if not token_valid:
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid X-Gateway-Token",
                        headers=gateway_challenge_headers,
                    )
            if not token_valid:
                raise HTTPException(
                    status_code=401,
                    detail="X-Gateway-Token header required",
                    headers=gateway_challenge_headers,
                )

        loopback_request = self._is_loopback_request(request)
        local_forward_trust = loopback_request and gateway_is_localhost
        forwarded_trusted = loopback_request and (token_valid or local_forward_trust)

        return GatewayRequestAuth(
            gateway_token_configured=token_configured,
            gateway_token_valid=token_valid,
            loopback_request=loopback_request,
            forwarded_connection_trusted=forwarded_trusted,
        )

    async def _prepare_chat_request(
        self,
        http_request: Request,
        chat_request: ChatRequest,
        auth: GatewayRequestAuth,
    ) -> None:
        if chat_request.openviking_connection is not None:
            if not auth.can_trust_forwarded_connection:
                raise HTTPException(
                    status_code=403,
                    detail="openviking_connection is only accepted from trusted server proxy",
                )
            if not self._ov_server_url():
                raise HTTPException(
                    status_code=503,
                    detail=OPENVIKING_UPSTREAM_NOT_CONFIGURED_DETAIL,
                )
            await self._assert_runtime_upstream_auth_mode(
                self._identity_headers_from_connection(chat_request.openviking_connection)
            )
            chat_request._principal_scope = self._connection_principal_scope(
                chat_request.openviking_connection
            )
            return

        connection, scope = await self._resolve_request_identity(http_request, auth)
        chat_request._principal_scope = scope
        if connection:
            chat_request.openviking_connection = OpenVikingConnection(**connection)

    async def _resolve_request_identity(
        self,
        http_request: Request,
        auth: GatewayRequestAuth,
    ) -> tuple[dict[str, Any] | None, str]:
        auth_mode = self._ov_server_auth_mode()
        if not auth_mode:
            return None, self._principal_scope("standalone")
        if auth_mode == "dev":
            if not self._is_safe_dev_boundary():
                raise HTTPException(
                    status_code=403,
                    detail="OpenViking dev auth can only be used when gateway and OpenViking server are localhost",
                )
            await self._assert_runtime_upstream_auth_mode({})
            return None, self._principal_scope("dev")
        if auth_mode == "api_key":
            if not self._has_openviking_auth_headers(http_request):
                raise HTTPException(status_code=401, detail="OpenViking API key header required")
            health = await self._request_upstream_health(http_request)
            self._assert_runtime_health_mode(health)
            connection = self._resolve_api_key_connection(http_request, health)
        elif auth_mode == "trusted":
            connection = await self._resolve_trusted_connection(http_request)
        else:
            raise HTTPException(
                status_code=503,
                detail=f"Unsupported OpenViking auth mode for gateway: {auth_mode}",
            )
        if connection is None and auth_mode == "api_key":
            raise HTTPException(status_code=401, detail="OpenViking API key header required")

        return connection, self._connection_principal_scope(connection)

    async def _resolve_request_principal(
        self,
        http_request: Request,
        auth: GatewayRequestAuth,
    ) -> str:
        _connection, scope = await self._resolve_request_identity(http_request, auth)
        return scope

    @staticmethod
    def _principal_scope(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    def _connection_principal_scope(self, connection: Any) -> str:
        if isinstance(connection, OpenVikingConnection):
            values = connection.model_dump(exclude_none=True)
        elif isinstance(connection, dict):
            values = connection
        else:
            values = {}
        account_id = str(values.get("account_id") or "").strip()
        user_id = str(values.get("user_id") or "").strip()
        if not account_id or not user_id:
            raise HTTPException(
                status_code=401,
                detail="OpenViking identity did not include account_id and user_id",
            )
        return self._principal_scope(f"openviking:{account_id}:{user_id}")

    @staticmethod
    def _scoped_session_id(principal_scope: str, session_id: str) -> str:
        return f"{principal_scope}:{session_id}"

    async def _gateway_health(self, request: Request) -> dict[str, Any]:
        from vikingbot import __version__

        server_url = self._ov_server_url()
        auth_mode = self._ov_server_auth_mode()
        source = self._ov_server_source()
        gateway_token_required = not self._is_gateway_localhost()
        payload: dict[str, Any] = {
            "status": "ok" if self._running else "unhealthy",
            "healthy": self._running,
            "version": __version__,
            "gateway": "vikingbot",
            "mode": f"openviking_{source}" if server_url else "standalone",
            "upstream_configured": bool(server_url),
            "upstream_source": source,
            "upstream_api_key_source": self._ov_server_api_key_source(),
            "gateway_token_required": gateway_token_required,
        }
        if auth_mode:
            payload["auth_mode"] = auth_mode
        if server_url:
            payload["upstream_url"] = server_url
            try:
                request_headers = self._identity_headers_from_request(request)
                caller_authenticated = self._has_openviking_auth_headers(request)
                probe_headers = (
                    request_headers
                    if caller_authenticated or self._ov_server_is_server_managed()
                    else None
                )
                health = await self._assert_runtime_upstream_auth_mode(probe_headers)
                payload["upstream_status"] = health.get("status", "ok")
                if caller_authenticated:
                    for field in ("role", "account_id", "user_id"):
                        value = health.get(field)
                        if value not in (None, ""):
                            payload[field] = value
            except HTTPException as exc:
                payload["upstream_status"] = "unavailable"
                payload["upstream_error"] = exc.detail
        return payload

    def _proxy_request_headers(self, request: Request) -> dict[str, str]:
        # OpenViking credentials and identity are request-scoped. The gateway must
        # never fill them from bot.ov_server while proxying a client request.
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            lower = key.lower()
            if lower in HOP_BY_HOP_HEADERS:
                continue
            if lower in {"host", "content-length", "x-gateway-token"}:
                continue
            headers[key] = value
        return headers

    @staticmethod
    def _proxy_response_headers(headers: httpx.Headers) -> dict[str, str]:
        forwarded: dict[str, str] = {}
        for key, value in headers.items():
            lower = key.lower()
            if lower in HOP_BY_HOP_HEADERS:
                continue
            forwarded[key] = value
        return forwarded

    async def _proxy_openviking_request(self, request: Request, path: str) -> Response:
        server_url = self._ov_server_url()
        if not server_url:
            raise HTTPException(
                status_code=503,
                detail=OPENVIKING_UPSTREAM_NOT_CONFIGURED_DETAIL,
            )
        await self._assert_runtime_upstream_auth_mode(self._identity_headers_from_request(request))

        upstream_url = f"{server_url}/api/v1/{path}"
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"

        client = httpx.AsyncClient(
            timeout=OPENVIKING_PROXY_TIMEOUT_SECONDS,
            trust_env=False,
        )
        try:
            content = None if request.method in {"GET", "HEAD"} else request.stream()
            upstream_request = client.build_request(
                request.method,
                upstream_url,
                content=content,
                headers=self._proxy_request_headers(request),
            )
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(
                status_code=502,
                detail=f"OpenViking upstream proxy request failed: {exc.__class__.__name__}",
            ) from exc
        except BaseException:
            await client.aclose()
            raise

        async def stream_upstream_response():
            try:
                async for chunk in upstream_response.aiter_raw():
                    yield chunk
            finally:
                await upstream_response.aclose()
                await client.aclose()

        return StreamingResponse(
            content=stream_upstream_response(),
            status_code=upstream_response.status_code,
            headers=self._proxy_response_headers(upstream_response.headers),
            media_type=upstream_response.headers.get("content-type"),
        )

    def _setup_routes(self) -> None:
        """Setup routes on the external FastAPI app."""
        if self._app is None:
            logger.warning("No external FastAPI app provided, cannot setup routes")
            return

        # Get the router and include it at root path
        # Note: openviking-server adds its own /bot/v1 prefix when proxying
        router = self.get_router()
        self._app.include_router(router, prefix="/bot/v1")
        self._app.include_router(self.get_gateway_router())
        logger.info("OpenAPI routes registered at /bot/v1 and gateway root")

    def _request_user_id(self, request: ChatRequest) -> str:
        if request.user_id:
            return request.user_id
        if request.openviking_connection and request.openviking_connection.user_id:
            return request.openviking_connection.user_id
        return "anonymous"

    def _request_metadata(self, request: ChatRequest) -> dict[str, Any]:
        disabled_tools = list(request.disabled_tools or [])
        if "message" not in disabled_tools:
            disabled_tools.append("message")
        return {"disabled_tools": disabled_tools}

    def _request_openviking_connection(self, request: ChatRequest) -> dict[str, Any] | None:
        if request.openviking_connection:
            connection = request.openviking_connection.model_dump(exclude_none=True)
            if connection:
                return connection
        return None

    def _request_actor_peer_id(self, request: ChatRequest, fallback: str) -> str:
        connection = self._request_openviking_connection(request) or {}
        return str(connection.get("actor_peer_id") or fallback)

    async def _handle_chat(self, request: ChatRequest) -> ChatResponse:
        """Handle a chat request."""
        session_id = request.session_id or str(uuid.uuid4())
        storage_key = self._scoped_session_id(request._principal_scope, session_id)
        user_id = self._request_user_id(request)

        # Create session if new
        if storage_key not in self._sessions:
            self._sessions[storage_key] = {
                "session_id": session_id,
                "principal_scope": request._principal_scope,
                "user_id": user_id,
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "message_count": 0,
                "messages": [],
            }

        # Update session activity
        self._sessions[storage_key]["last_active"] = datetime.now()
        self._sessions[storage_key]["message_count"] += 1

        # Create pending response tracker
        pending = PendingResponse()
        self._pending[storage_key] = pending

        try:
            # Build session key
            session_key = SessionKey(
                type="cli",
                channel_id=self.config.channel_id(),
                chat_id=storage_key,
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
                actor_peer_id=self._request_actor_peer_id(request, user_id),
                content=content,
                metadata=self._request_metadata(request),
                openviking_connection=self._request_openviking_connection(request),
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
                relevant_memories=pending.relevant_memories,
                token_usage=pending.token_usage,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error handling chat request: {e}")
            raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
        finally:
            # Clean up pending
            self._pending.pop(storage_key, None)

    async def _handle_chat_stream(self, request: ChatRequest) -> StreamingResponse:
        """Handle a streaming chat request."""
        session_id = request.session_id or str(uuid.uuid4())
        storage_key = self._scoped_session_id(request._principal_scope, session_id)
        user_id = self._request_user_id(request)

        # Create session if new
        if storage_key not in self._sessions:
            self._sessions[storage_key] = {
                "session_id": session_id,
                "principal_scope": request._principal_scope,
                "user_id": user_id,
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "message_count": 0,
                "messages": [],
            }

        self._sessions[storage_key]["last_active"] = datetime.now()
        self._sessions[storage_key]["message_count"] += 1

        pending = PendingResponse()
        self._pending[storage_key] = pending

        async def event_generator():
            try:
                # Build session key and send message
                session_key = SessionKey(
                    type="cli",
                    channel_id=self.config.channel_id(),
                    chat_id=storage_key,
                )

                msg = InboundMessage(
                    session_key=session_key,
                    sender_id=user_id,
                    actor_peer_id=self._request_actor_peer_id(request, user_id),
                    content=request.message,
                    metadata=self._request_metadata(request),
                    openviking_connection=self._request_openviking_connection(request),
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
                self._pending.pop(storage_key, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-VikingBot-Session-ID": session_id,
            },
        )

    async def _handle_bot_chat(self, channel_id: str, request: ChatRequest) -> ChatResponse:
        """Handle a BotChannel chat request."""
        session_id = request.session_id or str(uuid.uuid4())
        storage_key = self._scoped_session_id(request._principal_scope, session_id)
        user_id = self._request_user_id(request)

        # Ensure channel has session storage
        if channel_id not in self._bot_sessions:
            self._bot_sessions[channel_id] = {}

        # Create session if new
        if storage_key not in self._bot_sessions[channel_id]:
            self._bot_sessions[channel_id][storage_key] = {
                "session_id": session_id,
                "principal_scope": request._principal_scope,
                "user_id": user_id,
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "message_count": 0,
                "messages": [],
            }

        # Update session activity
        self._bot_sessions[channel_id][storage_key]["last_active"] = datetime.now()
        self._bot_sessions[channel_id][storage_key]["message_count"] += 1

        # Create pending response tracker
        pending = PendingResponse()
        self._bot_pending[channel_id][storage_key] = pending

        try:
            # Build session key with bot_api type
            session_key = SessionKey(
                type="bot_api",
                channel_id=channel_id,
                chat_id=storage_key,
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
                actor_peer_id=self._request_actor_peer_id(request, user_id),
                content=content,
                need_reply=request.need_reply,
                metadata=self._request_metadata(request),
                openviking_connection=self._request_openviking_connection(request),
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
                relevant_memories=pending.relevant_memories,
                token_usage=pending.token_usage,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error handling bot chat request for channel {channel_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
        finally:
            # Clean up pending
            if channel_id in self._bot_pending:
                self._bot_pending[channel_id].pop(storage_key, None)

    async def _handle_bot_chat_stream(
        self, channel_id: str, request: ChatRequest
    ) -> StreamingResponse:
        """Handle a BotChannel streaming chat request."""
        session_id = request.session_id or str(uuid.uuid4())
        storage_key = self._scoped_session_id(request._principal_scope, session_id)
        user_id = self._request_user_id(request)

        # Ensure channel has session storage
        if channel_id not in self._bot_sessions:
            self._bot_sessions[channel_id] = {}

        # Create session if new
        if storage_key not in self._bot_sessions[channel_id]:
            self._bot_sessions[channel_id][storage_key] = {
                "session_id": session_id,
                "principal_scope": request._principal_scope,
                "user_id": user_id,
                "created_at": datetime.now(),
                "last_active": datetime.now(),
                "message_count": 0,
                "messages": [],
            }

        self._bot_sessions[channel_id][storage_key]["last_active"] = datetime.now()
        self._bot_sessions[channel_id][storage_key]["message_count"] += 1

        pending = PendingResponse()
        self._bot_pending[channel_id][storage_key] = pending

        async def event_generator():
            try:
                # Build session key with bot_api type
                session_key = SessionKey(
                    type="bot_api",
                    channel_id=channel_id,
                    chat_id=storage_key,
                )

                msg = InboundMessage(
                    session_key=session_key,
                    sender_id=user_id,
                    actor_peer_id=self._request_actor_peer_id(request, user_id),
                    content=request.message,
                    metadata=self._request_metadata(request),
                    openviking_connection=self._request_openviking_connection(request),
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
                    self._bot_pending[channel_id].pop(storage_key, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-VikingBot-Session-ID": session_id,
            },
        )

    async def _handle_feedback(self, request: FeedbackRequest) -> FeedbackResponse:
        """Persist explicit user feedback and emit an analytics event."""
        session_key = self._get_feedback_session_key(request)
        feedback_timestamp = datetime.now()

        def apply_feedback(
            session: Any,
        ) -> tuple[dict[str, Any], float | None, Optional[dict[str, Any]]]:
            response_message = self._find_response_message(session.messages, request.response_id)
            if response_message is None:
                raise HTTPException(status_code=404, detail="Response not found")

            response_timestamp = self._parse_message_timestamp(response_message)
            feedback_delay_sec = None
            if response_timestamp is not None:
                feedback_delay_sec = round(
                    (feedback_timestamp - response_timestamp).total_seconds(), 3
                )

            feedback_event = {
                "response_id": request.response_id,
                "session_id": request.session_id,
                "user_id": request.user_id or response_message.get("sender_id"),
                "feedback_type": request.feedback_type.value,
                "feedback_score": request.feedback_score,
                "feedback_reason": request.feedback_reason,
                "feedback_text": request.feedback_text,
                "feedback_delay_sec": feedback_delay_sec,
                "channel": session_key.channel_key(),
                "created_at": feedback_timestamp.isoformat(),
            }
            session.metadata.setdefault("feedback_events", []).append(feedback_event)
            outcome_payload = self._build_outcome_payload(session, request.response_id)
            return feedback_event, feedback_delay_sec, outcome_payload

        session, feedback_update = await self._session_manager.update_session(
            session_key, apply_feedback
        )
        feedback_event, feedback_delay_sec, outcome_payload = feedback_update
        if outcome_payload is not None:
            LangfuseClient.get_instance().update_response_outcome(
                request.response_id,
                outcome_payload["outcome_label"],
                outcome_payload,
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    session_key=session_key,
                    content="",
                    event_type=OutboundEventType.RESPONSE_OUTCOME_EVALUATED,
                    response_id=request.response_id,
                    metadata={"response_outcome_evaluated": outcome_payload},
                )
            )

        await self.bus.publish_outbound(
            OutboundMessage(
                session_key=session_key,
                content="",
                event_type=OutboundEventType.FEEDBACK_SUBMITTED,
                response_id=request.response_id,
                metadata={"feedback_submitted": feedback_event},
            )
        )

        return FeedbackResponse(
            accepted=True,
            response_id=request.response_id,
            session_id=request.session_id,
            feedback_type=request.feedback_type,
            feedback_delay_sec=feedback_delay_sec,
            timestamp=feedback_timestamp,
        )

    def _get_feedback_session_key(self, request: FeedbackRequest) -> SessionKey:
        """Resolve the persisted session key for a feedback request."""
        storage_key = self._scoped_session_id(request._principal_scope, request.session_id)
        session_type = "bot_api" if request.channel_id else "cli"
        channel_id = request.channel_id or self.config.channel_id()
        scoped_key = SessionKey(
            type=session_type,
            channel_id=channel_id,
            chat_id=storage_key,
        )
        if request._principal_scope == self._principal_scope("standalone"):
            legacy_key = SessionKey(
                type=session_type,
                channel_id=channel_id,
                chat_id=request.session_id,
            )
            if not self._session_manager.has_persisted(
                scoped_key
            ) and self._session_manager.has_persisted(legacy_key):
                return legacy_key
        return scoped_key

    @staticmethod
    def _find_response_message(
        messages: List[Dict[str, Any]], response_id: str
    ) -> Optional[Dict[str, Any]]:
        for message in reversed(messages):
            if message.get("role") == "assistant" and message.get("response_id") == response_id:
                return message
        return None

    @staticmethod
    def _parse_message_timestamp(message: Dict[str, Any]) -> Optional[datetime]:
        timestamp = message.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            return None
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            return None

    def _build_outcome_payload(self, session: Any, response_id: str) -> Optional[dict[str, Any]]:
        """Evaluate and persist the latest known outcome for a response."""
        evaluation = evaluate_response_outcome(
            session.messages,
            response_id,
            feedback_events=session.metadata.get("feedback_events", []),
        )
        if evaluation is None:
            return None

        outcomes = session.metadata.setdefault("response_outcomes", {})
        previous = outcomes.get(response_id)
        if not should_update_outcome(previous, evaluation):
            return None

        outcome_payload = evaluation.to_dict()
        outcomes[response_id] = outcome_payload
        return outcome_payload


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
