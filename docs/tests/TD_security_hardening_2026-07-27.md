# TD: Security Hardening 2026-07-27

## Teststrategie und Zuordnung

| ID | Test | Typ | Bestehen |
|---|---|---|---|
| T01 | `javascript:`-Link ist nicht klickbar | Vitest | kein `href` mit gefährlichem Schema |
| T02 | Whitespace/Großschreibung/percent-kodiertes gefährliches Schema ist gesperrt | Vitest | alle Varianten inert |
| T03 | https, mailto, tel, relativer und `viking://`-Link bleiben korrekt | Vitest | erlaubte Navigation bleibt erhalten |
| T04 | Node-VM-Harness führt den aus `_render_graph_html` erzeugten Sanitizer für gefährliche Markdown-Ziele aus | pytest + Node | schädlicher `href` ausgeschlossen |
| T05 | Node-VM-Harness führt den Graph-Sanitizer für Viking/externe Ziele und Quote-Escaping aus | pytest + Node | erlaubter Link und Escape-Nachweis |
| T06 | WebDAV exakt 16 MiB nimmt gültigen UTF-8-Text an | pytest/ASGI | Erfolgsantwort und gespeicherter Inhalt |
| T07 | WebDAV 16 MiB + 1 mit Content-Length gibt 413 und schreibt nichts | pytest/ASGI | 413, kein Seiteneffekt |
| T08 | Kontrollierter ASGI-Receive-Stream ohne/falsche Länge und grenzüberschreitenden Chunks kann die Streamgrenze nicht umgehen | pytest/ASGI | 413 vor Write/Summarize |
| T09 | Lokale Default-Konfiguration bleibt gültig und CORS ist nicht wildcard | pytest | Validierung erfolgreich, explizit leer |
| T10 | Öffentliche Bindung ohne Base/Origins sowie mit Wildcard scheitert | pytest | Validierung/Start fail-closed |
| T11 | Gültige HTTPS-Basis dominiert MCP/OAuth trotz Host/Forwarded | pytest/ASGI | konsistente URL, Header wirkungslos |
| T12 | Abweichender OAuth-Issuer ist ein Konfigurationsfehler | pytest | fail-closed |
| T13 | Kompatible Python-Updates bestehen fokussierte Python-Gates und `pip-audit` | CLI | exit 0 oder dokumentierter Restbefund |
| T14 | Web-Studio Unit/Lint/Build und Production-Dependency-Audit | CLI | alle Commands grün; Restbefund dokumentiert |
| T15 | Rust-Test/Build und `cargo audit` | CLI | alle Commands grün; Restbefund dokumentiert |
| T16 | GitHub Actions YAML/Scancommands | statisch + CI | gültige Workflow-Datei, kein globales `continue-on-error` |
| T17 | EN/ZH Deployment- und Konfigurationsbeispiele haben keine öffentliche Wildcard-CORS-Empfehlung | Text-Regression | sichere Beispielwerte und Resolver-Regel |
| T18 | Baseline-Verifier akzeptiert nur exakt dokumentierte, nicht abgelaufene Scannerbefunde und verweigert neue/abgelaufene/verschobene Befunde | Unit/CLI | fail-closed JSON-Vertrag |

## Ausführungsreihenfolge

1. T01–T05 nach den P0-Slices.
2. T06–T12 nach dem Server-Slice.
3. T13–T17 nach den Dependency-/CI-Slices.
4. Danach die vorhandenen betroffenen Python-Suiten: `tests/server/test_api_webdav.py`, `tests/server/test_mcp_endpoint.py`, `tests/server/oauth/test_router.py`, `tests/session/memory/test_graph_view.py`, `tests/test_config_loader.py` sowie die jeweils neuen Tests.

Vorgesehene Commands werden erst gegen die tatsächlich vorhandenen Package-Skripte und Lockfiles verifiziert. Ein fehlender Browser-/Credential-/CI-Runtime-Nachweis wird nicht als bestanden gezählt; er wird mit exaktem Wiederholungsbefehl im Open-Item-Report geführt.

## Assertion-Regeln

Sicherheitsregressionen prüfen Negativ- **und** Positivfälle: nicht nur, dass ein Helper aufgerufen wird, sondern dass der endgültige HTML/DOM- bzw. HTTP-Pfad keine gefährliche Wirkung hat und eine gültige Nutzung weiter funktioniert. Keine bestehende Assertion wird gelockert, um einen Fix grün zu machen.
