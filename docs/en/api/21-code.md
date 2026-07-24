# Code Navigation

The Code Navigation API extracts structure, searches symbols, and expands symbols in source code already ingested into OpenViking. Every `uri` must be a `viking://` URI; ingest local code through the Resources API first.

## API Reference

### outline()

Extract the symbol outline of one source file.

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

### search()

Search symbols in supported source files under a URI. One request scans at most 200 files; the response asks the caller to narrow the URI if the cap is reached.

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

### expand()

Return the complete code fragment for a symbol in one file.

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

## Current Client Support

These endpoints are currently server HTTP capabilities and are not exposed by the public Python, TypeScript, or Go SDKs or the `ov` CLI. This page therefore shows only the HTTP tab.

## Related Documentation

- [Resources](02-resources.md) - ingest source code
- [Retrieval](06-retrieval.md) - generic semantic and pattern search
- [Content](12-content.md) - read source files
