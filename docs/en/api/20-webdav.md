# WebDAV

WebDAV provides file-protocol access to the `resources` namespace.

## WebDAV (Phase 1)

OpenViking Server also exposes a minimal WebDAV adapter for resource files:

```text
/webdav/resources
```

Phase 1 intentionally keeps the scope narrow:

- Resources only. Memories, skills, sessions, and other namespaces are not exposed.
- Text-first writes. `PUT` currently accepts UTF-8 text content only.
- WebDAV subset only. `OPTIONS`, `PROPFIND`, `GET`, `HEAD`, `PUT`, `DELETE`, `MKCOL`, and `MOVE` are supported.
- Semantic sidecars and internal system files stay internal. Derived or internal files such as `.abstract.md`, `.overview.md`, `.relations.json`, `.path.ovlock`, `.redirect.json`, and `.sync_log.json` are hidden from listings and cannot be accessed directly through WebDAV.

Behavior notes:

- Creating a new file through WebDAV triggers OpenViking semantic generation for that file path.
- Replacing an existing file through WebDAV refreshes related semantics and vectors, same as `write()`.
- `PUT` does not create parent collections automatically. Create missing directories with `MKCOL` first.
- User-created dot-directories and dot-files remain visible unless they match one of the reserved internal filenames above.
- When multi-write storage is enabled, files redirected to a backup are still exposed through the filesystem APIs as normal files; internal redirect and sync metadata never become visible to callers.

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `OPTIONS` | `/webdav/resources`, `/webdav/resources/{resource_path}` | Return supported methods and DAV version |
| `PROPFIND` | `/webdav/resources`, `/webdav/resources/{resource_path}` | Return properties for the target and immediate children |
| `GET` / `HEAD` | `/webdav/resources`, `/webdav/resources/{resource_path}` | Read file content or headers |
| `PUT` | `/webdav/resources`, `/webdav/resources/{resource_path}` | Create or overwrite a UTF-8 text file |
| `DELETE` | `/webdav/resources`, `/webdav/resources/{resource_path}` | Delete a file or recursively delete a directory |
| `MKCOL` | `/webdav/resources`, `/webdav/resources/{resource_path}` | Create a directory |
| `MOVE` | `/webdav/resources`, `/webdav/resources/{resource_path}` | Move or rename a file or directory |

Except for `OPTIONS`, WebDAV requests use the same authentication headers as other OpenViking APIs. Paths must remain under `resources` and cannot escape through `..`, backslashes, or equivalent forms.

### List a collection

`Depth` supports `0` and one level; other values are treated as one level. A successful response is `207 Multi-Status` with DAV XML.

**HTTP API**

```bash
curl -X PROPFIND http://localhost:1933/webdav/resources/docs \
  -H "X-API-Key: your-key" \
  -H "Depth: 1"
```

### Read and write files

`PUT` does not create parent directories. File creation returns `201`, replacement returns `204`, and non-UTF-8 content returns `415`.

**HTTP API**

```bash
curl http://localhost:1933/webdav/resources/docs/readme.md \
  -H "X-API-Key: your-key"
```

```bash
curl -X PUT http://localhost:1933/webdav/resources/docs/readme.md \
  -H "X-API-Key: your-key" \
  -H "Content-Type: text/plain; charset=utf-8" \
  --data-binary @README.md
```

### Create, move, and delete

`MOVE` requires a `Destination` header whose value remains under `/webdav/resources`. The destination parent directory must already exist.

**HTTP API**

```bash
curl -X MKCOL http://localhost:1933/webdav/resources/archive \
  -H "X-API-Key: your-key"
```

```bash
curl -X MOVE http://localhost:1933/webdav/resources/docs/readme.md \
  -H "X-API-Key: your-key" \
  -H "Destination: /webdav/resources/archive/readme.md"
```

```bash
curl -X DELETE http://localhost:1933/webdav/resources/archive \
  -H "X-API-Key: your-key"
```

WebDAV is a protocol entry point rather than an OpenViking SDK or `ov` CLI surface, so this page shows only the HTTP tab. Use [File System](03-filesystem.md) for SDK and CLI file operations.

## Related Documentation

- [File System](03-filesystem.md) - equivalent HTTP, SDK, and CLI file operations
