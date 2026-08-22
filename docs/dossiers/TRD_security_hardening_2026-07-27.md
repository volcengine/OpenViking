# TRD: Security Hardening 2026-07-27

## Technische Anforderungen

| Bereich | Soll-Verhalten | Nachweis |
|---|---|---|
| SEC-001 | Web Studio gibt für unzulässige Markdown-URLs keinen aktiven Link aus. Erlaubte externe Ziele und interne Viking-Downloads funktionieren. | Komponenten-/Unit-Test und Production-Build |
| SEC-002 | Graph-HTML lässt aus gespeicherten Markdown-Links keinen `javascript:`/`data:`-Href entstehen. | Python-Regression, HTML-String-Assertions |
| SEC-003 | PUT > 16 MiB, mit Content-Length oder chunked, endet mit 413 ohne Vollpufferung. | ASGI-Tests für beide Wege |
| SEC-004 | Nicht-loopback-Server akzeptiert weder `*` noch leere CORS-Origins; der lokale Default ist leer. | Konfigurationsmodell-Tests |
| SEC-005 | Nicht-loopback-Server braucht absolute HTTPS-`public_base_url`; gesetzte Basis schlägt Host/Forwarded-Header, und ein abweichender OAuth-Issuer ist ein Fehler. | MCP/OAuth-Konfigurations- und URL-Tests |
| SEC-006/007 | Kompatible Fixes werden gelockt und neu gescannt; Restbefunde sind nachvollziehbar dokumentiert. | Audit-Ausgaben und Build/Test pro Ökosystem |
| SEC-008 | GitHub Actions führt Node- und Rust-Dependency-Scans zusätzlich zur vorhandenen Analyse aus. | Workflow-Syntax und CI-Command-Review |

## Kompatibilitätsregeln

- Loopback-Defaults bleiben für lokale Entwicklung nutzbar.
- `OPENVIKING_PUBLIC_BASE_URL` bleibt eine Betreiberüberschreibung, muss aber eine absolute HTTPS-URL sein, wo sie öffentliche URLs konfiguriert.
- Kein Major-Upgrade als verdeckter Sicherheitsfix; insbesondere PyO3/RSA-Fälle werden nicht erzwungen.
- Keine Prozessneustarts sind Teil dieser Änderung.
- Die aktualisierten englischen und chinesischen Deployment-/Konfigurationsbeispiele dürfen keine öffentliche Wildcard-CORS-Konfiguration mehr empfehlen.

## Rollback

Alle Änderungen liegen in einem dedizierten Commit auf `agent-workflow/20260727-security-hardening`. Rollback erfolgt durch Revert dieses Commits nach Betreiberentscheidung; keine Datenmigration und keine persistenten Datenänderung sind vorgesehen.
