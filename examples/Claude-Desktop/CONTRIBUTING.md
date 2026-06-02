# Contributing to openviking-claude-desktop

Thank you for your interest in contributing.

## Author

**Sameh Khalifa**
Dubai, UAE

For questions, issues, or collaboration:
- Email: chinasameh@gmail.com
- GitHub Issues: preferred for bug reports and feature requests

---

## How to Contribute

### Report a Bug
Open an Issue with:
- Your OS version (Windows 10/11)
- Python version (`python --version`)
- Node.js version (`node --version`)
- OpenViking version (`pip show openviking`)
- Output of `verify-ov-hooks.ps1`
- Relevant log lines from `%USERPROFILE%\.claude-memory\logs\`

### Suggest a Feature
Open an Issue with the label `enhancement`.
Describe the use case, not just the feature.

### Submit a Pull Request
1. Fork the repo
2. Create a branch: `git checkout -b feature/your-feature-name`
3. Make changes — keep to the existing style (PS 5.1 compatible, ES5 JS)
4. Test with `verify-ov-hooks.ps1` — all checks must pass
5. Open a PR with a clear description of what changed and why

---

## Development Guidelines

### PowerShell (PS 5.1 only)
- Never use `?.` or `??` operators
- Never use `&` inside double-quoted `Write-Host` strings
- Never use `-RunOnlyIfNetworkAvailable $false`
- Always use `-UseBasicParsing` with `Invoke-WebRequest`
- Always start scripts with `Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force`
- Use `SafeGet` helper or explicit `.PSObject.Properties.Name -contains` checks

### JavaScript (ES5 only)
- No `async/await`, no arrow functions, no template literals
- No optional chaining `?.`
- Use `Promise` chains and `.then()/.catch()`
- Use `var` not `let/const` (for maximum compatibility)

### Python
- Python 3.10+ compatible
- No f-strings if targeting 3.5 compatibility — use `.format()`
- All API calls must include all 4 required OpenViking headers

### API Correctness
- Message format: `{role, content}` — never `{role, parts: [...]}`
- All 4 headers on every request: `Authorization`, `x-api-key`, `x-openviking-user`, `x-openviking-account`
- `ov.conf` valid fields only: `storage`, `log`, `embedding`, `vlm`, `server`
- Search results at `result.resources[]` and `result.memories[]`

---

## Areas Most Needed

| Area | What would help |
|------|----------------|
| Linux/macOS | Bash equivalents of all .ps1 scripts + systemd/launchd for tasks |
| Docker | Compose setup for the OpenViking server |
| Testing | Automated tests for the MCP bridge |
| OpenViking updates | Keep up with new API versions if response structure changes |
| Embedding providers | Support for additional embedding providers beyond Jina and Ollama |

---

## Questions

Email: chinasameh@gmail.com
GitHub Issues: preferred for bug reports and feature requests
Response time: best effort.
