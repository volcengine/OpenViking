---
description: Deploy OpenViking Gateway inside the server, with Docker Compose or Helm, or as a standalone process, then expose it, protect it and keep it running through upgrades.
---

# OpenViking Gateway deployment and operations

This page is for the people who run OpenViking Gateway as a service: deploy it in the form that suits you, put it behind a proxy, control who can change it, and keep it healthy through upgrades and migrations. You need an OpenViking Server that uses API-key authentication or local dev mode; the full list is under [Requirements](#requirements). If you are new to the gateway, read [OpenViking Gateway](15-gateway.md) first.

> **Note**: The gateway is in beta. Settings and APIs may change between releases, so read the release notes before you upgrade.

**Looking for something else?** This page covers running the gateway. For everything else:

- Studio tabs and settings: [Manage OpenViking Gateway in Studio](../gateway/06-manage-in-studio.md), then [Upstreams](../gateway/07-upstreams.md), [Context profiles](../gateway/08-context-profiles.md), [Gateway keys](../gateway/09-gateway-keys.md) and [Gateway overview and request log](../gateway/10-overview-and-requests.md).
- An error, a warning or a Studio setup card: [Troubleshoot OpenViking Gateway](../gateway/12-troubleshooting.md).
- A configuration field or environment variable: [Configuration reference](../gateway/13-configuration.md).
- What is stored, who can see it, retention and backups: [Security and data](../gateway/11-security-and-data.md).
- OpenViking tools or long conversations: [OpenViking tools in the gateway](../gateway/04-openviking-tools.md) and [Long conversations in the gateway](../gateway/05-long-conversations.md).
- Settings for one client: [Connect clients](../gateway/03-connect-clients.md).

## Requirements {#requirements}

| Requirement | Details |
| --- | --- |
| OpenViking authentication | `api_key` or local dev mode. The gateway does not support trusted authentication. |
| OpenViking version | An embedded gateway ships with the server, so there is nothing to check. A standalone gateway needs OpenViking Server 0.4.16 or later. |
| Installation | The gateway is part of the OpenViking core package. The optional `gateway-fast` extra (`pip install "openviking[gateway-fast]"`) adds a faster event loop and HTTP parser. |
| Sign-in | Remote deployments need an OpenViking API key; users, account admins and root can all sign in to the gateway pages in Studio. Local dev mode needs no key and always uses the `default/default` identity. |
| Storage | A local disk. The gateway's SQLite databases serve one host only; network filesystems and storage shared between hosts are not supported. |

## Choose a deployment form {#deployment-options}

The gateway runs either inside the OpenViking Server (embedded) or as its own process (standalone). Both serve the same model and management paths under `/gateway`.

| Form | Choose it when | How to enable it | Client address |
| --- | --- | --- | --- |
| Embedded in the server (including Compose and Helm) | You run the OpenViking Server yourself. There is no second process and no path-based reverse proxy. | Turn on `gateway.enabled` in `ov.conf` | The server address plus `/gateway`, `http://127.0.0.1:1933/gateway` by default |
| Standalone | You want to leave the OpenViking service's configuration untouched, for example an OpenViking Cloud service. | `openviking-gateway init`, then `openviking-gateway` | The gateway's own address plus `/gateway`, `http://127.0.0.1:1935/gateway` by default |

Then follow the section for your setup: [Enable it in the server](#enable-it-in-the-server), [Docker Compose](#docker-compose), [Helm](#helm) or [Standalone deployment](#standalone-deployment).

## Enable it in the server {#enable-it-in-the-server}

The embedded gateway runs inside the OpenViking Server on the same port, and it is off by default (`gateway.enabled` is `false`). Until you turn it on, Studio's **OpenViking Gateway** page shows **The gateway is not enabled on this OpenViking server**. Turn it on in one of three ways.

**With `openviking-server init`.** Answer yes to **Enable OpenViking Gateway on the same port?**, then start or restart the server. With an existing configuration, choose **Update gateway** from the menu. For a remote deployment, init also asks whether users may configure upstreams themselves; see [Gateway management key](#gateway-management-key).

**In `ov.conf`.** Merge this into `ov.conf` and restart the server:

```json
{
  "gateway": {
    "enabled": true
  }
}
```

**From Studio.** On a server that uses API keys, root can choose **Enable gateway** on Studio's OpenViking Gateway page. Studio writes `gateway.enabled: true` to `ov.conf` and restarts the server. The confirmation dialog can also generate a gateway management key; see [Gateway management key](#gateway-management-key). Exceptions:

- Restarting from Studio needs a single-worker server. Otherwise Studio only saves the file, and you restart the server yourself.
- When `OPENVIKING_GATEWAY_ENABLED` is set, it overrides `gateway.enabled` in `ov.conf`, so a false value keeps the gateway off after the restart.
- If `ov.conf` is read-only, for example mounted from a Helm ConfigMap, Studio cannot change it. Set `gateway.enabled: true` in the Helm values or `OPENVIKING_GATEWAY_ENABLED=true`, then restart the server.

**Check that it is on.** Open `/studio/` on the server and choose **OpenViking Gateway** in the sidebar: you should see the gateway tabs instead of the not-enabled card. In local dev mode, if the default account has no upstreams, the gateway imports the server's usable VLM credentials as upstreams at startup and creates a `default` context profile. Imported Coding Plan upstreams need confirmation before use; if nothing can be imported, add an upstream by hand ([Upstreams](../gateway/07-upstreams.md#add-an-upstream)).

**Letting an agent change the configuration.** An agent that holds the root API key can edit `ov.conf` and restart the server through the [runtime configuration API](../api/08-admin.md#runtime-configuration). A standalone gateway does not offer this API.

## Docker Compose {#docker-compose}

With Docker Compose, the gateway runs inside the `openviking` service; there is no separate service or profile.

1. Set `OPENVIKING_GATEWAY_ENABLED=true` in `.env`, or turn on `gateway.enabled` in the mounted `ov.conf`. When the variable is set, it wins over `ov.conf`.
2. Make sure the mounted `ov.conf` uses API-key authentication: the server listens on `0.0.0.0` inside the container.
3. Run `docker compose up -d`, open `/studio/` on port 1933 and choose **OpenViking Gateway**. You should see the gateway tabs; if the server does not come up, read the startup errors with `docker compose logs openviking`.

Without Compose, add `-e OPENVIKING_GATEWAY_ENABLED=true` to `docker run`:

```bash
docker run -d -p 1933:1933 -v ~/.openviking:/app/.openviking \
  -e OPENVIKING_GATEWAY_ENABLED=true \
  ghcr.io/volcengine/openviking:latest
```

- **Management key**: set `OPENVIKING_GATEWAY_MANAGEMENT_KEY` in `.env`, and the bundled Compose file passes it into the container; or put it in `gateway.management_key` in the mounted `ov.conf`. The Compose file also passes `OPENVIKING_GATEWAY_ENCRYPTION_KEY`, which is only read once to migrate v0.5 data; see [Migration](#migration).
- **Public address**: set `OPENVIKING_PUBLIC_BASE_URL` (the bundled Compose file passes it); the gateway uses it when it has no `public_url` of its own. See [Public address](#public-address).
- **After a configuration change**: after editing the mounted `ov.conf`, run `docker compose restart openviking`. After editing `.env`, run `docker compose up -d`: `restart` reuses the existing container and does not pick up new `.env` values.

The bundled Caddy forwards all paths to `openviking:1933`. Clients can keep using the compatibility port 1934, or go straight to the server on 1933. For HTTPS, a single `reverse_proxy openviking:1933` carries Studio, the management API and model traffic; see [Public access](12-public-access.md).

## Helm {#helm}

With the Helm chart, set `gateway.enabled: true` in the chart values. The gateway runs inside the server container, keeps its data in `<persistence.mountPath>/gateway`, and uses the server port for both the Service and the Ingress. Keep `replicaCount: 1`.

```yaml
gateway:
  enabled: true
  existingSecret: ""  # optional: Secret name, entry management-key
config:
  gateway:
    public_url: https://ov.example.com
```

- The gateway encrypts its data with the chart's `config.encryption` settings, the same as the server.
- With a `management-key` entry in the `existingSecret` Secret, changing upstreams and context profiles requires that management key.
- Model requests stream: the Ingress must turn off response buffering and allow request bodies of at least 32 MiB and timeouts of 600 seconds (see [Reverse proxy and HTTPS](#reverse-proxy-and-https)).

The NGINX annotations, the commands to create the Secrets and the notes on upgrading from the sidecar chart are in the [chart README](https://github.com/volcengine/OpenViking/blob/main/deploy/helm/README.md#openviking-gateway).

## Standalone deployment {#standalone-deployment}

A standalone gateway does not read `ov.conf`. It only needs the OpenViking address, so an existing open-source or OpenViking Cloud service needs no configuration change.

1. Run `openviking-gateway init`. It asks for the OpenViking address and checks it (showing the server's authentication mode and version), the listen address (`127.0.0.1:1935` by default), and whether to require a [management key](#gateway-management-key). It writes `~/.openviking/gateway.conf`, or the file named by `--config`, readable only by you:

   ```json
   {
     "openviking_url": "https://ov.example.com",
     "host": "127.0.0.1",
     "port": 1935
   }
   ```

2. Run `openviking-gateway`. It runs the [startup checks](#startup-checks) and then listens on the address you chose.
3. Open the gateway's `/studio/` and sign in with the API key of an OpenViking user, account admin or root. You should see the gateway pages and a connection page that shows which OpenViking address the gateway uses.
4. Add upstreams by hand. A standalone gateway cannot read the remote server's VLM settings, so it cannot import model settings.

To change the settings later, run `openviking-gateway init` again: it offers the current values as defaults and keeps existing secrets unless you change them.

### Where the settings come from {#standalone-config-sources}

The only command-line flag is `--config`. When `--config` or `OPENVIKING_GATEWAY_CONFIG_FILE` is set, it must point to an existing file; otherwise the gateway reads the first of `~/.openviking/gateway.conf` and `/etc/openviking/gateway.conf` that exists. Environment variables override any field in it, so a container needs no file at all: set `OPENVIKING_GATEWAY_OPENVIKING_URL`, plus for example `OPENVIKING_GATEWAY_HOST=0.0.0.0`.

Without an OpenViking address from either source, the gateway exits and asks you to run `openviking-gateway init` or set `OPENVIKING_GATEWAY_OPENVIKING_URL`. Every field and the override rule are in the [Configuration reference](../gateway/13-configuration.md#configuration-reference).

### Startup checks {#startup-checks}

Before it starts, a standalone gateway contacts OpenViking once:

- If OpenViking uses trusted authentication, the gateway refuses to start.
- When the gateway binds to a non-loopback address, it starts only after confirming that OpenViking uses `api_key` authentication. If OpenViking is unreachable or runs in local dev mode, the gateway exits. Start OpenViking first, or bind to `127.0.0.1`.
- When OpenViking runs on another host, connect over TLS or a private network.

### Connecting to the public OpenViking service {#public-openviking-service}

A standalone gateway that connects to the public OpenViking service, `https://api.vikingdb.cn-beijing.volces.com/openviking`, without a management key is open to that service's users. The startup log shows the `public_openviking_without_management_key` warning, and Studio shows **This gateway is open to every OpenViking Cloud user**: anyone with an API key for that service can sign in to this gateway and change the upstreams in their account.

If the gateway is only for you or your team, set `OPENVIKING_GATEWAY_MANAGEMENT_KEY` and restart to restrict changes. This does not limit who can sign in or whether they can issue gateway keys for themselves.

## Routes {#routes}

Every gateway URL is under `/gateway`, in both forms. An embedded server hands the whole `/gateway` prefix to the gateway and keeps every other path for itself:

| Path prefix | Purpose |
| --- | --- |
| `/gateway/v1/` | Model APIs and the model list |
| `/gateway/api/v3/` | Ark-compatible clients |
| `/gateway/api/compatible/v1/` | Ark Anthropic-compatible clients |
| `/gateway/uploads` | Tool uploads |
| `/gateway/downloads` | Tool downloads |
| `/gateway/admin/` | Management API (authenticated) |

Clients therefore use these base URLs, with `http://127.0.0.1:1933` replaced by your server's address. Per-client settings are in [Connect clients](../gateway/03-connect-clients.md#connect-clients).

| Client | Base URL |
| --- | --- |
| Claude Code and Anthropic SDKs | `http://127.0.0.1:1933/gateway` |
| OpenAI-compatible clients and SDKs, including Codex, OpenCode, pi and Open WebUI | `http://127.0.0.1:1933/gateway/v1` |
| Volcano Engine Ark and BytePlus ModelArk SDKs | `http://127.0.0.1:1933/gateway/api/v3` |

`/health` stays the server's own health check; an embedded gateway has no separate `/health`. A standalone gateway serves the same `/gateway` paths on its own port, plus two at the root:

- `/` redirects to `/studio/`, where Studio has only the gateway and connection pages.
- `/health` returns `service: "openviking-gateway"`, `auth_mode` and `openviking_url`, and also the caller's identity when the request carries a valid OpenViking credential.

### Public address {#public-address}

The public address, `public_url`, is the address clients use to reach the server (or the standalone gateway), written without `/gateway`; the gateway appends `/gateway` itself. It is empty by default, and then an embedded gateway uses the server's public address (`OPENVIKING_PUBLIC_BASE_URL` or `server.public_base_url`). Set it for every shared deployment, and always when a reverse proxy is in front:

- **Embedded**: set `gateway.public_url` in `ov.conf`, then restart the server.
- **Standalone**: set `public_url` in `gateway.conf`, or `OPENVIKING_GATEWAY_PUBLIC_URL`, then restart the gateway.

Two things are built from it:

- **Studio's setup guides**: Anthropic clients use `<public_url>/gateway`, and OpenAI-style clients `<public_url>/gateway/v1`.
- **Upload and download links** for OpenViking tools, unless `mcp_file_links` is `direct`; see [Import files and skills](../gateway/04-openviking-tools.md#import-files-and-skills).

With no public address at all, the guides use the scheme and host the request reached the server with. Behind a reverse proxy this can be an internal or `http://` address, and the **Connect** tab warns **Clients may not be able to reach this address**; for a loopback address such as `127.0.0.1`, it warns **Only the gateway's own machine can use this address**.

## Reverse proxy and HTTPS {#reverse-proxy-and-https}

Forward the whole site to the server (embedded) or to the gateway (standalone); no path-based routing is needed. With Caddy:

```caddyfile
ov.example.com {
    reverse_proxy 127.0.0.1:1933 {
        flush_interval -1
    }
}
```

Whatever proxy you use, it needs these settings:

| Setting | Value | Why |
| --- | --- | --- |
| Response buffering | Off | Streamed output arrives in real time |
| Request body size | At least 32 MiB | The gateway's own default limit for requests and uploads; long conversations send their full history ([Side effects](../gateway/05-long-conversations.md#side-effects)) |
| Read timeout | At least 600 seconds | The gateway waits up to 600 seconds for a provider by default |
| Logging of `/gateway/uploads` and `/gateway/downloads` | Leave out the query strings | They carry one-time tokens |

Set up authentication and the [public address](#public-address) before you expose the service; see [Public access](12-public-access.md).

## Keys and credentials {#keys-and-credentials}

The gateway involves four keys, each with one job:

| Key | Purpose | Where it is used |
| --- | --- | --- |
| OpenViking API key | Signs in to Studio; decides which account and user memory is read and written as | `X-API-Key` or `Authorization: Bearer` on management requests |
| Gateway key (`ovgw_…`) | Connects a client; each one binds a user, a context profile and a set of upstreams ([Gateway keys](../gateway/09-gateway-keys.md#gateway-keys)) | The client's API key field |
| Gateway management key (optional) | Restricts who can change upstreams and context profiles | `gateway.management_key`; the `X-OpenViking-Gateway-Management-Key` request header |
| Upstream API key | Calls the model provider ([Credentials](../gateway/07-upstreams.md#credentials)) | The upstream editor |

The gateway has no encryption key of its own: it encrypts its local storage with OpenViking's encryption settings; see [Encryption](#encryption).

### Gateway management key {#gateway-management-key}

The gateway management key is an optional deployment secret that restricts who can change [upstreams and context profiles](15-gateway.md#key-terms). It is not set by default, so every signed-in user in an account can add and change that account's upstreams and context profiles. Once it is set, only requests that carry it can change them, OpenViking admins and root included. It does not limit who can sign in, or whether users can issue gateway keys for themselves.

What read-only users can still see and do in Studio is in [Who can change what](../gateway/06-manage-in-studio.md#who-can-change-what); the full rules, including the management API, are in the [access matrix](../api/25-gateway.md#credentials-and-account-scope).

**Treat it as a deployment secret.** The management key also works on its own, without an OpenViking API key: with `account_id` it can read, create, change and delete upstreams and context profiles in any account, including pointing an upstream at a different provider address. It cannot read memory or logs, or issue gateway keys. Share it only with the people who maintain upstreams.

To set one:

- **`openviking-server init`** (embedded): for a remote deployment, answer no to **Allow users to configure their own gateway upstreams?**. Init generates a value starting with `ovgm_`, prints it and saves it to `gateway.management_key` in `ov.conf`.
- **`openviking-gateway init`** (standalone): answer yes to **Require a management key to change upstreams and profiles?**. Init generates an `ovgm_` value, prints it once and saves it to `management_key` in `gateway.conf`.
- **Studio** (embedded, root): the confirmation dialog of **Enable gateway** has a checkbox, **Only people with the gateway management key can change upstreams and context profiles**, which is off by default. If you check it, Studio generates an `ovgm_` value, saves it to `gateway.management_key` in `ov.conf` along with `gateway.enabled: true`, and shows it once.
- **By hand**: use at least 32 characters, for example from `openssl rand -base64 32`. Put it in `management_key`, or set the `OPENVIKING_GATEWAY_MANAGEMENT_KEY` environment variable, which overrides the file in both forms; see [Configuration reference](../gateway/13-configuration.md#configuration-reference).
- **Compose and Helm**: Compose passes `OPENVIKING_GATEWAY_MANAGEMENT_KEY` into the container; Helm reads it from the `management-key` entry of the Secret named in `gateway.existingSecret`. See [Docker Compose](#docker-compose) and [Helm](#helm).

There is no command-line flag for the management key, because flags show up in process lists and shell history. Setting, changing or removing it takes a restart. It does not revoke issued gateway keys or change existing upstreams and context profiles.

#### Lost the management key {#lost-the-management-key}

If nobody has the [gateway management key](#gateway-management-key), look where the gateway reads it from, in this order:

1. The `OPENVIKING_GATEWAY_MANAGEMENT_KEY` environment variable, which wins over the files. Compose usually sets it in the `.env` file next to `docker-compose.yml`; Helm reads it from the `management-key` entry of the Secret named in `gateway.existingSecret`.
2. Otherwise, `gateway.management_key` in `ov.conf` (embedded) or `management_key` in `gateway.conf` (standalone). A value written as `$VAR` lives in that environment variable.

On an embedded gateway, root can also open `ov.conf` in Studio under **Settings** → **Server configuration** → **File editor**. Keys generated by `openviking-server init` and by Studio's **Enable gateway** are stored there in plain text.

If you can't recover it, replace it:

1. Generate a new value of at least 32 characters, for example with `openssl rand -base64 32`.
2. Put it in the same place. An empty environment variable does not clear the value in the file; unset the variable instead.
3. Restart. With Compose, a change in `.env` takes effect only after `docker compose up -d`, not `docker compose restart`.
4. In Studio, choose **Enter gateway management key** and paste the new value. Issued gateway keys, upstreams and context profiles are unaffected.

## Encryption {#encryption}

The gateway encrypts stored credentials and conversation state with OpenViking's own encryption (see the [Encryption guide](08-encryption.md)). When encryption is off or not configured, the gateway stores everything in plain text, protected only by file permissions.

- **Embedded**: the gateway follows the server's `encryption` section in `ov.conf`. There is nothing to set for the gateway itself.
- **Standalone**: add an `encryption` block to `gateway.conf`. It has exactly the same fields as the `encryption` section of `ov.conf`, for example:

  ```json
  {
    "openviking_url": "https://ov.example.com",
    "encryption": {
      "enabled": true,
      "provider": "local",
      "local": { "key_file": "~/.openviking/master.key" }
    }
  }
  ```

Changing the setting later:

- **Turning encryption on** is safe: values written before stay readable, and new writes are encrypted.
- **Turning it off, or switching to a different encryption root key**, makes the encrypted values unreadable, so keep the settings the data was written with.
- **Losing the encryption root key** makes the gateway's databases unreadable. Back it up as the Encryption guide describes.

Data written by the v0.5 gateway, which used its own key, is converted on the first start; see [Migration](#migration).

## Scaling {#scaling}

One storage directory serves one deployment on one host, and an embedded and a standalone gateway must not share a directory. An embedded gateway follows the server's worker setting; a standalone gateway takes `workers`, from 1 to 64. For load balancing, failover or rate limiting across providers, add a service such as LiteLLM as an upstream.

## Monitoring and routine checks {#monitoring}

The gateway exports no metrics such as Prometheus and writes no HTTP access log. Check its state on Studio's [Overview and Requests](../gateway/10-overview-and-requests.md#overview) tabs, or through the management API's `/gateway/admin/overview` and `/gateway/admin/logs`.

### Routine checks {#day-to-day-operations}

- **Liveness:** `/health`, which is the server's health check in an embedded deployment and the gateway's own in a standalone one.
- **Gateway health:** the **First-call cache hit rate** on the Overview tab ([what it means](../gateway/10-overview-and-requests.md#overview)). If it drops noticeably, see [The first-call cache hit rate dropped](../gateway/12-troubleshooting.md#the-first-call-cache-hit-rate-dropped).
- **A single request:** expand its row on the Requests tab; see [Requests](../gateway/10-overview-and-requests.md#requests).
- **Public address:** `public_url` still matches the address clients use; see [Public address](#public-address).
- **Backups:** the storage directory and, with encryption on, the encryption root key; see [Backups and recovery](../gateway/11-security-and-data.md#backups).

## Upgrades {#upgrades}

- Read the release notes before you upgrade. If a new gateway version cannot read the old storage format, it stops at startup with `Unsupported gateway schema`; see [The gateway does not start](../gateway/12-troubleshooting.md#the-gateway-does-not-start).
- A standalone gateway needs OpenViking Server `min_server_version` (0.4.16) or later. With an older server, no keys can be issued and saving retries fail with `openviking_version_mismatch`; memory search may also fail, shown as `openviking_http_<status>`. When you upgrade the two separately, upgrade OpenViking first, then the gateway. An embedded gateway ships with its server and skips this check.
- To move from the sidecar or an older standalone gateway, see [Migration](#migration).

## Migration from earlier releases {#migration}

These steps apply when you move from the sidecar or standalone gateway of earlier releases to the embedded gateway. Steps 4 and 7 are detailed in the tables after the list.

1. **Back up first.** Back up `storage_path` (both databases and `encryption.key`, if it is there). Stop the old gateway process or sidecar before you enable the embedded gateway.
2. **Remove old fields.** Delete `host`, `port`, `workers`, `url`, `openviking_url`, `admin_token_env` and `encryption_key_env` from the `gateway` section of `ov.conf`. The server ignores them and logs a warning at startup, so an old `ov.conf` still starts, but clean them up; `openviking-server init` → **Update gateway** removes them for you.

   To keep running the gateway on its own instead, run `openviking-gateway init`, or move its settings to a separate `gateway.conf`, without the `gateway` wrapper and `enabled`. The standalone command no longer takes `--openviking-url`, `--host`, `--port`, `--workers` or `--storage-path`; set them in `gateway.conf` or as environment variables.
3. **Fix the public address.** Point `public_url` at the server's public address, without `/gateway`, or remove it. The old quick start set it to `http://127.0.0.1:1935`, which the embedded gateway no longer listens on, so setup guides and upload links would point at nothing. When `public_url` points at another port, `openviking-server init` → **Update gateway** offers to clear it.
4. **Update client addresses and deployment config.** Every gateway URL now lives under `/gateway`, so clients change both the port and the path, and the old paths are not served any more; see [New addresses](#new-addresses). Remove the old 1935 Service and Ingress routes, the Compose `gateway` service and profile, and the Helm sidecar settings `gateway.workers`, `gateway.port` and `gateway.resources`.
5. **Let the first start convert stored data.** The v0.5 gateway encrypted its data with its own key. On the first start, the new gateway (embedded or standalone) decrypts it with that key and rewrites it under the current [encryption](#encryption) settings, or in plain text when encryption is off, so turn on OpenViking encryption before this start if you want the data to stay encrypted.

   The old key is read from the `OPENVIKING_GATEWAY_ENCRYPTION_KEY` environment variable, or from `encryption.key` in `storage_path`. `encryption_key_env` is ignored: if you used it to point at another variable, export the key as `OPENVIKING_GATEWAY_ENCRYPTION_KEY` for this start. If old encrypted data exists and neither source has the key, the gateway refuses to start and the error names both places. With Helm, keep the `encryption-key` entry in the `existingSecret` Secret for the upgrade release only. After a successful start the old key is never read again, so you can remove the variable, the Secret entry and `encryption.key`.
6. **Remove the shared admin token.** `OPENVIKING_GATEWAY_ADMIN_TOKEN` is **not** used as a management key. To restrict who can change upstreams and context profiles, set `management_key` explicitly; see [Gateway management key](#gateway-management-key).
7. **Check upstream addresses.** On the first start, the gateway rewrites stored Base URLs once, so each upstream keeps calling the same address as before the upgrade; see [How stored upstream addresses change](#upstream-address-rewrite). Use **Test** on the Upstreams tab to confirm each one still answers.
8. **Note the changes to logs and revocation.** Request logs gain a user column; older records have no user and appear only in account admin and root views. Revoking a key no longer deletes the user's data; explicit deletion and retention still apply.

### New addresses {#new-addresses}

Replace `host` with your server's address:

| What | Before | After |
| --- | --- | --- |
| OpenAI-compatible clients | `http://host:1935/v1` | `http://host:1933/gateway/v1` |
| Claude Code `ANTHROPIC_BASE_URL` | `http://host:1935` | `http://host:1933/gateway` |
| Volcano Engine Ark and BytePlus ModelArk SDKs | The old gateway address | `http://host:1933/gateway/api/v3` |
| Scripts that call the management API | `/api/v1/admin/gateway/...` | `/gateway/admin/...` |

1935 is still the standalone default port, and a standalone gateway uses the same `/gateway` paths.

### How stored upstream addresses change {#upstream-address-rewrite}

| Upstream | Stored Base URL | After the first start |
| --- | --- | --- |
| Ark and ModelArk, Chat Completions or Responses | Only a domain | `/api/v3` added |
| Other Chat Completions or Responses | A path that does not end in a version segment such as `/v1` or `/v4`, including a bare domain or a path prefix | `/v1` added: `https://api.deepseek.com` → `https://api.deepseek.com/v1`, `https://openrouter.ai/api` → `https://openrouter.ai/api/v1`, or a self-hosted proxy under `/litellm` |
| Ark and ModelArk, Anthropic | Only a domain | `/api/compatible` added |
| Anthropic | Ends in `/v1` | `/v1` removed |
| Any | Already ends in another version segment, such as Zhipu's `https://open.bigmodel.cn/api/paas/v4` | Unchanged. Before the upgrade it became `…/v4/v1` and failed; now it works as entered. |

New and edited upstreams follow the rules in [How the Base URL is joined](../gateway/07-upstreams.md#base-url), and an Anthropic Base URL ending in `/v1` is rejected.

## Next steps {#next-steps}

- [Manage OpenViking Gateway in Studio](../gateway/06-manage-in-studio.md): add upstreams, create context profiles and issue the first gateway keys.
- [Security and data](../gateway/11-security-and-data.md): review what the gateway stores, who can reach it, and how to back it up and delete data.
- [Troubleshoot OpenViking Gateway](../gateway/12-troubleshooting.md): match a startup error, setup card or failed request to its fix.
