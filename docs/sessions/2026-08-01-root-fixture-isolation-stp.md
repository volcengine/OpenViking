# STP — Root-Fixture-Isolation

## Transfer snapshot

- **Fork:** `https://github.com/manni07/OpenViking.git`
- **Worktree:** `/Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-root-fixture-isolation`
- **Branch:** `agent-workflow/20260801-root-fixture-isolation`
- **Base:** `22919c337f2837ab65cbc4d778496090f9d77fad` (fork `main`)
- **Main checkout:** `/Volumes/ExtremePro/projects/OpenViking` untouched; its
  pre-existing untracked files were not staged or removed.
- **Current state:** implementation and docs uncommitted; no push/PR yet.
- **Historical-doc boundary:** `docs/manuals/2026-08-01-root-test-collection-manual.html`
  and its `root-collection-fix` STP describe a prior campaign on another
  worktree/commit (6382 historical collection evidence). They are not evidence
  for this fixture-isolation branch; use the current manual/TD/STP below.

## Implemented artifacts

- ARD/TRD/ID: `docs/dossiers/2026-08-01-root-fixture-isolation-*.md`
- PD/QWF: `docs/plan/2026-08-01-root-fixture-isolation-*.md`
- TD: `docs/tests/2026-08-01-root-fixture-isolation-td.md`
- STP: `docs/sessions/2026-08-01-root-fixture-isolation-stp.md`
- Diary: `docs/diaries/Development_Diary_v000.md`
- Manual: `docs/manuals/2026-08-01-root-fixture-isolation-manual.html`
- Open items: `docs/openitem/2026-08-01-root-fixture-isolation-open-items.md`
- Proposal/PPD: `docs/proposals/2026-08-01-root-fixture-isolation-proposal.md`,
  `docs/vision/2026-08-01-root-fixture-isolation-ppd.md`
- Regression: `tests/test_root_fixture_isolation.py`
- Source/fixture changes: `openviking_cli/utils/config/open_viking_config.py`,
  `tests/conftest.py`

## Gates and evidence

- RED `/app` fixture failure: recorded in ID.
- GREEN focused suite: `40 passed` (11 isolation + 29 Config-Legacy).
- Boundary suite: `3 passed`, one known qdrant warning.
- Root collection: `6302 collected`, one optional `vikingbot` collection error
  plus 15 known warnings; **FAIL/HOLD**, not a full-suite PASS.
- Native lifecycle: missing `ragfs_python`; **HOLD**.
- OpenClaw P0/service, H1/H2 and Provider-live: **HOLD**, not run.

## Evidence matrix

| Gate | Evidence | Status |
|---|---|---|
| G0 identity/base/main checkout | current branch/base and dirty-check before commit | RECHECK |
| G1 historical RED `/app` | ID TDD section and sanitized reproduction | PASS as historical RED |
| G2 early override/sentinel | 2 ordering tests, AGFS/VectorDB path assertions | PASS |
| G3 safe per-test config/env | fixture + raw `ov.conf` endpoint/key assertions | PASS |
| G4 deterministic local fakes | embedder and VLM contract tests | PASS |
| G5 malformed/legacy contracts | top-level/storage/empty/cached tests + 29 config cases | PASS |
| G6 tmp_path isolation | per-test root assertion | PASS; xdist evidence open |
| G7 boundaries | 3 tests, one qdrant warning | PASS with warning |
| G8 root collection | optional `vikingbot` error + 15 warnings | FAIL/HOLD |
| G9 native lifecycle | missing `ragfs_python` | HOLD |
| G10 OpenClaw/service/H1/H2/provider-live | no authorized live run | NOT_RUN/HOLD |
| G11 diff/hash/lock/env review | capture immediately before commit | RECHECK |
| G12 agy review | unavailable in headless workflow | NOT_RUN/UNAVAILABLE |

## Hash- und Umgebungscheckpoint

Die folgenden SHA-256-Werte wurden nach dem letzten Offline-Lauf erfasst. Die
STP-Datei selbst ist bewusst nicht Bestandteil dieses Manifests, damit die
Dokumentation den geprüften Kandidaten nicht zirkulär verändert.

| Artefakt | SHA-256 |
|---|---|
| `openviking_cli/utils/config/open_viking_config.py` | `5de84b48dac5d5cfe55750e1f54db3b07cbd1e599ff601363578fa5f6831ea9e` |
| `tests/conftest.py` | `5bdc37bf14a88a7846530fb501c88e3f589c0b18fb8c3651353ee59e103d9b7d` |
| `tests/test_root_fixture_isolation.py` | `af417bdaac736b367f622d4773405bae25224ead93caa5ad05f2d6a5242407f6` |
| `pyproject.toml` | `94e55055a65c82e7661adbaed5289c6b994300624a5998aff31bfe2053c06160` |
| `uv.lock` | `804e7faa47a7c6d3a0d015c64507f3c451c8447fa5eb26a8ce36158dde27547a` |
| `docs/dossiers/...-ard.md` | `6f52da33bca3198a4ba5e156d42def282c78fe248bb043b79dd5520cbd8f3242` |
| `docs/dossiers/...-trd.md` | `85627767d7c124c80a2f2dcf799c5f1bd5fe99dffd046f685773da6f5a2f9c83` |
| `docs/dossiers/...-id.md` | `e172e66735bc7ff0414d8feaff422d56e1578c424a8eaae60556871b62fbb2e0` |
| `docs/plan/...-pd.md` | `da274e919d81463efe9a424c9ec1f6e2a9ffc534cb38d9af2d797e5a21da5271` |
| `docs/plan/...-qwf.md` | `431f74884d6b72acf517d45fa1dc500c704b18040d81bc3f1c374385290594d7` |
| `docs/tests/...-td.md` | `c3a123e9f63d18937f296219468082d94b450fa6cb05d4a22bc5ac8de10708e2` |
| `docs/manuals/...-manual.html` | `c61a42301a391fb2e6f2a28aa8a55746665e6d36a2d8aabe9c3166396fa99f74` |
| `docs/openitem/...-open-items.md` | `9d7b08d0e29a1752430fbe147a1d94dd0747ec64ef85ce2e2118138a37dcc29f` |
| `docs/proposals/...-proposal.md` | `cb141b0118766d1001d9cee0b12dc54087a5451f3b5b259fd9066fddda3903db` |
| `docs/vision/...-ppd.md` | `4f74b4e29019d85b17c93ff4fa174240c2ee1255a350e0777f0d10fc0e28e6f7` |
| Development Diary | `e8319911c4c74f090a41724e75aa1a6b0f431f776f5d970d654284c704bd1938` |

Vor dem Commit ist zusätzlich auszuführen:

```text
git rev-parse HEAD
git diff --check
git diff --binary -- . ':(exclude)docs/sessions/2026-08-01-root-fixture-isolation-stp.md' | shasum -a 256
env | sed 's/=.*//' | sort | rg '^(OPENAI|ANTHROPIC|GOOGLE|OPENVIKING|LITELLM|VIKINGBOT)_' || true
```

Die letzte Env-Zeile gibt ausschließlich Namen aus; Secret-Werte werden
nicht erfasst.

Der staged-Kandidatenpatch ohne diese STP-Datei hatte an diesem Checkpoint den
Fingerprint `cd5ee6ef1464dc58d5bedd2b20790434d422d20da26bd3bb8ba5c33afe08fdbb`.
Nach jeder Änderung außerhalb der STP-Datei ist dieser Wert neu zu erfassen;
bei Hashabweichung gilt der Checkpoint als ungültig.

## Safe continuation

```text
cd /Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-root-fixture-isolation
test "$(git rev-parse --show-toplevel)" = "$PWD"
test "$(git rev-parse HEAD)" = "22919c337f2837ab65cbc4d778496090f9d77fad"
PYTHONPATH="$PWD" .venv/bin/python -m pytest \
  tests/test_root_fixture_isolation.py tests/test_config_loader.py \
  -q -o addopts= -p no:cacheprovider
git diff --check
git status --short --branch
```

Before commit, re-run the focused tests from a fresh process, review the exact
diff, capture source/document SHA-256s and verify the main checkout remains
unchanged. Commit, push, and open a Draft PR against `manni07/OpenViking` only
under the confirmed delivery scope; no merge is part of this handoff. Do not
start services, run live providers, or reboot anything.

## Stop rules

Stop and mark FAIL/HOLD if the safe config path changes, `/app` or repository
`test_data` is written, a credential/endpoint appears in logs, a native/live
gate is presented as PASS, the branch base drifts, or the main checkout changes.
