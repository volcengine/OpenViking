# ARD: Security Hardening 2026-07-27

## Ziel und Erfolgskriterien

Die synchronisierte Main-Fassung wird gegen die Befunde SEC-001 bis SEC-008 gehärtet. Erfolg bedeutet: gespeicherter Inhalt kann keine nicht erlaubten Link-Schemas aktivieren, WebDAV nimmt höchstens 16 MiB Text pro PUT an, ein öffentlich gebundener Server verlangt konkrete Betreiberwerte für CORS und öffentliche URLs, und kompatibel aktualisierbare Abhängigkeiten sowie reproduzierbare Scans sind integriert.

Nicht Ziele sind ein Proxy-Redesign, eine Major-Version-Aktualisierung oder das Unterdrücken verbleibender Scannerbefunde.

## Architektur- und Vertrauensgrenzen

```text
gespeicherter Markdown ──> Web Studio / Graph-HTML ──> Browser DOM
Client/Proxy ──> ASGI Server ──> CORS / OAuth / MCP veröffentlichte URL
WebDAV Client ──> Request-Stream ──> Speicher / Resource Store
Lockfiles ──> Build und CI-Scanner ──> veröffentlichbarer Artefaktstand
```

Untrusted Inputs sind Markdown-Inhalt, URL-Schemas, WebDAV-Request-Bytes sowie Host-/Forwarded-Header. Betreiberkonfiguration (`host`, `cors_origins`, `public_base_url` und Umgebungsüberschreibung) ist der einzige Vertrauensanker für öffentliche URL-Ausgabe.

## Verbindliche Architekturentscheidungen

1. **Link-Policy:** nur `https`, `http`, `mailto`, `tel` und kontrolliert erzeugte `viking://`-Ziele sind Links. `data:` und `blob:` sind kein anklickbares Ziel; unbekannte sowie percent-kodierte gefährliche Schemas werden inert. Der Scope dieser Änderung sind Links: Bilder behalten ReactMarkdowns Standard-Sanitisierung, und die globale Identity-Transformation wird entfernt.
2. **Graph-Renderer:** Markdown bleibt textbasiert escaped. Ein Link wird nur nach Schema-Validierung erzeugt, ansonsten als Text gerendert; keine Content-Zeile darf ein fremdes `href` in `innerHTML` einbringen.
3. **WebDAV:** `Content-Length` oberhalb 16 MiB wird vor dem Lesen mit 413 abgewiesen. Unbekannte/chunked Längen werden mit einem begrenzten Stream gelesen und bei Überschreitung ebenfalls mit 413 beendet.
4. **Deployment-Modus:** Loopback-Bindung ist lokal und hat eine leere CORS-Standardliste. Jede nicht-loopback Bindung verlangt mindestens eine nicht-leere, explizite CORS-Allowlist ohne Wildcards sowie eine absolute HTTPS-`public_base_url`; fehlende Werte sind Konfigurationsfehler.
5. **Öffentliche URLs:** Ein gemeinsamer, validierter Resolver ist die alleinige Quelle für App-Start, MCP und OAuth. Bei öffentlichem Betrieb sind Header kein Fallback. Bei lokalem Betrieb bleiben Host-/Listen-Fallbacks aus Kompatibilitätsgründen möglich; explizite Werte dominieren immer.
6. **Abhängigkeiten:** Nur semver-kompatible Updates. Nicht kompatibel lösbare Advisories bleiben mit Paket, Pfad, Scanner-Output und Upgrade-Voraussetzung im Open-Item-Report.
7. **OAuth-Konsistenz:** Wenn `oauth.issuer` und die effektive öffentliche Basis beide explizit gesetzt sind, müssen ihre Origins übereinstimmen; ein Konflikt wird vor dem bisher breit abgefangenen OAuth-Router-Block zum Startfehler statt einer uneinheitlichen Metadaten-/Token-Origin.
8. **Beobachtbarkeit:** Abgewiesene übergroße WebDAV-Requests erhalten eine sichere Warnung ohne Body/Secrets; ungültige Public-Konfiguration erzeugt einen präzisen Startdiagnosefehler.

## Risiken und mindestens drei Gegenmaßnahmen je Risiko

| Risiko | Gegenmaßnahmen |
|---|---|
| Strenge Konfiguration legt fehlerhafte Deployments offen | (1) nur nicht-loopback validieren, (2) präzise Validierungsfehler, (3) Beispielkonfiguration und Handbuch, (4) Konfigurationstests |
| Streaming-Änderung bricht WebDAV-Clients | (1) Grenze auf klare 16 MiB festlegen, (2) Content-Length- und Chunked-Tests, (3) bestehende PUT-Regressionen, (4) 413 als explizite Antwort |
| Link-Sanitisierung bricht lokale Verweise | (1) `viking://` explizit unterstützen, (2) relative Viking-Pfadtests, (3) externe erlaubte Links getrennt testen, (4) unbekannte Schemata fail-closed |
| Dependency-Updates verursachen API-/Build-Bruch | (1) nur kompatible Range/Lockfile-Schritte, (2) pro Ökosystem Audit plus Build/Test, (3) Major-Fixes dokumentieren statt erzwingen, (4) Rollback durch einzelnen Commit |
| CI-Scanner sind in der Umgebung nicht ausführbar | (1) lokale Syntax-/Tool-Prüfung, (2) klarer Workflow-Pfad, (3) exakte CI-Kommandos, (4) offener Punkt statt Erfolgsbehauptung |

## QWF

`Auditbasis → ARD/TRD/PD → ID → 95%-Simulation → TD → P0/P1-Fixes → Dependency/CI → Tests → Open Items/STP/Diary/Manual/PPD → Commit/Push/Draft PR`.

Gates: kein Implementierungsstart vor ID/Simulation/TD; keine PR vor grünen ausführbaren Tests, dokumentierten Blockern und abschließendem Sicherheitsreview.
