# Architecture Requirement Dossier

## Codex-Compaction und OpenViking Responses State

Stand: 2026-08-01
Status: Offline-Follow-up PASS; H1/H2, Aktivierung und Promotion auf HOLD
Urspruengliche Basis: `60ef45d4c3a7d07ceb1df4e9d7dde7a14449ac50`
Aktuelle Fork-Basis: `c4e3cc52272c086843f3dc64808ed1e8956abede`

## 1. Ergebnis

Der Kandidat trennt zwei unabhängige Sicherheitsgrenzen:

1. Ein gehärteter, quellkontrollierter Codex-Compaction-Hook wurde unter
   `tools/codex_compaction_hooks/` implementiert. Er ist **nicht** in der globalen
   Codex-Konfiguration installiert.
2. `CodexVLM` besitzt einen additiven, opt-in Responses-State-Pfad. Bestehende
   `create()`- und `get_completion()`-Aufrufe bleiben zustandslos.

Die Architektur ist fail-closed: State-Bindings, Generation, Ablauf, Limits,
Tool-Call-Integrität und Capability werden vor oder während des Requests geprüft.
Ein State wird erst nach einem vollständigen `response.completed` veröffentlicht.

## 2. Architekturgrenzen

| Bereich | Implementierter Kandidat | Bewusst ausgeschlossen |
|---|---|---|
| Hook | Private Metadaten, feste Ausgabe, atomare Datei, Korrelation | Globale Installation oder Aktivierung |
| Responses | Aufruferverwalteter, unveränderlicher State | Conversations und `previous_response_id` |
| Speicherung | Jede zustandsbehaftete Anfrage erzwingt `store=false` | Zusage vollständiger Provider-Zero-Retention |
| Provider | Eine `CodexVLM`-Instanz und genau ein Credential | Account-/Provider-Failover innerhalb einer Chain |
| OAuth | Nur `https://chatgpt.com/backend-api/codex` | Benutzerdefinierte OAuth-Origins |
| Compaction | Opt-in Threshold nach erfolgreichem Capability-Probe | Stiller Fallback bei fehlender Capability |
| Kompatibilität | Additive Methoden; `VLMBase` unverändert | Änderung anderer Provider |

## 3. Komponenten

### 3.1 Gehärteter Hook

`tools/codex_compaction_hooks/codex_compaction_hook.py` implementiert:

- private Ablage unter dem Codex-Zustandsverzeichnis mit `0700` für das
  Verzeichnis und `0600` für Dateien;
- Eigentümer-, Zielverzeichnis- und Symlink-Prüfungen für jede Komponente von
  `CODEX_HOME` bis zum State-Verzeichnis;
- per Directory-FD verankerte Lese-, Schreib- und Austauschoperationen ohne
  erneute Pfadauflösung nach der Prüfung;
- atomaren Austausch einer sicheren Temp-Datei unter Prozess- und Thread-Lock;
- maximal 64 KiB Eingabe und eine erzwungene externe Fünf-Sekunden-Deadline;
- begrenzte Retention mit höchstens 256 Records, 24 Stunden TTL und maximal
  1024 untersuchten Verzeichniseinträgen;
- keine Repository-, Transcript-, Pfad- oder Dateinamen in der injizierten
  Ausgabe;
- feste kleine Hinweise für PreCompact und `SessionStart(source=compact)`;
- PostCompact-Korrelation und Invariantenprüfung statt behaupteter semantischer
  Vollständigkeit.

Der kritische Pfad liest weder ein vollständiges Transcript noch Git-Metadaten.

### 3.2 Responses-State

`openviking/models/vlm/backends/codex_responses_adapter.py` enthält:

- den eingefrorenen `CodexResponsesState`;
- den eingefrorenen Ergebniscontainer `CodexResponsesTurn[T]`;
- Sync-/Async-Adapter;
- kanonische, verlustfreie Übernahme sämtlicher `response.output`-Items;
- Beschneidung ausschließlich vor dem neuesten gültigen Compaction-Item;
- State-Integrität, TTL, Ressourcenlimits und Chain-Concurrency;
- Tool-Ausgabe genau einmal für eine offene Call-ID der aktuellen Generation;
- native Async-Iteration und commit-on-complete.

Der State bindet Chain-ID, Generation, Modell, Instructions-Digest, Origin,
Principal-/Credential-Fingerprint, Ablaufzeit, Items und offene Tool-Calls.
Opaque Felder sind nicht Bestandteil normaler Repräsentationen oder Logs.

### 3.3 `CodexVLM` und Konfiguration

`openviking/models/vlm/backends/codex_vlm.py` stellt additive
`get_completion_with_state()`-Pfade und den expliziten
`probe_responses_compaction_capability()` bereit.

`openviking_cli/utils/config/vlm_config.py` führt zwei opt-in Einstellungen ein:

- `responses_state_enabled: false`
- `responses_compact_threshold: null`

Ein Threshold ohne State-Modus ist ungültig. Der State-Modus verlangt exakt ein
`openai-codex`-Credential. Dadurch bleibt der Legacy-Pfad Default.

## 4. Sicherheitsinvarianten

1. Stateful Requests enthalten `store=false` und `stream=true`.
2. `conversation`, `previous_response_id`, `background` und Escape-Hatches über
   `extra_body` werden abgelehnt.
3. Modell, Instructions, Origin, Principal, Credential und Generation können
   innerhalb einer Chain nicht unbemerkt wechseln.
4. Timeout, Fehler, Cancellation oder Teil-Stream mutieren den alten State nicht.
5. Nach dem ersten Stream-Ereignis erfolgt kein automatischer Retry.
6. Tool-Ausgaben werden nur für offene IDs, genau einmal und innerhalb der
   aktuellen Generation angenommen.
7. Der Capability-Probe hat keinen stillen Fallback.
8. OAuth wird im State-Modus ausschließlich an den freigegebenen HTTPS-Origin
   gesendet.
9. State-Inhalte erscheinen standardmäßig weder in Logs noch Telemetrie.

`store=false` verhindert die reguläre gespeicherte Response, ist aber keine
Garantie vollständiger Provider-Zero-Retention. Maßgeblich bleiben die offiziellen
Dokumente zu [Conversation State](https://developers.openai.com/api/docs/guides/conversation-state)
und [Compaction](https://developers.openai.com/api/docs/guides/compaction).

## 5. Harte Limits

| Limit | Default |
|---|---:|
| State | 32 MiB |
| Items | 4096 |
| Turns | 256 |
| Bilder | 8 |
| Bytes je Bild | 8 MiB |
| Bytes je Tool-Ausgabe | 1 MiB |
| Tool-Ausgaben gesamt | 4 MiB |
| Retained Tool-Call-IDs | 4096 |
| Bytes je Tool-Call-ID | 512 |
| TTL | 3600 s |
| Gleichzeitige Chains | 16 |

Überschreitungen schlagen explizit fehl. Bereits gesehene Call-IDs werden in die
kanonische State-Byte-Bilanz einbezogen.

## 6. Verifikation

| Evidenz | Ergebnis |
|---|---|
| Neue Hook- und State-Suiten | 102 bestanden, 0 fehlgeschlagen |
| Core-Kombination | 132 gesammelt, 131 bestanden, 1 bestätigter Baseline-Fehler |
| Erweiterte Kombination | 152 gesammelt, 140 bestanden, 12 bestätigte Baseline-Fehler |
| Ruff Check | PASS |
| Ruff Format Check | 8 Dateien formatiert |
| Compileall | PASS |
| `git diff --check` | PASS |
| Shared OpenViking MCP | Health und read-only `search_experience` PASS |
| Globale Codex-Dateien | Unverändert und identisch zu SHA-256-verifiziertem Backup |

Der eine Core-Fehler ist
`test_vlm_config_default_provider_resolves_codex`; er reproduziert auf der
unveränderten Basis mit 29/30 bestandenen Tests. Weitere elf Stream-Config-Fehler
reproduzieren ebenfalls auf der Basis mit 9/20 bestandenen Tests. Sie werden nicht
als Kandidatenregression ausgegeben.

## 7. Freigabestatus

Der Kandidat ist implementiert und offline verifiziert, aber nicht live
freigegeben. Folgende HOLDs sind zwingend:

- Es existiert noch keine kontrollierte A/B-Matrix mit mindestens 20
  sanitisierten realen Langsitzungen und 10 synthetischen Szenarien.
- Der Capability-Probe und Canary gegen den exakt verwendeten Codex-Endpunkt
  wurden nicht ausgeführt. Der Probe kann Requests erzeugen und potenziell Kosten
  verursachen; er erfordert vorherige ausdrückliche Freigabe.
- Die bestätigten Legacy-Baseline-Fehler sind nicht bereinigt.
- Es erfolgte keine Aktivierung, Default-Promotion oder globale Hook-Installation.

Die Default-Promotion bleibt ein separater Evidenzentscheid. Ein Restart von
Rechner, Server, Runtime oder Service ist weder erforderlich noch autorisiert.

Der frühere Security-Re-Review Revision 2 des Responses-State-Kandidaten hob
dessen Offline-Veto auf: keine
offenen Critical-/High-Befunde, 95,6 % aggregiert und mindestens 91 % je
Kriterium. Da das geforderte aktuelle Claude Opus nicht verfügbar war, ist die
Bewertung mit einem Codex-Ersatzmodell vorläufig. Die verbleibenden
Medium-Befunde wurden in den Offline-Follow-ups `325e5cff` und `0556a9aa`
geschlossen und durch 102/102 Kandidatentests verifiziert. Eine unabhängige
Revalidierung vor Aktivierung bleibt wegen des Ersatzmodells erforderlich. Diese
frühere Bewertung ist nicht das spätere Legacy-VLM-H3-Security-Urteil.

## 8. Verknüpfte Artefakte

- [TRD](2026-07-31-codex-compaction-openviking-responses-trd.md)
- [Implementation Dossier](2026-07-31-codex-compaction-openviking-responses-id.md)
- [Planning Document](../plan/2026-07-31-codex-compaction-openviking-responses-pd.md)
- [Test Dossier](../tests/2026-07-31-codex-compaction-openviking-responses-td.md)
- [Open Items](../sessions/2026-07-31-codex-compaction-openviking-responses-open-items.md)
- [Manual](../manuals/2026-07-31-codex-compaction-openviking-responses-manual.html)

## 9. Legacy-VLM-H3-Follow-up — 2026-07-31

### 9.1 Anlass und Evidenzgrenze

Dieser Follow-up erweitert die historische Kampagne, ersetzt aber keine frühere
Evidenz. Er plant ausschließlich die Auflösung von H3: zwei veraltete
Testverträge mit exaktem `Dict`-Vergleich und die durch einen Upstream-Verlauf
entfernte OpenAI-kompatible Streaming-Unterstützung.

Die kontrollierte Offline-Ausgangslage lautet:

| Matrix | Ergebnis | Einordnung |
|---|---:|---|
| Gezielte Legacy-Baseline | 46 gesammelt, 33 PASS, 13 FAIL | 2 stale Exact-`Dict`-Assertions und 11 Streaming-Fehler |
| Breitere VLM-Matrix | 216 gesammelt, 195 PASS, 21 FAIL | enthält zusätzlich 8 vorbestehende VolcEngine-Konstruktor-Testfehler |

Die acht VolcEngine-Fehler liegen außerhalb der autorisierten zwei Reparaturen.
Sie dürfen weder still mitbehoben noch zur grünen H3-Aussage umetikettiert
werden. Ein separater Befund bleibt erforderlich.

### 9.2 Root Cause und Architekturentscheidung

Commit `d739a5be` führte `stream` für OpenAI-kompatible Text-/Vision-Aufrufe,
Sync/Async-Verarbeitung und zugehörige Tests ein. Commit `44d3cc41` entfernte
den Request-Parameter und die Stream-Reducer wieder, während
`VLMBase.stream` und die historischen Tests erhalten blieben. Dadurch ist der
Produktionsvertrag zwischen Basis, Backend und Tests auseinandergefallen.

Für die beiden stale Exact-`Dict`-Assertions ist keine Produktionsänderung an
`openviking_cli/utils/config/vlm_config.py` gerechtfertigt. Die normalisierte
Provider-Konfiguration enthält absichtlich Defaultfelder; die Tests müssen den
relevanten Vertrag prüfen statt vollständige interne Dictionaries festzunageln.
`VLMConfig` bleibt in diesem Follow-up unverändert.

Die Streaming-Reparatur ist dagegen absichtlich cross-layer:

```text
VLMBase.stream und Responsevertrag
        ↓
OpenAIVLM Request + Sync/Async Stream-Reducer + Cleanup
        ↓
model_retry definiert mark_vlm_error_non_retryable()/is_vlm_error_non_retryable()
        ↓
VLMBase importiert/prüft Marker; VikingBot markiert native Streamereignisse
        ↓
Contract-/Regressionstests
```

Erforderlicher Produktionsscope:

- `openviking/models/vlm/backends/openai_vlm.py`;
- `openviking/utils/model_retry.py`;
- `openviking/models/vlm/base.py` nur für Import und Fail-closed-Prüfung der
  in `model_retry.py` definierten Marker;
- `bot/vikingbot/providers/vlm_adapter.py` für den Adapter-Retry-Guard und die
  Fortschrittsmarkierung nach jedem gelesenen Ereignis im bereits vorhandenen
  nativen VolcEngine-Stream;
- keine Änderung an `VLMConfig` und keine Reparatur der acht separaten
  VolcEngine-Konstruktor-Testfehler.

### 9.3 Verbindlicher Streaming-Vertrag

1. **Preflight und Request:** `stream=True` zusammen mit `tools` wird in Sync und
   Async vor `get_client()`, Credentialauflösung oder Netzwerkzugriff laut
   abgelehnt. Für toolfreie Text- und Vision-Aufrufe wird das explizite
   `stream`-Flag aus `VLMBase` übergeben; Default bleibt `False`.
   Provider-native Retries bleiben deaktiviert.
2. **String-Content und Usage:** Ausschließlich String-Content-Deltas werden in
   Reihenfolge genau einmal zusammengefügt; eine Aggregation von Reasoning-Text-
   Deltas wird nicht zugesagt. Leere oder usage-only Chunks sind zulässig.
   Prompt-, Completion-, Cache- und Reasoning-Token-Details werden höchstens
   einmal aus der letzten belastbaren Usage-Angabe veröffentlicht.
3. **Tools:** Es gibt in diesem Follow-up keine Tool-Delta-Aggregation. Tools
   bleiben ausschließlich im bestehenden Non-Streaming-Pfad mit
   `VLMResponse`. Die Kombination `stream=True` plus Tools ist ein lokaler
   Contract-Fehler ohne Clienterzeugung oder Provideraufruf.
4. **Sync/Async-Parität:** Text-, Vision-, Usage-, Fehler-, Cleanup- und
   Leerstream-Semantik müssen für Iterator und Async-Iterator übereinstimmen.
5. **Cleanup:** Jeder eröffnete Stream wird in `finally` genau einmal geschlossen
   — bei Erfolg, Iteratorfehler, Parserfehler und Cancellation. Für OpenAI SDK
   2.30.0 bevorzugt Async das awaitbare `close()`; `aclose()` ist nur ein
   kompatibler Fallback, wenn `close` fehlt. Cleanup bleibt cancellation-sicher
   und überschreibt weder Primärfehler noch `CancelledError`.
6. **Lokaler Retry:** `OpenAIVLM` wiederholt ausschließlich Fehler der
   Stream-Erstellung. Iterator-, Parsing- und Cleanup-Fehler werden lokal nie
   wiederholt, auch nicht vor dem ersten Ereignis.
7. **Äußerer Failover und No-Replay:** Ein äußerer Failover darf einen
   Iteratorfehler vor dem ersten Stream-Ereignis weiterhin klassifizieren. Nach
   dem ersten Ereignis markiert OpenAIVLM den Fehler mit
   `mark_vlm_error_non_retryable(exc)`. Die Erkennung folgt `__cause__`,
   `__context__` und aggregierten `AllCredentialsFailedError`-Einträgen; alle
   Wrapper prüfen `is_vlm_error_non_retryable(exc)` vor Klassifikation,
   Callback oder Zustandsänderung und dürfen den Turn niemals replayen.
8. **Adaptergrenze:** VikingBot darf nur vor dem ersten Ereignis seine bestehende
   Fehlerklassifikation anwenden. `chat` prüft den Marker vor seiner
   Retryklassifikation; der native VolcEngine-Pfad markiert Fortschritt nach
   jedem aus dem Iterator gelesenen Ereignis. Nach einem Teilstream darf weder
   Adapter noch Failover denselben Turn erneut senden. Dies ist ausdrücklich
   keine Reparatur des VolcEngine-Konstruktors.

### 9.4 Live- und Betriebsgrenze

Es wird kein neuer API-Key bereitgestellt. Alle OpenAI-/Codex-Live-Provider-
Requests bleiben **HOLD**. Online-Tests sind grundsätzlich autorisiert, dürfen
aber erst starten, wenn Credential, exakter HTTPS-Origin, Kostenbudget und
Secret-Handling separat positiv gegated sind. Dieser Follow-up autorisiert
weder Restart noch Hook-/State-Aktivierung, Canary, Merge oder Promotion.

### 9.5 Architektur-Risiken

| Risiko | Mindestens drei Mitigationen |
|---|---|
| Doppelter Providerturn nach Teilstream | Fortschritt ab erstem Chunk markieren; Retry-Helper fail-closed prüfen; VikingBot-No-Replay-Regressionstest |
| Ressourcenleck oder falsche Fehlerpriorität | `finally`-Cleanup; genau-einmal Close-Guard; primären Fehler/Cancellation vor Cleanup-Fehler bewahren |
| Tool-Stream gelangt bis Client oder Netzwerk | Guard vor `get_client()`; Sync-/Async-Call-Count null testen; Tools nur im Non-Streaming-Vertrag belassen |
| Scope-Creep in Konfiguration oder VolcEngine | `VLMConfig` unverändert lassen; acht VolcEngine-Fehler separat reporten; Diff-Scope vor Freigabe prüfen |
| Unbelegte Live-Aussage | Live-Gate explizit HOLD; keine Credentials erzeugen/erfragen; Offline- und Provider-Evidenz getrennt ausweisen |

## 10. Security Review Revision 1 — 2026-07-31

### 10.1 Urteil und VETO

| Score | Critical | High | Medium | Entscheidung |
|---:|---:|---:|---:|---|
| 78/100 | 0 | 5 | 1 | **Security VETO; Source gesperrt** |

Die H3-Architektur ist erst implementierungsfähig, wenn H1–H5 in TRD, ID und TD
normativ geschlossen sind. Bis dahin dürfen weder Produktions- noch Testdateien
geändert werden. Ein Re-Review hebt das VETO nur bei `0 Critical`, `0 High` und
mindestens 90/100 auf.

### 10.2 Verbindliche Architekturkorrekturen

| ID | Schutzziel | Verbindlicher Architekturentscheid |
|---|---|---|
| H1 | Keine Fehlerdaten-Exfiltration | Markierte VikingBot-Fehler erscheinen weder per `str` noch `repr` in Response, Logger oder Langfuse; nur feste redigierte Meldung und feste Kategorie, mit Sentinel-Capture belegt |
| H2 | Begrenzte Marker-Grapharbeit | Harte Budgets: 256 Nodes, 512 Edges, 256 Aggregate-Kinder; beide Cause-/Context-Kanten und alle Aggregate-Kinder; Budgetüberschreitung oder unlesbare/malformed Struktur liefert fail-closed `True` |
| H3 | Tool-Stream-Preflight an der äußersten Grenze | Failover/MultiCredential validieren rekursiv vor Selection, Provider und State; ein mögliches `stream=True` oder heterogene/unklare Streammodi schlagen fail-closed fehl |
| H4 | Marker-Fast-Fail ohne Catch-Mutation | Primary, aktives Backup, aktives Credential ungleich null und failback-due werden in Text/Vision Sync/Async abgedeckt; nach Provider-Snapshot identischer Rethrow und null Catch-Side-Effects |
| H5 | Deterministisches Cancellation-Cleanup | Gepatchte Shield-Barrieren, vorerzeugte Cancellation-Objekte, genau ein Cleanup-Task/Close, vollständige Prioritätsmatrix und keine Orphan-Task |
| M1 | Kontrollierter Live-Egress | Live bleibt HOLD bis exakter HTTPS-Allowlist-Origin, ein Credential-Slot-Fingerprint, Modell/Vision/Capabilities und numerische Request-/Output-/Bild-/Kostenlimits feststehen; kein Failover/Retry |

Der MCP-Handshake bleibt ein separater read-only Betriebsnachweis und kann M1
nicht erfüllen. Bestehende Verbote für Restart, Merge, Aktivierung und Promotion
bleiben unverändert.

## 11. Security Reviews Revision 2 und 3 — 2026-07-31

| Revision | Score | Befunde | Urteil |
|---|---:|---:|---|
| 1 | 78/100 | 0C / 5H / 1M | VETO |
| 2 | 84/100 | 0C / 3H / 1M | VETO; H4/H5 auf Definitionsebene geschlossen |
| 3 | 89/100 | 0C / 1H / 1M | HOLD; H2–H5 geschlossen, H1 offen |

Revision 3 schloss H2–H5, ließ H1 jedoch offen. Source und Tests bleiben
gesperrt. M1 bleibt unverändert HOLD. Das finale Gate `0 Critical`, `0 High` und
mindestens 90/100 wurde verfehlt; es folgt HOLD statt einer vierten Revision.

### 11.1 Finale Architekturpräzisierungen

- **H1:** Markierte Adapterpfade verwenden exakt den sichtbaren Text
  `VLM response interrupted after partial output.`, die Langfuse-Kategorie
  `partial_stream_non_retryable` und den Loggertext
  `VLM adapter stopped a non-retryable partial stream.`. Keine variable
  Sanitizer-Logik; unmarkierte Legacy-Fehler bleiben Kontrollfall.
- **H2:** 256 Nodes, 512 erreichbare Edges und 256 Aggregate-Kinder. Genau 256
  Kinder sind zulässig, 257 fail-closed. Getterfehler, malformed Tupel,
  Nicht-Exception-Kinder und jeder Budgetüberlauf liefern `True`.
- **H3:** Die identity-safe Wrapper-Graphprüfung läuft vor jeder Selection und
  State-Mutation. Sichere Zyklen terminieren und dürfen genau einen Providercall;
  tiefe unsichere, unlesbare, malformed oder mehr als 256 Ziele umfassende
  Graphen schlagen ohne Selection oder I/O fail-closed fehl.

## 12. Finales Security- und Betriebsgate

Security Revision 3 endet bei **89/100, 0 Critical, 1 High, 1 Medium**. H2–H5
sind im finalen Review geschlossen; H1, der exakte Konstantenvertrag für
markierte VikingBot-Fehler, bleibt offen. Das geforderte Gate `0C/0H` und
mindestens 90/100 ist verfehlt: **Source-Unlock verweigert, HOLD**. Es gibt keine
weitere Revision zur H1-Schließung in diesem Lauf.

OpenViking MCP Health und ein echter read-only `search_experience`-Aufruf sind
PASS. Das beweist MCP-Betriebszugriff, nicht Responses-/Compaction-Fähigkeit des
Providers. Der User hat den Live-Provider-Test vertagt; M1, Aktivierung, Restart
und Merge bleiben HOLD.

## 13. User-autorisierter Offline-HOLD-Lift

Nach dem oben historisch dokumentierten finalen HOLD autorisierte der User
einen neuen Offline-Zyklus. Architektur: 97/100 Design, 96/100 Interfaces,
100/100 Scope. H1 war zunächst direkt RED; das Pre-Source-Security-Gate bestand
mit 93/100, 0C/0H. Die Implementierungssimulation erreichte 96,6 Prozent bei
mindestens 95 Prozent je Kriterium. Der erste Source-Stand bestand 267/267,
erhielt aber in Security Rev1 wegen H6 ein VETO (86/100, 0C/1H/2M).

H6 verlangt für nicht instanzmarkierbare Exceptions einen opaken,
klassenmarkierten Wrapper mit dem identischen Original als `__cause__`. M2
stoppt beim 257. Aggregate-Kind fail-closed, bevor Kind 258 gelesen wird. Nach
fünf direkten RED-Tests bestanden 5/5, 189/189 und final 272/272 ohne Fail,
Skip oder Xfail.
Testsimulation: 98 Prozent, Minimum 96. Security Rev2: 96/100, 0C/0H/1M, PASS;
`offline_hold_lifted=true`.

Damit ist ausschließlich der **Offline Legacy-VLM HOLD aufgehoben**. **Live M1
bleibt HOLD**; kein Live-Test, keine Aktivierung, Promotion, kein Merge oder
Restart erfolgte.

## 14. Architekturabschluss des Open-Items-Follow-ups — 2026-08-01

Der Fork-Merge `manni07/OpenViking#2` ist als `c4e3cc52272c086843f3dc64808ed1e8956abede`
die aktuelle Basis. Der falsche Upstream-PR #3667 ist geschlossen. H3, M2, M3
und L1-L3 sind offline mit dem aktuellen Provider-, Pydantic-, Stream- und
Watch-Vertrag geschlossen; der historische Aggregate-Befund wird zur
Eindeutigkeit `SEC-M2` genannt.

Architektonisch offen bleiben nur H1 und H2. H1 darf keinen Credential-, Client-
oder Netzwerkpfad erreichen, bevor Modell, numerische Limits, Fixture-/Tree-
Hashes, Preisbasis und Credential-Lifecycle-Policies genehmigt und vollstaendig
schema-validiert sind. H2 setzt H1 PASS und eine separate Datenfreigabe voraus.
Die finale Offline-Evidenz umfasst 102/102 State/Hook, 500/500 in der
18-Dateien-Matrix unter Pydantic Warning-as-error und 150/150 Watch-Faelle;
Ruff check/format und diff-check sind PASS. Daraus folgt keine Live-, Canary-,
Aktivierungs- oder Promotionsfreigabe.

Der `agy`-Review war wegen Headless-Command-Berechtigung nicht verfuegbar und
ist als UNAVAILABLE, nicht als PASS, klassifiziert.

## 15. Root-Test-Ownership-Grenze — 2026-08-01

Die Root-Pytest-Suite und die beiden Live-Harnesses sind getrennte
Architekturkomponenten. `tests/api_test` und `tests/oc2ov_test` besitzen eigene
Abhaengigkeiten, Arbeitsverzeichnisse und Workflows und werden deshalb exakt
ueber `collect_ignore` aus dem Root-Collector ausgeschlossen. Alle anderen
Root-Testbaeume bleiben sichtbar. Optionale Provider-E2E-Module muessen ohne
ihr Extra sammelbar sein; die fehlende Laufzeitabhaengigkeit bleibt bei einer
tatsaechlichen Aktivierung ein lauter Fehler. Eine frische, lockfile-identische
Root-venv ist Teil des Testvertrags und ersetzt keine Produktcodekorrektur.

Diese Grenze beseitigt die 20 bekannten Collection-Fehler, beweist jedoch
nicht den Laufzeitstatus der Standalone-Harnesses. H1/H2 bleiben davon
unabhaengig HOLD.
