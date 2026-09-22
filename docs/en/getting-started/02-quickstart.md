# Quick Start

OpenViking runs as a server. Connect to it with the standalone `ov` CLI, import a small document, and retrieve its content. If you use a managed service or someone else's deployment, you only need the CLI.

## 1. Choose a service

**Already have an endpoint?** Keep its URL and API key ready and skip to step 2. For a server using API key authentication, use a user/admin key for data access; a root key is for administration. See [Authentication](../guides/04-authentication.md).

Otherwise, choose one:

### Managed service on Volcengine

Open the [OpenViking console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing) and obtain your key from **User Management → API Key**. The service endpoint is:

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

No server installation or local model configuration is needed. See the [product page](https://www.volcengine.com/product/openviking-service) and [service documentation](https://docs.volcengine.com/docs/84313/2374478) for managed-service usage and limits. Continue to step 2.

### Self-host a server

[Install uv](https://docs.astral.sh/uv/getting-started/installation/) on the server machine, then install OpenViking:

```bash
uv tool install openviking --upgrade
openviking-server init
openviking-server doctor
openviking-server
```

The setup wizard configures the server's models and writes `~/.openviking/ov.conf`. Prepare access to an embedding model and a VLM — Volcengine (Doubao) models are recommended; see the [Volcengine Purchase Guide](../guides/02-volcengine-purchase-guide.md) for purchase and activation — then check the configuration with `doctor`. Keep the server running and use another terminal for the remaining steps.

A single curl confirms the server is up — no client required:

```bash
curl http://127.0.0.1:1933/health
# {"status":"ok","healthy":true,...}
```

The local endpoint is `http://127.0.0.1:1933`; the default local setup needs no API key. Web Studio is available at `/studio`. For Docker, persistent storage, and remote access, see [Deployment](../guides/03-deployment.md), [Model Configuration](../guides/01-configuration.md), and [Authentication](../guides/04-authentication.md).

## 2. Install and connect the CLI

On your client machine, with Node.js and npm installed:

```bash
npm install -g @openviking/cli
ov language en
ov config
```

In the interactive configuration, choose **OpenViking Service** for Volcengine or **Custom** for a self-hosted endpoint. Enter the API key and, for a custom service, its URL. Leave the key empty for the default local server. Save and activate the configuration.

The CLI stores the active connection in `~/.openviking/ovcli.conf`. This is separate from the server's `ov.conf`. For scripted setup or multiple endpoints, see [CLI Setup](05-cli-setup.md).

Verify the connection:

```bash
ov health
```

This checks that the server responds; the import below also exercises model processing and data access.

## 3. Import a document

Save the following as `quickstart.md` in your current directory:

```markdown
# Project Atlas

Project Atlas backs up its documents every Friday.
Maya owns the backup process. Keep each backup for 30 days.
```

Import it into a new resource directory:

```bash
ov add-resource ./quickstart.md --to viking://resources/quickstart-demo --wait --timeout 120
```

The CLI uploads the local file automatically. `--wait` waits for processing; continue after the command succeeds. Without it, save the returned `task_id` and use `ov task status <task_id>` until the task is `completed`. See [Background Tasks](../api/17-tasks.md).

Use an unused target URI for this example. If you repeat the example, choose a new target and use that same URI in the commands below.

## 4. Browse and search

```bash
ov tree viking://resources/quickstart-demo
ov overview viking://resources/quickstart-demo
ov find "Who owns the backup process?" --uri viking://resources/quickstart-demo
```

`tree` lists the imported structure; `overview` reads its generated summary. `find` returns relevant context with URIs and scores. To read a match, pass its returned URI to `ov read`:

```bash
ov read "<returned-file-uri>"
```

Replace `<returned-file-uri>` with a file URI from the results, without the angle brackets. For more resource types and retrieval options, see [Resources](../api/02-resources.md) and [Retrieval](../api/06-retrieval.md).

## Use an SDK

OpenViking also provides Python, TypeScript/JavaScript, and Go SDKs that connect to the same server. See the [API Overview](../api/01-overview.md) for client examples.
