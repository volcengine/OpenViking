# Relations

The Relations API creates, reads, and removes explicit links between Viking URIs.

## API Reference

### link()

Create relations between resources.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| from_uri | str | Yes | - | Source URI |
| to_uris | str or List[str] | Yes | - | Target URI(s) |
| reason | str | No | "" | Reason for the link |


**Python SDK (Embedded / HTTP)**

```python
# Single link
client.link(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/",
    reason="Security best practices for authentication"
)

# Multiple links
client.link(
    "viking://resources/docs/api/",
    [
        "viking://resources/docs/auth/",
        "viking://resources/docs/errors/"
    ],
    reason="Related documentation"
)
```

**TypeScript SDK**

```typescript
await client.link(
  "viking://resources/docs/api/",
  [
    "viking://resources/docs/auth/",
    "viking://resources/docs/errors/",
  ],
  "Related documentation",
);
```

**HTTP API**

```
POST /api/v1/relations/link
```

```bash
# Single link
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uris": "viking://resources/docs/security/",
    "reason": "Security best practices for authentication"
  }'

# Multiple links
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/api/",
    "to_uris": ["viking://resources/docs/auth/", "viking://resources/docs/errors/"],
    "reason": "Related documentation"
  }'
```

**CLI**

```bash
openviking link viking://resources/docs/auth/ viking://resources/docs/security/ --reason "Security best practices"
```


**Response**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### relations()

Get relations for a resource.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |


**Python SDK (Embedded / HTTP)**

```python
relations = client.relations("viking://resources/docs/auth/")
for rel in relations:
    print(f"Related: {rel['uri']}")
    print(f"  Reason: {rel['reason']}")
```

**TypeScript SDK**

```typescript
const relations = await client.relations("viking://resources/docs/auth/");
console.log(relations);
```

**HTTP API**

```
GET /api/v1/relations?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking relations viking://resources/docs/auth/
```


**Response**

```json
{
  "status": "ok",
  "result": [
    {"uri": "viking://resources/docs/security/", "reason": "Security best practices"},
    {"uri": "viking://resources/docs/errors/", "reason": "Error handling"}
  ],
  "time": 0.1
}
```

---

### unlink()

Remove a relation.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| from_uri | str | Yes | - | Source URI |
| to_uri | str | Yes | - | Target URI to unlink |


**Python SDK (Embedded / HTTP)**

```python
client.unlink(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/"
)
```

**TypeScript SDK**

```typescript
await client.unlink(
  "viking://resources/docs/auth/",
  "viking://resources/docs/security/",
);
```

**HTTP API**

```
DELETE /api/v1/relations/link
```

```bash
curl -X DELETE http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uri": "viking://resources/docs/security/"
  }'
```

**CLI**

```bash
openviking unlink viking://resources/docs/auth/ viking://resources/docs/security/
```


**Response**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### build_graph()

Generate a self-contained HTML relation graph from multiple memory roots and write it to a Viking URI.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `space_uris` | string[] | Yes | Memory roots to merge |
| `output_uri` | string | Yes | Viking URI of the output HTML file |

**HTTP API**

```http
POST /api/v1/relations/build_graph
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/relations/build_graph \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "space_uris":[
      "viking://user/default/memories",
      "viking://user/default/peers/project-a/memories"
    ],
    "output_uri":"viking://user/default/memories/.graph.html"
  }'
```

This endpoint does not currently have a public SDK or CLI wrapper, so this section shows only the HTTP tab.

---

## Related Documentation

- [Retrieval](06-retrieval.md) - retrieve and use related content
- [File System](03-filesystem.md) - manage relation targets
- [Memory](16-memory.md) - memory namespaces and types
