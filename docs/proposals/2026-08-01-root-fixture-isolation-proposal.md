# Proposal — Root-Fixture-Isolation

## Entscheidung

Den `/app`-Blocker durch einen frühen, optionalen Workspace-Override im
Config-Singleton und eine sichere, function-scoped Root-Testgrenze beheben.
Die Änderung bleibt opt-in auf Test-/Embedded-Pfade; gespeicherte Host-
Konfigurationen, Provider-Defaults und Live-Harnesses werden nicht global
umgeschrieben.

## Evidenz

- Vorher: `StorageConfig.resolve_paths()` scheiterte beim Host-
  `storage.workspace=/app/...`, bevor `path` greifen konnte.
- Nachher: 40 fokussierte Regressionen (11 Isolation plus 29 Config-Legacy-
  Fälle) PASS;
  Host-Sentinel und `/app`-Guard bleiben unberührt.
- Root-Collection und native Lifecycle sind wegen eigenständiger optionaler
  Abhängigkeiten separat FAIL/HOLD und werden nicht als Erfolg behauptet.

## Rollout

1. Fork-Commit und Draft-PR öffnen.
2. CI-/Review-Gates abwarten; kein Merge in diesem Arbeitslauf.
3. Native/Live-Phase nur mit separater Genehmigung starten.
4. Promotion erst nach grüner Root-Collection, nativer Lifecycle-Regression
   und den bestehenden H1/H2-/Provider-Gates.
