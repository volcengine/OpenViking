# PD: P0-P2 Fixplan – Security Hardening

## Priorisierter Plan

| Priorität | Arbeitspaket | Abhängigkeit | Abnahmekriterium |
|---|---|---|---|
| P0 | SEC-001 Web-Studio-Link-Policy | ID/TD | keine aktiven gefährlichen Link-Schemas |
| P0 | SEC-002 Graph-Link-Policy | ID/TD | keine gefährlichen `href` im gerenderten Graph-HTML |
| P1 | SEC-003 WebDAV-Limit | ID/TD | 16 MiB, Content-Length und chunked jeweils 413 |
| P1 | SEC-004/005 Public Deployment | ID/TD | Public host fail-closed ohne konkrete Origins und Base URL |
| P1 | SEC-006/007 Dependency-Fixes | P0/P1 Quelle stabil | kompatible Updates, neue Scanbelege, Restblocker |
| P2 | SEC-008 CI-Scans | Abhängigkeitskommandos | Node/Rust-Scans als Workflow-Schritte |
| P2 | Operative Dokumentation | vollständiger Teststand | STP, Diary, Manual, PPD und Open Items |

## Team-Orchestrierung (10 Rollen, gestaffelt)

1. Master Orchestrator; 2. Architecture; 3. Simulation; 4. Frontend; 5. Server/API; 6. Graph/Memory; 7. Dependency/DevOps; 8. Test/Unit; 9. Integration/E2E; 10. Documentation/Open Items/Session.

Es laufen höchstens drei Worker parallel. Serena und Superpowers sind in dieser Sitzung nicht als Skills verfügbar; stattdessen werden `rg`, die vorhandenen Tests und die Git- sowie Sicherheits-Skills als nachvollziehbarer Ersatz eingesetzt. Die nicht vorhandenen Profile `test_engineer_agent` und `test_integration_agent` werden durch `test_unit_agent` bzw. `test_e2e_agent` ersetzt und im TD vermerkt.

## Qualitätsworkflow und Stop-Regeln

- `tccode` führt die Phasen und Artefakte; `agent-workflow-v4` liefert fachlich abgegrenzte Reviews und Implementierungsslices.
- Eine Simulation muss pro Dimension und insgesamt mindestens 95 % erreichen. Darunter wird der Plan überarbeitet, nicht implementiert.
- Jeder Source-Slice erhält fokussierte Tests vor dem nächsten Slice.
- Tests dürfen nicht gelockert werden. Nicht ausführbare Integrations-/Browser-Gates bleiben ausdrücklich rot/blockiert dokumentiert.
- Nur Dateien dieses Worktrees werden gestaged; kein Merge in `main`, kein Server- oder Rechnerneustart.
