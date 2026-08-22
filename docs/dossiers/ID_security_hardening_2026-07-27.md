# ID: Security Hardening 2026-07-27

## Änderungsumfang auf Dateiebene

| Paket | Dateien | Änderung | Testbeweis |
|---|---|---|---|
| Frontend P0 | `web-studio/src/routes/resources/-components/file-preview.tsx`, neue nahe Vitest-Datei | Pure URL-Klassifikation: erlaubte Link-Ziele, lokale Viking-Auflösung, blockierte explizite Schemas. Keine Identity-`urlTransform`; blockierte Links ohne `href`. | Vitest: `javascript:`/kodiert/Whitespace/data/blob blockiert; https/relative/Viking erlaubt |
| Graph P0 | `openviking/session/memory/graph_view.py`, `tests/session/memory/test_graph_view.py` | JS-Template ergänzt `sanitizeMarkdownHref` mit Schema-Allowlist und Attribut-Escaping vor der `innerHTML`-Nutzung; die schmale Funktion wird im gerenderten Script testbar exponiert. | Python ruft einen Node-VM-Harness auf, der den generierten JS-Pfad ausführt und schädliche/sichere Link-Ausgabe prüft |
| WebDAV P1 | `openviking/server/config.py`, `openviking/server/routers/webdav.py`, `tests/server/test_api_webdav.py` | `webdav_max_body_bytes=16*1024*1024`, Content-Length-Frühprüfung, begrenztes Lesen über `request.stream()`, 413 vor Speicher-Schreibpfad. | 16 MiB Erfolg, 16 MiB+1 und Chunked/fehlende Länge 413 |
| Public Deploy P1 | neuer enger Resolver `openviking/server/public_url.py`, `config.py`, `app.py`, `oauth/router.py`, `mcp_endpoint.py`, passende Server-/OAuth-/MCP-Tests | Zentraler Validierer/Resolver: non-loopback benötigt explizite HTTPS-Basis und konkrete Origins; OAuth/MCP verwenden dieselbe explizite Basis; abweichender OAuth-Issuer ist Fehler vor dem bisher breit abgefangenen OAuth-Registrierungsblock. | Konfigurations-, Header-Manipulations- und Konsistenztests |
| Dependencies/CI P1/P2 | Package- und Lockdateien nur bei kompatiblem Fix; `.github/workflows/*`, `.github/security-audit-baseline.json`, enger Baseline-Verifier, EN/ZH Deployment-/Konfigurationsbeispiele | Paketweise Upgrade, Scan-Schritte für Python/Node/Rust, keine globale Scanner-Ausnahme; verifizierte verbleibende Advisory-Baseline mit Ablaufdatum. | Audit, Build/Test pro Ökosystem, Workflow-Validierung |

## Algorithmus- und Vertragsdetails

### URL-Klassifikation

1. Eingabe trimmen und einmal defensiv percent-dekodieren, nur um ein vorhandenes Schema zu erkennen.
2. Leerwert und Fragment bleiben nicht-externe Navigation.
3. Ein explizites Schema ist nur erlaubt, wenn es in der jeweiligen Allowlist steht. `javascript`, `vbscript`, `data`, `blob`, `file` und jedes unbekannte Schema werden **blocked**.
4. Ohne explizites Schema wird ausschließlich ein relativer Viking-Pfad aus `fileUri` gebildet. Dadurch wird kein unbekanntes Schema als lokaler Pfad fehlinterpretiert.
5. Blocked wird im Link-Renderer ohne `href` ausgegeben. Bildquellen werden nicht durch die Link-Allowlist erweitert.

### WebDAV

`Content-Length > limit` gibt 413 zurück, bevor der Stream gelesen wird. Andernfalls werden Chunks gezählt und höchstens bis zum Limit gepuffert. Beim ersten überschreitenden Chunk wird 413 zurückgegeben; `decode`, `write_file` und die nachgelagerte Verarbeitung laufen nicht. Der vorhandene String-Persistenzvertrag bleibt unverändert.

### Öffentliche Konfiguration

- Als loopback gelten `127.0.0.1`, `::1` und `localhost`; alle übrigen Werte, inklusive `0.0.0.0` und `::`, sind öffentlich.
- Lokaler Default: `cors_origins=[]`; Same-Origin funktioniert ohne CORS-Header.
- Öffentlicher Modus: jede Origin ist eine absolute HTTP(S)-Origin ohne Pfad, Query, Fragment oder Credentials; Wildcards sind verboten. `public_base_url` ist absolute HTTPS-Origin ohne Pfad/Query/Fragment/Credentials.
- `public_url.py` stellt `validate_public_deployment_config`, `resolve_configured_public_base_url` und die Origin-Validierung bereit. `create_app` lädt den OAuth-Kontext wie bisher über `get_openviking_config()` und ruft die Cross-Config-Prüfung nach diesem Laden, aber vor dem OAuth-`try/except`, auf. Tests monkeypatchen genau diesen Loader für direkte `create_app(config=...)`-Aufrufe und beweisen, dass die Ausnahme hochläuft.
- Der explizite Env-Override ist ebenfalls zu validieren. Wenn OAuth-Issuer und effektive öffentliche Basis konkurrieren, ist das ein Konfigurationsfehler. Für OAuth wird die Resolver-Origin zugleich für SDK-Routen, Metadaten und Upload-Instruktionen verwendet.
- Erst nach dieser Validierung dürfen MCP/OAuth explizite Werte verwenden; öffentliche Header-Fallbacks sind untersagt. Lokale Header-Fallbacks bleiben klar getrennt.
- Bei 413 protokolliert der Server ausschließlich Limit, deklarierte/gezählte Größe und Request-Metadaten ohne Inhalt; die Konfigurationsvalidierung nennt das fehlerhafte Feld ohne Secrets.

## Sequenz, Abhängigkeiten und Rollback

Zuerst die drei voneinander unabhängigen Source-Slices (Frontend, Graph, Server). Danach isoliert Dependency/CI, damit Lockfile-Diffs die Ursachenanalyse nicht verdecken. Nach jedem Slice: fokussierte Tests; nach allen: Gesamtgates und Scanner.

Rollback ist ein Revert des einzigen Branch-Commits. Kein Schema, keine Daten, keine Laufzeitkonfiguration und kein Prozess wird während der Umsetzung verändert.

## Schnittstellen- und Nebenwirkungsprüfung

- `ServerConfig` wird von `create_app` vor Middleware und Router-Initialisierung validiert; dies ist der zentrale Durchsetzungspunkt.
- `mcp_endpoint` und OAuth haben bisher verschiedene URL-Auflösungen. Der Fix muss die Explizit-Werte mit gleicher Priorität nutzen, darf lokalen Host-Fallback aber nur lokal bewahren.
- `request.stream()` ist einmalig konsumierbar; nur die WebDAV-PUT-Route wird umgestellt und erhält keinen späteren `request.body()`-Zugriff.
- ReactMarkdown-Komponenten erhalten weiterhin relative und Viking-URLs, dürfen aber keine gesperrten Ziele in einen Anchor übernehmen.
- Die OAuth-Routerregistrierung darf keine Konfigurationsfehlermeldung in ein bloßes `Skipping ...` umwandeln. Der schmale Import-/optional-dependency-Fehlerpfad bleibt getrennt, aber eine validierte Konfiguration scheitert laut.

## Dependency-Entscheidungsmatrix

| Ökosystem | Kandidat für kompatiblen Fix | Regel bei Erfolg | Regel bei fehlendem kompatiblen Fix |
|---|---|---|---|
| Python | `litellm` innerhalb `<1.91.2`, `aiohttp`, `mcp`, `json-repair`, `pydantic-settings`, `PyJWT` innerhalb der bestehenden Constraints | Lockfile gezielt aktualisieren, fokussierte Tests und `pip-audit` ausführen | Advisory-ID, Pfad, Fixversion und Neubewertung in Open Items |
| Web Studio | `axios` innerhalb `^1.14.0` und transitive production-fähige Patch/Minor-Fixes | `package-lock.json` gezielt aktualisieren, Vitest/Lint/Build/audit | keine Major-/`latest`-Umschreibung ohne eigene Freigabe; als reproduzierbares P2-Risiko dokumentieren |
| Bot Node | `shell-quote`, `lodash-es` nur über kompatible Elternpaket-Updates | Lockfile und Audit prüfen | kein Testskript wird als Runtime-Beweis ausgegeben; Grenze dokumentieren |
| Rust | `quinn-proto >=0.11.15`, kompatible `rustls-webpki`-Patchstände, sofern Cargo-Auflösung dies ohne Major-Wechsel zulässt | `cargo update` gezielt, `cargo test` und `cargo audit` | PyO3 >=0.29, ungepatchtes RSA, gix-/transitive Major-Pfade bleiben explizite Blocker |

Die CI verwendet keine blanket ignore. Die committed Datei `.github/security-audit-baseline.json` ist ein JSON-Array; jedes Element enthält exakt `id`, `ecosystem`, `package_path`, `scanner_command`, `scanner_version`, `expires_on`, `owner`, `rationale` und `removal_condition`. Der enge Verifier nimmt die JSON-Outputs der drei Scanner entgegen und schlägt fehl, wenn ein Befund neu ist, sein Paketpfad vom Baseline-Eintrag abweicht, ein Eintrag fehlt oder sein Ablaufdatum überschritten ist. Eine verbleibende Baseline ist nur pro Advisory mit Neubewertung am 2026-08-27 zulässig.
