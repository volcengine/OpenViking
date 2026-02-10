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
- **Go**: 1.25.1+ (Required for building AGFS components)
- **C++ Compiler**: GCC 9+ or Clang 11+ (Required for building core extensions, must support C++17)
- **CMake**: 3.12+

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

### 3. Configure Environment

Create a configuration file `~/.openviking/ov.conf`:

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_key": "your-api-key",
    "model": "doubao-seed-1-8-251228",
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

---

## Project Structure

```
openviking/
├── pyproject.toml        # Project configuration
├── third_party/          # Third-party dependencies
│   └── agfs/             # AGFS filesystem
│
├── openviking/           # Python SDK
│   ├── async_client.py   # AsyncOpenViking client
│   ├── sync_client.py    # SyncOpenViking client
│   │
│   ├── core/             # Core data models
│   │   ├── context.py    # Context base class
│   │   └── directories.py # Directory definitions
│   │
│   ├── parse/            # Resource parsers
│   │   ├── parsers/      # Parser implementations
│   │   ├── tree_builder.py
│   │   └── registry.py
│   │
│   ├── retrieve/         # Retrieval system
│   │   ├── retriever.py  # Main retriever
│   │   ├── reranker.py   # Reranking
│   │   └── intent_analyzer.py
│   │
│   ├── session/          # Session management
│   │   ├── session.py    # Session core
│   │   └── compressor.py # Compression
│   │
│   ├── storage/          # Storage layer
│   │   ├── viking_fs.py  # VikingFS
│   │   └── vectordb/     # Vector database
│   │
│   ├── utils/            # Utilities
│   │   └── config/       # Configuration
│   │
│   └── prompts/          # Prompt templates
│
├── tests/                # Test suite
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

# Run specific test file
pytest tests/test_parser.py

# Run with coverage
pytest --cov=openviking --cov-report=term-missing

# Run with verbose output
pytest -v
```

### Writing Tests

Place test files in `tests/` directory with `test_*.py` naming:

```python
# tests/test_client.py
import pytest
from openviking import AsyncOpenViking

class TestAsyncOpenViking:
    @pytest.mark.asyncio
    async def test_initialize(self, tmp_path):
        client = AsyncOpenViking(path=str(tmp_path / "data"))
        await client.initialize()
        assert client._viking_fs is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_add_resource(self, tmp_path):
        client = AsyncOpenViking(path=str(tmp_path / "data"))
        await client.initialize()

        result = await client.add_resource(
            "./test.md",
            reason="test document"
        )
        assert result["status"] == "success"
        assert "root_uri" in result

        await client.close()
```

---

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
| **Push to Main** | `ci.yml` | Runs **Test Full** (All OS: Linux/Win/Mac, All Py versions: 3.10-3.13) and **CodeQL** (Security scan). Ensures main branch stability. (Displayed as **02. Main Branch Checks**) |
| **Release Published** | `release.yml` | Triggered when you create a Release on GitHub. Automatically builds source distribution and wheels, determines version from Git Tag, and publishes to **PyPI**. (Displayed as **03. Release**) |
| **Weekly Cron** | `schedule.yml` | Runs **CodeQL** security scan every Sunday. (Displayed as **04. Weekly Security Scan**) |

### 2. Manual Trigger Workflows

Maintainers can manually trigger the following workflows from the "Actions" tab to perform specific tasks or debug issues.

#### A. Lint Checks (`11. _Lint Checks`)
Runs code style checks (Ruff) and type checks (Mypy). No arguments required.

> **Tip**: It is recommended to install [pre-commit](https://pre-commit.com/) locally to run these checks automatically before committing (see [Automated Checks](#automated-checks-recommended) section above).

#### B. Test Suite (Lite) (`12. _Test Suite (Lite)`)
Runs fast integration tests, supports custom matrix configuration.

*   **Inputs**:
    *   `os_json`: JSON string array of OS to run on (e.g., `["ubuntu-latest"]`).
    *   `python_json`: JSON string array of Python versions (e.g., `["3.10"]`).

#### C. Test Suite (Full) (`13. _Test Suite (Full)`)
Runs the full test suite on all supported platforms (Linux/Mac/Win) and Python versions (3.10-3.13). Supports custom matrix configuration when triggered manually.

*   **Inputs**:
    *   `os_json`: List of OS to run on (Default: `["ubuntu-latest", "macos-latest", "windows-latest"]`).
    *   `python_json`: List of Python versions (Default: `["3.10", "3.11", "3.12", "3.13"]`).

#### D. Security Scan (`14. _CodeQL Scan`)
Runs CodeQL security analysis. No arguments required.

#### E. Build Distribution (`15. _Build Distribution`)
Builds Python wheel packages only, does not publish.

*   **Inputs**:
    *   `os_json`: List of OS to build on (Default: `["ubuntu-latest", "macos-latest", "macos-15-intel", "windows-latest"]`).
    *   `python_json`: List of Python versions (Default: `["3.10", "3.11", "3.12", "3.13"]`).
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
