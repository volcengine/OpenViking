# QWF — Open Items Completion (2026-08-01)

| Gate | Arbeitspaket | Nachweis | Status |
|---|---|---|---|
| Q0 | Evidence freeze und Doku-Abgleich | Worktree-/Branch-/Basis-/venv-Ledger | PASS |
| Q1 | Marker, Helper, Fixtures und direkte Konstruktoren | RED/GREEN-Regressionen, `/app`-Guard | PASS |
| Q2 | Root-/Bot-Ownership | Root strict collection + separater Bot-Run | PASS |
| Q3 | Offline-Legacy-Suite | Root `6129/232`, Legacy `268/3`, Storage `395/2` | PASS |
| Q4 | Native `ragfs-python` | Import, Smoke `5`, Lifecycle `2` | PASS |
| Q5 | OpenClaw P0/Service | Health/Handshake/aktuelle P0-Tests | HOLD / NOT RUN |
| Q6 | Codex H1 Capability | exakter OAuth-Origin/Model/Threshold | HOLD / NOT RUN |
| Q7 | Codex H2 Benchmark | 20 reale + 10 synthetische Szenarien | HOLD / NOT RUN |
| Q8 | Dokumentation und Fork-PR | Diff-/Testcheck, Commit/Push/Review | PASS / MERGED |

Der lokale CI-Nachweis für Q8 ist PASS. Fork-PR #8 wurde als
`373aa383511a62a8178208511c60b655ea406dfa` in `manni07/OpenViking:main`
gemergt. Offen bleiben ausschließlich die absichtlich angehaltenen Live-Gates
und der dokumentierte Upstream-Warning-Rest.

## Agent-Waves (logical team 10)

Die fachlichen Rollen bleiben `master_orchestrator`,
`documentation_agent`, `session_transfer_agent`, `architecture_agent`,
`code_quality_api_agent`, `security_agent` (Veto), `simulation_agent`,
`test_unit_agent`, `mcp_coordinator_agent` und `devops_agent`. In dieser
Umgebung liefen höchstens drei Kinder parallel; gemeinsame Dateien wurden
seriell geändert. Die Offline-Testsimulation erfüllt die geforderten
Erfolgskriterien; Live-Gates haben absichtlich keinen Simulations-PASS erhalten.

## Risiken und Gegenmaßnahmen

1. **Host-Singleton/TOCTOU:** früher temporärer Config-Pfad, per-Test
   Workspace, Reset und hostile `/app`-Regression.
2. **Optionale Abhängigkeiten:** lockfile-basierte venv, Root-/Bot-/OpenClaw-
   Besitzgrenzen und maschinenlesbare HOLD/NOT_RUN-Ergebnisse.
3. **Native ABI:** Build aus dem tatsächlich verwendeten Workspace-Crate,
   Import-/Mount-Smoke vor Lifecycle.
4. **Live-Leak/Cost:** OAuth-Whitelist, `store=false`, Retry=0,
   sanitisiertes Logging, Timeout/TTL und sofortiger HOLD bei Abweichung.
5. **Harness-Schaden:** keine `reset --hard`, `clean`, `rm -rf` oder `pkill`-
   Skripte; direkte aktuelle Testpfade in isolierter Umgebung.
