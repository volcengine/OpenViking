# Planning Document

## Optimierung von Codex-Compaction und OpenViking Responses

Stand: 2026-07-31
Status: Offline Legacy-VLM HOLD aufgehoben; Live M1 und Promotions-Gates auf HOLD

## 1. Ziel und Erfolgskriterien

Das Vorhaben sollte lokale Codex-Compaction sicherer machen und OpenViking um
einen expliziten, aufruferverwalteten Responses-State erweitern. Erfolg bedeutet:

- additive Implementierung ohne Änderung des Legacy-Defaults;
- keine Cross-Chain-Leaks oder ungebundene Tool-Ausgaben;
- keine Veröffentlichung eines Teil-States;
- Hook-Sicherheit gegen Rechte-, Symlink-, Parallelitäts-, Timeout- und
  Prompt-Injection-Risiken;
- verifizierte Tests und dokumentierter Rollback;
- keine Aktivierung oder Promotion ohne gesonderte Evidenz.

Der Kandidat erfüllt die implementierbaren Offline-Kriterien. Die empirischen und
Live-Kriterien sind offen und bleiben fail-closed.

## 2. Quick-Win-First-Ausführung

| Reihenfolge | Arbeitspaket | Ergebnis |
|---:|---|---|
| 1 | Hook-Sicherheit und Backup | Kandidat implementiert; globale Dateien unverändert |
| 2 | Baseline und Capability-Grenze | Baseline-Fehler reproduziert; Live-Probe nicht autorisiert |
| 3 | Lokale A/B-Messung | **HOLD:** Corpus 20 real + 10 synthetisch fehlt |
| 4 | State-Vertrag | Implementiert und getestet |
| 5 | Tests | 102 neue Tests bestanden |
| 6 | Adapter und Config | Implementiert; Default bleibt aus |
| 7 | Canary | **HOLD:** potenziell kostenpflichtig, Genehmigung erforderlich |
| 8 | Promotion | **HOLD:** keine Freigabeevidenz |

Die Reihenfolge verhinderte, dass Live-Aufrufe oder globale Änderungen als
Voraussetzung für die sichere Offline-Implementierung behandelt wurden.

## 3. Phasenstatus

### Phase 0 — Isolation, Dossiers und Hook

Erledigt:

- isolierter Worktree auf Basis `60ef45d4`;
- globale Codex-Dateien vorab gesichert und SHA-256-verifiziert;
- quellkontrollierter Hook mit privaten Rechten, atomarem Schreiben,
  komponentenweiser Symlink-Prüfung, fester Ausgabe und Ressourcenlimits;
- 30 Hook-Tests bestanden, einschließlich Directory-FD-, Deadline- und
  Retention-Grenzen.

Nicht durchgeführt:

- Installation oder Aktivierung des Hooks;
- Restart eines Rechners, Servers, Dienstes oder einer Runtime.

### Phase 1 — Baseline, A/B und Capability

Erledigt:

- aktuelle Baseline-Ausfälle auf dem unveränderten Checkout reproduziert;
- Vergleichskandidaten und Promotionsmetriken definiert;
- expliziter Capability-Probe implementiert.

HOLD:

- keine 20 sanitisierten realen Langsitzungen und 10 synthetischen Szenarien;
- kein Live-Probe am exakten Codex-Endpunkt;
- keine Kandidatenwahl für den globalen Compaction-Schwellwert.

Der vorhandene Wert 206720 bleibt unverändert. 175k wurde nicht übernommen.

### Phase 2 — Responses-State

Erledigt:

- immutable State- und Turn-Verträge;
- Sync-/Async-Streaming;
- `store=false`, Delta-only und verbotene Conversation-Felder;
- vollständige Item-Weitergabe und neueste-Compaction-Beschneidung;
- Commit-on-complete, Tool exactly once, Bindings, TTL und Limits;
- OAuth-Origin- und Single-Credential-Grenze;
- opt-in Konfiguration;
- 72 State-/Adaptertests bestanden, einschließlich stabiler Credential-Slots
  ohne `client_id` und cancellation-sicherem Cleanup.

### Phase 3 — Abschlussdokumentation

Erledigt:

- ARD, TRD, PD, TD und Implementation Dossier;
- Development Diary, Manual, Proposal Dossier und Open-Item-Bericht;
- Session-Transfer-Protokoll wird separat geführt.

Nicht durchgeführt:

- Push, PR, Merge, Aktivierung oder Promotion.

Die Offline-Implementierung wurde gezielt auf der isolierten Branch committet;
dies ändert weder globale Codex-Dateien noch einen laufenden Dienst.

## 4. Verifikation

| Gate | Ist | Status |
|---|---:|---|
| Neue Tests | 102/102 | PASS |
| Core kombiniert | 131/132; 1 Baseline-Fehler | CANDIDATE PASS / LEGACY HOLD |
| Erweitert | 140/152; 12 Baseline-Fehler | CANDIDATE PASS / LEGACY HOLD |
| Ruff | Check und Format PASS | PASS |
| Compileall | PASS | PASS |
| Diff-Whitespace | PASS | PASS |
| MCP read-only | Health und Suche PASS | PASS |
| Endpoint Capability | nicht ausgeführt | HOLD |
| A/B-Corpus | nicht ausgeführt | HOLD |

Die bestehenden Fehler sind ein Codex-Config-Test und elf Stream-Config-Tests.
Alle reproduzieren auf dem unveränderten Basis-Checkout.

## 5. Promotionsgate

Eine spätere Default-Promotion erfordert kumulativ:

- keine Qualitätsverschlechterung und keinen kritischen Szenarioverlust;
- mindestens 20 % weniger mediane Output-Tokens;
- höchstens 10 % schlechtere p95-Latenz;
- keine höhere Fehlerrate;
- null Cross-Chain-Leaks;
- 100 % kritische Kontinuitäts-, Security- und Legacy-Tests.

Bei einem verfehlten Kriterium bleibt der Modus opt-in. Der Capability-Probe und
Canary können Requests gegen einen potenziell kostenpflichtigen Endpoint erzeugen
und dürfen nur nach ausdrücklicher Genehmigung ausgeführt werden.

## 6. Stop- und Rollback-Regeln

- Keine globale Änderung ohne Vergleich mit den SHA-256-verifizierten Backups.
- Kein Restart ohne ausdrückliche Bestätigung.
- Kein stiller Capability-Fallback.
- Kein Failover innerhalb einer State-Chain.
- Kein State-Commit nach Teil-Stream, Fehler oder Cancellation.
- Bei einem Live- oder A/B-Gate-Fehler bleibt die Funktion aus; es erfolgt keine
  Promotion.

## 7. Simulation

Die Test-Simulation erreichte 97,2 % aggregiert bei mindestens 96 % je
Einzelkriterium. Die Implementierungs-Selbstsimulation im
[Implementation Dossier](../dossiers/2026-07-31-codex-compaction-openviking-responses-id.md)
erreicht 96,0 % aggregiert und mindestens 92 % je Kriterium. Beide überschreiten
95 % aggregiert und 90 % je Kriterium. Diese Simulation ist keine unabhängige
Live-Evidenz und hebt die HOLDs nicht auf.

Der frühere Security-Re-Review Revision 2 des Responses-State-Kandidaten
erreichte 95,6 % aggregiert und mindestens 91 % je Kriterium. Er meldet keine
offenen Critical-/High-Befunde und hebt dessen Offline-Veto auf. Wegen
Nichtverfügbarkeit des geforderten aktuellen
Claude Opus wurde Codex vorläufig als Ersatzmodell eingesetzt. Die drei
Medium-Befunde wurden in den Offline-Follow-ups `325e5cff` und `0556a9aa`
geschlossen und mit 102/102 Kandidatentests verifiziert; vor Aktivierung bleibt
eine unabhängige Revalidierung erforderlich.

Diese frühere Bewertung ist nicht das spätere Legacy-VLM-H3-Security-Urteil.

## 8. Nächste autorisierte Entscheidung

Ohne neue Genehmigung ist nur Offline-Arbeit zulässig. Die nächste materielle
Entscheidung ist entweder:

1. A/B-Corpus bereitstellen und Messung freigeben; oder
2. den potenziell kostenpflichtigen Capability-Probe plus Canary explizit
   genehmigen.

Bis dahin bleibt der Kandidat nicht aktiviert.

## 9. Artefakte

- [ARD](../dossiers/2026-07-31-codex-compaction-openviking-responses-ard.md)
- [TRD](../dossiers/2026-07-31-codex-compaction-openviking-responses-trd.md)
- [Implementation Dossier](../dossiers/2026-07-31-codex-compaction-openviking-responses-id.md)
- [Test Dossier](../tests/2026-07-31-codex-compaction-openviking-responses-td.md)
- [Development Diary](../diaries/Development_Diary_v000.md)
- [Manual](../manuals/2026-07-31-codex-compaction-openviking-responses-manual.html)
- [Proposal Dossier](../vision/2026-07-31-codex-compaction-openviking-responses-ppd.md)
- [Open Items](../sessions/2026-07-31-codex-compaction-openviking-responses-open-items.md)

## 10. Legacy-VLM-H3 Follow-up-Plan — 2026-07-31

### 10.1 Ziel, Nicht-Ziele und Gate

Ziel ist die enge Auflösung von H3 durch genau zwei Reparaturen:

1. zwei stale Exact-`Dict`-Assertions an den bestehenden normalisierten
   `VLMConfig`-Vertrag anpassen, ohne Produktionscode zu ändern;
2. den in `d739a5be` eingeführten und in `44d3cc41` entfernten
   OpenAI-kompatiblen Streaming-Vertrag einschließlich Cleanup und No-Replay
   wiederherstellen; `stream=True` plus Tools bleibt ausdrücklich verboten.

Nicht im Scope sind die acht vorbestehenden VolcEngine-Konstruktor-Testfehler,
eine neue Provider-/State-API, Live-Capability, Aktivierung, Restart, Merge oder
Promotion.

Ausgangsevidenz:

- gezielt 46 Tests: 33 PASS, 13 FAIL = 2 stale Dict + 11 Streaming;
- breitere VLM-Matrix 216 Tests: 195 PASS, 21 FAIL, davon zusätzlich 8
  vorbestehende VolcEngine-Konstruktor-Testfehler.

### 10.2 Quick-Win-First-Reihenfolge

| Rang | Arbeitspaket | Grund/Gate |
|---:|---|---|
| 1 | Baseline und Scope einfrieren | Verhindert, dass VolcEngine- oder Live-Themen in H3 einfließen |
| 2 | Zwei stale Dict-Tests auf Feld-/Subset-Vertrag korrigieren | Kleinster reversibler Fix; `VLMConfig` bleibt unverändert |
| 3 | Preflight-, No-Replay- und Cleanup-Tests zunächst rot definieren | Tool-Stream vor Clientzugriff stoppen und doppelte Providerturns verhindern |
| 4 | `openai_vlm.py` Stream-Reducer für toolfreies Text/Vision, Sync/Async wiederherstellen | Schließt die 11 historischen Streaming-Lücken ohne Tool-Delta-Scope |
| 5 | Marker in `model_retry.py` definieren und `base.py` per Import/Prüfung fail-closed anbinden | Kein Replay nach erstem Ereignis |
| 6 | VikingBot-Adapter `chat` fail-closed schalten und native VolcEngine-Ereignisse markieren | Verhindert Replay oberhalb des VLM-Layers; keine Konstruktorreparatur |
| 7 | Gezielte 46er-Matrix, danach breitere 216er-Matrix ausführen | H3 und Fremdbaseline getrennt bewerten |
| 8 | Provider-Live-Test | **HOLD:** kein neuer API-Key; Credential-/Origin-/Kosten-Gates offen |
| 9 | Dokumentation und unabhängiger Review | Erst nach finaler Offline-Evidenz; keine Aktivierung/Merge |

### 10.3 Phasen und Erfolgskriterien

#### Phase A — Testvertrag und Config-Grenze

- Die zwei vollständigen Dict-Vergleiche werden auf die fachlich relevanten
  Providerfelder reduziert.
- `openviking_cli/utils/config/vlm_config.py` erscheint nicht im Produktionsdiff.
- Gezielte Config-Tests unterscheiden normalisierte Defaults von Nutzerwerten.

Gate: beide stale Assertions grün, ohne Produktionsänderung.

#### Phase B — Stream-State-Machine

- Text und Vision setzen `stream` in Sync/Async explizit.
- `stream=True` plus Tools wird vor `get_client()` und Netzwerk laut abgelehnt.
- Toolfreie String-Content-Deltas werden deterministisch aggregiert; die letzte
  Usage einschließlich Cache-/Reasoning-Token-Details wird genau einmal
  übernommen. Reasoning-Text- und Tool-Deltas werden nicht aggregiert.
- Non-Streaming-Rückgabetypen und -Semantik bleiben unverändert.
- Jeder Stream wird bei Erfolg, Fehler und Cancellation genau einmal geschlossen.

Gate: alle elf historischen Streaming-Fälle plus neue Cleanup- und
Tool-Preflight-Fälle grün.

#### Phase C — Cross-Layer No-Replay

- Lokale `OpenAIVLM`-Retries gelten ausschließlich für Stream-Erstellung;
  Iterator-, Parser- und Cleanupfehler werden lokal nie wiederholt.
- Vor dem ersten Ereignis darf ein äußerer Failover den Fehler klassifizieren.
- Nach dem ersten Ereignis ruft `openai_vlm.py`
  `mark_vlm_error_non_retryable(exc)` auf; `model_retry.py` und Wrapper prüfen
  `is_vlm_error_non_retryable(exc)` vor Klassifikation und Backoff.
- `model_retry.py` definiert die beiden Helper und traversiert `__cause__`,
  `__context__` und `AllCredentialsFailedError`; beide Klassifizierer und
  `retry_sync`/`retry_async` prüfen fail-closed vor Custom-Callbacks.
- `base.py` importiert und prüft den Marker ausschließlich. Failover und
  MultiCredential werfen ihn vor jeder Zustandsänderung unmittelbar erneut.
- VikingBot `chat` prüft den Marker vor seiner Retryklassifikation. Der native
  VolcEngine-Stream markiert nach jedem gelesenen Ereignis; dies erweitert den
  Produktionsscope des Adapters, nicht den Scope der VolcEngine-Konstruktorfixes.

Gate: lokale Retries betreffen nur Stream-Erstellung; nach Teilstream ist der
Provider-Call-Count exakt eins. Fehler vor erstem Ereignis bleiben ausschließlich
für äußeren Failover klassifizierbar.

#### Phase D — Offline-Regression und Status

- gezielte Matrix: Ziel 46/46;
- breitere Matrix: die 13 autorisierten Fehler müssen verschwinden; die acht
  VolcEngine-Fremdfehler werden unverändert und separat ausgewiesen, sofern sie
  weiterhin reproduzieren;
- keine Live-, Aktivierungs- oder Merge-Aussage.

Gate: H3 darf nur bei vollständiger Erfüllung des autorisierten Offline-Vertrags
geschlossen werden. Eine weiterhin rote breite Suite wird nicht pauschal als
grün bezeichnet.

### 10.4 Risikomatrix

| Risiko | Mindestens drei Mitigationen |
|---|---|
| Teilstream wird wiederholt und dupliziert Providerwirkung | No-Replay ab erstem Chunk; Prüfung in beiden Retry-Helpern; exakte Call-Count-Tests im Backend und VikingBot |
| Cleanup leakt Stream oder verdeckt Primärfehler | `finally` für jeden Pfad; Async bevorzugt awaitbares `close()` mit `aclose()`-Fallback; Fehler-/Cancellation-Priorität testen |
| Tool-Stream erreicht Client oder Provider | Guard vor `get_client()`; Sync-/Async-Nullaufruf testen; Tools nur im Non-Streaming-Pfad regressieren |
| Config-Fix verändert Produktion unnötig | nur Assertions ändern; `VLMConfig` im Scope-Gate verbieten; normalisierte Felder gezielt testen |
| VolcEngine-Fehler werden H3 zugerechnet | Baseline mit acht Fällen festhalten; Ergebnisse nach Scope trennen; keine VolcEngine-Datei ohne neue Freigabe ändern |
| Retry-Vertrag divergiert zwischen Sync, Async und Adapter | gemeinsame Semantik definieren; identische Vor-/Nach-Chunk-Szenarien; VikingBot-Adapter in Regression aufnehmen |
| Live-Test ohne abgesichertes Credential | alle Providerrequests HOLD; keinen neuen API-Key beschaffen; Origin/Kosten/Secret-Gate vor künftigem Lauf verlangen |
| Historische Kampagnenevidenz wird überschrieben | datierten Follow-up anhängen; alte Counts stehen lassen; spätere Resultate als neue Evidenz mit Command/Commit ausweisen |

### 10.5 Freigabe- und Stop-Regeln

- Kein OpenAI-/Codex-Live-Provider-Request ohne neues positives
  Credential-/Origin-/Kosten-Gate; die Nutzerentscheidung lautet aktuell HOLD.
- Kein Restart von Rechner, Server, Runtime oder Service.
- Keine Hook-/Responses-State-Aktivierung und keine Threshold-Änderung.
- Kein Merge oder Default-Promotion aus diesem Plan.
- Keine Änderung an `VLMConfig`-Produktionscode oder VolcEngine im autorisierten
  Zwei-Fix-Scope.
- Bei Replay, unvollständigem Cleanup, Toolverlust oder Scope-Drift: STOP und
  erneute Architektur-/Security-Prüfung.

## 11. Security Remediation Plan Revision 1 — 2026-07-31

### 11.1 VETO und Reihenfolge

Security bewertet den Stand mit **78/100, 0 Critical, 5 High, 1 Medium** und
setzt ein VETO. Source und Tests bleiben bis zum bestandenen Re-Review gesperrt.

| Rang | Paket | Exit-Gate |
|---:|---|---|
| 1 | H1 feste redigierte Response-/Log-/Langfuse-Semantik | Sentinel fehlt in allen drei Senken; kein `str`/`repr` markierter Fehler |
| 2 | H2 Graphbudgets und fail-closed malformed-Pfade | 256 Nodes, 512 Edges, 256 Aggregate-Kinder; Wide/Deep/Malformed work-bounded |
| 3 | H3 rekursiver Wrapper-Preflight | vor Selection/Provider/State/Vision-I/O; heterogene oder unklare Modi fail-closed |
| 4 | H4 Fast-Fail-Szenariomatrix | Primary/Backup/Multi/failback-due, Text/Vision Sync/Async; identischer Rethrow, null Catch-Mutatoren |
| 5 | H5 deterministische Cancellation-Matrix | ein Cleanup-Task/Close, vorerzeugte Fehleridentität, keine Orphans |
| 6 | Security RED-Tests und Mutationstest | jedes H1–H5-Ziel zunächst aus exakt fehlender Produktion RED |
| 7 | Security Re-Review | `0C/0H` und mindestens 90/100; erst danach Source-Unlock |
| 8 | M1 Live-Gate | HOLD bis exakter Origin, ein Slot-Fingerprint und numerische Request-/Token-/Bild-/Kostenlimits |

### 11.2 Stop- und Betriebsregeln

- Keine Source- oder Testimplementierung unter Revision-1-VETO.
- Kein Live-Request, solange irgendein M1-Feld offen ist; kein Failover oder
  Retry im späteren Harness.
- MCP bleibt getrennte read-only Evidenz und hebt kein Provider-HOLD auf.
- Kein Rechner-, Server-, Runtime- oder Service-Restart ohne Bestätigung.
- Kein Merge, Push, Canary, Aktivierung oder Promotion aus diesem Plan.

## 12. Security Revision 3 — Finaler Remediation-Plan

Revision 2 endet bei **84/100, 0C/3H/1M** und hält das VETO aufrecht. H4 und H5
sind auf Spezifikations-/Testdefinitionsebene geschlossen; Source und Tests
bleiben gesperrt. Revision 3 ist die letzte zulässige Reviewrunde.

| Reihenfolge | Offener Befund | Finales Exit-Gate |
|---:|---|---|
| 1 | H1 feste Senkenwerte | exakter Response-/Loggertext und exakte Langfuse-Kategorie in markiertem Chat/Native-Pfad; Output und Metadata ohne Sentinel; unmarkierter Legacy-Kontrollfall |
| 2 | H2 erreichbares Graphbudget | 256 Nodes, 512 Edges, 256 Aggregate-Kinder; exact-256 erlaubt/257 fail-closed; >512 kombinierte Kanten sowie Getter-/Tuple-/Child-Malformation work-bounded `True` |
| 3 | H3 rekursiver Zielgraph | all-safe und zyklisch-safe erlaubt je einen aktiven Provider; tiefer unsafe Child, unreadable/malformed oder >256 Ziele fail-closed vor Selection/State/I-O |
| 4 | Security-RED-Definition aktualisieren | Text/Vision Sync/Async, tiefe statt nur flache Heterogenität, harte Mutationsziele |
| 5 | Finales Re-Review | `0C/0H`, mindestens 90/100; andernfalls HOLD ohne Revision 4 |

M1 bleibt konkret HOLD: exakter HTTPS-Allowlist-Origin, ein Credential-Slot-
Fingerprint, fixes Modell/Vision/Capabilities und numerische Request-, Output-
Token-, Bildbyte- und Kostencaps müssen vor einem Live-Request feststehen; Retry
und Failover bleiben null, MCP-Evidenz separat. Kein Restart, Merge oder
Aktivierung.

## 13. Finaler Planstatus — HOLD

Security Revision 3: **89/100, 0C/1H/1M**. H2–H5 sind geschlossen, H1 bleibt
exakt offen. Source-Unlock ist verweigert; keine vierte Security-Revision und
keine H1-Implementierung werden in diesem Lauf geplant. Der sichere nächste
Zustand ist HOLD.

OpenViking MCP Health und read-only `search_experience` sind PASS, jedoch kein
Provider-Capability-Nachweis. Der User hat den Live-Test vertagt. A/B, M1,
Source/Testcode, Restart, Merge, Aktivierung und Promotion bleiben offen oder
gesperrt wie zuvor.

## 14. Neuer QWF-Zyklus zur Offline-HOLD-Aufhebung

Der User autorisierte nach dem alten finalen HOLD einen neuen Offline-Zyklus:
H1 test-first → Architektur/Security → vier Sourcefiles → neue Befunde
test-first → gezielte und breite Evidenz getrennt. Architektur 97/96/100,
Pre-Source Security 93/100 bei 0C/0H und Implementierungssimulation 96,6
Prozent (Minimum 95) bestanden. Der erste Stand erreichte 267/267, erhielt aber
wegen H6 bei 86/100, 0C/1H/2M ein VETO.

Fünf H6-Tests wechselten nach opakem klassenmarkiertem Wrapper, Original-
`__cause__` und fail-closed Stopp beim 257. Kind vor dem Lesen von Kind 258 von
RED zu 5/5 GREEN. Zusätzlich bestanden
189/189 und 272/272. Testsimulation: 98 Prozent, Minimum 96. Security Rev2:
96/100, 0C/0H/1M, PASS. **Offline Legacy-VLM HOLD aufgehoben; Live M1 bleibt
HOLD.** Kein Live-Aufruf, keine Aktivierung, Promotion, kein Merge oder Restart.
