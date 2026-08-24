# Contributing to OpenViking

English / [中文](CONTRIBUTING_CN.md) / [日本語](CONTRIBUTING_JA.md)

Thank you for contributing to OpenViking. This guide exists to help contributors
submit changes that are clear, focused, and practical to review.

We welcome bug reports, feature requests, documentation improvements, and code
contributions.

## What We Value

OpenViking values focused, well-understood changes. Contributors are responsible
for understanding, explaining, and validating their changes, whether or not AI
tools were used.

Prefer the smallest complete change. Concise code means fewer concepts, branches,
duplicated rules, and speculative abstractions—not fewer necessary lines. A good
change is direct, readable, and easy to explain from its entrypoint to its
observable behavior.

In practice:

- Solve one coherent problem per PR. Do not mix unrelated cleanup or refactoring.
- Reuse the existing owner of a rule instead of introducing a parallel mechanism.
- Avoid speculative fallbacks, flags, state fields, and abstractions.
- Remove code, tests, and compatibility paths that the new implementation replaces.
- Keep necessary structure when it makes ownership, lifecycle, or failure handling
  clearer.

### Review Priority

Maintainer time is limited, so focused PRs are reviewed first:

- PRs with **100 or fewer changed lines** are usually reviewed more promptly.
- PRs with **200 or fewer changed lines** are prioritized over larger PRs.

These are review priorities, not hard limits or response-time guarantees. Changed
lines mean additions plus deletions in hand-written source, tests, and documentation;
generated files, vendored code, and lockfiles are excluded when assessing size.

Do not omit necessary tests or documentation to stay below a threshold. Split a
large change only where each PR remains independently understandable and correct.
Small size does not override correctness, design quality, or compatibility.

## Before You Start

1. Search existing issues, PRs, and code for the same behavior or domain rule.
2. For a bug, reproduce it through the real production entrypoint when possible.
3. Identify the owning module and trace where the value or state is created,
   normalized, stored, and consumed.
4. For a feature, describe the problem and expected behavior before designing the
   implementation.

Open an issue or start a discussion before implementing a change that affects:

- public REST, SDK, CLI, MCP, or configuration semantics;
- persisted data, storage schemas, VFS/AGFS paths, or encrypted file behavior;
- asynchronous task ownership, queues, cancellation, cleanup, or result state;
- resource import/watch behavior, session lifecycle, or memory extraction;
- retrieval levels, directory scope, or ranking semantics;
- tenant, account, user, or peer identity boundaries;
- multiple owner modules or a large architectural refactor.

Include the current behavior, proposed behavior, a concrete request or configuration
example, and any compatibility impact. This lets maintainers confirm the design
boundary before implementation work begins.

Use the repository's GitHub templates for [bug reports](https://github.com/volcengine/OpenViking/issues/new?template=bug_report.yml),
[feature requests](https://github.com/volcengine/OpenViking/issues/new?template=feature_request.yml),
and [questions](https://github.com/volcengine/OpenViking/issues/new?template=question.yml).

## Find the Right Area

If you know the affected area, mention it in the issue or PR. If you are unsure,
describe the observable behavior and use case first; a maintainer will help route it.

This map reflects sustained authorship and review activity in PRs merged from June 24
through August 24, 2026. It is routing guidance, not exclusive code ownership; mention
only the contacts relevant to the change.

| Domain | Area | Representative paths or topics | Active maintainers / reviewers |
|---|---|---|---|
| Platform | Server, API, auth, identity, admin, tasks | `openviking/server`, `openviking/service` | `@qin-ctx` |
| Resource | Ingestion, watch, and task pipeline | `openviking/resource` | `@qin-ctx`, `@KCHENPENGFEI` |
| Resource | Resource parsing | `openviking/parse` | `@zihengli-bytedance`, `@KCHENPENGFEI` |
| Memory | Session, memory extraction, and compilation | `openviking/session`, memory extraction, `ov compile` | `@chenjw`, `@heaoxiang-ai`, `@fujiajie666` |
| Retrieval | Search and vector databases | `openviking/retrieve`, `openviking/storage/vectordb` | `@zhoujh01`, `@t0saki` |
| Storage | RAGFS, PathLock, QueueFS, and encryption | `openviking/storage`, `openviking/pyagfs`, `openviking/crypto`, `crates/ragfs*` | `@baojun-zhang` |
| Integration | Agent plugins and MCP | `agent-plugins`, memory plugin examples, server MCP | `@t0saki`, `@ZaynJarvis` |
| Integration | VikingBot and agent compilation | `bot`, `ov compile` | `@yeshion23333`, `@fujiajie666` |
| Client | SDKs, CLI, and LangChain | `sdk`, `crates/ov_cli`, `integrations/langchain` | `@zhoujh01`, `@t0saki`, `@ehz0ah` |
| Product | Web Studio | `web-studio` | `@yufeng201`, `@ZaynJarvis` |
| Project | Documentation, CI, and plugin releases | `docs`, `.github/workflows` | `@yufeng201`, `@ZaynJarvis` |

For cross-module changes or areas without a clear owner, identify the primary affected
area first, then mention `@qin-ctx`, `@ZaynJarvis`, or `@zhoujh01`.

## Development Setup

### Prerequisites

- Python 3.10+
- Rust 1.91.1+ for source builds, Rust bindings, and the bundled `ov` CLI
- Go 1.22+ only for development under `sdk/go`
- A C++17 compiler: GCC 9+ or Clang 11+
- CMake 3.15+

On Linux, install `build-essential` and, where needed, `pkg-config`. On macOS,
install Xcode Command Line Tools. On Windows, install CMake and MinGW for local
native builds.

### Install

Fork the repository, then clone your fork:

```bash
git clone https://github.com/YOUR_USERNAME/OpenViking.git
cd OpenViking
```

We recommend using `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --all-extras
```

Verify the environment:

```bash
uv run python -c "import openviking; print(openviking.__version__)"
```

To configure a local server, run:

```bash
uv run openviking-server init
uv run openviking-server doctor
```

Configuration details and provider examples are in the
[configuration guide](https://docs.openviking.ai/en/guides/01-configuration).

If you modify the RAGFS Rust binding, bundled Rust CLI, or C++ extensions, rebuild
the native components:

```bash
uv pip install -e . --force-reinstall
```

Component-specific SDKs, integrations, plugins, and benchmarks may have additional
setup instructions in their local README or package manifest.

## Making a Change

### Ownership and Design

- Put behavior in its owning module. Higher layers should transport or consume the
  result, not reimplement the same rule.
- Convert external compatibility shapes into one canonical domain model at the
  boundary. Keep inner business logic free of input-shape guessing.
- Preserve meaningful server, network, timeout, authentication, and conflict errors
  at client-facing boundaries.
- Keep task state causally tied to the task that produced it. Do not infer completion
  from global queue state or an unrelated callback.
- Prefer one authoritative source for every value and rule.

If a local edge case starts changing task boundaries, public semantics, or the
overall architecture, stop and return to the design discussion instead of adding
special branches throughout the main path.

### Code Style

Python uses Ruff for formatting and linting, and mypy for type checking. The
configured line width is 100 characters.

Run checks on the paths you changed:

```bash
uv run ruff format <changed-paths>
uv run ruff check <changed-paths>
uv run mypy <changed-paths>
```

Public APIs should have short, useful docstrings. Prefer clear names and direct
control flow over comments that restate the code.

For Rust, Go, TypeScript, documentation, and plugin changes, use the formatter,
lint, type-check, and test commands defined by that component.

### Tests

Validate the smallest meaningful public contract and major failure boundary affected
by the change.

- Prefer updating an existing high-value contract test.
- Do not add a new unit test or test file by default.
- Do not test private helper existence, mock call order, simple field forwarding, or
  framework behavior unless it protects a lasting public contract.
- A small, clear fix does not automatically require a new test, but its validation
  must be explained.
- Put temporary reproduction, diagnostic, stress, and validation scripts in
  `test_scripts/`, not in source, benchmark, or maintenance script directories.

Run the relevant focused tests, for example:

```bash
uv run pytest tests/client/test_http_client_config.py
uv run pytest tests/server/ -k "search"
```

Run the full Python suite only when the scope and risk justify it:

```bash
uv run pytest
```

## Submitting a Pull Request

Create a branch from the latest `main`, make the focused change, and open a PR
against the appropriate repository branch.

Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages
and PR titles:

```text
feat(parser): support xlsx resources
fix(retrieval): preserve rerank score order
docs: clarify server configuration
refactor(storage): remove duplicate path normalization
```

Complete the repository's PR template. A useful description states:

- the observable behavior before and after the change;
- the root cause and real execution path for a bug fix;
- the affected entrypoint and owner module;
- compatibility or migration impact, if any;
- the exact validation commands that were run;
- whether the issue was reproduced or only inferred from the code.

Mark the **Human Involvement** field accurately. AI-assisted contributions are welcome,
but the author remains responsible for the change and must be able to explain how it
interacts with the rest of the system.

Before submitting:

- Review the complete diff and remove unrelated or generated changes.
- Confirm that replaced helpers, branches, mocks, and comments are gone.
- Update relevant documentation when public behavior changes.
- Report skipped checks and the concrete reason; do not claim tests that were not run.

CI runs checks based on the affected paths. A green CI result is required, but it
does not replace author validation or maintainer review.

## Documentation

Project documentation lives under `docs/en/` and `docs/zh/`. Keep examples runnable,
use concise language, and update both languages when the changed documentation has a
corresponding translation.

## Community

Be respectful, inclusive, constructive, and focused on the technical discussion.
Use [GitHub Discussions](https://github.com/volcengine/OpenViking/discussions) for
open-ended design or usage discussions and [GitHub Issues](https://github.com/volcengine/OpenViking/issues)
for actionable bugs and feature requests.
