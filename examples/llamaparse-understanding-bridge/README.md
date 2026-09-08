# LlamaParse v2 Understanding API bridge

This example lets OpenViking use LlamaParse v2 without changes to OpenViking core. It runs as
a small HTTP service and implements the Understanding API endpoints that OpenViking already uses.

```text
OpenViking -> Understanding API bridge -> LlamaParse v2
           <- Markdown and image ZIP  <-
```

The bridge does not store LlamaParse file or job state. A LlamaParse file ID is also the
Understanding file ID. A LlamaParse job ID is also the Understanding response ID. This means that
polling continues to work after a bridge restart. The bridge stores only short-lived result ZIP files
in its local cache.

## Requirements

- Python 3.10 or later and [`uv`](https://docs.astral.sh/uv/), or Docker
- A [LlamaCloud API key](https://developers.llamaindex.ai/llamaparse/general/api_key/)
- An OpenViking server configuration

## Run with `uv`

Run all commands from the OpenViking repository root.

1. Create the bridge settings file:

   ```bash
   cp examples/llamaparse-understanding-bridge/.env.example \
     examples/llamaparse-understanding-bridge/.env
   ```

2. Set these two values in the new `.env` file:

   ```text
   LLAMA_CLOUD_API_KEY=llx-...
   PARSER_BRIDGE_API_KEY=<a-random-secret-with-at-least-32-characters>
   ```

   OpenViking and the bridge must use the same `PARSER_BRIDGE_API_KEY`. You can create one with:

   ```bash
   python -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

3. Start the bridge:

   ```bash
   uv run --frozen --env-file examples/llamaparse-understanding-bridge/.env \
     --project examples/llamaparse-understanding-bridge \
     openviking-llamaparse-bridge
   ```

4. Confirm that it is ready:

   ```bash
   curl http://127.0.0.1:8080/health
   ```

## Configure OpenViking

Run `openviking-server init` first if you do not have an OpenViking configuration. Then add this
section to `~/.openviking/ov.conf`:

```json
{
  "parser_api": {
    "enable": true,
    "host": "http://127.0.0.1:8080",
    "api_key": "${PARSER_BRIDGE_API_KEY}",
    "extensions": ["pdf", "docx", "pptx", "xlsx"],
    "http_timeout_seconds": 130
  }
}
```

Set `PARSER_BRIDGE_API_KEY` in the environment that starts OpenViking. OpenViking expands environment
variables in `ov.conf`. You can also put the key directly in `ov.conf`, but take care not to commit
the file.

The `extensions` list controls routing. OpenViking sends only the listed file types to this bridge.
It continues to use its built-in parsers for other file types. Restart OpenViking after you change
`ov.conf`.

`http_timeout_seconds` must be greater than `BRIDGE_HTTP_TIMEOUT_SECONDS`. The values above give the
bridge 120 seconds for one LlamaCloud request and give OpenViking 130 seconds to receive the bridge
response. Calibrate these values with representative documents before production use.

Start OpenViking and the bridge as separate processes. If one process stops, it does not stop the
other process.

## Run with Docker

Create the `.env` file as shown above. Then run this command from the repository root:

```bash
docker compose -f examples/llamaparse-understanding-bridge/compose.yaml up --build
```

The default configuration publishes the bridge only at `http://127.0.0.1:8080`. If OpenViking runs
in a different container, both containers must share a network. Set `parser_api.host` and
`BRIDGE_PUBLIC_URL` to an address that the OpenViking container can reach, such as the bridge service
name. Do not use `127.0.0.1` between separate containers. To accept connections from another host,
also set `BRIDGE_PUBLISH_HOST=0.0.0.0` and restrict access with a firewall.

## Main settings

| Setting | Default | Purpose |
|---|---|---|
| `LLAMA_CLOUD_API_KEY` | Required | Authenticates requests to LlamaCloud. |
| `PARSER_BRIDGE_API_KEY` | Required | Authenticates OpenViking requests. Use at least 32 characters. |
| `LLAMAPARSE_REGION` | `na` | Selects the `na` or `eu` LlamaCloud endpoint. |
| `LLAMAPARSE_TIER` | `agentic` | Selects `cost_effective`, `agentic`, or `agentic_plus`. |
| `LLAMAPARSE_COST_OPTIMIZER` | `true` | Sends simple pages to the lower-cost tier. It requires `agentic` or `agentic_plus`. |
| `BRIDGE_PUBLIC_URL` | `http://127.0.0.1:8080` | Base URL that OpenViking uses to download result ZIP files. |
| `BRIDGE_PORT` | `8080` | Local bridge port. |

The bridge does not offer the LlamaParse `fast` tier. OpenViking requires Markdown, and the `fast`
tier does not return Markdown. Cost Optimizer is an option, not a tier. It can route simple pages to
`cost_effective` while the selected agentic tier handles complex pages.

## File types

LlamaParse accepts [130+ file formats](https://developers.llamaindex.ai/llamaparse/general/supported_document_types/),
including common documents, images, spreadsheets, and audio files. Add only the extensions that you
want OpenViking to route to this bridge. Start with file types that you have tested.

This example supports local file uploads and public HTTP or HTTPS document URLs. It does not support:

- direct `input_image`, `input_audio`, or `input_video` URL requests;
- Feishu or Lark credential payloads;
- the Understanding API resumable-upload endpoints.

Keep `parser_api.enable_resumable_upload` disabled. The simple upload path supports files up to
OpenViking's default limit of 512 MiB. Your LlamaCloud account and selected parser can apply a lower
limit.

## Advanced settings

Most users do not need more settings. To pass LlamaParse v2 parse options, set one JSON object:

```text
LLAMAPARSE_PARSE_OPTIONS_JSON={"input_options":{"spreadsheet":{"detect_sub_tables_in_sheets":true}}}
```

See the [LlamaParse configuration guide](https://developers.llamaindex.ai/llamaparse/parse/guides/configuring-parse/)
for available fields. The bridge owns `file_id`, `source_url`, `tier`, and `version`, so the JSON
object cannot replace them. `LLAMAPARSE_COST_OPTIMIZER` controls
`processing_options.cost_optimizer`. The bridge also requests embedded and layout images because
OpenViking needs the files referenced by the returned Markdown.

Other advanced environment variables are documented in `.env.example`.

## API mapping

| OpenViking request | LlamaParse v2 operation |
|---|---|
| `POST /api/v3/files` | Upload a file with `purpose=parse`. |
| `POST /api/v3/responses` | Create a parse job. |
| `GET /api/v3/responses/{response_id}` | Read the parse job status. |
| `GET /artifacts/{job_id}.zip` | Download a cached ZIP with `content.md` and parsed images. |

Artifact URLs use an HMAC signature and expire after five minutes by default. OpenViking downloads the
ZIP as soon as parsing completes. Before it reports `completed`, the bridge downloads result images
concurrently and writes the ZIP to a bounded local cache. A repeated download reads the same cached
file. The bridge accepts only HTTPS image URLs that resolve to public addresses, checks each redirect,
and does not send the LlamaCloud API key to image hosts.

The cache defaults to the system temporary directory, expires files after five minutes, and uses at
most 1 GiB. Configure `BRIDGE_ARTIFACT_CACHE_DIR`, `BRIDGE_ARTIFACT_TTL_SECONDS`, and
`BRIDGE_ARTIFACT_CACHE_MAX_BYTES` only when the defaults do not fit the deployment. This local cache
is for one bridge instance. Shared cache storage and multi-replica operation are outside this discovery
example.

## Local tests

The tests use mock HTTP services. They do not consume LlamaCloud credits.

```bash
uv --native-tls run \
  --frozen \
  --project examples/llamaparse-understanding-bridge \
  --extra test --extra dev \
  pytest -c examples/llamaparse-understanding-bridge/pyproject.toml \
  examples/llamaparse-understanding-bridge/tests -q
```

Run the other checks with the same environment:

```bash
uv --native-tls run --frozen --project examples/llamaparse-understanding-bridge --extra dev \
  ruff format --check examples/llamaparse-understanding-bridge
uv --native-tls run --frozen --project examples/llamaparse-understanding-bridge --extra dev \
  ruff check examples/llamaparse-understanding-bridge
uv --native-tls run --frozen --project examples/llamaparse-understanding-bridge --extra dev \
  mypy examples/llamaparse-understanding-bridge/openviking_llamaparse_bridge
uv --native-tls run --frozen --project examples/llamaparse-understanding-bridge --extra dev \
  python -m build --no-isolation examples/llamaparse-understanding-bridge
```

## Live test

The live test uses LlamaCloud credits. Start with a small, non-sensitive PDF. Start the bridge and
OpenViking, add that PDF through the normal OpenViking resource API or client, and confirm these
results:

1. The bridge returns a LlamaParse file ID and job ID.
2. OpenViking polls until the job is complete.
3. OpenViking downloads the signed ZIP.
4. The new resource contains the parsed Markdown and its images.
5. Search can retrieve text from the new resource.
6. Record upload, job-creation, polling, ZIP-preparation, and ZIP-download times.
7. Record the HTTPS hosts used for presigned result images and redirects.
