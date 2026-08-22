# Product Proposal Dossier

## Nächste Schritte für Codex-Compaction und Responses State

Stand: 2026-07-31
Status: Vorschläge; keine Freigabe zur Umsetzung oder Aktivierung

## 1. Delta zum Kandidaten

| Bereich | Kandidat | Vorgeschlagene Erweiterung |
|---|---|---|
| Qualität | Deterministische Offline-Tests | Repräsentativer A/B-Corpus |
| Capability | Expliziter Probe vorhanden | Genehmigter, auditierbarer Live-Nachweis |
| Reducer | Beispiel- und Contract-Tests | Property-/Mutationstests |
| Betrieb | Harte Limits und typisierte Fehler | Inhaltsfreie Aggregatmetriken |
| Architektur | Codex-spezifischer Pilot | Erst evidenzbasiert abstrahieren |

QWF: Security-HOLD respektieren → erst nach gesonderter künftiger Freigabe
A/B-Corpus → Capability-Evidenz → Reducer-Härtung → sichere Metriken →
Provider-Abstraktion.

Der finale Legacy-VLM-H3-Security-Review endet nach drei Revisionen bei
**89/100, 0C/1H/1M**. H2–H5 sind geschlossen, H1 bleibt exakt offen. Das Gate
`0C/0H` und mindestens 90/100 ist verfehlt; Source-Unlock wurde verweigert und
in diesem Lauf folgt keine weitere H1-Schließungsrevision. OpenViking MCP Health
und echter read-only `search_experience` sind PASS, aber kein Provider-
Capability-Beleg. Der User hat den Live-Test vertagt.

## 2. Vorschlag 1 — Reproduzierbarer A/B-Corpus

Rationale: Ohne identische Long-Horizon-Aufgaben ist keine belastbare Wahl
zwischen 206720/total, gehärtetem Hook und 200000-Kandidaten möglich.

Vorteile:

1. Direkter Nachweis von Qualität, Tokens, Latenz und Fehlern.
2. Reproduzierbare Promotionsentscheidung statt Benchmark-Übertragung.
3. Regressionen werden auf realen und synthetischen Verläufen sichtbar.

Nachteile:

1. Sanitierung und Freigabe realer Sitzungen kosten Zeit.
2. Nichtdeterministische Fälle benötigen Dreifachläufe.
3. Corpus-Pflege wird zu einer dauerhaften Aufgabe.

| Risiko | Drei Mitigationen |
|---|---|
| Vertrauliche Inhalte | Sanitierung; Freigabereview; keine Rohdaten in Artefakten |
| Unfaire Varianten | identische Inputs; feste Metriken; randomisierte Reihenfolge |
| Überanpassung | separater Holdout; synthetische Randfälle; periodischer Refresh |

## 3. Vorschlag 2 — Auditierbarer Capability-Recorder

Rationale: Der exakte Codex-Endpunkt muss `context_management`,
Compaction-Items und Replay tatsächlich unterstützen, ohne Credentials oder
Responses in Dossiers zu persistieren.

Vorteile:

1. Endpoint-Fähigkeit wird eindeutig statt implizit belegt.
2. Unsupported Features bleiben fail-closed.
3. Freigabe kann auf minimales, strukturiertes Ergebnis verweisen.

Nachteile:

1. Probe und Canary können Providerkosten verursachen.
2. Capability kann sich zeitlich ändern.
3. Ein Recorder erweitert die Security-Angriffsfläche.

| Risiko | Drei Mitigationen |
|---|---|
| Unautorisierte Kosten | explizite Genehmigung; Request-Budget; kein Retry nach Event |
| Secret-Leak | nur boolesche/aggregierte Evidenz; Log-Sentinels; Credential-Redaction |
| Veraltete Evidenz | Timestamp und Origin-Bindung; kurze Gültigkeit; Re-Probe vor Promotion |

## 4. Vorschlag 3 — Property- und Mutationstests für Reducer

Rationale: Beispieltests decken bekannte Fälle ab; der State-Reducer benötigt
zusätzlich generierte Reihenfolgen und gezielte Mutationen seiner Invarianten.

Vorteile:

1. Unerwartete Item-Kombinationen werden systematisch geprüft.
2. Tests beweisen, dass Schutzbedingungen tatsächlich wirksam sind.
3. Compaction- und Tool-Call-Regeln werden langfristig stabiler.

Nachteile:

1. Generatoren können schwer verständliche Fehlerfälle erzeugen.
2. Mutationstests verlängern den CI-Lauf.
3. Shrinking und Seeds benötigen Pflege.

| Risiko | Drei Mitigationen |
|---|---|
| Flaky Tests | feste Seeds; reproduzierbare Shrinks; getrennte Slow-Suite |
| Falsche Orakel | invariantenbasierte Orakel; Review; Referenzbeispiele |
| CI-Kosten | Zeitbudget; gezielte Mutanten; Nightly-Ausführung |

## 5. Vorschlag 4 — Inhaltsfreie Betriebsmetriken

Rationale: Limits und Fehler sind implementiert, aber ein Canary braucht
aggregierte Sicht auf Latenz, Tokenmengen, Compactions und Fehlerraten, ohne
opaque State-Inhalte offenzulegen.

Vorteile:

1. Promotionsgrenzen werden messbar.
2. Ressourcenprobleme werden früh sichtbar.
3. Security bleibt mit inhaltsfreien Kennzahlen vereinbar.

Nachteile:

1. Auch Metadaten können korrelierbar sein.
2. Zusätzliche Instrumentierung erhöht Komplexität.
3. Falsch gewählte Buckets können Diagnosen erschweren.

| Risiko | Drei Mitigationen |
|---|---|
| Re-Identifikation | grobe Buckets; keine Chain-ID; kurze Retention |
| Cardinality-Explosion | feste Labels; Limits; Drop unbekannter Dimensionen |
| Sensitive Fehlertexte | Fehlercodes statt Texte; Allowlist; Sentinel-Tests |

## 6. Vorschlag 5 — Provider-neutrale Abstraktion nach dem Pilot

Rationale: Eine Generalisierung ist erst sinnvoll, wenn der Codex-Pilot reale
Wiederverwendungsmuster belegt. Bis dahin bleibt die Logik provider-spezifisch.

Vorteile:

1. Spätere Provider können einen geprüften Vertrag wiederverwenden.
2. Gemeinsame Tests reduzieren langfristige Duplikation.
3. Klare Capability-Schnittstellen vermeiden Ad-hoc-Fallbacks.

Nachteile:

1. Zu frühe Abstraktion würde unbekannte Providerunterschiede glätten.
2. Migration kann öffentliche Typen verändern.
3. Gemeinsame Basisklassen erhöhen Kopplung.

| Risiko | Drei Mitigationen |
|---|---|
| Leaky abstraction | erst Pilotdaten; provider-spezifische Capabilities; kein kleinster Nenner |
| Legacy-Bruch | additive Schnittstelle; Deprecation-Plan; Contract-Suite |
| Scope-Wachstum | separates Dossier; eigenes Gate; keine Umsetzung im aktuellen Kandidaten |

## 7. Priorisierung

| Rang | Vorschlag | Voraussetzung | Entscheid |
|---:|---|---|---|
| 0 | Offline-HOLD-Lift | User-Autorisierung und Security Rev2 | PASS; 96/100, 0C/0H/1M |
| 1 | A/B-Corpus | Sanitierte, freigegebene Daten | HOLD |
| 2 | Capability-Recorder | Security-Unlock plus Kosten-/Live-Genehmigung | User hat Live-Test vertagt |
| 3 | Property-/Mutationstests | Security-Unlock und Offline-Budget | HOLD |
| 4 | Inhaltsfreie Metriken | Canary-Konzept | Später |
| 5 | Provider-Abstraktion | Erfolgreicher Pilot | Zurückstellen |

Keiner dieser Vorschläge autorisiert Provider-Aufrufe, Aktivierung, Restart oder
Default-Promotion.

## 8. Nachtrag zum Offline-HOLD-Lift

Der neue Offline-Zyklus schloss H1, H6 und M2 test-first. Der gezielte Satz
bestand 272/272; Security Rev2 bestand mit 96/100 und ohne Critical oder High.
Damit ist der Offline-HOLD aufgehoben. Die acht vorbestehenden VolcEngine-
Konstruktorfehler bleiben ein separater Legacy-Baselinebefund. Capability-Probe,
Canary, Aktivierung und Promotion bleiben bis zum späteren Live-Test HOLD.

## 9. Verweise

- [Implementation Dossier](../dossiers/2026-07-31-codex-compaction-openviking-responses-id.md)
- [Lessons Learned](../lessons/2026-07-31-codex-compaction-openviking-responses-lessons-learned.md)
- [Open Items](../sessions/2026-07-31-codex-compaction-openviking-responses-open-items.md)
