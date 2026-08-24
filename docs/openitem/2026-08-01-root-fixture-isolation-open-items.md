# Open Items — Root-Fixture-Isolation (2026-08-01)

## High (3)

1. **H1 — Native Lifecycle:** `ragfs_python` im Test-Venv bauen/installieren
   und die repräsentativen Root-Client-/Service-Tests mit dem sicheren Config-
   Pfad ausführen; bis dahin HOLD.
2. **H2 — Root-Collection:** Optionales `vikingbot`-Subprojekt in einer
   kanonischen Testumgebung bereitstellen oder seine eigenständige Harness-
   Grenze formal registrieren; den aktuellen Collection-FAIL nicht verdecken.
3. **H3 — Live-Gates:** OpenClaw-P0/Service, H1/H2 und Provider-Live bilden
   eine externe, separat freizugebende Ausführungsgrenze. Erst danach mit
   OAuth/API-Credentials, Rollback-Plan und expliziter Start-/Restart-
   Bestätigung ausführen; kein impliziter Start/Restart.

Die aktuelle Einstufung ist mit der Evidence-Matrix im STP abzugleichen.
H1/H2/H3 bleiben bis zu ihren jeweiligen Freigaben HOLD; ein Offline-PASS
schließt diese offenen Gates nicht.

## Medium (3)

1. **M1 — Direct constructors:** Root-Tests, die `AsyncOpenViking` oder
   `OpenVikingService` ohne `client`-/`uninitialized_client`-Fixture erzeugen,
   einzeln auf die sichere Bootstrap-/Fixture-Grenze auditieren.
2. **M2 — Warning cleanup:** 11 `cli_remote`-, 1 `qdrant`- und 3
   Helper-Class-Collection-Warnungen separat registrieren und nach Scope
   beheben; nicht mit der `/app`-Behebung vermischen.
3. **M3 — CI gate:** Einen expliziten offline Root-Collection-Job mit
   provider-/URL-env-Clearing, kanonischer venv und PASS/FAIL/HOLD-Artefakten
   ergänzen.

## Low (3)

1. **L1 — Bootstrap lifetime:** Die globale Test-Bootstrap-Datei nach dem
   vollständigen Root-Testdesign auf eine pytest-session-scoped Fixture oder
   einen dokumentierten Import-Hook reduzieren.
2. **L2 — Type precision:** `AsyncGenerator[dict, None]` und Config-Dict auf
   präzisere TypedDict-/Mapping-Typen umstellen, sobald die Fixture stabil ist.
3. **L3 — Parallel evidence:** Mit der nativen Umgebung einen kontrollierten
   xdist-Lauf (`-n 2`) wiederholen und eindeutige Workspace-/Cleanup-Nachweise
   archivieren; bis dahin serialer Offline-Gate.
