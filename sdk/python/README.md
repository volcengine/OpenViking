# openviking-sdk

Lightweight Python HTTP SDK for OpenViking.

`openviking-sdk` is the small package for users who only need to call an existing OpenViking server over HTTP. It avoids the heavier local-runtime, server, and CLI dependencies from the main `openviking` package.

## Installation

```bash
pip install openviking-sdk
```

Requirements:

- Python 3.10+
- A reachable OpenViking HTTP server, for example `http://127.0.0.1:1933`

## Package Name vs Import Name

- PyPI package name: `openviking-sdk`
- Python import name: `openviking_sdk`

```python
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient, TextPart
```

## Configuration Sources

You can configure the SDK in three ways, with this precedence:

1. Explicit constructor arguments
2. Environment variables such as `OPENVIKING_URL`, `OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_ACTOR_PEER_ID`, and `OPENVIKING_TIMEOUT`
3. `ovcli.conf`, either from `OPENVIKING_CLI_CONFIG_FILE` or the default path `~/.openviking/ovcli.conf`

This means existing setups that relied on `ovcli.conf` continue to work after the SDK split.

## Authentication Model

Most deployments use API key authentication.

Common client fields:

- `url`: OpenViking server base URL
- `api_key`: root key or user key
- `account`: optional account override, usually only needed with a root key
- `user`: optional user override, usually only needed with a root key
- `user_id`: legacy alias for `user`
- `actor_peer_id`: optional actor peer override
- `agent_id`: legacy alias for `actor_peer_id`
- `event_hooks`: optional `httpx.AsyncClient` event hooks, such as async request or response hooks

Compatibility notes:

- `user_id` and `agent_id` are still accepted for legacy callers
- `actor_peer_id` and `agent_id` cannot be passed together

Example:

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-or-root-key",
)
```

If you are using a root key and want to act as a specific tenant user:

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-root-key",
    account="demo-account",
    user="demo-user",
)
```

## Request-Scoped Actor Peer

Applications can reuse one initialized, credential-bound client while selecting
the active actor peer for each request:

```python
from openviking_sdk import (
    SyncHTTPClient,
    use_actor_peer,
)

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-key",
)
client.initialize()

with use_actor_peer("assistant-a"):
    memories = client.find(query="deployment preference")
```

The scope is isolated with Python `ContextVar`, so concurrent async tasks and
sync calls dispatched through the SDK worker loop do not overwrite each
other. Nested scopes restore the previous actor peer automatically.

This scope does not change authentication or tenant ownership. Account and user
identity remain bound to the API key or OAuth credential. Use a separate
credential-bound client for each OpenViking user, and derive actor peer values
only from authenticated application state. The server applies the actor peer
only to endpoints that accept an actor-peer view; session APIs remain
user-scoped.

## Quick Start: Sync Client

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-key",
)
client.initialize()

healthy = client.health()
print("health:", healthy)

session = client.create_session(session_id="demo-session")
print("session:", session)

client.session(session_id="demo-session").add_message(
    role="user",
    content="hello from sdk",
)
client.session(session_id="demo-session").add_message(
    role="assistant",
    parts=[TextPart(text="hello from a specific peer")],
    peer_id="peer-alice",
)
context = client.session(session_id="demo-session").get_session_context(token_budget=4096)
print("context:", context)

client.close()
```

## Quick Start: Async Client

```python
import asyncio

from openviking_sdk import AsyncHTTPClient


async def main() -> None:
    client = AsyncHTTPClient(
        url="http://127.0.0.1:1933",
        api_key="your-user-key",
    )
    await client.initialize()

    healthy = await client.health()
    print("health:", healthy)

    session = await client.create_session(session_id="demo-session-async")
    print("session:", session)

    session_client = client.session(session_id="demo-session-async")
    await session_client.add_message(
        role="user",
        content="hello from async sdk",
    )
    context = await session_client.get_session_context(token_budget=4096)
    print("context:", context)

    await client.close()


asyncio.run(main())
```

## Common Operations

### Create a Session

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")
client.initialize()
event_config = {
    "events": {
        "tags": ["team=search", "channel=web"],
    }
}
result = client.create_session(
    session_id="demo-session",
    options={
        "memory_extraction_config": event_config,
    },
)
# Explicit None disables a server-wide auto-commit default at creation time.
client.create_session(
    session_id="manual-session",
    options={"auto_commit_policy": None},
)
client.update_session_config(
    session_id="demo-session",
    options={
        "auto_commit_policy": {"message_count_threshold": 25},
        "memory_extraction_config": {
            "events": {"tags": ["team=search", "channel=app"]}
        },
    },
)
# Explicit None disables automatic commits; omitting the argument leaves it unchanged.
client.update_session_config(
    session_id="demo-session",
    options={"auto_commit_policy": None},
)
client.session(session_id="demo-session").commit(
    options={"event_tags": ["team=search", "channel=web"]}
)
# Use event_tags=[] to skip the session defaults for one commit.
print(result)
```

### Add a Resource from a Local File

`add_resource` handles file upload for local paths automatically.

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")
client.initialize()

result = client.add_resource(
    path="/path/to/notes.md",
    to="viking://resources/demo-notes",
    wait=True,
    options={
        "reason": "knowledge import",
    },
)
print(result)
```

To ingest content without VLM semantic understanding, pass `processing_mode="vectors_only"`.
This writes/syncs the resource tree and vectorizes current files, but does not generate
or refresh `.abstract.md` / `.overview.md`.

```python
result = client.add_resource(
    path="/path/to/notes.md",
    to="viking://resources/demo-notes",
    wait=True,
    options={
        "processing_mode": "vectors_only",
    },
)
```

### Filesystem Operations

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")
client.initialize()

client.mkdir(uri="viking://resources/demo-dir")
print(client.ls(uri="viking://resources"))
print(client.read(uri="viking://resources/demo-dir/example.md"))
```

### Retrieval

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")
client.initialize()

result = client.find(query="hello", limit=5)
print(result)
```

### Core Parameters and Options

Frequently used fields are explicit parameters. For readability, prefer named
arguments such as `to` and `wait` with `add_resource`; `target_uri`
and `limit` with retrieval; and `role`, `content`, and `parts` with
`add_message`. `add_message` also accepts `peer_id` as an explicit message
field. For batch writes, place `peer_id` in each message dictionary. Positional
calls remain supported.

For structured messages, `parts` accepts either wire-format dictionaries or
the SDK's `TextPart`, `ContextPart`, `ImagePart`, and `ToolPart` objects. The
SDK serializes both forms to the same HTTP JSON payload.

Use the method's typed `options` dictionary for advanced fields, such as
`processing_mode`, retrieval filters, session extraction configuration, or
`telemetry`.
Do not pass advanced fields as bare keyword arguments. A field must be passed
through exactly one entry point; SDK-defined fields in `options` and
`extra` cannot override explicit parameters.

Image search uses the same methods. Pass a local path, bytes, data URI, HTTP URL, or `viking://` URI with the explicit `image` parameter. The server must use a multimodal embedding model.

```python
result = client.find(limit=5, image="/path/to/photo.png")
result = client.search(
    image="viking://resources/poster.png",
)
```

Complex requests use typed Options dictionaries. Use the `extra` key only for
server fields that the installed SDK version does not yet expose:

```python
result = client.find(
    query="authentication",
    limit=10,
    options={"extra": {"future_server_field": False}},
)
```

## Admin Operations

If you connect with a root key, the SDK also exposes admin APIs such as:

- `admin_create_account`
- `admin_register_user`
- `admin_list_accounts`
- `admin_list_users`
- `admin_regenerate_key`
- `admin_delete_account`

Example:

```python
from openviking_sdk import SyncHTTPClient

root_client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-root-key",
)
root_client.initialize()

result = root_client.admin_create_account(
    account_id="demo-account",
    admin_user_id="demo-admin",
    seed="demo-admin-seed",
)
print(result)

root_client.admin_register_user(
    account_id="demo-account",
    user_id="alice",
    role="user",
    seed="alice-seed",
    user_config={
        "add_targets": {
            "resource_uri": "viking://~/resources/project-a",
            "skill_uri": "viking://~/skills",
        }
    },
)

root_client.admin_regenerate_key(
    account_id="demo-account",
    user_id="alice",
    seed="alice-new-seed",
)
```

`admin_create_account` also accepts `user_config` with the same shape.
These fields initialize server-side user config; ordinary add calls still just
omit `to` / `parent` / `target_uri` and let the server resolve defaults.
When `seed` is set, the returned API key is derived from
`sha256(user_id + "\0" + seed)`; omit it for random key generation.

## Error Handling

The SDK maps server-side error codes to Python exceptions.

```python
from openviking_sdk import OpenVikingError, SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")
client.initialize()

try:
    print(client.read(uri="viking://resources/not-exists.md"))
except OpenVikingError as exc:
    print(type(exc).__name__, exc)
```

## Relationship to `openviking`

Use `openviking-sdk` when you want:

- the HTTP client only
- the smallest dependency footprint
- a package suitable for application-side integration

Use `openviking` when you want:

- the full Python package
- local runtime integrations
- server entrypoints
- compatibility imports that re-export the HTTP clients

## Development

Install from source:

```bash
cd sdk/python
pip install -e .
```

Build distributions:

```bash
cd sdk/python
python -m build
```

The SDK version is derived from git tags with this format:

```text
python-sdk@0.1.3
```

That tag namespace is independent from the main package release tags such as:

```text
v0.3.26
```

## Release

The repository is configured so SDK releases can be driven by SDK-only tags.

Typical flow:

1. Merge SDK changes.
2. Create and push a tag like `python-sdk@0.1.3`.
3. GitHub Actions builds `sdk/python`.
4. GitHub Actions publishes `openviking-sdk` to PyPI.
