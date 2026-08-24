# Session Transfer Protocol — Open Items Completion

**Stand:** 2026-08-01
**Status:** Offline PASS; Live-Gates HOLD; Fork-PR-Abschluss ausstehend

## Identität und Fortsetzung

| Feld | Wert |
|---|---|
| Worktree | `/Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-open-items-completion` |
| Branch | `agent-workflow/20260801-open-items-completion` |
| `origin` | `https://github.com/manni07/OpenViking.git` |
| `upstream` | `https://github.com/volcengine/OpenViking.git` |
| Ausgangs-HEAD | `fdccb0b3` |
| Haupt-Checkout | `/Volumes/ExtremePro/projects/OpenViking` — nicht verändern |
| kanonische venv | `.venv` im Worktree |
| lokaler CI-Runner | `/Volumes/ExtremePro/projects/local-ci-gate` |

Vor jeder Fortsetzung zuerst `git status --short --branch`, Branch und Remote
prüfen. Fremde Änderungen nicht resetten, überschreiben oder automatisch
auflösen.

## Evidence-Ledger

- Root collection: `6359 tests collected`, strict markers, keine
  `PytestCollectionWarning`.
- Root full offline: `6129 passed, 232 skipped, 4 warnings`.
- Bot standalone: `271 passed, 4 warnings`.
- Legacy-/Config-/Boundary: `268 passed, 3 skipped`.
- Integration/storage/rerank: `420 passed, 2 skipped`.
- Storage: `395 passed, 2 skipped`.
- Native AGFS: Smoke `5 passed`, Lifecycle `2 passed`.
- Lokales CI-Gate: alle vier Checks PASS; GitHub wurde für diesen Nachweis
  nicht verwendet.
- Warnungen: ausschließlich `lark_oapi`/`websockets`, nicht lokal erzeugt.
- Neustarts: keiner; keine Live-Credentials oder Live-Endpunkte verwendet.

## Nachtrag 2026-08-02 — Lark/WebSockets

Nach der Dependency-/SansIO-Behebung und der Isolation des Word-Parser-
Test-Helfers bestand der lokale Merge-Runner alle fünf Checks. Die aktuelle
Evidenz lautet Root `6164 passed, 246 skipped, 1` upstream Lark-Warnung und
Bot `271 passed, 2` upstream Lark-Warnungen. Die WebSockets-Legacy-Warnungen
sind beseitigt; die zwei vendorten Lark-Warnungen bleiben bewusst sichtbar.

## Sichere Offline-Reproduktion

```bash
cd /Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-open-items-completion
git status --short --branch
git diff --check
uv run --directory /Volumes/ExtremePro/projects/local-ci-gate \
  local-ci-gate run --stage merge --project "$PWD"
env -u GOOGLE_API_KEY -u OPENAI_API_KEY -u OPENAI_ACCESS_TOKEN \
  -u ANTHROPIC_API_KEY -u OPENVIKING_CONFIG_FILE \
  PYTHONPATH="$PWD" .venv/bin/python -m pytest tests --collect-only -q \
  -o addopts= -p no:cacheprovider --no-cov --strict-markers \
  -W error::pytest.PytestCollectionWarning
env -u OPENVIKING_CONFIG_FILE PYTHONPATH="$PWD" .venv/bin/python -m pytest -q \
  --no-cov -o addopts= -W error::pytest.PytestUnraisableExceptionWarning \
  -W error::pytest.PytestUnhandledThreadExceptionWarning \
  --basetemp=/tmp/openviking-root-final-14 tests
cd bot
../.venv/bin/python -m pytest -q -c pytest.ini
```

## Stop-/HOLD-Regeln

- OpenClaw-P0/Service, H1, H2 und Provider-Live nur mit separater Freigabe,
  disposable Credentials/Config und eigenem Evidence-Log starten.
- OAuth nur am exakt freigegebenen HTTPS-Codex-Origin; kein API-Key-Fallback.
- Kein Rechner-, Server-, Runtime-, Container-, Service- oder Prozess-Restart.
- Bei Hash-, Branch-, Lockfile-, Credential- oder Workspace-Drift FAIL-CLOSED.
- PR-Review/CI sind nach dem Push externe Restpunkte; kein automatischer Merge.
