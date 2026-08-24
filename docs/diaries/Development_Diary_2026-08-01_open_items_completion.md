# Development Diary — Open Items Completion (2026-08-01)

## Ausgangslage

Die Root-Vollsuite war durch Host-/Fixture-Annahmen, eigenständige Harness-
Einbindung und optionale Imports nicht belastbar. Zusätzlich waren mehrere
vorbestehende Legacy-Verträge nur durch fokussierte Tests sichtbar. Live-
Gates wurden ausdrücklich nicht als Offline-Arbeit behandelt.

## Durchgeführte Arbeit

1. `$tccode`-Dossiers und QWF festgelegt; agent-workflow-v4 mit den zehn
   Rollen für Architektur-, Security-, Test-, Dokumentations- und DevOps-
   Reviews eingesetzt.
2. Root-/Bot-Testgrenzen, temporäre Configs, Singleton-Reset und
   `/app`-Regressionen gehärtet. Der Bot-Harness liest keine persönliche
   `ovcli.conf` mehr, sofern ein Test keinen eigenen Pfad setzt.
3. Legacy-Fehler in OpenGauss-Update, URI-Scopes, Embedder-/Gemini-/Rerank-
   Config, Namespace-/Memory-/Prompt-Kompatibilität und Bot-Retention behoben.
4. Native AGFS importiert und mit Smoke-/Lifecycle-Tests verifiziert.

## Verifikation

Root: `6129 passed, 232 skipped, 4 warnings`; Bot: `271 passed, 4 warnings`.
Die Warnungen sind Drittanbieter-Deprecations aus `lark_oapi`/`websockets`.
Es wurden keine Neustarts und keine Provider-/OAuth-Live-Aufrufe durchgeführt.

## Noch offen

OpenClaw-P0/Service, H1/H2 und Provider-Live bleiben HOLD. Danach stehen
externer PR-Review/CI und ein gesonderter Promotionsentscheid aus.

## Nachtrag 2026-08-02 — Lark/WebSockets-Kompatibilität

Die vorbestehenden WebSockets-Warnungen wurden auf der Ursachebene geprüft.
`lark-oapi 1.5.3` und `uvicorn 0.41.0` verwendeten mit `websockets 16.0`
Legacy-Symbole. `lark-oapi 1.7.1`, `uvicorn 0.52.1` und `websockets 15.0.1`
wurden in `pyproject.toml`/`uv.lock` festgeschrieben; die Serverpfade wählen
`websockets-sansio` explizit. Neue RED/GREEN-Tests schützen den Import- und
Bootstrap-Vertrag. Die zwei verbleibenden Lark-Warnungen (`utcfromtimestamp`
und fehlender Event-Loop) stammen aus vendortem upstream Code und werden nicht
unterdrückt. Der lokale Gate-Lauf ist danach vollständig grün: Root `6164
passed, 246 skipped, 1` upstream Lark-Warnung und Bot `271 passed, 2`
upstream Lark-Warnungen. Zusätzlich wurde der Word-Parser-Test-Helfer gegen
eine process-globale `asyncio.to_thread`-Patch-Verfälschung isoliert; kein
Produktionscode war dafür erforderlich. H1/H2, OpenClaw-P0 und Provider-Live
bleiben weiterhin HOLD.
