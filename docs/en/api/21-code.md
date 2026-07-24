# Code Navigation

The Code Navigation API extracts structure, searches symbols, and expands symbols in source code already imported into OpenViking. Every `uri` must be a `viking://` URI; import local code through the Resources API first.

**Code entry points**:

- `openviking/server/routers/code.py` - HTTP routes and request models
- `openviking/parse/parsers/code/ast/code_tools.py` - outline, search, and symbol expansion

## API Reference

### outline()

Extract the symbol outline of one source file.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uri` | string | Yes | `viking://` URI of an imported source file |

**HTTP API**

```http
POST /api/v1/code/outline
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/code/outline \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/project/src/main.py"}'
```

**Response Example**

`result` is a text outline suitable for display or direct Agent context.

```json
{
  "status": "ok",
  "result": "viking://resources/project/src/main.py  [python, 24 lines]\n\nclass SessionService  L16-24\n\ndef main()  L8-12"
}
```

### search()

Search symbols in supported source files below a URI. One request scans at most 200 files; when capped, `result` ends with a prompt to narrow the URI.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uri` | string | Yes | File or directory URI to search recursively |
| `query` | string | Yes | Non-empty symbol name or search term |

**HTTP API**

```http
POST /api/v1/code/search
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/code/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/project/src","query":"SessionService"}'
```

**Response Example**

```json
{
  "status": "ok",
  "result": "1 matches for \"SessionService\" (scanned 8 files)\n\nviking://resources/project/src/session.py\n  SessionService  L16-24"
}
```

### expand()

Return the complete code block for one symbol in a source file.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uri` | string | Yes | Source file URI containing the symbol |
| `symbol` | string | Yes | Non-empty symbol name to expand |

**HTTP API**

```http
POST /api/v1/code/expand
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/code/expand \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/project/src/main.py","symbol":"main"}'
```

**Response Example**

```json
{
  "status": "ok",
  "result": "# viking://resources/project/src/main.py  L8-10  (main)\n\ndef main():\n    service = SessionService()\n    service.run()"
}
```

The current implementation returns business failures such as a non-`viking://` URI, an empty query or symbol, or a non-text file with `status="ok"` and an `Error:` message in `result`. A request body missing required fields returns HTTP `400` with `INVALID_ARGUMENT`.

## Current Client Support

These endpoints are currently server-only HTTP capabilities. They are not wrapped by the public Python, TypeScript, or Go SDKs or by the `ov` CLI, so this page shows only the HTTP tab.

## Related Documentation

- [Resources](02-resources.md) - import source code
- [Retrieval](06-retrieval.md) - general semantic and pattern retrieval
- [Content](12-content.md) - read source file content
