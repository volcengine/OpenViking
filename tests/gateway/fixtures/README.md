# Real client request fixtures

Recorded on 2026-10-02 against a local OpenViking Gateway forwarding to the live
Ark `deepseek-v4-1-flash-260910` model. Each file contains the last two requests
from a successful synthetic deployment-cluster question and resumed follow-up.

| Fixture | Client version | Mode |
| --- | --- | --- |
| `claude-code.json` | Claude Code 2.1.286 | `-p`, then `--resume`, Messages |
| `codex-cli.json` | Codex CLI 0.146.0 | `exec`, then `exec resume`, Responses |
| `openai-python.json` | OpenAI Python SDK 2.29.0 | Chat Completions, full history |

Clients used an isolated synthetic workspace. Claude tools, hooks, user settings
and MCP integrations were disabled. Codex used read-only mode, ignored user
configuration/rules, disabled shell and received an explicit no-tools prompt.
No development task was delegated to either client.

Headers are allowlisted and contain no authentication. Model names, paths,
session identifiers and client metadata are sanitized. Client system instructions,
developer instructions, long text and tool descriptions are replaced with stable
placeholders. Tool schemas, content-block representations, cache controls,
reasoning items and protocol extension fields retain their recorded shapes.
These are sanitized serialization fixtures, not byte-identical wire recordings
or native Anthropic signed samples.

`test_recorded_client_prefixes` sends the recorded histories to the local mock
upstream, overriding only `stream` to use its JSON response. It checks normalized
prefix continuity with and without session headers, frozen injection reuse,
concurrent retries, capture deduplication with a session header, anonymous capture and prefix reuse, and client visibility. A separate check
verifies exact bytes of the sanitized requests when enhancement is disabled.
Protocol streaming is covered by the existing SSE tests and the live run.

When updating a client, repeat the synthetic two-turn conversation, capture only
request bodies and the allowlisted routing headers, sanitize as above, and rerun
the gateway tests. Preserve the relationship between shared text and identifiers
in successive requests. Do not commit raw headers, upload tokens, user workspace
contents or provider credentials.
