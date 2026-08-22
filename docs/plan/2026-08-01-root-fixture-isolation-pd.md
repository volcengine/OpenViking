# PD — Root-Fixture-Isolation (tccode)

## Ziel

Den dokumentierten Root-Blocker `/app` und die damit verbundene Host-
`ov.conf`-Übernahme beheben, ohne Live- oder Service-Aktionen. Die Umsetzung
bleibt auf dem Fork `manni07/OpenViking`, branch
`agent-workflow/20260801-root-fixture-isolation`, ohne Merge.

## QWF-Reihenfolge

1. Fehler reproduzieren und Host-/Provider-Leakage als RED festhalten.
2. Architektur-, Security-, DevOps- und MCP-Reviews parallel in bounded waves.
3. Frühzeitigen Config-Override und explizite Root-Fixture implementieren.
4. Testsimulation mit fünf Kriterien (je mindestens 90 %, aggregiert mindestens
   95 %) und maximal drei Revisionen.
5. Regressionen, Config-Contract und Collection-Gates offline ausführen.
6. Dossiers, STP, Diary, Manual, Open-Items und Proposal aktualisieren.
7. Gezielten Commit erstellen, Fork pushen und Draft-PR öffnen; kein Merge.

## Team (logische Größe 10)

`master_orchestrator`, `architecture_agent`, `security_agent`, `devops_agent`,
`mcp_coordinator_agent`, `simulation_agent`, `test_unit_agent`,
`code_quality_api_agent`, `documentation_agent`, `session_transfer_agent`.

Die Plattform erlaubt höchstens drei gleichzeitig laufende Subagenten;
deshalb werden die zehn Rollen in Wellen ausgeführt. Jeder Agent arbeitet
read-only oder an einem eindeutig abgegrenzten Artefakt; die Root-Integration
erfolgt zentral.

## Gate-Matrix

| Gate | Nachweis | Statusbedingung |
|---|---|---|
| G0 | Fork/Worktree/HEAD/dirty-Hauptcheckout | PASS, ohne Hauptcheckout-Änderung |
| G1 | sanitisiertes `/app`-RED und frühes Override | PASS |
| G2 | Root-Fixture ohne Host-Env/Endpoint/Credential | PASS |
| G3 | tmp_path- und Cleanup-Isolation | PASS |
| G4 | Security/Simulation | >=95 % aggregiert, >=90 % je Kriterium |
| G5 | Offline-Regressionen/Config-Legacy | PASS |
| G6 | Native Lifecycle/Live-Harness | HOLD, separat genehmigungspflichtig |

## Stop-Regeln

Bei Host-Datei-/Credential-Leak, Cross-Worker-Schreibzugriff, Netzwerkaufruf,
fehlender Native-Bindung in einem als PASS behaupteten Test oder Drift des
Haupt-Checkouts wird angehalten und der Zustand als FAIL/HOLD dokumentiert.
