# Test Dossier — Lark/WebSockets Compatibility

## RED/GREEN-Matrix

| Test | Baseline | Erwartung nach Fix |
|---|---|---|
| `test_lark_sdk_no_longer_imports_deprecated_websocket_symbols` | FAIL: zwei WebSockets-Warnungen | PASS: keine WebSockets-Warnung |
| `test_uvicorn_sansio_websocket_protocol_imports_without_deprecation` | FAIL: Legacy-/fehlender SansIO-Pfad | PASS: keine `DeprecationWarning` |
| `test_main_keeps_config_host_when_cli_host_is_omitted` | FAIL: kein `ws`-Argument | PASS: `websockets-sansio` |

## Ausführung

```bash
uv lock --check
env -u OPENVIKING_CONFIG_FILE -u OPENVIKING_CLI_CONFIG_FILE \
  -u OPENAI_API_KEY -u OPENAI_ACCESS_TOKEN -u GOOGLE_API_KEY \
  -u ANTHROPIC_API_KEY PYTHONPATH=. .venv/bin/python -m pytest -q \
  --no-cov -o addopts= tests/test_lark_websockets_compat.py \
  tests/server/test_bootstrap.py
```

Danach wird der lokale Merge-Runner ausgeführt:

```bash
uv run --directory /Volumes/ExtremePro/projects/local-ci-gate \
  local-ci-gate run --stage merge \
  --project /Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-open-items-completion
```

## Finale Offline-Evidenz — 2026-08-02

Ausgeführt über `/Volumes/ExtremePro/projects/local-ci-gate` (kein GitHub-
Runner, keine Live-Credentials):

| Check | Ergebnis |
|---|---:|
| `git-diff-check` | PASS |
| `lark-websockets-compatibility` | PASS, 2 Tests |
| `collection-fixture-regressions` | PASS, 16 Tests |
| `root-offline-suite` | PASS, 6164 passed, 246 skipped, 1 upstream Lark-Warnung |
| `bot-standalone-suite` | PASS, 271 passed, 2 upstream Lark-Warnungen |

Der Root-Lauf dauerte 703,04 s, der Bot-Lauf 8,43 s. Der zuvor in der
Vollsuite beobachtete Word-Parser-Fehler war ein Test-Isolationsfehler: Der
Helper patchte das gemeinsam genutzte `asyncio`-Modul. Er ersetzt nun nur die
Referenz im Parser-Modul durch einen kleinen Proxy; Produktionscode und
process-globale Asyncio-Funktionen bleiben unangetastet.

## Warnungsgrenze

Die Tests verbieten WebSockets-Legacy-Warnungen. Die zwei bekannten
`lark_oapi`-Upstream-Warnungen (`utcfromtimestamp`, fehlender Event-Loop) sind
als Rest-Risiko erfasst und werden nicht mit einem globalen Filter kaschiert.
