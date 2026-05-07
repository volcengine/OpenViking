# Contributing Guide

Thank you for your interest in OpenViking! We welcome contributions of all kinds:

- Bug reports
- Feature requests
- Documentation improvements
- Code contributions

---

## Development Setup

### Prerequisites

- **Python**: 3.10+
- **Go**: 1.22+ (Required for building AGFS components from source)
- **Rust**: 1.91.1+ (Required for source builds because the bundled `ov` CLI is built during packaging)
- **C++ Compiler**: GCC 9+ or Clang 11+ (Required for building core extensions, must support C++17)
- **CMake**: 3.12+

#### Platform-Specific Native Build Tools

- **Linux**: Install `build-essential`; some environments may also require `pkg-config`
- **macOS**: Install Xcode Command Line Tools (`xcode-select --install`)
- **Windows**: Install CMake and MinGW for local native builds

#### Supported Platforms (Pre-compiled Wheels)

OpenViking provides pre-compiled **Wheel** packages for the following environments:

- **Windows**: x86_64
- **macOS**: x86_64, arm64 (Apple Silicon)
- **Linux**: x86_64, arm64 (manylinux)

For other platforms (e.g., FreeBSD), the package will be automatically compiled from source during installation via `pip`. Ensure you have the [Prerequisites](#prerequisites) installed.

### 1. Fork and Clone

```bash
git clone https://github.com/YOUR_USERNAME/openviking.git
cd openviking
```

### 2. Install Dependencies

We recommend using `uv` for Python environment management:

```bash
# Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies and create virtual environment
uv sync --all-extras
source .venv/bin/activate  # Linux/macOS
# or .venv\Scripts\activate  # Windows
```

#### Local Development & Native Rebuilds

OpenViking defaults to `binding-client` mode for AGFS, which requires pre-built native artifacts. If you modify the **AGFS (Go)** code, the bundled **Rust CLI**, or the **C++ extensions**, or if the pre-built artifacts are not found, you need to re-compile and re-install them. Run the following command in the project root:

```bash
uv pip install -e . --force-reinstall
```

This command ensures that `setup.py` is re-executed, triggering rebuilds for AGFS, the bundled `ov` CLI, and the C++ components.

### 3. Configure Environment

Create a configuration file `~/.openviking/ov.conf`:

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_key": "your-api-key",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

Set the environment variable:

```bash
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf
```

### 4. Verify Installation

```python
import asyncio
import openviking as ov

async def main():
    client = ov.AsyncOpenViking(path="./test_data")
    await client.initialize()
    print("OpenViking initialized successfully!")
    await client.close()

asyncio.run(main())
```

### 5. Build Rust CLI (Optional)

The Rust CLI (`ov`) provides a high-performance command-line client for interacting with OpenViking Server.

Even if you do not plan to use `ov` directly, the Rust toolchain is still required when building OpenViking from source because packaging also builds the bundled CLI binary.

```bash
# Build and install from source
cargo install --path crates/ov_cli

# Or use the quick install script (downloads pre-built binary)
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

After installation, run `ov --help` to see all available commands. CLI connection config goes in `~/.openviking/ovcli.conf`.

---

## Project Structure

```
openviking/
├── pyproject.toml        # Project configuration
├── Cargo.toml            # Rust workspace configuration
├── third_party/          # Third-party dependencies
│   └── agfs/             # AGFS filesystem
│
├── openviking/           # Python SDK
│   ├── async_client.py   # AsyncOpenViking client
│   ├── sync_client.py    # SyncOpenViking client
│   ├── client/           # Local and HTTP client implementations
│   ├── console/          # Standalone console UI and proxy service
│   ├── core/             # Core data models and directory abstractions
│   ├── message/          # Session message and part models
│   ├── models/           # Embedding and VLM backends
│   ├── parse/            # Resource parsers and detectors
│   ├── resource/         # Resource processing and watch management
│   ├── retrieve/         # Retrieval system
│   ├── server/           # HTTP server
│   ├── service/          # Shared service layer
│   ├── session/          # Session management and compression
│   ├── storage/          # Storage layer
│   ├── telemetry/        # Operation telemetry
│   ├── trace/            # Trace and runtime tracing helpers
│   ├── utils/            # Utilities and configuration helpers
│   └── prompts/          # Prompt templates
│
├── crates/               # Rust components
│   └── ov_cli/           # Rust CLI client
│       ├── src/          # CLI source code
│       └── install.sh    # Quick install script
│
├── src/                  # C++ extension sources (Python abi3)
│
├── tests/                # Test suite
│   ├── client/           # Client tests
│   ├── console/          # Console tests
│   ├── core/             # Core logic tests
│   ├── parse/            # Parser tests
│   ├── resource/         # Resource processing tests
│   ├── retrieve/         # Retrieval tests
│   ├── server/           # Server tests
│   ├── service/          # Service layer tests
│   ├── session/          # Session tests
│   ├── storage/          # Storage tests
│   ├── telemetry/        # Telemetry tests
│   ├── vectordb/         # Vector database tests
│   └── integration/      # End-to-end tests
│
└── docs/                 # Documentation
    ├── en/               # English docs
    └── zh/               # Chinese docs
```

---

## Code Style

We use the following tools to maintain code consistency:

| Tool | Purpose | Config |
|------|---------|--------|
| **Ruff** | Linting, Formatting, Import sorting | `pyproject.toml` |
| **mypy** | Type checking | `pyproject.toml` |

### Automated Checks (Recommended)

We use [pre-commit](https://pre-commit.com/) to automatically run these checks before every commit. This ensures your code always meets the standards without manual effort.

1. **Install pre-commit**:
   ```bash
   pip install pre-commit
   ```

2. **Install the git hooks**:
   ```bash
   pre-commit install
   ```

Now, `ruff` (check & format) will run automatically when you run `git commit`. If any check fails, it may automatically fix the file. You just need to add the changes and commit again.

### Running Checks

```bash
# Format code
ruff format openviking/

# Lint
ruff check openviking/

# Type check
mypy openviking/
```

### Style Guidelines

1. **Line width**: 100 characters
2. **Indentation**: 4 spaces
3. **Strings**: Prefer double quotes
4. **Type hints**: Encouraged but not required
5. **Docstrings**: Required for public APIs (1-2 lines max)

---

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run specific test module
pytest tests/client/ -v
pytest tests/server/ -v
pytest tests/parse/ -v

# Run specific test file
pytest tests/client/test_lifecycle.py

# Run specific test
pytest tests/client/test_lifecycle.py::TestClientInitialization::test_initialize_success

# Run by keyword
pytest -k "search" -v

# Run with coverage
pytest --cov=openviking --cov-report=term-missing
```

### Writing Tests

Tests are organized in subdirectories under `tests/`. The project uses `asyncio_mode = "auto"`, so async tests do **not** need the `@pytest.mark.asyncio` decorator:

```python
# tests/client/test_example.py
from openviking import AsyncOpenViking


class TestAsyncOpenViking:
    async def test_initialize(self, uninitialized_client: AsyncOpenViking):
        await uninitialized_client.initialize()
        assert uninitialized_client._service is not None
        await uninitialized_client.close()

    async def test_add_resource(self, client: AsyncOpenViking, sample_markdown_file):
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="test document"
        )
        assert "root_uri" in result
        assert result["root_uri"].startswith("viking://")
```

Common fixtures are defined in `tests/conftest.py`, including `client` (initialized `AsyncOpenViking`), `uninitialized_client`, `temp_dir`, `sample_markdown_file`, and more.

---

## Maintainer Routing and Contribution Entry

### Contributor-Facing Module Map

If you are not sure where your question, issue, or PR belongs, start with this table:

| Domain | Area | Primary Contact |
|--------|------|-----------------|
| Integration | Bot | `@yeshion23333` |
| Integration | OpenClaw Plugin | `@Mijamind719`, `@wlff123` |
| Platform | Framework / Multi-tenant / Resources / Session | `@qin-ctx` |
| Platform | Incremental / Scheduled Update | `@myysy` |
| Knowledge | Memory | `@chenjw` |
| Knowledge | Retrieval / Directory Semantics | `@zhoujh01` |
| Storage & Security | Virtual FS / File Encryption | `@chuanbao666`, `@baojun-zhang` |

If the area is still unclear, mention one of the cross-module maintainers listed below.

### Maintainer Routing Map

Use this table when routing issues, PRs, or design questions to a more specific owner:

| Domain | Subarea | Representative Paths or Topics | Primary Contact | Backup / Cross-Module |
|--------|---------|--------------------------------|-----------------|-----------------------|
| Integration | Bot Runtime | `bot/vikingbot`, `bot/bridge`, deployment scripts, bot docs | `@yeshion23333` | `@chenjw` |
| Integration | OpenClaw Plugin | `examples/openclaw-plugin`, installation, remote mode, compatibility | `@Mijamind719`, `@wlff123` | `@LinQiang391` |
| Platform | Server & Multi-tenant | `openviking/server`, `openviking/service`, auth, identity, admin, tenant boundary | `@qin-ctx` | `@MaojiaSheng` |
| Platform | Resource & Session Lifecycle | `openviking/resource`, `openviking/session`, resource ingestion, session lifecycle | `@qin-ctx` | `@MaojiaSheng` |
| Platform | Incremental & Scheduled Update | `openviking/resource/watch_manager.py`, `openviking/resource/watch_scheduler.py` | `@myysy` | `@qin-ctx` |
| Knowledge | Memory Engine | `openviking/session/memory`, `memory_extractor.py`, `memory_deduplicator.py` | `@chenjw` | `@qin-ctx` |
| Knowledge | Retrieval & Directory Semantics | `openviking/retrieve`, intent analysis, hierarchical retrieval, directory semantics | `@zhoujh01` | `@qin-ctx` |
| Storage & Security | VFS / AGFS Path Semantics | `openviking/storage`, `openviking/pyagfs`, filesystem behavior, path semantics | `@chuanbao666`, `@baojun-zhang` | `@zhoujh01` |
| Storage & Security | Encryption & Data Safety | `openviking/crypto`, file encryption, storage safety | `@chuanbao666`, `@baojun-zhang` | `@zhoujh01` |

For areas without a stable owner yet, cross-module maintainers will help route the request first.

### Cross-Module Maintainers

- `@MaojiaSheng`
- `@qin-ctx`
- `@zhoujh01`

Cross-module maintainers help with issue routing, cross-cutting design questions, and fallback review support.

### How to Ask for Help

- If you already know the affected module, mention it in the issue or PR description.
- If you are unsure about the module, describe the use case and affected behavior first.
- If you want to work on an issue, leave a comment before starting, especially for cross-module changes.
- If your PR spans multiple areas, call out the primary affected domain in the description.

### Contribution Entry Labels

Issue templates already classify reports such as `bug`, `enhancement`, and `question`. Maintainers may also use the following labels to make contribution entry clearer:

| Label | Meaning |
|-------|---------|
| `good first issue` | Newcomer-friendly work with clear scope and acceptance criteria |
| `help wanted` | Tasks that benefit from contributors who already know the codebase or review style |
| `needs-design` | Work that needs maintainer clarification before implementation |
| `needs-review` | Pull requests waiting for the first review round |

### Contributor Growth Path

The project uses a practical contribution path so contributors can see what “next step” looks like:

| Stage | Typical Signals | Common Next Step |
|-------|------------------|------------------|
| New Contributor | First issue or first PR, often docs, tests, or scoped fixes | Start with `good first issue` items and get familiar with local workflow |
| Active Contributor | One or more merged contributions | Pick up `help wanted` work in an area you already touched |
| Module Contributor | Repeated contributions in the same subarea | Help with triage, reproduction, docs, or review comments in that area |
| Backup Reviewer Candidate | Stable contribution record in one subarea | Help with first-pass review, routing, and contributor support |

## Contribution Workflow

### 1. Create a Branch

```bash
git checkout main
git pull origin main
git checkout -b feature/your-feature-name
```

Branch naming conventions:
- `feature/xxx` - New features
- `fix/xxx` - Bug fixes
- `docs/xxx` - Documentation updates
- `refactor/xxx` - Code refactoring

### 2. Make Changes

- Follow code style guidelines
- Add tests for new functionality
- Update documentation as needed

### 3. Commit Changes

```bash
git add .
git commit -m "feat: add new parser for xlsx files"
```

### 4. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub.

---

## Commit Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `style` | Code style (no logic change) |
| `refactor` | Code refactoring |
| `perf` | Performance improvement |
| `test` | Tests |
| `chore` | Build/tooling |

### Examples

```bash
# New feature
git commit -m "feat(parser): add support for xlsx files"

# Bug fix
git commit -m "fix(retrieval): fix score calculation in rerank"

# Documentation
git commit -m "docs: update quick start guide"

# Refactoring
git commit -m "refactor(storage): simplify interface methods"
```

---

## Pull Request Guidelines

### PR Title

Use the same format as commit messages.

### PR Description Template

```markdown
## Summary

Brief description of the changes and their purpose.

## Type of Change

- [ ] New feature (feat)
- [ ] Bug fix (fix)
- [ ] Documentation (docs)
- [ ] Refactoring (refactor)
- [ ] Other

## Testing

Describe how to test these changes:
- [ ] Unit tests pass
- [ ] Manual testing completed

## Related Issues

- Fixes #123
- Related to #456

## Checklist

- [ ] Code follows project style guidelines
- [ ] Tests added for new functionality
- [ ] Documentation updated (if needed)
- [ ] All tests pass
```

---

## CI/CD Workflows

We use **GitHub Actions** for Continuous Integration and Continuous Deployment. Our workflows are designed to be modular and tiered.

### 1. Automatic Workflows

| Event | Workflow | Description |
|-------|----------|-------------|
| **Pull Request** | `pr.yml` | Runs **Lint** (Ruff, Mypy) and **Test Lite** (Integration tests on Linux + Python 3.10). Provides fast feedback for contributors. (Displayed as **01. Pull Request Checks**) |
| **Push to Main** | `ci.yml` | Runs **Test Full** (All OS: Linux/Win/Mac, All Py versions: 3.10-3.14) and **CodeQL** (Security scan). Ensures main branch stability. (Displayed as **02. Main Branch Checks**) |
| **Release Published** | `release.yml` | Triggered when you create a Release on GitHub. Automatically builds source distribution and wheels, determines version from Git Tag, and publishes to **PyPI**. (Displayed as **03. Release**) |
| **Weekly Cron** | `schedule.yml` | Runs **CodeQL** security scan every Sunday. (Displayed as **04. Weekly Security Scan**) |

Other repository workflows also exist for PR review automation, Docker image builds, and Rust CLI packaging.

### 2. Manual Trigger Workflows

Maintainers can manually trigger the following workflows from the "Actions" tab to perform specific tasks or debug issues.

#### A. Lint Checks (`11. _Lint Checks`)
Runs code style checks (Ruff) and type checks (Mypy). No arguments required.

> **Tip**: It is recommended to install [pre-commit](https://pre-commit.com/) locally to run these checks automatically before committing (see [Automated Checks](#automated-checks-recommended) section above).

#### B. Test Suite (Lite) (`12. _Test Suite (Lite)`)
Runs fast integration tests, supports custom matrix configuration.

*   **Inputs**:
    *   `os_json`: JSON string array of OS to run on (e.g., `["ubuntu-24.04"]`).
    *   `python_json`: JSON string array of Python versions (e.g., `["3.10"]`).

#### C. Test Suite (Full) (`13. _Test Suite (Full)`)
Runs the full test suite on all supported platforms (Linux/Mac/Win) and Python versions (3.10-3.14). Supports custom matrix configuration when triggered manually.

*   **Inputs**:
    *   `os_json`: List of OS to run on (Default: `["ubuntu-24.04", "macos-14", "windows-latest"]`).
    *   `python_json`: List of Python versions (Default: `["3.10", "3.11", "3.12", "3.13", "3.14"]`).

#### D. Security Scan (`14. _CodeQL Scan`)
Runs CodeQL security analysis. No arguments required.

#### E. Build Distribution (`15. _Build Distribution`)
Builds Python wheel packages only, does not publish.

*   **Inputs**:
    *   `os_json`: List of OS to build on (Default: `["ubuntu-24.04", "ubuntu-24.04-arm", "macos-14", "macos-15-intel", "windows-latest"]`).
    *   `python_json`: List of Python versions (Default: `["3.10", "3.11", "3.12", "3.13", "3.14"]`).
    *   `build_sdist`: Whether to build source distribution (Default: `true`).
    *   `build_wheels`: Whether to build wheel distribution (Default: `true`).

#### F. Publish Distribution (`16. _Publish Distribution`)
Publishes built packages (requires build Run ID) to PyPI.

*   **Inputs**:
    *   `target`: Select publish target (`testpypi`, `pypi`, `both`).
    *   `build_run_id`: Build Workflow Run ID (Required, get it from the Build run URL).

#### G. Manual Release (`03. Release`)
One-stop build and publish (includes build and publish steps).

> **Version Numbering & Tag Convention**:
> This project uses `setuptools_scm` to automatically extract version numbers from Git Tags.
> *   **Tag Naming Convention**: Must follow the `vX.Y.Z` format (e.g., `v0.1.0`, `v1.2.3`). Tags must be compliant with Semantic Versioning.
> *   **Release Build**: When a Release event is triggered, the version number directly corresponds to the Git Tag (e.g., `v0.1.0` -> `0.1.0`).
> *   **Manual/Non-Tag Build**: The version number will include the commit count since the last Tag (e.g., `0.1.1.dev3`).
> *   **Confirm Version**: After the publish job completes, you can see the published version directly in the **Notifications** area at the top of the Workflow **Summary** page (e.g., `Successfully published to PyPI with version: 0.1.8`). You can also verify it in the logs or the **Artifacts** filenames.

*   **Inputs**:
    *   `target`: Select publish target.
        *   `none`: Build artifacts only (no publish). Used for verifying build capability.
        *   `testpypi`: Publish to TestPyPI. Used for Beta testing.
        *   `pypi`: Publish to official PyPI.
        *   `both`: Publish to both.
    *   `os_json`: Build platforms (Default includes all).
    *   `python_json`: Python versions (Default includes all).
    *   `build_sdist`: Whether to build source distribution (Default: `true`).
    *   `build_wheels`: Whether to build wheel distribution (Default: `true`).

> **Publishing Notes**:
> *   **Test First**: It is strongly recommended to publish to **TestPyPI** for verification before publishing to official PyPI. Note that PyPI and TestPyPI are completely independent environments, and accounts and package data are not shared.
> *   **No Overwrites**: Neither PyPI nor TestPyPI allow overwriting existing packages with the same name and version. If you need to republish, you must upgrade the version number (e.g., tag a new version or generate a new dev version). If you try to publish an existing version, the workflow will fail.

---

## Issue Guidelines

### Bug Reports

Please provide:

1. **Environment**
   - Python version
   - OpenViking version
   - Operating system

2. **Steps to Reproduce**
   - Detailed steps
   - Code snippets

3. **Expected vs Actual Behavior**

4. **Error Logs** (if any)

### Feature Requests

Please describe:

1. **Problem**: What problem are you trying to solve?
2. **Solution**: What solution do you propose?
3. **Alternatives**: Have you considered other approaches?

---

## Documentation

Documentation is in Markdown format under `docs/`:

- `docs/en/` - English documentation
- `docs/zh/` - Chinese documentation

### Documentation Guidelines

1. Code examples must be runnable
2. Keep documentation in sync with code
3. Use clear, concise language

---

## Code of Conduct

By participating in this project, you agree to:

1. **Be respectful**: Maintain a friendly and professional attitude
2. **Be inclusive**: Welcome contributors from all backgrounds
3. **Be constructive**: Provide helpful feedback
4. **Stay focused**: Keep discussions technical

---

## Getting Help

If you have questions:

- [GitHub Issues](https://github.com/volcengine/openviking/issues)
- [Discussions](https://github.com/volcengine/openviking/discussions)

---

Thank you for contributing!
