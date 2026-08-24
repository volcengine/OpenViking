# QWF — Root-Fixture-Isolation

| ID | Arbeitspaket | Abhängigkeit | Nachweis |
|---|---|---|---|
| Q1 | `/app`-Fehler und feste `test_data/tmp`-Ablage reproduzieren | G0 | RED-Ausgabe |
| Q2 | Architektur/Security/DevOps/MCP-Review | Q1 | vier bounded Reports |
| Q3 | Frühzeitiger Workspace-Override + Root-Config-Fixture | Q2 | drei betroffene Dateien, diff-check |
| Q4 | Unit-/Contract-Regressionen und Legacy-Config-Suite | Q3 | fokussierte pytest-Ausgaben |
| Q5 | Offline-Collection/Boundary-Bericht | Q4 | PASS/FAIL/HOLD/NOT_RUN getrennt |
| Q6 | Dossiers/STP/Diary/Manual/Open Items/Proposal | Q5 | verlinkte Artefakte |
| Q7 | Commit/Push/Draft-PR | Q6 | SHA, Remote, PR-URL; kein Merge |

Der QWF führt keine Provider-, OpenClaw- oder Service-Live-Tests aus. Der
separate H1/H2-/Live-Plan bleibt bestehen und wird nur referenziert.
