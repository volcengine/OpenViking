# Developing the Hermes memory provider

This directory imports the OpenViking provider from
[`NousResearch/hermes-plugin-openviking`](https://github.com/NousResearch/hermes-plugin-openviking/tree/5dca75f4d3dcef9467ce2ff32e170d84c679de5f),
commit `5dca75f4d3dcef9467ce2ff32e170d84c679de5f`.

At the initial import, `__init__.py`, `_setup.py`, and `plugin.yaml` matched that
handoff and `plugins/memory/openviking/` in Hermes Agent commit
`d177b119e9c56c9ddc0b7379ffce52341ec06584`. The original MIT license is retained
in this directory. Original contributor history is available in
[Hermes Agent](https://github.com/NousResearch/hermes-agent/commits/d177b119e9c56c9ddc0b7379ffce52341ec06584/plugins/memory/openviking).

Contributions to this directory are provided under its [MIT license](LICENSE).
Preserve the existing copyright and permission notice.

The distribution name is `hermes-plugin-openviking`. The provider, plugin, and
Hermes catalog key remain `openviking`. Existing `memory.openviking`
settings, environment variables, linked `ovcli.conf` files, data paths, and
`viking_*` tools keep their current behavior.

The active-session commit lifecycle was ported from
[KoNit-K's Hermes PR #112533](https://github.com/NousResearch/hermes-agent/pull/112533),
with the original author retained. The OpenViking adaptation uses a configurable
pending-token threshold instead of the original six-turn trigger.

Native memory mirroring is adapted from
[Hermes PR #100187](https://github.com/NousResearch/hermes-agent/pull/100187),
commit `32f75a9e6728a9a3d2f50a870dab3715a1f34fd7`, which continues
[austinlaw076's PR #85860](https://github.com/NousResearch/hermes-agent/pull/85860).
The external plugin uses relative imports and Hermes's context-preserving worker
helper. Its connection cache and session-commit lifecycle retain the later
OpenViking fixes.

Gateway sender attribution and recall scope adapt
[Hermes PR #105812](https://github.com/NousResearch/hermes-agent/pull/105812),
including liuhao1024's capture change from
[PR #98506](https://github.com/NousResearch/hermes-agent/pull/98506), with the
original author retained. The adaptation uses Hermes's existing per-turn author
hooks and preserves the original sender-ID encoding and Personal/Shared setup
presets. Shared Agent changes gateway session settings only after confirmation;
Personal Agent preserves them. An upgrade with no recall scope set retains the
previous recall requests. No Hermes core patch is required.

Profile-bound connection and recall settings adapt
[starship-s's Hermes PR #83647](https://github.com/NousResearch/hermes-agent/pull/83647),
with the original author retained. This port leaves memory URI handling as it
is: the original PR's UID-less `viking://user/memories/...` rewrite is not
accepted by current OpenViking servers.

Quick Local adapts [Hermes PR #94851](https://github.com/NousResearch/hermes-agent/pull/94851)
(head `d15e0e65d67a2dd6d3f3ab09a382c1f1684e2560`). Provisioning remains separate
from the setup UI. The port uses current Hermes PM, authenticated server reuse,
and paths derived from the provider's bound home. On Python 3.14, the private
server uses LiteLLM 1.83.7, within OpenViking's supported range; later LiteLLM
releases exclude that Python version. The profile server controller verifies
the PID, creation time, executable and config path before stopping a process.
It repairs permissions on plugin-owned private files after backup restoration;
no Hermes backup changes are required.

`local_packages.py` selects compatible OpenViking wheels from PyPI and reviewed
llama-cpp-python binaries, with SHA-256 verification before installation. Both PM
and pre-PM installers receive verified local wheel files. Setup records the applied
requirements so that a newer server release or native package pin updates an
existing private runtime. Change native package pins only after installation,
model and service checks pass on the listed platforms. Source builds require
explicit consent. Platform CI runs real installation, local embedding, capture,
restart and port-recovery checks. It uses a static test LLM configuration and
does not establish live memory extraction; that remains a release check.
The OpenViking hash comes from PyPI at setup time; llama-cpp-python hashes are
fixed in this plugin. PM/uv resolve the other dependencies; this is not a fully
locked server runtime.
Source builds use package version pins without reviewed binary hashes. A platform
needs a tested OpenViking/embedding-wheel pair for the prebuilt path.
Setup also checks LLM access through the installed OpenViking backend. The CI
fixture must serve an actual completion; an unreachable LLM must fail setup.

## Migration coordination

Hermes publishes this provider through `plugin-catalog/openviking.yaml`. The
entry must contain:

- `name: openviking`
- `repo: https://github.com/volcengine/OpenViking`
- `subdir: examples/hermes-plugin`
- `sha`: the full reviewed OpenViking commit SHA
- `category: memory`
- `tier: community`

Include the required `capabilities` block for tools, hooks, middleware, and
environment variables. Its declarations must match the plugin at the reviewed
SHA. Follow the [Hermes catalog entry schema](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md#entry-schema)
and validate the pinned installation directory:

```bash
hermes plugins validate /path/to/hermes-profile/plugins/openviking
```

Hermes PR [#131267](https://github.com/NousResearch/hermes-agent/pull/131267)
published the catalog entry. Hermes PR
[#114569](https://github.com/NousResearch/hermes-agent/pull/114569) added catalog
recovery for configured providers that no longer resolve. Keep the reviewed
catalog pin and migration path valid. Hermes PR
[#134350](https://github.com/NousResearch/hermes-agent/pull/134350) removed the
bundled OpenViking provider from main. Older releases still load their bundled
copy first.

This plugin does not add a Desktop `config_schema.py`.
The wizard uses private helpers from `hermes_cli.memory_setup`; changes to
those helpers require compatibility checks. The plugin uses HTTP. Quick Local installs the server in a separate
profile runtime through Hermes PM, or the earlier Hermes uv installer on the
tested release baseline. It does not add server dependencies to Hermes.
Explicit Quick Local setup resolves the latest stable, Python/platform-compatible
OpenViking 0.4 release from PyPI and verifies the wheel against PyPI's download
hash. The native embedding package remains pinned and hash-verified. The resolved
requirements are recorded per profile; unchanged requirements reuse the runtime,
while a new release follows the existing validation and restart path. Chat and
recovery do not resolve or upgrade packages.

## Validation

The `Hermes Plugin Tests` workflow runs this directory's complete external-provider
suite on plugin changes, pushes to `main`/`develop`, and manual dispatch.
It uses Python 3.14 and a reviewed Hermes commit, with test retries disabled.
The release-host job runs the complete provider suite on Hermes v2026.9.24
with Python 3.13. Legacy autostart uses Hermes's profile environment helper and
credential scrubber on both hosts. Unrelated process and profile secrets are
removed; missing helpers or secret-scope errors prevent spawning.
When updating the host SHA in `.github/workflows/hermes-plugin-tests.yml`, check
the host dependency pins and run the suite before submitting the change.
These regression tests use mock responses and local test servers; live-service
validation remains part of release testing.

CI also runs `scripts/check-hermes-plugin-install.py` through the real Hermes
CLI in an isolated profile. It installs this repository's plugin subdirectory,
enables it through Hermes's dependency manager, validates the installed directory,
and checks that the external provider loads. The bundled OpenViking copy is
temporarily removed from the test checkout. This catches dependency conflicts
across supported platforms that runtime tests alone do not exercise.

Use a Hermes checkout with its development dependencies installed.

The complete mirror tests require the committed-entry event contract introduced
in [Hermes PR #118903](https://github.com/NousResearch/hermes-agent/pull/118903)
and merged through [#120003](https://github.com/NousResearch/hermes-agent/pull/120003)
(commit `5908e1aaa83e82aaf12541d7a9d90762d0b46a64`):
`MemoryManager` forwards `previous_content` for each successful replace/remove.
The legacy compatibility tests verify that missing metadata skips these remote
mutations. Do not substitute a guessed match in tests or production.

From that checkout, run its canonical test runner against this directory:

```bash
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh /path/to/OpenViking/examples/hermes-plugin/tests \
  --confcutdir=/path/to/OpenViking/examples/hermes-plugin/tests -q
```

`--confcutdir` keeps pytest from importing the plugin as a test package before
Hermes loads it under its own namespace. The tests use temporary profile homes
and remove bundled-provider discovery.
They cover external loading, profile isolation, setup, tools, session commits,
and native memory mirroring. The mirror suite includes ordered writes, restart
continuity, registry failures, connection isolation, and concurrent workers.
Gateway tests use mock events through Hermes's turn hooks and memory manager.
They cover sender changes, capture retries, commits, recall scopes, compression
fallback, and missing sender metadata. Setup tests cover both presets,
confirmation, cancellation, profile-local persistence, connection routes, and
actual Hermes session keys.
Provider-specific regression tests belong here and must use the shared external
loader fixture. Generic Hermes framework tests remain in Hermes.

For compatibility checks against a Hermes release that bundles OpenViking,
also run its provider tests. These load the bundled copy unless explicitly
routed through the external loader; they do not replace this directory's tests:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/memory/test_openviking_provider.py \
  tests/plugins/memory/test_openviking_optional_peer.py \
  tests/plugins/memory/test_openviking_shutdown.py \
  tests/plugins/memory/test_openviking_endpoint_always_blocked.py \
  tests/openviking_plugin/test_openviking.py -q
```
