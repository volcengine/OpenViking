# PPD: Security Hardening — Weiterentwicklung

**Stand:** 2026-07-27 · **Voraussetzung:** Keine dieser Vorschläge erweitert die aktuelle Patchserie ohne eigene Freigabe.
**QWF-Regel:** hoher Sicherheitsgewinn, geringer Eingriff und klare Rückrollbarkeit zuerst.

## Delta und QWF

| Rang | Vorschlag | Ist-Zustand | Zielzustand | Nutzen | Aufwand/Risiko |
|---:|---|---|---|---|---|
| 1 | PPD-01 Dependency-Renovation | 74 befristete Ausnahmen | kleine, getestete Update-PRs | hoch | mittel |
| 2 | PPD-02 Öffentliche Konfigurationsprüfung | serverseitig fail-closed | vorab prüfbarer Deployment-Check | hoch | niedrig |
| 3 | PPD-03 Browser-Sicherheitsregression | Unit-Tests, kein E2E-Nachweis | echte DOM-/Proxy-Smokes | hoch | mittel |
| 4 | PPD-04 WebDAV-Quoten | Requestlimit | Nutzer-/Pfad- und Rate-Grenzen | mittel | mittel |
| 5 | PPD-05 Graph-Renderer-Härtung | Sanitizer im String-HTML | CSP/strukturiertes DOM | mittel | hoch |

## PPD-01 — Gestaffelte Dependency-Renovation

**Rationale:** Die 74 verifizierten Ausnahmebefunde müssen vor Ablauf systematisch reduziert werden.

**Pros:** kleine überprüfbare Diffs; verkürzt das Risiko-Fenster; trennt API-Migrationen von Sicherheits-Patches.
**Cons:** mehr PRs; CI-Zeit steigt; transitive Pfade bleiben komplex.
**Risiken und Mitigierungen:**

1. Major-API-Bruch — Kompatibilitätsmatrix erstellen; Upgrade in isoliertem Zweig testen; Rollback-Commit vorbereiten.
2. Lockfile-Drift — nur gezielte Update-Kommandos verwenden; Diff paketweise reviewen; Scanner vor/nachher vergleichen.
3. Ablauf ohne Eigentümer — Owner pro Baseline-Eintrag pflegen; Termin 2026-08-27 im Tracker setzen; CI auf abgelaufene Einträge fail-closed lassen.

## PPD-02 — Preflight für öffentliche Konfiguration

**Rationale:** Fail-closed Verhalten ist stärker, wenn Betreiber die Konfiguration vor dem Start reproduzierbar prüfen können.

**Pros:** Fehler früher sichtbar; weniger Proxy-/Header-Fehlannahmen; dokumentierbarer Release-Gate.
**Cons:** zusätzlicher CLI-Pfad; Konfigurationsvarianten müssen gepflegt werden; kann bestehende Automatisierung anpassen.
**Risiken und Mitigierungen:**

1. Falsch-positive Validierung — lokale und öffentliche Fixtures abdecken; klare Fehlermeldungen liefern; zuerst nur warnenden Dry-Run anbieten.
2. Geheimnisleck in Logs — nur Feldnamen/Typen loggen; keine Werte ausgeben; Logging-Test hinzufügen.
3. Umgehung durch Deployment-Skripte — Preflight im CI-Template verankern; manuelle Checkliste ergänzen; Abweichungen als Open Item erfassen.

## PPD-03 — Browser- und Reverse-Proxy-Sicherheitsregression

**Rationale:** Unit-Tests beweisen keine vollständige DOM- oder Header-Kette.

**Pros:** realistische Stored-XSS-Prüfung; validiert CORS/Origin-Verhalten; findet Integrationsregressionen früh.
**Cons:** längere Laufzeit; Testinfrastruktur nötig; Proxy-Fixtures müssen gewartet werden.
**Risiken und Mitigierungen:**

1. Flaky Browserläufe — feste Testdaten verwenden; Screenshot/Trace bei Fehlern sichern; wiederholbare Container- oder lokales Fixture bereitstellen.
2. Test gegen Produktion — ausschließlich isolierte Test-Origin nutzen; keine Produktivschlüssel; Netzwerkzugriff einschränken.
3. Falsches Sicherheitsgefühl — Unit- und E2E-Gates kombinieren; erwartete Negativfälle prüfen; fehlende Umgebung als blocker reporten.

## PPD-04 — WebDAV-Quoten und Missbrauchstelemetrie

**Rationale:** Das 16-MiB-Requestlimit verhindert eine einzelne Body-Überlastung, ersetzt aber keine Nutzungskontrolle.

**Pros:** bessere DoS-Resistenz; frühere Missbrauchserkennung; nachvollziehbare Kapazitätsplanung.
**Cons:** Zustandsverwaltung; mögliche legitime Upload-Ablehnungen; Metrikbetrieb kostet Ressourcen.
**Risiken und Mitigierungen:**

1. Falsche Quoten-Sperren — konservative Defaults wählen; Admin-Ausnahmeprozess; Ablehnungen mit limitbezogenen Metriken beobachten.
2. Datenschutz durch Telemetrie — keine Inhalte loggen; pseudonymisierte Dimensionen; Aufbewahrungsgrenze definieren.
3. Bypass über andere Routen — Upload-Pfade inventarisieren; gemeinsame Limit-Helfer verwenden; Regressionstests pro Route ergänzen.

## PPD-05 — Graph-Renderer in strukturiertes DOM mit CSP überführen

**Rationale:** Das aktuelle Sanitizing reduziert Risiko, aber String-basiertes HTML bleibt ein anspruchsvoller Vertrauensbereich.

**Pros:** kleinere XSS-Angriffsfläche; bessere Browser-Sicherheitsrichtlinie; testbarere Rendering-Schnittstellen.
**Cons:** erheblicher Umbau; CSP kann Integrationen stören; Rendering-Performance muss geprüft werden.
**Risiken und Mitigierungen:**

1. Funktionsverlust im Graph — Golden-HTML-/DOM-Fixtures erstellen; Feature-Flag für kontrollierten Rollout; Visual-Regressionen ausführen.
2. CSP bricht externe Ressourcen — explizite Source-Liste; Report-Only-Phase; Drittanbieterinventar vor Enforcement.
3. Unvollständige Sanitizer-Migration — alle `innerHTML`-Sinks suchen; Code-Review mit Security-Veto; Negativtests für Schemas und Attribute.

## Entscheidungs- und Messgates

- PPD-01 beginnt vor Ablauf der Baseline; Erfolg: Zahl der Ausnahmen sinkt nachweisbar und kein neuer Befund entsteht.
- PPD-02/PDD-03 dürfen nur mit nicht-sekreten Testwerten arbeiten; Erfolg: lokale und öffentliche Konfigurationspfade sowie Browser-Negativfälle sind reproduzierbar.
- PPD-04/PPD-05 sind Architekturarbeit, kein stilles Patch-Add-on; Start nur mit akzeptiertem Dossier und eigenem Rollbackplan.

Die vorgeschlagenen Maßnahmen wurden nicht implementiert und verändern keine Laufzeitkonfiguration.
