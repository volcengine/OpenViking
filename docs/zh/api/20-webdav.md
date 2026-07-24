# WebDAV

WebDAV 为 `resources` 命名空间提供文件协议访问。

**代码入口**：`openviking/server/routers/webdav.py`

## WebDAV（Phase 1）

OpenViking Server 也提供了一个面向资源文件的精简 WebDAV 适配层：

```text
/webdav/resources
```

Phase 1 有意把范围控制得比较小：

- 仅开放 `resources` 命名空间，不暴露 memories、skills、sessions 等其他空间。
- 以文本写入为主，当前 `PUT` 只接受 UTF-8 文本内容。
- 只实现一小部分 WebDAV 方法：`OPTIONS`、`PROPFIND`、`GET`、`HEAD`、`PUT`、`DELETE`、`MKCOL`、`MOVE`。
- 语义侧边文件和系统内部文件保持内部可见。`.abstract.md`、`.overview.md`、`.relations.json`、`.path.ovlock`、`.redirect.json`、`.sync_log.json` 这些派生或内部文件不会出现在 WebDAV 列表中，也不能被直接访问。

行为说明：

- 通过 WebDAV 新建文件时，会对该文件路径触发 OpenViking 的语义生成。
- 通过 WebDAV 覆盖已有文件时，会像 `write()` 一样刷新相关语义和向量。
- `PUT` 不会自动创建父目录。缺失的目录需要先用 `MKCOL` 创建。
- 用户自己创建的点目录或点文件仍然可见，只有上面列出的保留内部文件名会被隐藏。
- 启用多写存储时，被 redirect 到 backup 的文件仍会通过文件系统 API 呈现为普通文件；内部 redirect 和同步元数据不会暴露给调用方。

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| `OPTIONS` | `/webdav/resources`、`/webdav/resources/{resource_path}` | 返回支持的方法和 DAV 版本 |
| `PROPFIND` | `/webdav/resources`、`/webdav/resources/{resource_path}` | 返回目标及一级子项属性 |
| `GET` / `HEAD` | `/webdav/resources`、`/webdav/resources/{resource_path}` | 读取文件内容或响应头 |
| `PUT` | `/webdav/resources`、`/webdav/resources/{resource_path}` | 创建或覆盖 UTF-8 文本文件 |
| `DELETE` | `/webdav/resources`、`/webdav/resources/{resource_path}` | 删除文件或递归删除目录 |
| `MKCOL` | `/webdav/resources`、`/webdav/resources/{resource_path}` | 创建目录 |
| `MOVE` | `/webdav/resources`、`/webdav/resources/{resource_path}` | 移动或重命名文件/目录 |

除 `OPTIONS` 外，WebDAV 请求使用与其他 OpenViking API 相同的认证头。路径必须位于 `resources` 下，不能通过 `..`、反斜杠或其他形式逃逸命名空间。

| 请求头 | 使用方法 | 必填 | 说明 |
|--------|----------|------|------|
| `X-API-Key` | 除 `OPTIONS` 外 | 是 | OpenViking API Key |
| `Depth` | `PROPFIND` | 否 | `0` 仅返回目标；其他值按一级深度处理 |
| `Destination` | `MOVE` | 是 | `/webdav/resources` 下的目标路径 |
| `Overwrite` | `MOVE` | 否 | 默认 `T`；设为 `F` 时不覆盖已有目标 |

### 查询目录

`Depth` 仅支持 `0` 和一级深度；其他值按一级处理。成功时返回 `207 Multi-Status` 和 DAV XML。

**HTTP API**

```bash
curl -X PROPFIND http://localhost:1933/webdav/resources/docs \
  -H "X-API-Key: your-key" \
  -H "Depth: 1"
```

**响应示例**

```xml
<?xml version='1.0' encoding='utf-8'?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/webdav/resources/docs/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>docs</d:displayname>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontenttype>httpd/unix-directory</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
```

### 读取与写入文件

`PUT` 不会创建父目录。创建文件返回 `201`，覆盖文件返回 `204`；非 UTF-8 内容返回 `415`。

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

### 创建、移动和删除

`MOVE` 必须提供 `Destination` 头，且目标仍位于 `/webdav/resources` 下。目标父目录必须已经存在。

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

**状态码**

| 操作 | 成功状态 | 常见失败 |
|------|----------|----------|
| `GET` / `HEAD` | `200` | `404` 不存在；`405` 目标是目录 |
| `PUT` | `201` 新建；`204` 覆盖 | `409` 父目录不存在；`415` 不是 UTF-8 |
| `MKCOL` | `201` | `405` 已存在；`409` 父目录不存在 |
| `MOVE` | `201` 新目标；`204` 覆盖 | `400` 缺少目标；`409` 目标父目录不存在；`412` 禁止覆盖 |
| `DELETE` | `204` | `404` 不存在；`405` 尝试删除根目录 |

WebDAV 是协议入口，不对应 OpenViking SDK 或 `ov` CLI 方法，因此本页只展示 HTTP Tab。需要 SDK/CLI 文件操作时使用[文件系统](03-filesystem.md)。

## 相关文档

- [文件系统](03-filesystem.md) - 对应的 HTTP、SDK 和 CLI 文件操作
