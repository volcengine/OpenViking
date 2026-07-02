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
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient
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

## Quick Start: Sync Client

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-key",
)

healthy = client.health()
print("health:", healthy)

session = client.create_session("demo-session")
print("session:", session)

client.session("demo-session").add_message("user", "hello from sdk")
context = client.session("demo-session").get_session_context(token_budget=4096)
print("context:", context)
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

    healthy = await client.health()
    print("health:", healthy)

    session = await client.create_session("demo-session-async")
    print("session:", session)

    session_client = client.session("demo-session-async")
    await session_client.add_message("user", "hello from async sdk")
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
result = client.create_session("demo-session")
print(result)
```

### Add a Resource from a Local File

`add_resource` handles file upload for local paths automatically.

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")

result = client.add_resource(
    "/path/to/notes.md",
    to="viking://resources/demo-notes",
    reason="knowledge import",
    wait=True,
)
print(result)
```

### Filesystem Operations

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")

client.mkdir("viking://resources/demo-dir")
print(client.ls("viking://resources"))
print(client.read("viking://resources/demo-dir/example.md"))
```

### Retrieval

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")

result = client.find("hello", limit=5)
print(result)
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
            "resource_uri": "viking://user/resources/project-a",
            "skill_uri": "viking://user/skills",
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

try:
    print(client.read("viking://resources/not-exists.md"))
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
