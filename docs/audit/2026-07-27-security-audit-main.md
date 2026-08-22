# Sicherheits-Audit der synchronisierten Main-Fassung

**Prüfdatum:** 2026-07-27
**Geprüfte Basis:** `60ef45d4c3a7d07ceb1df4e9d7dde7a14449ac50` (`origin/main`)
**Methode:** gezielte Quellcode- und Konfigurationsprüfung, `pip-audit`, `npm audit` und `cargo audit`.
**Geltung:** Dieses Dokument hält den Befund vor der Behebung fest. Der Implementierungsstand und verbleibende Risiken werden im Session Transfer Protocol dokumentiert.

## Ergebnisübersicht

| ID | Priorität | Befund | Ziel der Behebung |
|---|---|---|---|
| SEC-001 | P0 | Stored XSS über Markdown-Links im Web Studio | Nur freigegebene URL-Schemas; lokale Viking-URIs explizit auflösen |
| SEC-002 | P0 | Stored XSS über Graph-HTML-Markdown-Links | HTML-escapen und Link-Ziele strikt erlauben |
| SEC-003 | P1 | WebDAV liest PUT-Bodies unbegrenzt in den Speicher | Früh- und Streaming-Limit von 16 MiB, Antwort 413 |
| SEC-004 | P1 | Wildcard-CORS mit Credentials | Sichere lokale Defaults; öffentliche Bindung nur mit konkreter Allowlist |
| SEC-005 | P1 | Öffentliche URLs können aus Host/Forwarded-Headern entstehen | Öffentlicher Betrieb benötigt explizite `public_base_url` |
| SEC-006 | P1 | Python- und Node-Abhängigkeiten mit Advisories | Kompatible Updates; nicht kompatible Fixes als Blocker |
| SEC-007 | P1 | Rust-Abhängigkeiten mit Advisories | Kompatible Updates; PyO3/RSA u. a. als Blocker dokumentieren |
| SEC-008 | P2 | Kein vollständiger Dependency-Scan für Node/Rust in CI | Reproduzierbare Scans in GitHub Actions ergänzen |

## Wesentliche Nachweise

- `web-studio/src/routes/resources/-components/file-preview.tsx` leitete in der Auto-Preview `urlTransform={(url) => url}` an ReactMarkdown weiter und behandelte unbekannte Schemas als lokale Viking-Pfade. Dadurch können schädliche Link-Schemas im gespeicherten Markdown aktiv bleiben.
- `openviking/session/memory/graph_view.py` rendert Markdown in `innerHTML`; Links wurden mit dem unvalidierten `href` zusammengesetzt.
- `openviking/server/routers/webdav.py` verwendete bei PUT `await request.body()` ohne Begrenzung. Die reguläre temporäre Upload-Route hatte dagegen bereits Limits.
- `openviking/server/config.py` defaultete `cors_origins` auf `['*']`; `openviking/server/app.py` kombinierte dies mit `allow_credentials=True`.
- `openviking/server/mcp_endpoint.py` und `openviking/server/oauth/router.py` fielen nach Headern auf die Listen-Adresse zurück. Das ist für eine öffentliche, reverse-proxied Bereitstellung kein belastbarer Vertrauensanker.
- Die Scanner meldeten vor dem Update u. a. Advisories für `litellm`, `aiohttp`, `mcp`, `axios`, `shell-quote`, `lodash-es`, `quinn-proto`, `pyo3`, `rustls-webpki` und RSA. Ihre tatsächliche Restlage wird nach den kompatiblen Updates erneut erhoben.

## Grenzen

Dieses Audit ist keine externe Penetrationstest-Bescheinigung. Es ersetzt weder eine produktionsnahe Proxy-Konfigurationsprüfung noch einen Test mit echten Betreiber-Origin-Werten. Solche fehlenden Umweltbelege werden fail-closed als offene Punkte ausgewiesen.
