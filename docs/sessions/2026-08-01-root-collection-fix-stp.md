# Session Transfer Protocol: Root-Test-Collection-Fix

**Stand:** 2026-08-01
**Status:** Root-Collection PASS; eigenstaendige Harnesses und Live-Phasen HOLD

Dieses Dokument ist der restartbare Uebergabestand. Es erlaubt eine
Fortsetzung ohne Annahmen ueber den Zustand anderer Checkouts oder Services.

## 1. Repository-Identitaet

| Feld | Wert |
|---|---|
| Fork/`origin` | `https://github.com/manni07/OpenViking.git` |
| Read-only Referenz/`upstream` | `https://github.com/volcengine/OpenViking.git` |
| Worktree | `/Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-root-collection-fix` |
| Branch | `agent-workflow/20260801-root-collection-fix` |
| Startbasis | `bc0a3ad639aa65f0b3e9ae8d99c5416a1e8c1f3e` |
| Verifizierter Implementierungscommit | `9a2bcd130e47ef0e9109ba7902e0335c537a690b` |
| Kanonische lokale Test-venv | `.venv-root-collect` |
| Live-Aktivierung | nicht erfolgt |

Am Dokumentationscheckpoint sind die Implementierungsdateien als
`9a2bcd130e47ef0e9109ba7902e0335c537a690b` committed; die
Dokumentationsaenderungen werden in einem nachfolgenden Commit gebuendelt. Vor
jeder Fortsetzung sind Branch, HEAD, Status und Hashes neu zu pruefen. Fremde
oder unerwartete Aenderungen duerfen nicht verworfen, ueberschrieben oder
automatisch zusammengefuehrt werden.

## 2. Scope und Aenderungen

Der Fix besteht aus drei chirurgischen Aenderungen:

1. `tests/conftest.py` ignoriert bei der Root-Collection exakt die
   eigenstaendigen Projekte `api_test` und `oc2ov_test`.
2. `tests/integration/test_gemini_e2e.py` verschiebt den optionalen
   Gemini-Embedder-Import in die Fixture beziehungsweise den direkt nutzenden
   Test.
3. `tests/test_test_suite_boundaries.py` fixiert beide Besitzgrenzen und das
   fail-loud-Verhalten bei echter Nutzung ohne Provider-Extra.

Es gab keine Produktivcode-, Service-, Runtime-, Credential- oder
Provideraenderung.

## 3. Hash-Checkpoint

SHA-256 der Implementierungs- und Umgebungsvertragsdateien am
Dokumentationscheckpoint:

| Datei | SHA-256 |
|---|---|
| `tests/conftest.py` | `8a71501a99ccdd6e0799e5ed4ed63a7cb18d148a3187bd532f1ddfedc0b1cd0a` |
| `tests/integration/test_gemini_e2e.py` | `b47afc19627e73e6f27b28e63315ace66764e6d837498a639f5f27604c85e43c` |
| `tests/test_test_suite_boundaries.py` | `d99431f444d90e97b8b9b31064c3051b1d8d5f67c8b2bb9e72f15b32ffaf1865` |
| `pyproject.toml` | `94e55055a65c82e7661adbaed5289c6b994300624a5998aff31bfe2053c06160` |
| `uv.lock` | `804e7faa47a7c6d3a0d015c64507f3c451c8447fa5eb26a8ce36158dde27547a` |

Dokumentationsdateien tragen absichtlich keinen selbstreferenziellen Hash in
ihrem eigenen Inhalt. Nach einer weiteren Aenderung oder einem Commit gilt der
Checkpoint nur nach erneuter Hashpruefung.

## 4. Frische Testevidenz

| Gate | Ergebnis |
|---|---:|
| TDD RED | 3 FAIL vor Implementierung |
| Boundary-Vertraege, kanonische venv | 3 PASS |
| Gemini-E2E-Collection ohne Credentials | 5 gesammelt, Exit 0 |
| Vollstaendige Root-Collection | 6382 gesammelt in 18.07 s, Exit 0, 0 Collection-Fehler |

Die Umgebung nutzt `uv 0.8.20`, Python `3.12.11`, `mcp 1.28.1` und
`scrapy 2.16.0`. Die 15 verbleibenden Warnungen sind kein Collection-Fehler,
bleiben aber offen dokumentiert.

## 5. Sichere Reproduktion

```bash
cd /Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-root-collection-fix
git status --short --branch
git branch --show-current
git rev-parse HEAD
git diff --check

UV_PROJECT_ENVIRONMENT=.venv-root-collect \
  uv sync --frozen --python 3.12.11 --extra test

env -u GOOGLE_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
  .venv-root-collect/bin/python -m pytest \
  tests/test_test_suite_boundaries.py \
  -q -o addopts= -p no:cacheprovider --no-cov

env -u GOOGLE_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
  .venv-root-collect/bin/python -m pytest \
  tests/integration/test_gemini_e2e.py --collect-only \
  -q -o addopts= -p no:cacheprovider --no-cov

env -u GOOGLE_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
  -u OPENAI_ACCESS_TOKEN \
  .venv-root-collect/bin/python -m pytest \
  tests --collect-only \
  -q -o addopts= -p no:cacheprovider --no-cov
```

Eine abweichende Testzahl, ein geaendertes Lockfile, ein Hashunterschied oder
ein unerwarteter Git-Status ist zuerst als Drift zu klaeren; nicht pauschal als
neuer PASS zu uebernehmen.

## 6. Aussage- und Stopregeln

- Die Root-Collection ist PASS; die vollstaendige Root-Testausfuehrung wurde
  nicht durchgefuehrt.
- `api_test` und `oc2ov_test` sind korrekt aus der Root-Collection entfernt,
  aber ihre eigenen Suiten wurden nicht ausgefuehrt und duerfen nicht PASS
  genannt werden.
- H1 Capability-Probe und H2 Live-Benchmark/Canary bleiben HOLD, bis eine neue
  ausdrueckliche Live-, Credential-, Daten- und Kostenfreigabe vorliegt.
- Kein Provider-, Credential- oder Netzwerkaufruf zur Umgehung eines fehlenden
  Offline-Belegs.
- Kein Rechner-, Server-, Runtime-, Container-, Service- oder Prozess-Restart
  ohne ausdrueckliche Bestaetigung.
- Bei Scope-, Hash-, Credential-, Lockfile-, Test- oder Repository-Drift gilt
  fail-loud HOLD.
- Kein Commit, Push, PR, Merge, Reset, Rebase oder Cleanup allein aufgrund
  dieses Transferdokuments; dafuer muss die aktive Autorisierung geprueft
  werden.

## 7. Verknuepfte Artefakte

- [Test Dossier](../tests/2026-08-01-root-collection-fix-td.md)
- [Manual](../manuals/2026-08-01-root-test-collection-manual.html)
- [Open Items](../openitem/2026-08-01-root-collection-fix-open-items.md)
