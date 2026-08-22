# Architecture Requirement Dossier — Lark/WebSockets Compatibility

**Stand:** 2026-08-02
**Scope:** Offline-M1 aus `docs/openitem/2026-08-01-open-items-completion.md`
**Workflow:** `$tccode` (`thorough`, `critical`) innerhalb Agent-Workflow-v4

## Problem

Der Offline-Lauf meldete Warnungen aus drei Drittanbietergrenzen:

- `lark-oapi 1.5.3` griff auf `websockets.InvalidStatusCode` zu;
- `uvicorn 0.41.0` importierte den `websockets.legacy`-Serverpfad;
- `websockets 16.0` markiert diese Legacy-Symbole als deprecated.

Zusätzlich erzeugt `lark-oapi` beim Import vendorten Protobuf-Code mit
`datetime.utcfromtimestamp` und initialisiert einen globalen Event-Loop. Diese
beiden Warnungen sind in `lark-oapi 1.7.1` weiterhin upstream vorhanden und
werden nicht durch eine lokale Filterregel verschleiert.

## Ziel

Der OpenViking-Produktionspfad darf den deprecated WebSockets-Legacy-Adapter
nicht mehr verwenden. Die Abhängigkeiten müssen reproduzierbar auf einem
kompatiblen stabilen Bereich liegen. Upstream-Warnungen werden als Rest-Risiko
separat sichtbar gehalten.

## Architekturentscheidung

1. `lark-oapi` wird auf `>=1.7.1,<2.0` begrenzt. Diese Version verwendet für
   den Client `InvalidHandshake` statt des deprecated `InvalidStatusCode`.
2. `uvicorn` wird auf `>=0.51.0` begrenzt und der OpenViking-/VikingBot-
   Serverpfad wählt `websockets-sansio` explizit.
3. Das Bot-Extra begrenzt `websockets` auf `>=13.0,<16`, passend zum
   unterstützten Lark-Bereich und dem Uvicorn-SansIO-Pfad.
4. Keine Änderungen an `VLMBase`, Providern, Live-Credentials oder laufenden
   Diensten; kein Restart.

## Erfolgskriterien

- `uv.lock` enthält `lark-oapi 1.7.1`, `uvicorn 0.52.1` und
  `websockets 15.0.1`.
- Import-/Bootstrap-Regressionen bestehen und erzeugen keine WebSockets-
  Legacy-Warnung.
- Der lokale CI-Runner besteht alle Checks.
- Die zwei verbleibenden Lark-Upstream-Warnungen sind im Evidence-Ledger
  benannt, nicht unterdrückt (siehe
  [`2026-08-02-live-gates-and-lark-warning-ledger.md`](2026-08-02-live-gates-and-lark-warning-ledger.md)).
