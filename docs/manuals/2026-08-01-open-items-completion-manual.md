# Manual — Open Items Completion

## Zweck

Dieses Manual beschreibt den sicheren Offline-Nachweis und die Übergabe des
Fork-PRs. Es aktiviert keine Live-Gates.

## Offline-Lauf

1. Im angegebenen Worktree Branch, Remotes und `git status` prüfen.
2. Provider-Credentials und `OPENVIKING_CONFIG_FILE` für die Collection
   entfernen; die Root-Fixture erzeugt eine private temporäre Config.
3. Root-Collection mit strict markers und `PytestCollectionWarning` als Fehler
   ausführen.
4. Root-Vollsuite und danach `bot/pytest.ini` als eigenständige Suite starten.
5. Ergebnisse und Drittanbieter-Warnungen getrennt protokollieren. Ein Skip
   ist kein Pass für ein Live-Gate.

Der verbindliche lokale Merge-Lauf wird aus dem separaten Runner-Projekt
gestartet:

```bash
uv run --directory /Volumes/ExtremePro/projects/local-ci-gate \
  local-ci-gate run --stage merge --project "$PWD"
```

Der Runner führt nur die versionierten Argumentlisten aus
`.local-ci-gate.toml` aus und verwendet weder GitHub noch eine Shell.

## Live-Gate-Vorbereitung

OpenClaw, H1, H2 und Provider-Live benötigen jeweils eine neue Freigabe,
disposable Credentials, Kosten-/TTL-Grenzen, Capability-Probe und einen
Rollback-/Stop-Plan. OAuth wird nur an der freigegebenen HTTPS-Codex-Origin
verwendet. `store=false` ist kein allgemeines Zero-Retention-Versprechen.

## Übergabe

Nach `git diff --check` und frischen Tests im Fork committen und pushen. PR-
Status, CI und Review dokumentieren. Nicht automatisch mergen, Dienste neu
starten oder den Haupt-Checkout verändern.
