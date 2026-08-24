# Technical Requirement Dossier

## Codex Responses State und Compaction

Stand: 2026-08-01
Status: Offline-Follow-up PASS; H1/H2 und Promotion auf HOLD

## 1. Technischer Vertrag

Die Änderung ist additiv. `VLMBase`, andere Provider sowie die bestehenden
zustandslosen `create()`- und `get_completion()`-Pfade bleiben unverändert.

### 1.1 State

`CodexResponsesState` ist unveränderlich und aufruferverwaltet. Er enthält:

- Chain-ID und Generation;
- Modell und Instructions-Digest;
- Origin sowie Principal-/Credential-Fingerprint;
- Ablaufzeit;
- kanonische Responses-Items;
- offene und bereits verwendete Tool-Call-IDs;
- Turn-, Bild- und Tool-Ausgabe-Zähler;
- Integritätstag.

Ein expliziter Fork ist nur ohne offene Tool-Calls erlaubt. Stale Generation,
Binding-Wechsel, Replay, Manipulation oder Ablauf schlagen vor dem
Netzwerkzugriff fehl, soweit die jeweilige Prüfung lokal entscheidbar ist.

### 1.2 Request

Für jeden zustandsbehafteten Request gelten:

```text
store = false
stream = true
conversation = verboten
previous_response_id = verboten
background = verboten
```

Die Regeln gelten auch für `extra_body`. Bei vorhandenem State akzeptiert die
API nur neue Turn-Deltas. Das vollständige, kanonische Ledger wird vom Adapter
zusammengesetzt.

### 1.3 Response und Commit

- Sämtliche `response.output`-Items werden strukturerhaltend übernommen.
- Reasoning-, Tool- und Compaction-Items werden nicht semantisch reduziert.
- Nur der Abschnitt vor dem neuesten gültigen Compaction-Item wird entfernt.
- Ein neuer State wird ausschließlich nach `response.completed` veröffentlicht.
- Fehler, Timeout, Cancellation und Teil-Stream geben keinen Kandidaten-State
  frei.
- Nach dem ersten Stream-Ereignis gibt es keinen automatischen Retry.
- Async verwendet natives, cancellation-sicheres `async for`.

### 1.4 Tool-Calls

Eine Tool-Ausgabe wird nur akzeptiert, wenn:

1. die Call-ID offen ist;
2. Chain und Generation aktuell sind;
3. die Call-ID noch nicht verbraucht wurde;
4. Einzel- und Gesamtgrößenlimits eingehalten werden.

Die Ausgabe schließt die offene ID genau einmal.

## 2. Capability und Credential

`responses_compact_threshold` ist optional und nur mit aktiviertem State-Modus
zulässig. Vor der Verwendung ist ein erfolgreicher Capability-Probe für den
tatsächlich verwendeten Codex-Endpunkt erforderlich. Unsupported Features führen
zu einem expliziten Fehler, nicht zu einem Legacy-Fallback.

Im Pilot gilt:

- exakt ein `openai-codex`-Credential;
- kein Credential-/Account-/Provider-Failover innerhalb der Chain;
- OAuth ausschließlich zu
  `https://chatgpt.com/backend-api/codex`;
- keine benutzerdefinierten OAuth-Origins.

Der Probe wurde nicht live ausgeführt. Er ist potenziell kostenpflichtig und darf
erst nach ausdrücklicher Genehmigung erfolgen.

## 3. Konfiguration

```yaml
vlm:
  provider: openai-codex
  responses_state_enabled: false
  responses_compact_threshold: null
```

Die Defaults ändern das bestehende Verhalten nicht. Für einen später genehmigten
Pilot wären `responses_state_enabled: true`, ein positiver Threshold und genau ein
Credential erforderlich. Diese Dokumentation aktiviert nichts.

## 4. Hook-Vertrag

Der quellkontrollierte Hook-Kandidat:

- liest höchstens 64 KiB Eingabe;
- besitzt eine interne Laufzeitgrenze von fünf Sekunden;
- schreibt ausschließlich in ein privates, eigentümergeprüftes Verzeichnis;
- erzwingt `0700`/`0600`;
- verweigert Symlinks in jeder Pfadkomponente von `CODEX_HOME` bis zum
  State-Verzeichnis sowie unsichere Ziele;
- aktualisiert atomar;
- injiziert nur konstante, kleine Hinweise;
- korreliert PreCompact, Compact-SessionStart und PostCompact über Metadaten.

Es werden keine Transcript-Inhalte, Repository-Namen, Pfade, Dateinamen oder
Git-Metadaten in den Prompt übernommen. Der Kandidat wurde nicht in
`~/.codex/config.toml`, `~/.codex/hooks.json` oder den globalen Hook-Pfad
installiert.

## 5. Fehler- und Limitmodell

Eigene Fehlertypen unterscheiden Validierung, Ablauf, Generation, Binding,
Tool-Integrität, Limits, Capability, Concurrency und Transport. Default-Limits:

| Ressource | Grenze |
|---|---:|
| State | 32 MiB |
| Items | 4096 |
| Turns | 256 |
| Bilder | 8 |
| Bild | 8 MiB |
| Tool-Ausgabe einzeln / gesamt | 1 MiB / 4 MiB |
| Retained Tool-Call-IDs | 4096 |
| Tool-Call-ID | 512 Bytes |
| TTL | 3600 s |
| Chains | 16 |

Opaque State-Daten dürfen nicht in Logs, Traces, Dossiers oder Telemetrie
ausgegeben werden. Tests verwenden Sentinel-Secrets zur Prüfung. Gesehene
Tool-Call-IDs zählen zur kanonischen State-Byte-Grenze.

## 6. Erfüllungsmatrix

| Anforderung | Implementierung | Evidenz |
|---|---|---|
| Additive API | Adapter und `CodexVLM` | Neue Tests PASS |
| Immutabler State | Frozen Dataclasses plus Integrität | Neue Tests PASS |
| Delta-only | Request-Validierung | Contract-Tests PASS |
| `store=false` | Zwang und Escape-Hatch-Prüfung | Contract-Tests PASS |
| Lossless Items | Kanonischer Reducer | Reasoning/Tool/Compaction PASS |
| Commit-on-complete | Stream-State-Maschine | Timeout/Partial/Cancel PASS |
| Native Async | Async-Adapter | Sync/Async-Parität PASS |
| Tool exactly once | Open-/Seen-ID-Vertrag | Replay-Tests PASS |
| Limits/TTL/Chains | Lokale Guards | Boundary-Tests PASS |
| Hook-Härtung | Quellkontrolliertes Tool | 30 Hook-Tests PASS |
| Legacy default | Opt-in Config | Core bis auf Baseline-Fehler |
| Live Capability | Expliziter Probe | **HOLD: nicht genehmigt/ausgeführt** |

## 7. Verification Baseline

```text
Neue Suiten:       102 passed
Core kombiniert:  131 passed, 1 failed (132 collected)
Erweitert:         140 passed, 12 failed (152 collected)
Ruff check:        PASS
Ruff format:       PASS
compileall:        PASS
git diff --check:  PASS
```

Der Core-Fehler und elf zusätzliche Stream-Config-Fehler reproduzieren auf dem
unveränderten Basis-Checkout. Deshalb sind sie keine neu eingeführten
Regressionen, verhindern aber eine Aussage „Legacy vollständig grün“.

## 8. Freigaberegel

Live-Promotion bleibt HOLD, bis:

1. 20 reale sanitierte und 10 synthetische Long-Horizon-Szenarien ausgewertet
   sind;
2. Capability-Probe und Canary nach Genehmigung am exakten Endpoint bestehen;
3. Qualität nicht sinkt, mediane Output-Tokens mindestens 20 % fallen,
   p95-Latenz höchstens 10 % steigt, Fehlerrate nicht steigt und Cross-Chain-Leaks
   null bleiben;
4. alle kritischen Security-, Kontinuitäts- und Legacy-Gates bestehen.

Kein Restart und keine Aktivierung sind Bestandteil dieses Kandidaten.

Der frühere Security-Re-Review Revision 2 des Responses-State-Kandidaten meldet
keine offenen Critical-/High-Befunde und hebt dessen Offline-Veto auf. Er
bewertete diesen Kandidaten mit 95,6 %
aggregiert und mindestens 91 % je Kriterium. Die Bewertung ist vorläufig, weil
das geforderte aktuelle Claude Opus nicht verfügbar war und Codex als
Ersatzmodell eingesetzt wurde.

Diese frühere Bewertung ist vom späteren Legacy-VLM-H3-Security-Review getrennt.

Die drei Medium-Restbefunde aus diesem Review wurden anschließend offline
geschlossen: eine stabile Credential-Slot-Bindung funktioniert auch ohne
`client_id`, Async-Ressourcen werden trotz wiederholter Cancellation vollständig
geschlossen, und der Hook verwendet Directory-FD-Verankerung, eine erzwungene
Deadline sowie begrenzte Retention. Evidenz: Follow-up-Commit
`325e5cff3895036a2fc0e8a0a93131e77f7c9d0d`, ergänzende
Cancellation-Fehlerpriorität in `0556a9aac049d2563893e1abe4068c0260024542`
und 102/102 Kandidatentests.

## 9. Artefakte

- [ARD](2026-07-31-codex-compaction-openviking-responses-ard.md)
- [Implementation Dossier](2026-07-31-codex-compaction-openviking-responses-id.md)
- [Test Dossier](../tests/2026-07-31-codex-compaction-openviking-responses-td.md)
- [Manual](../manuals/2026-07-31-codex-compaction-openviking-responses-manual.html)

## 10. Legacy-VLM-H3 Technical Follow-up — 2026-07-31

### 10.1 Verifizierte Baseline

```text
gezielt:  46 collected = 33 passed + 13 failed
          2 stale exact-Dict assertions + 11 streaming failures

breit:   216 collected = 195 passed + 21 failed
          zusätzlich 8 vorbestehende VolcEngine constructor-test failures
```

Die acht VolcEngine-Fehler sind dokumentierte Fremdbaseline und nicht Teil des
autorisierten Reparaturumfangs. Die beiden Exact-`Dict`-Fehler werden durch
vertragsspezifische Assertions korrigiert; Produktionscode in
`openviking_cli/utils/config/vlm_config.py` bleibt unangetastet.

### 10.2 File-Level-Vertrag

| Datei/Schicht | Geplante Verantwortung | Nicht zulässig |
|---|---|---|
| `openviking/models/vlm/backends/openai_vlm.py` | `stream=True` plus Tools vor Clientzugriff ablehnen; toolfreie vier Call-Pfade streamen; Sync-/Async-Reducer; Usage; Cleanup; Teilstreamfehler markieren | Tool-Delta-Aggregation, Iterator-Retry oder stille Fallback-Semantik |
| `openviking/utils/model_retry.py` | `mark_vlm_error_non_retryable(exc)` und `is_vlm_error_non_retryable(exc)` definieren; Exception-Ketten und Aggregate traversieren; Klassifizierer und Retry-Wrapper fail-closed schalten | Sleep, Custom-Callback oder zweiter Aufruf nach Teilstream |
| `openviking/models/vlm/base.py` | bestehendes `stream=False` und `VLMResponse` bewahren; Marker aus `model_retry.py` importieren und in Failover/MultiCredential vor Klassifikation oder Zustandsänderung prüfen | Helperdefinition, neue öffentliche State-API oder Provider-Spezialfall |
| `bot/vikingbot/providers/vlm_adapter.py` | `chat` vor Retryklassifikation fail-closed schalten; im nativen VolcEngine-Stream Fortschritt nach jedem gelesenen Ereignis markieren | VolcEngine-Konstruktorreparatur oder Replay eines gestarteten Streams |
| `tests/unit/test_stream_config_vlm.py` | Preflight, Request-, Text-, Vision-, Usage-, Cleanup- und No-Replay-Vertrag | Tool-Delta-Aggregation oder vollständige interne Dict-Snapshots |
| `tests/unit/test_codex_vlm.py` und `tests/unit/test_kimi_glm_vlm.py` | nur relevante normalisierte Felder prüfen | Produktionsdefaults entfernen |
| `tests/unit/test_vikingbot_vlm_adapter_retry.py` | äußere Klassifikation vor erstem Ereignis und No-Replay danach beweisen | Mock-only Aussage ohne Provider-Call-Zählung |
| VolcEngine-Konstruktor-Tests | separat als vorbestehend berichten | Änderung innerhalb dieses Follow-ups |

### 10.3 Reducer- und Ergebnisvertrag

Der Stream-Reducer muss folgende Zustandsmaschine einhalten:

```text
PREFLIGHT
  ├─ stream=True + tools → lokaler Fehler; kein Client/Netzwerk
  └─ toolfreier Stream → CREATE_STREAM
         ├─ Erstellungsfehler → lokaler Retry nach Klassifikation/Budget
         └─ Stream erstellt → ITERATE; lokal nie wiederholen
                ├─ Fehler vor Ereignis → Cleanup; äußerer Failover darf klassifizieren
                └─ erstes Ereignis → STARTED
                       ├─ String-Content aggregieren; letzte Usage festhalten
                       ├─ Fehler → mark_vlm_error_non_retryable(exc) → Cleanup
                       └─ Ende/Cancellation → Cleanup → Ergebnis/Fehler propagieren
```

- Ausschließlich String-Content-Deltas werden in Providerreihenfolge verkettet;
  `None` und leere Deltas werden ignoriert, nicht als Ende gewertet. Dieser
  Vertrag verspricht keine Aggregation von Reasoning-Text-Deltas.
- Usage wird nicht pro Chunk doppelt gezählt. Die letzte vollständige Angabe
  bestimmt Prompt-, Completion-, Cache- und Reasoning-Token-Details und wird
  genau einmal an den lokalen Tracker übergeben.
- Tool-Deltas werden nicht aggregiert. `stream=True` plus `tools` schlägt vor
  `get_client()` beziehungsweise Credential-/Netzwerkzugriff fehl.
- Toolfreie Streams liefern weiterhin `str`. Tools bleiben im unveränderten
  Non-Streaming-Pfad und liefern dort `VLMResponse`.
- Der Non-Streaming-Pfad darf semantisch nicht verändert werden.

### 10.4 Cleanup- und Retry-Vertrag

1. Streamobjekte werden nach erfolgreicher Erstellung lokal gehalten und in
   einem `finally`-Pfad geschlossen.
2. Sync verwendet `close()`. Async bevorzugt für OpenAI SDK 2.30.0 das
   awaitbare `close()` und wartet dessen Ergebnis ab; `aclose()` ist nur der
   kompatible Fallback, wenn `close` nicht vorhanden ist. Jeder Close wird
   höchstens einmal initiiert.
3. Bei Cancellation wird Async-Cleanup abgeschirmt und vollständig abgewartet,
   ohne die Cancellation zu verschlucken.
4. Scheitert Verarbeitung und Cleanup, bleibt der Verarbeitungs-/Transportfehler
   primär; Cleanup wird nur redigiert diagnostiziert.
5. Ein Versuch gilt ab dem ersten aus dem Iterator gelesenen Chunk als gestartet,
   auch wenn dieser nur Usage oder leeren Content enthält.
6. Lokale `OpenAIVLM`-Retries umfassen nur die Stream-Erstellung. Nach Übergabe
   eines Streamobjekts sind Iterator-, Parser- und Cleanup-Fehler lokal nie
   retrybar.
7. Vor dem ersten Ereignis bleibt ein Iteratorfehler für äußere
   Failover-Klassifikation unmarkiert. Nach dem ersten Ereignis wird er mit
   `mark_vlm_error_non_retryable(exc)` markiert; `model_retry` und Wrapper prüfen
   `is_vlm_error_non_retryable(exc)` vor Klassifikation und Backoff.
8. Die Marker-Helper werden ausschließlich in `model_retry.py` definiert. Die
   Prüfung traversiert `__cause__`, `__context__` und die Einzelfehler eines
   `AllCredentialsFailedError`. `classify_api_error`,
   `is_retryable_api_error`, `retry_sync` und `retry_async` prüfen fail-closed;
   die Retry-Wrapper tun dies vor einem benutzerdefinierten Callback.
9. `FailoverVLM` und `MultiCredentialVLM` werfen markierte Fehler unmittelbar
   erneut, bevor Switcher, Credentialindex oder aggregierte Fehler verändert
   werden. VikingBot `chat` prüft ebenso vor seiner Klassifikation; sein nativer
   VolcEngine-Stream markiert Fortschritt nach jedem gelesenen Ereignis.
10. Provider-SDK-Retries bleiben `0`.

### 10.5 Test- und Online-Gates

Offline müssen mindestens belegt werden:

- Text und Vision, Sync und Async, `stream=False` und `stream=True`;
- String-Content, letzte Usage einschließlich Cache-/Reasoning-Token-Details und
  leere Chunks sowie Ablehnung von
  `stream=True` plus Tools vor Client-/Netzwerkzugriff;
- Close bei Erfolg, Fehler und Cancellation sowie Fehlerpriorität;
- transienter Stream-Erstellungsfehler wird lokal innerhalb des Budgets
  wiederholt; Iterator-/Cleanupfehler lokal nie;
- Fehler vor erstem Ereignis bleibt für äußeren Failover klassifizierbar;
- Fehler nach erstem Ereignis trägt den standardisierten Marker und darf über
  keine Schicht replayt werden;
- VikingBot-Adapter wiederholt keinen teilweise sichtbaren Turn;
- beide stale Dict-Tests prüfen Felder/Subset statt vollständige Dictionaries;
- gezielte 46er- und breitere 216er-Matrix werden erneut ausgeführt, wobei die
  acht VolcEngine-Fremdfehler separat bleiben.

Online-Provider-Tests sind mangels neuem API-Key und noch offenem Credential-/
Origin-Gate **HOLD**. Kein Offline-Test darf als Live-Provider-Beweis bezeichnet
werden.

### 10.6 Technische Risiken

| Risiko | Mindestens drei Mitigationen |
|---|---|
| Marker geht beim Exception-Wrapping verloren | exakt `mark_vlm_error_non_retryable`/`is_vlm_error_non_retryable` verwenden; Wrapperpfade testen; Retry-Call-Count exakt assertieren |
| Async-Cancellation unterbricht Cleanup | Cleanup-Task abschirmen; Cancellation danach erneut propagieren; wiederholte-Cancellation-Test ergänzen |
| Streaming mit Tools erreicht versehentlich Provider | Guard vor `get_client()`; Sync-/Async-Clientfactory nicht aufgerufen assertieren; Non-Streaming-Toolvertrag regressieren |
| Usage wird doppelt gezählt | letzte vollständige Usage verwenden; Update genau einmal; Sync-/Async-Token-Tracker assertieren |
| Stale Tests treiben Produktionsänderung | Assertions auf Vertrag reduzieren; `VLMConfig` aus Diff ausschließen; Config-Regressionen separat laufen lassen |
| Breite Suite bleibt wegen VolcEngine rot | acht Fehler vorab inventarisieren; Ergebnis je Scope berichten; keine Fremdfixes ohne neue Autorisierung |
| Live-Test kompromittiert Credential oder Origin | kein neues Secret anfordern; HTTPS-Origin vor Request prüfen; Kosten-/Logging-Gate dokumentieren und bis dahin HOLD |

## 11. Security Review Revision 1 — Technische Normen

Status: **78/100; 0 Critical, 5 High, 1 Medium; Security VETO.** Source bleibt
gesperrt. Die folgenden Anforderungen ersetzen keine Tests und gelten zusätzlich
zu Abschnitt 10.

### 11.1 H1 — Redigierte markierte Fehler

In `VLMProviderAdapter.chat()` und `_chat_stream_volcengine()` darf eine durch
`is_vlm_error_non_retryable()` erkannte Exception niemals über `str(exc)`,
`repr(exc)`, Tracebacktext oder interpolierte Argumente in `LLMResponse`, Loguru
oder Langfuse gelangen. Response und Telemetrie erhalten ausschließlich eine
feste redigierte Meldung und feste Kategorie, beispielsweise
`partial_stream_non_retryable`; Logs verwenden ebenfalls einen konstanten Text.
Das Originalobjekt bleibt nur im Kontrollfluss. Sentinel-Capture prüft Response,
Logger-Record und vollständigen Langfuse-Payload negativ.

### 11.2 H2 — Begrenzte fail-closed Marker-Graphsuche

`is_vlm_error_non_retryable()` traversiert identitätssicher beide
`__cause__`-/`__context__`-Kanten und alle Kinder jedes
`AllCredentialsFailedError`. Harte Gesamtbudgets pro Aufruf:

- höchstens 256 unterschiedliche Nodes;
- höchstens 512 untersuchte Kanten;
- höchstens 256 Aggregate-Kinder.

Sobald das nächste Element ein Budget überschreiten würde, liefert die Funktion
fail-closed `True`. Das gilt ebenso, wenn Attribute nicht lesbar sind, Getter
werfen, Aggregate-Strukturen malformed sind oder Kinder nicht sicher als
Exceptiongraph ausgewertet werden können. Wide-, Deep-, Zyklus-, malformed- und
instrumentierte Work-bound-Tests beweisen die Obergrenzen.

### 11.3 H3 — Rekursiver Wrapper-Preflight

`FailoverVLM` und `MultiCredentialVLM` erhalten eine wrapper-level Methode
`_validate_stream_request(tools)`. Bei nichtleeren Tools prüft sie rekursiv alle
möglichen Ziel-VLMs, bevor `should_try_primary()`, `maybe_failback()`, Provider,
Credential-/Switcher-State, Requestbau oder Vision-I/O erreicht werden. Sobald
ein mögliches Ziel `stream=True` hat, Streammodus nicht lesbar ist oder
heterogene Zielmodi keine eindeutige sichere Aussage erlauben, folgt lokaler
`NotImplementedError`. Text/Vision Sync/Async testen null Provider-, Selection-,
State-, Credential- und Datei-I/O. Die Rekursion ist identitätssicher; malformed
Zielgraphen schlagen ebenfalls fail-closed fehl.

### 11.4 H4 — Catch-Phase Fast-Fail

Markerprüfung ist die erste Catch-Anweisung für:

- Fehler des Primary;
- Fehler des aktuell aktiven Backup;
- `MultiCredentialVLM` mit aktivem Index ungleich null;
- failback-due Auswahl.

Jedes Szenario wird für Text/Vision Sync/Async ausgeführt. Ein Provider-Side-
Effect nimmt unmittelbar beim Aufruf den Snapshot nach zulässiger Pre-call-
Selection. Nach Catch wird dasselbe Exceptionobjekt geworfen und der Snapshot
ist unverändert; Annotation, Classifier, Logger, Switcher-Mutator und nächster
Provider haben Count null.

### 11.5 H5 — Deterministische Cancellation

Tests patchen `asyncio.shield` mit getrennten First-/Second-observation- und
Cleanup-release-Barrieren. Die zu werfenden `CancelledError`-Objekte werden
vorerzeugt, damit Identität und Nachricht prüfbar bleiben. Ein Create-Task-Spy
erfasst exakt denselben einen Cleanup-Task; Close-Count ist eins.

Verbindliche Outcomes:

- Body success + Cleanupfehler + Wait-Cancellation → erste Wait-Cancellation;
- Primärfehler + Cleanupfehler + Wait-Cancellation → identischer Primärfehler;
- Body-Cancellation + weitere Wait-Cancellations → identische Body-Cancellation.

Alle Tasks werden gezielt awaited; keine Task bleibt pending oder erzeugt eine
unhandled Exception.

### 11.6 M1 — Live-Allowlist und Budgets

Provider-Live bleibt HOLD, bis ein Evidence Record exakt festschreibt:

- einen HTTPS-Allowlist-Origin, für den aktuellen Pilot ausschließlich
  `https://chatgpt.com/backend-api/codex`;
- genau einen Credential-Slot-Fingerprint;
- Modell, Visionmodus und benötigte Capabilities;
- numerische Obergrenzen für Gesamtrequests, Output-Tokens, Bildbytes und Kosten.

Der Live-Harness erzwingt null Failover und null Retry. MCP-Handshake/read-only
Tool-Call werden separat berichtet und sind kein Provider-Capability- oder
Egressbeweis.

### 11.7 Re-Review-Gate

Source wird erst freigegeben, wenn die finale Security Revision 3 die offenen
H1–H3 als testbar und M1 als weiterhin fail-closed bestätigt: `0 Critical`,
`0 High`, Score mindestens 90/100. Kein Restart, Merge, Push, Canary oder
Aktivierung ist dadurch impliziert.

## 12. Security Revision 3 — Finale technische Spezifikation

Revision 2: **84/100, 0 Critical, 3 High, 1 Medium, VETO**. H4 und H5 sind auf
Spezifikations-/Testdefinitionsebene geschlossen. Dies ist keine Source- oder
Ausführungsevidenz; Source und Tests bleiben bis zum finalen Re-Review gesperrt.

### 12.1 H1 — Exakte Konstanten statt Sanitizer

Ein früher Marker-Branch verwendet ohne variable Sanitizer-Logik exakt:

- sichtbarer `LLMResponse.content`:
  `VLM response interrupted after partial output.`;
- strukturierte Langfuse-Kategorie:
  `partial_stream_non_retryable`;
- Logger-Aufruf mit einzigem festen Text:
  `VLM adapter stopped a non-retryable partial stream.`.

Markierter `chat()` und nativer Stream assertieren jeweils exakt Response,
Logger und Langfuse `output`/`metadata`; kein Feld darf `str(exc)`, `repr(exc)`,
Args, Traceback oder Sentinel enthalten. Ein unmarkierter Legacy-Kontrollfall
beweist, dass nur der Markerpfad redigiert wird und keine globale variable
Sanitizer-Schicht entsteht.

### 12.2 H2 — Erreichbares 256/512/256-Budget

Das korrigierte Gesamtbudget lautet 256 eindeutige Nodes, 512 erreichbare Kanten
und 256 Aggregate-Kinder. Eine Kante ist jeder tatsächlich untersuchte
`__cause__`-, `__context__`- oder Aggregate-Verweis. Exakt 256 Aggregate-Kinder
sind zulässig; der Versuch, Kind 257 zu lesen, liefert fail-closed `True`.
Exakt 512 wohlgeformte Kanten bleiben zulässig; jede weitere erreichbare Kante
liefert `True`.

Tests konstruieren einen Graphen mit mehr als 512 erreichbaren Kanten durch eine
Kombination aus Aggregate-Kindern und Cause-/Context-Kanten, nicht nur eine
unrealistische flache Liste. Separate Fakes werfen beim Lesen von `__cause__`,
`__context__` oder `errors`, liefern ein malformed Aggregate-Tupel oder ein
Nicht-`BaseException`-Kind. Jede solche Struktur ist `True`. Node-Visit-, Edge-
und Child-Instrumentierung beweist die harten Work-bounds auch für Wide-, Deep-
und Zyklusgraphen.

### 12.3 H3 — Identity-safe rekursiver Wrapper-Zielgraph

`_validate_stream_request(tools)` traversiert mit einem Identity-Visited-Set
rekursiv alle potenziellen Ziele vor jeder Selection, Failbackentscheidung,
State-Mutation, Provider- oder Vision-I/O. Das Zielbudget beträgt 256 Objekte.

Verbindliche Graphfälle für Text/Vision Sync/Async:

- ein beliebig verschachtelter all-safe-Graph mit ausschließlich `stream=False`
  ist erlaubt und ruft den aktiven Provider genau einmal;
- ein tiefer `stream=True`-Child unter mehreren safe Wrappern schlägt fail-closed
  fehl; eine flache `[False, True]`-Liste allein reicht nicht als Test;
- ein zyklischer all-safe-Graph terminiert und erlaubt genau einen Providercall;
- unreadable `stream`, unreadable/werfender Validator, malformed Target oder ein
  Graph mit mehr als 256 Targets liefert lokalen `NotImplementedError`;
- ein tiefer True-Child ist der Heterogenitätsdiskriminator, auch wenn alle
  oberflächlichen Wrapper safe erscheinen.

Jeder Reject-Fall assertiert null `should_try_primary`, `maybe_failback`,
Switcher-/Credential-State, Provider, Requestbau sowie Bild-/Datei-I/O.

### 12.4 Revisions- und Betriebsgrenze

| Revision | Score | Befunde | Ergebnis |
|---|---:|---:|---|
| 1 | 78/100 | 0C/5H/1M | VETO |
| 2 | 84/100 | 0C/3H/1M | VETO; H4/H5 geschlossen |
| 3 | 89/100 | 0C/1H/1M | HOLD; H2–H5 geschlossen, H1 offen |

Revision 3 verfehlte `0C/0H` und mindestens 90/100; das Vorhaben bleibt HOLD,
ohne vierte Revision. M1 bleibt unverändert konkret HOLD mit
exaktem HTTPS-Origin, einem Slot-Fingerprint, fixem Modell/Vision/Capabilities
und numerischen Request-/Token-/Bild-/Kostencaps, null Retry/Failover und separat
bewerteter MCP-Evidenz. Keine Source-/Teständerung, kein Restart oder Merge.

## 13. Finales Security-Urteil

Revision 3 erreicht **89/100 bei 0C/1H/1M**. H2–H5 sind geschlossen. H1 bleibt
exakt offen: Die drei bindenden markierten Adapterkonstanten und ihre
Response-/Logger-/Langfuse-Assertions sind nicht implementiert oder verifiziert.
Da `0H` und 90/100 verfehlt sind, wird Source nicht freigegeben; es folgt HOLD
statt Revision 4.

MCP Health plus echter read-only `search_experience`-Aufruf sind PASS, aber kein
Provider-Capability-Beleg. Der User hat Live-Tests vertagt. M1, Source/Testcode,
Restart, Merge, Aktivierung und Promotion bleiben gesperrt.

## 14. Technischer Abschluss des Offline-HOLD-Lifts

Der neue Zyklus bestand Architektur 97/96/100, Pre-Source Security 93/100 bei
0C/0H und Implementierungssimulation 96,6 Prozent bei Minimum 95. Nach zunächst
267/267 fand Security Rev1 H6 (86/100, 0C/1H/2M). Die Korrektur verwendet einen
opaken, klassenmarkierten Wrapper mit Originalexception als `__cause__`; M2
stoppt beim 257. Aggregate-Kind, bevor Kind 258 gelesen wird. Fünf direkte Tests
wechselten RED zu 5/5 GREEN;
189/189 und 272/272 bestanden ebenfalls ohne Fail, Skip oder Xfail.

Die Testsimulation erreichte 98 Prozent bei Minimum 96. Security Rev2 bestand
mit 96/100, 0C/0H/1M. Vier bekannte Pydantic-Warnungen blieben sichtbar. Alle
Läufe nutzten `/Volumes/ExtremePro/projects/OpenViking/.venv/bin/python` mit
`PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731` und
`pytest -q -o addopts=`. Worker und Supervisor reproduzierten den finalen
Breitscope mit 364 PASS plus exakt acht vorbestehenden VolcEngine-
Konstruktorfehlern. **Offline Legacy-VLM HOLD aufgehoben; Live M1 bleibt HOLD.**

## 15. Technischer Follow-up-Abschluss — 2026-08-01

Das aktuelle Inventar ersetzt die verwaisten VolcEngine-Cachetests durch drei
Factory-/Sync-/Async-Vertragstests gegen `chat.completions`. Der gezielte Stand
war 129 PASS, der breite Stand vor spaeteren Follow-up-Aenderungen 348 PASS.
Der Streamtest wurde in ein nicht sammelbares Supportmodul geteilt und blieb mit
50/50 sowie der 274er VLM-Matrix gruen. `WatchTask` bestand 7/7 und die 274er
Matrix unter Pydantic Warning-as-error. L1-L3 sind durch vorhandene
Sentinel-Senken, 16 Fail-fast-Faelle, Rueckgabewerttreue aller Marker-Aufrufer,
Cleanup und Built-in-Post-Event-Cancellation geschlossen.

Die Legacy-Isolation bestand Resource 37/37, Service-Fixtures ohne Setupfehler,
Recovery/Scheduler 19/19, Connector 50/50, Watch 21/21 und Feishu/Queue 23/23.
Der Deferred-Testdouble liefert nur fuer `defer_post_processing=True` den
vorbereiteten Payload; kein `wait=True` und keine Abschwaechung des produktiven
Missing-Payload-Guards wurden eingefuehrt.

Finale konsolidierte Evidenz: 102/102 State/Hook, 500/500 in 18 Dateien unter
Pydantic Warning-as-error und 150/150 Watch nach Ruff-Format; Ruff check,
Ruff format und diff-check PASS. H1 bleibt ohne freigegebene Modell-, Limit-,
Hash-, Preis- und Credential-Policies vor I/O gesperrt; H2 bleibt davon und von
der Datenfreigabe abhaengig. `agy` ist wegen Headless-Berechtigung UNAVAILABLE,
nicht PASS.

## 16. Root-Collection-Vertrag — 2026-08-01

- Environment: uv 0.8.20, Python 3.12.11,
  `UV_PROJECT_ENVIRONMENT=.venv-root-collect uv sync --frozen --extra test`.
- Verpflichtende Imports: `mcp` und `scrapy` muessen in diesem Environment
  importierbar sein; fehlende Imports sind Environment-FAIL, kein Skip-Grund.
- Root-Grenze: `collect_ignore` enthaelt exakt `api_test` und `oc2ov_test`.
- Gemini: kein Import von `gemini_embedders` bei Modul-Collection; Import erst
  in Fixture bzw. direktem Task-Type-Test; kein `importorskip`.
- Gate: vollstaendige Root-Collection Exit 0, mindestens ein Root-Node, keine
  Node-ID aus den beiden Standalone-Harnesses und kein Lockfile-Drift.

Erreichte Evidenz: 6382 Root-Tests gesammelt, null Collection-Fehler. Die
bekannten Marker- und Hilfsklassen-Warnungen bleiben separat offen.
