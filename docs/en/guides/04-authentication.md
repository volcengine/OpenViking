# Authentication

OpenViking Server supports API key authentication to secure access.

## API Key Authentication

### Setting Up (Server Side)

Configure the API key in the `server` section of `ov.conf` (`~/.openviking/ov.conf`):

```json
{
  "server": {
    "api_key": "your-secret-key"
  }
}
```

Then start the server:

```bash
python -m openviking serve
```

### Using API Key (Client Side)

OpenViking accepts API keys via two headers:

**X-API-Key header**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-secret-key"
```

**Authorization: Bearer header**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "Authorization: Bearer your-secret-key"
```

**Python SDK (HTTP)**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-secret-key"
)
```

**CLI (via ovcli.conf)**

Configure the API key in `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-key"
}
```

## Development Mode

When no API key is configured in `ov.conf`, authentication is disabled. All requests are accepted without credentials.

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933
  }
}
```

```bash
# No api_key in ov.conf = auth disabled
python -m openviking serve
```

## Unauthenticated Endpoints

The `/health` endpoint never requires authentication, regardless of configuration. This allows load balancers and monitoring tools to check server health.

```bash
curl http://localhost:1933/health
# Always works, no API key needed
```

## Related Documentation

- [Deployment](03-deployment.md) - Server setup
- [API Overview](../api/01-overview.md) - API reference
