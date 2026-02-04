# Contributing Guide

Thank you for your interest in OpenViking! We welcome contributions of all kinds:

- Bug reports
- Feature requests
- Documentation improvements
- Code contributions

---

## Development Setup

### Prerequisites

- **Python**: 3.9+
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

Create a configuration file `ov.conf`:

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
export OPENVIKING_CONFIG_FILE=ov.conf
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
| **Pull Request** | `pr.yml` | Runs **Lint** (Ruff, Mypy) and **Test Lite** (Integration tests on Linux + Python 3.10). Fast feedback for contributors. |
| **Push to Main** | `ci.yml` | Runs **Test Full** (All OS: Linux/Win/Mac, All Py: 3.9-3.12) and **CodeQL** (Security scan). Ensures main branch stability. |
| **Release Published** | `release.yml` | Triggered when you create a Release on GitHub. Automatically builds wheels, verifies the Git Tag matches `pyproject.toml`, and publishes to **PyPI**. |
| **Weekly Cron** | `schedule.yml` | Runs **CodeQL** security scan every Sunday. |

### 2. Manual Trigger Workflows

Maintainers can manually trigger workflows from the "Actions" tab to perform specific tasks or debug issues.

#### A. Run Tests Manually (`_Test Lite` / `_Test Full`)

You can run tests with custom matrix configurations using `_Test Lite`.

*   **Inputs**:
    *   `os_json`: JSON string array of OS to run on (e.g., `["ubuntu-latest", "windows-latest"]`).
    *   `python_json`: JSON string array of Python versions (e.g., `["3.10", "3.12"]`).

#### B. Manual Release / Publish (`Publish to PyPI`)

You can manually trigger the `release.yml` workflow (listed as "Publish to PyPI") to build and publish without creating a GitHub Release.

*   **Inputs**:
    *   `target`: Select where to publish.
        *   `none`: Build artifacts only (no publish). Good for verifying build capability.
        *   `testpypi`: Publish to TestPyPI. Good for beta testing.
        *   `pypi`: Publish to official PyPI.
        *   `both`: Publish to both.

> **Note**: For manual triggers, the strict "Git Tag matches pyproject.toml" check is relaxed to a warning, allowing you to test builds on non-tagged commits.

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
