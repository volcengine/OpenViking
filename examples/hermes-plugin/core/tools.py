"""The ``openviking_*`` tools: server MCP tools registered through the catalogue, and their wrappers."""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from . import tool_catalog
from .host import spawn_context_thread, tool_error
from .http import _resolve_user_space
from .log import get_logger

logger = get_logger()


_REMOTE_RESOURCE_PREFIXES = ("http://", "https://", "git@", "ssh://", "git://")
# OpenViking-generated summaries; non-.md sidecars are already rejected by the .md check.
_GENERATED_MEMORY_SUMMARY_FILENAMES = {".abstract.md", ".overview.md"}
_MCP_PATH = "/mcp"

# Read-only recall tools. Their results are captured like any other tool result,
# so the server can attribute which memories were used.
_OPENVIKING_RECALL_TOOL_NAMES = set(tool_catalog.RECALL_TOOL_NAMES)
_P = tool_catalog.TOOL_PREFIX
# Per-tool system-prompt guidance; system_prompt_block() keeps only the lines whose tool
# is registered, so the prompt never names a tool the model cannot call.
_SYSTEM_PROMPT_TOOL_GUIDANCE = (
    (f"{_P}search",
     f"Use {_P}search for extracted memories, facts, entities, events, and resources. For questions about "
     "remembered people, preferences, projects, events, or prior user context, search OpenViking before asking the "
     "user to repeat context. Prefer one or two focused searches, then read the strongest result URIs. If repeated "
     "searches return the same evidence or no stronger evidence, stop searching, answer from available evidence, and "
     "state uncertainty if needed."),
    (f"{_P}find", f"Use {_P}find for a quick semantic lookup without session context."),
    (f"{_P}read", f"Use {_P}read when you already have a specific viking:// URI and need its content."),
    (f"{_P}list", f"Use {_P}list, {_P}tree, {_P}glob and {_P}grep to browse or match viking:// paths; prefer search and read for evidence."),
    (f"{_P}remember", f"Use {_P}remember to store important facts."),
    (f"{_P}forget", f"Use {_P}forget to delete exact memory file URIs."),
    (f"{_P}add_resource", f"Use {_P}add_resource to index URLs, local files or directories."),
)


def _strip_front_matter(text: str, uri: str, *, single: bool) -> str:
    """Drop the YAML front matter of a generated summary; it names a server-local path."""
    if single:
        return re.sub(r"\A---\n.*?\n---\n?", "", text, count=1, flags=re.S)
    header = re.escape(f"=== {uri} ===\n")
    return re.sub(rf"({header})---\n.*?\n---\n?", r"\1", text, count=1, flags=re.S)


def _zip_directory(dir_path: Path) -> Path:
    """Zip a directory tree into a temp file, skipping symlinks, escapes, and read-blocked files."""
    from .host import raise_if_read_blocked

    root = dir_path.resolve()
    zip_path = Path(tempfile.gettempdir()) / f"openviking_upload_{uuid.uuid4().hex}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in dir_path.rglob("*"):
            if file_path.is_symlink() or not file_path.is_file():
                continue
            try:
                resolved = file_path.resolve()
                resolved.relative_to(root)
                raise_if_read_blocked(str(resolved))
            except ValueError:
                continue
            zipf.write(file_path, arcname=str(file_path.relative_to(dir_path)).replace("\\", "/"))
    return zip_path


def _is_windows_absolute_path(value: str) -> bool:
    return len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in {"/", "\\"}


def _validate_forget_memory_uri(raw_uri: Any, *, user_space: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    uri = raw_uri.strip() if isinstance(raw_uri, str) else ""
    if not uri:
        return None, "uri is required"
    parsed = urlparse(uri)
    if parsed.scheme != "viking" or not uri.startswith("viking://"):
        return None, "openviking_forget only accepts viking:// memory file URIs"
    if parsed.query or parsed.fragment:
        return None, "openviking_forget requires an exact URI without query or fragment"
    if uri.endswith("/") or not uri.endswith(".md"):
        return None, "openviking_forget only deletes concrete .md memory files"
    parts = [part for part in uri[len("viking://") :].split("/") if part]
    if any(unquote(part) in {".", ".."} for part in parts):
        return None, "openviking_forget does not accept dot path segments"
    # ``memories`` index for ``<scope>/[peers/<agent>/]memories/``; under ``user`` the uid is
    # required, since the uid-less shorthands are deprecated upstream.
    offsets = ((1, None), (3, 1)) if parts[:1] == ["~"] else ((2, None), (4, 2)) if parts[:1] == ["user"] else ()
    memories_idx = next((idx for idx, peer_at in offsets
                         if len(parts) > idx and parts[idx] == "memories" and (peer_at is None or parts[peer_at] == "peers")), None)
    if memories_idx is None or len(parts) < memories_idx + 2:
        return None, "openviking_forget only deletes user memory file URIs"
    # An explicit uid can name someone else's space. Do not send a destructive
    # request unless the server has confirmed that this uid belongs to the caller.
    if parts[0] == "user":
        if not user_space:
            return None, "openviking_forget could not verify the current OpenViking user identity; retry or use viking://~/..."
        if parts[1] != user_space:
            return None, (f"openviking_forget only deletes your own memories; use viking://user/{user_space}/... "
                          "or viking://~/... instead")
    if uri.rsplit("/", 1)[-1] in _GENERATED_MEMORY_SUMMARY_FILENAMES:
        return None, "openviking_forget cannot delete generated memory summary files"
    return uri, None


def _is_local_path_reference(value: str) -> bool:
    if not value or "\n" in value or "\r" in value or value.startswith(_REMOTE_RESOURCE_PREFIXES):
        return False
    if _is_windows_absolute_path(value):
        return True
    return value.startswith(("/", "./", "../", "~/", ".\\", "..\\", "~\\")) or "/" in value or "\\" in value


def _mcp_connection(settings: Dict[str, str], client: Any, deps: Any):
    """MCP connection on ``client``'s endpoint and identity when it is a REST client, else on ``settings``.

    Headers come from ``build_openviking_headers`` with the same inputs as the REST
    client's, so MCP and REST requests carry the same identity.
    """
    from .http import _openviking_user_agent, build_openviking_headers, plugin_version
    from .mcp_bridge import McpConnection

    snapshot = getattr(client, "_conn_snapshot", None)
    if isinstance(snapshot, tuple) and len(snapshot) == 5 and all(isinstance(v, str) for v in snapshot):
        endpoint, api_key, account, user, agent = snapshot
    else:
        endpoint, api_key, agent = settings["endpoint"], settings["api_key"], settings["agent"]
        account, user = settings["account"] or "default", settings["user"] or "default"

    def headers() -> Dict[str, str]:
        return build_openviking_headers(api_key=api_key, account=account, user=user, trusted_identity=not api_key,
                                        actor_peer_id=agent, user_agent=_openviking_user_agent())

    return McpConnection(
        url=endpoint.rstrip("/") + _MCP_PATH,
        headers=headers,
        spawn=spawn_context_thread,
        session_factory=deps.mcp_session,
        client_version=plugin_version(),
    )


def prime_tool_cache(hermes_home: str, deps: Any) -> bool:
    """Fill the disk tool cache for ``hermes_home``'s saved connection; the setup wizard calls this."""
    from .connection import (
        _load_hermes_openviking_config,
        _profile_openviking_env,
        _resolve_connection_settings,
    )
    from .mcp_bridge import list_tools

    try:
        env = _profile_openviking_env(hermes_home)
        resolved = _resolve_connection_settings(_load_hermes_openviking_config(hermes_home, env=env), env=env)
        settings = {k: str(resolved.get(k) or "") for k in ("endpoint", "api_key", "account", "user", "agent")}
        if not settings["endpoint"]:
            return False
        tools = list_tools(_mcp_connection(settings, None, deps), timeout=tool_catalog.LIVE_LIST_BUDGET_SECONDS)
    except Exception as exc:
        logger.debug("OpenViking tool cache priming failed: %s", exc)
        return False
    key = tool_catalog.fingerprint(*(settings[k] for k in ("endpoint", "api_key", "account", "user", "agent")))
    tool_catalog.remember(key, hermes_home, settings["endpoint"], tools)
    return True


class ToolsMixin:
    """The ``openviking_*`` tools of ``OpenVikingMemoryProvider``; mixed into that class."""

    # -- catalogue -----------------------------------------------------------

    def _tool_connection_settings(self) -> Optional[Dict[str, str]]:
        """Connection values for MCP: the live ones after initialize(), else resolved like is_available()."""
        if getattr(self, "_endpoint", ""):
            return {"endpoint": self._endpoint, "api_key": self._api_key, "account": self._account,
                    "user": self._user, "agent": self._agent}
        from .connection import _load_hermes_openviking_config, _resolve_connection_settings
        from .settings import _DEFAULT_ENDPOINT

        try:
            settings = _resolve_connection_settings(_load_hermes_openviking_config())
        except Exception as exc:
            logger.debug("OpenViking tool catalogue: no connection settings: %s", exc)
            return None
        settings = {k: str(settings.get(k) or "") for k in ("endpoint", "api_key", "account", "user", "agent")}
        settings["endpoint"] = settings["endpoint"] or _DEFAULT_ENDPOINT
        return settings

    def _tools_hermes_home(self) -> str:
        if getattr(self, "_hermes_home", ""):
            return self._hermes_home
        from .host import get_hermes_home

        try:
            return str(get_hermes_home())
        except Exception:
            return ""

    def _extra_tools(self) -> tuple:
        from .connection import _load_hermes_openviking_config
        from .host import get_secret

        try:
            if getattr(self, "_hermes_home_bound", False):
                config, env = self._profile_config_and_env()
                value = (env or {}).get("OPENVIKING_EXTRA_TOOLS") if env is not None else get_secret("OPENVIKING_EXTRA_TOOLS", "")
            else:
                config, value = None, get_secret("OPENVIKING_EXTRA_TOOLS", "")
            if not str(value or "").strip():
                value = (config if config is not None else _load_hermes_openviking_config()).get("extra_tools", "")
        except Exception:
            value = ""
        return tool_catalog.parse_extra_tools(value)

    def _mcp_connection(self, settings: Dict[str, str], client: Any = None):
        return _mcp_connection(settings, client, self._deps)

    def _catalog_key(self, settings: Dict[str, str]) -> str:
        return tool_catalog.fingerprint(*(settings[k] for k in ("endpoint", "api_key", "account", "user", "agent")))

    def _list_server_tools(self, settings: Dict[str, str], budget: float) -> List[Dict[str, Any]]:
        from .mcp_bridge import list_tools

        return list_tools(self._mcp_connection(settings, getattr(self, "_client", None)), timeout=budget)

    def _openviking_tool_schemas(self) -> List[Dict[str, Any]]:
        settings = self._tool_connection_settings()
        tools: List[Dict[str, Any]] = []
        if settings is not None:
            tools = tool_catalog.load(
                self._catalog_key(settings), self._tools_hermes_home(), settings["endpoint"],
                lambda budget: self._list_server_tools(settings, budget),
            )
        schemas = tool_catalog.build_schemas(tools, self._extra_tools())
        # Hermes fixes its routing table from the first answer; later answers stay within it.
        routed = getattr(self, "_routed_tool_names", None)
        if routed is None:
            self._routed_tool_names = frozenset(s["name"] for s in schemas)
            return schemas
        return [s for s in schemas if s["name"] in routed]

    def _refresh_tool_catalog(self) -> None:
        """Refresh both catalogue caches on a one-shot thread; never blocks initialize()."""
        settings = self._tool_connection_settings()
        if settings is None:
            return
        from .mcp_bridge import list_tools

        # The thread holds no reference to the provider, so a dropped provider stays collectable.
        home, key, conn = self._tools_hermes_home(), self._catalog_key(settings), self._mcp_connection(settings, self._client)

        def refresh() -> None:
            try:
                tools = list_tools(conn, timeout=tool_catalog.LIVE_LIST_BUDGET_SECONDS)
            except Exception as exc:
                logger.debug("OpenViking tool catalogue refresh failed: %s", exc)
                return
            tool_catalog.remember(key, home, settings["endpoint"], tools)

        try:
            spawn_context_thread(refresh, name="openviking-tool-catalog", daemon=True).start()
        except Exception as exc:
            logger.debug("OpenViking tool catalogue refresh not started: %s", exc)

    # -- calls ---------------------------------------------------------------

    def _call_openviking_tool(self, tool_name: str, args: dict) -> str:
        name = tool_catalog.server_name(tool_name)
        if name not in tool_catalog.exposed_server_names(self._extra_tools()):
            return tool_error(f"Unknown tool: {tool_name}")
        settings = self._tool_connection_settings()
        if settings is None:
            return tool_error("OpenViking server not connected")
        args = dict(args or {})
        # One client for the whole call: an identity checked on it must be the one the call uses.
        client = self._client
        wrapper = getattr(self, f"_prepare_{name}", None)
        cleanup: List[Path] = []
        try:
            if wrapper is not None:
                prepared = wrapper(args, client, cleanup)
                if isinstance(prepared, str):
                    return prepared  # a tool_error from argument checks
                args = prepared
            from .mcp_bridge import call_tool

            result = call_tool(self._mcp_connection(settings, client), name, args)
        finally:
            for path in cleanup:
                shutil.rmtree(path, ignore_errors=True)
        if result.is_error:
            return tool_error(result.text, **({"http_status": result.http_status} if result.http_status is not None else {}))
        text = result.text
        if name == "read":
            uris = args.get("uris")
            uri_list = uris if isinstance(uris, list) else [uris]
            for uri in uri_list:
                if isinstance(uri, str) and uri.rsplit("/", 1)[-1] in _GENERATED_MEMORY_SUMMARY_FILENAMES:
                    text = _strip_front_matter(text, uri, single=len(uri_list) == 1)
        return text

    def _prepare_search(self, args: dict, client: Any, cleanup: List[Path]):
        if self._session_id and getattr(self, "_agent_context", "primary") == "primary":
            from .transcript import openviking_session_id

            args["session_id"] = openviking_session_id(self._session_id)
        return args

    def _prepare_forget(self, args: dict, client: Any, cleanup: List[Path]):
        # _resolve_user_space, not _user_space: its "default" fallback is a guess, not an identity.
        uri, error = _validate_forget_memory_uri(args.get("uri"), user_space=_resolve_user_space(client))
        if error:
            return tool_error(error)
        return {**args, "uri": uri, "recursive": False}

    def _prepare_add_resource(self, args: dict, client: Any, cleanup: List[Path]):
        """Remote URLs go through as is; a local file or directory is uploaded first."""
        from .host import raise_if_read_blocked

        url = args.get("path") or ""
        if not isinstance(url, str) or not url or args.get("temp_file_id"):
            return args
        parsed_url = urlparse(url)
        source_path = None
        if url.startswith(_REMOTE_RESOURCE_PREFIXES):
            return args
        if parsed_url.scheme == "file":
            if parsed_url.netloc not in {"", "localhost"}:
                return tool_error(f"Unsupported non-local file URI: {url}")
            source_path = Path(url2pathname(parsed_url.path)).expanduser()
        elif not parsed_url.scheme or _is_windows_absolute_path(url):
            source_path = Path(url).expanduser()
        if source_path is None or not source_path.exists():
            if source_path is not None and _is_local_path_reference(url):
                return tool_error(f"Local resource path does not exist: {url}")
            return args
        if source_path.is_dir():
            # Directories upload as a zip named after the directory.
            staging = Path(tempfile.mkdtemp(prefix="openviking_upload_"))
            cleanup.append(staging)
            upload = _zip_directory(source_path).rename(staging / f"{source_path.resolve().name or 'resource'}.zip")
        elif source_path.is_file():
            try:
                raise_if_read_blocked(str(source_path))
            except ValueError as exc:
                return tool_error(str(exc))
            upload = source_path
        else:
            return tool_error(f"Unsupported local resource path: {url}")
        prepared = {k: v for k, v in args.items() if k != "path"}
        prepared["temp_file_id"] = client.upload_temp_file(upload)
        return prepared
