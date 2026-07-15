# Codex Experience Memory Tools Design

## Goal

Extend the existing OpenViking Codex plugin so an installed and enabled plugin is sufficient for Codex to discover the `ov-experience-memory` skill and call `search_experience` and `read_experience`. Users must not configure another MCP server or duplicate OpenViking credentials.

## Scope

This change is limited to the Codex plugin integration:

- Bundle `ov-experience-memory` under the plugin's `skills/` directory.
- Add `search_experience` and `read_experience` to the tools exposed by the plugin's existing MCP proxy.
- Reuse the current `ovcli.conf` and environment-variable credential resolution.
- Preserve all existing OpenViking MCP tools and lifecycle hooks.

The change does not add tools to the OpenViking server MCP endpoint and does not broaden Usage Reporter recognition beyond the two official tool names.

## Architecture

The existing stdio MCP proxy remains the only MCP server started by the plugin. The shared proxy core gains an optional local-tool adapter interface. The Codex entrypoint supplies the Experience adapter; other plugin entrypoints omit it and retain their current behavior.

For `tools/list`, the proxy forwards the request to OpenViking and appends the two local tool definitions to the successful upstream result. For `tools/call`, the proxy handles the two Experience tools locally and forwards every other tool unchanged.

The adapter calls OpenViking REST endpoints with the same URL, API key, account, user, and peer headers already resolved for the MCP proxy. It derives the REST base URL from the configured MCP URL by removing the trailing `/mcp` path.

## Tool Contracts

### `search_experience`

Input:

```json
{
  "query": "没有订单号时如何处理换货",
  "limit": 5
}
```

Behavior:

- Calls `POST /api/v1/search/find`.
- Forces `target_uri` to `viking://user/memories/experiences/`, which the server resolves against the authenticated request user.
- Clamps `limit` to a positive bounded value.
- Returns only memory results whose canonical URI belongs to an Experience directory.

Tool result text is a JSON object:

```json
{
  "results": [
    {
      "uri": "viking://user/test/memories/experiences/example.md",
      "title": "example",
      "score": 0.82,
      "snippet": "Matched experience summary"
    }
  ]
}
```

This shape allows the committed ToolPart output to be parsed by `MemoryUsageExtractor` through `results[].uri`.

### `read_experience`

Input:

```json
{
  "uri": "viking://user/test/memories/experiences/example.md"
}
```

Behavior:

- Rejects blank URIs and URIs outside `viking://user/<owner>/memories/experiences/`.
- Calls `GET /api/v1/content/read?uri=<encoded_uri>`.
- Returns `{ "uri": "...", "content": "..." }` as JSON text.

The committed ToolPart input contains the Experience URI, allowing `MemoryUsageExtractor` to emit `memory.injected`.

## MCP Behavior

Tool results use the standard MCP result envelope:

```json
{
  "content": [
    {"type": "text", "text": "{...}"}
  ]
}
```

Input validation and OpenViking API failures return an MCP tool result with `isError: true`. Transport, authentication, session retry, notification, and shutdown behavior remain owned by the existing proxy core.

## Skill Packaging

The plugin bundles:

```text
examples/codex-memory-plugin/skills/ov-experience-memory/SKILL.md
```

The bundled file uses the existing Experience Skill contract and exact tool names. The source under `examples/skills/ov-experience-memory/SKILL.md` remains the canonical standalone example; packaging tests ensure the plugin copy does not drift in the tool contract.

## Compatibility

- Existing MCP tools are forwarded without request or response changes.
- Existing hooks and credential resolution are unchanged.
- The local-tool adapter is optional, so Claude Code and other consumers of the shared proxy core retain current behavior unless they explicitly provide an adapter.
- No OpenViking server API or Usage Reporter schema changes are required.

## Tests

Automated tests cover:

- Plugin package contains the Experience Skill.
- `tools/list` preserves upstream tools and appends both Experience tools.
- `search_experience` sends the fixed Experience target URI and returns structured results.
- Search output excludes non-Experience URIs.
- `read_experience` reads a valid Experience URI and returns URI plus content.
- `read_experience` rejects non-Experience URIs without calling OpenViking.
- Unknown tools continue to pass through to the upstream MCP endpoint.
- Authentication and account/user headers are present on Experience REST calls.

End-to-end acceptance uses the installed Codex plugin against the Docker OpenViking instance on port 1933. A Codex task must call both tools, plugin hooks must commit the session, and Kafka must receive one `memory.recalled` and one `memory.injected` event for the selected Experience URI.
