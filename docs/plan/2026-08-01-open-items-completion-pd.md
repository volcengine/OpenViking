# PD — Open Items Completion (2026-08-01)

## Ziel

Die Root-Sammlung, die Warning-/Collection-Bilanz, die eigenständige
VikingBot-Harness und der lokale Native-Lifecycle sollen reproduzierbar und
hostisoliert grün werden. Live-Gates bleiben explizit getrennt und fail-closed.

## Phasen und Ergebnis

1. **Q0 Evidence freeze — PASS:** Fork, Worktree, Ausgangs-HEAD, venv,
   Host-/Provider-Isolation und vorherige Fehlerklassen wurden festgehalten.
2. **Q1 Testvertrag — PASS:** Collection-, `/app`-, Config-, Legacy- und
   Bot-Isolationsregressionen wurden zuerst als prüfbare Verträge formuliert.
3. **Q2 Minimalfix — PASS:** Root-/Bot-Pythonpfad, Marker, Helper-Sammlung,
   Fixture-Scopes und direkte Legacy-Fehler wurden chirurgisch korrigiert.
4. **Q3 Offline-Gate — PASS:** strict Collection, Root-Vollsuite,
   fokussierte Legacy-/Config-/Boundary-Suiten und Bot-Vollsuite sind grün;
   vier externe Drittanbieter-Warnungen sind dokumentiert.
5. **Q4 Native-Gate — PASS:** isolierter AGFS-Smoke-/Lifecycle-Nachweis mit
   Import, Read/Write und Cleanup.
6. **Q5 OpenClaw-Gate — HOLD/NOT RUN:** echter P0-/Service-Handschlag und
   read-only MCP-Aufruf werden erst in der separat genehmigten Live-Phase
   ausgeführt.
7. **Q6 H1 — HOLD/NOT RUN:** Capability-Probe am exakt freigegebenen
   Codex-Endpunkt mit OAuth, `store=false`, ohne Conversation-State.
8. **Q7 H2 — HOLD/NOT RUN:** 20 reale und 10 synthetische Szenarien,
   dreifache Wiederholung der nichtdeterministischen Fälle und die festgelegte
   Promotionsmatrix.
9. **Q8 Abschluss — IN PROGRESS:** Diff-/Hash-/Testcheck, Commit, Push und
   Fork-PR; kein automatischer Merge oder Aktivierung.

## Stop-Regeln

- `/app`, Host-Konfigurationspfad, Credential-Inhalt oder fremder Prozess wird
  berührt: sofort STOP.
- Native ABI, OAuth-Origin, Modell oder Capability nicht exakt prüfbar: Gate
  HOLD; kein Fallback.
- Ein Service-Neustart wäre für einen nicht identifizierten Prozess nötig:
  STOP und Zustand melden. In dieser Runde wird nichts neu gestartet.
- Ein Test würde Daten außerhalb seines temporären Workspace schreiben: STOP;
  den vorherigen Zustand unverändert lassen.
- Drittanbieter-Warnungen werden nicht mit lokalem Code vermischt; Upgrade-
  Arbeit bleibt ein separater, überprüfbarer Wartungspunkt.
