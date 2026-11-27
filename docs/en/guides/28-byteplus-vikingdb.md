# Use BytePlus VikingDB with a self-hosted OpenViking server

This setup runs the open-source OpenViking server and uses BytePlus VikingDB for
vector storage and embeddings. For a managed OpenViking instance, use
[BytePlus managed OpenViking setup](27-byteplus-setup.md).

## Connection configuration

BytePlus Johor uses region `ap-southeast-1` and two separate API hosts:

| Purpose | Host |
| --- | --- |
| Collection and index management | `vikingdb.ap-southeast-1.byteplusapi.com` |
| Data, search and embeddings | `api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com` |

Use the `volcengine` vector backend with `console_host` and `data_host`. These
fields override the corresponding AK/SK endpoints independently. Omit them to
retain the existing region-based Volcengine hosts. Supply bare hostnames, without
`https://` or a path. The existing `host` field remains the API-key data endpoint;
it does not select the two AK/SK endpoints.

Add these sections to the server's `ov.conf`:

```json
{
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "backend": "volcengine",
      "name": "openviking_context",
      "index_name": "context_index",
      "project": "default",
      "volcengine": {
        "region": "ap-southeast-1",
        "console_host": "vikingdb.ap-southeast-1.byteplusapi.com",
        "data_host": "api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com",
        "ak": "<access-key>",
        "sk": "<secret-key>",
        "session_token": "<sts-token>"
      }
    }
  },
  "embedding": {
    "dense": {
      "provider": "vikingdb",
      "model": "bge-m3",
      "input": "text",
      "dimension": 1024,
      "region": "ap-southeast-1",
      "host": "api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com",
      "ak": "<access-key>",
      "sk": "<secret-key>",
      "session_token": "<sts-token>"
    }
  }
}
```

Set `session_token` in both sections when AK/SK are temporary STS credentials.
Omit it for permanent credentials. Replace all three values together when the
temporary credentials expire; OpenViking does not obtain replacement STS
credentials. An embedding credential entry can supply its own `session_token`.
When it supplies different AK/SK, it must also supply their matching token.

The embedding model must be enabled in the account and its vector dimension
must match the collection. Verify the current model list and API hosts in the
[BytePlus SDK documentation](https://docs.byteplus.com/en/docs/VikingDB/sdk_installation_and_initializationv2).

## Verify the connection

Test the management API, embedding generation, and data APIs separately. A
successful collection-list request proves management access; it does not prove
that data or model permissions are available. In a dedicated test collection,
write one vector, fetch it by ID, search it through the test index, and remove
the test collection after verification. Index creation and updates can take
time to become searchable.

These checks use the VikingDB credentials. Document summaries and memory
extraction also require a separately configured `vlm` provider. Keep connection
checks separate from that full ingestion test when diagnosing an endpoint or
authentication error.
