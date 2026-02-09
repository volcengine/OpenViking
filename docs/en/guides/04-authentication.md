# Authentication

OpenViking Server supports API key authentication to secure access.

## API Key Authentication

### Setting Up (Server Side)

**Option 1: Command line**

```bash
python -m openviking serve --path ./data --api-key "your-secret-key"
```

**Option 2: Environment variable**

```bash
export OPENVIKING_API_KEY="your-secret-key"
python -m openviking serve --path ./data
```

**Option 3: Config file** (via `OPENVIKING_CONFIG_FILE`)

```json
{
  "server": {
    "api_key": "your-secret-key"
  }
}
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

**Python SDK**

```python
import openviking as ov

client = ov.OpenViking(
    url="http://localhost:1933",
    api_key="your-secret-key"
)
```

Or use the `OPENVIKING_API_KEY` environment variable:

```bash
export OPENVIKING_URL="http://localhost:1933"
export OPENVIKING_API_KEY="your-secret-key"
```

```python
import openviking as ov

# api_key is read from OPENVIKING_API_KEY automatically
client = ov.OpenViking()
```

## Development Mode

When no API key is configured, authentication is disabled. All requests are accepted without credentials.

```bash
# No --api-key flag = auth disabled
python -m openviking serve --path ./data
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
