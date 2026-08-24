# Test Dossier

## Codex-Compaction und Responses State

Stand: 2026-08-01
Status: Offline-Follow-up PASS; 500/500 konsolidiert und 150/150 Watch; H1/H2 HOLD

## 1. Testabsicht

Die Tests kodieren die Sicherheits- und Kontinuitätsgründe der Änderung:

- untrusted Hook-Eingaben dürfen nicht in den nächsten Prompt gelangen;
- ein unsicherer Dateipfad darf keine private Kontinuitätsdatei ersetzen;
- State darf weder Chain-, Credential- noch Generationsgrenzen überschreiten;
- ein unvollständiger Stream darf keinen neuen State veröffentlichen;
- Tool-Ausgaben dürfen genau einmal und nur für offene IDs angenommen werden;
- Compaction darf keine Items nach dem neuesten Compaction-Item verlieren;
- der Legacy-Pfad muss Default bleiben.

## 2. Testinventar

| Datei | Fokus | Ergebnis |
|---|---|---:|
| `tests/unit/test_codex_compaction_hook.py` | Rechte, Directory-FDs, Deadline, Retention, Korrelation, Parallelität, Injection | 30 PASS |
| `tests/unit/test_codex_responses_state.py` | State, Streaming, Cleanup, Credential-Bindung, Compaction, Tool-Calls, Limits, Config | 72 PASS |
| **Gesamt neu** |  | **102 PASS** |

## 3. Hook-Abdeckung

Die Hook-Suite prüft:

- ausschließlich konstante Ausgabe bei bösartigen Transcript-, Pfad- und
  Repository-Feldern;
- `0700`-Verzeichnis und `0600`-Datei;
- atomaren Austausch und Parallelzugriffe;
- Symlink-Ablehnung am Ziel und in jeder Pfadkomponente von `CODEX_HOME` bis
  `state/compaction-hooks`;
- Eigentümer- und Verzeichnisinvarianten;
- keine erneute Pfadauflösung nach einer Directory-FD-verankerten Prüfung;
- 64-KiB-Eingabelimit und erzwungene externe Fünf-Sekunden-Deadline;
- höchstens 256 Records, 24 Stunden TTL, maximal 1024 untersuchte Einträge und
  idempotente Retention bei Parallelzugriffen;
- PreCompact/SessionStart/PostCompact-Korrelation;
- keine behauptete semantische Transcript-Vollständigkeit.

## 4. Responses-Abdeckung

Die State-Suite prüft:

- immutable Branches und explizites Forking;
- Bindings für Modell, Instructions, Origin, Principal und Credential;
- stale Generation, Replay, manipulierten Integrity-Tag und TTL;
- vollständige Reasoning-, Tool-, Output- und Compaction-Items;
- Beschneidung ausschließlich vor dem neuesten Compaction-Item;
- parallele und verschachtelte Chains ohne Datenübertritt;
- Tool-Ausgabe genau einmal für offene IDs;
- Timeout, Cancellation, Fehler und Teil-Stream ohne State-Commit;
- keine automatische Wiederholung nach dem ersten Event;
- natives Async-Streaming und Sync-/Async-Parität;
- State-, Item-, Turn-, Bild-, Tool-Ausgabe- und Chain-Limits;
- Sentinel-Secrets in Log-Capture;
- keine sichtbaren oder opaken Tool-Inhalte in State-spezifischen Traces;
- threadsichere Singleton-Initialisierung der Adapter;
- Credential-I/O außerhalb des Async-Event-Loops;
- stabile Credential-Slot-Bindung bei wechselndem Resolver-Owner auch ohne
  `client_id`;
- vollständiges Stream- und Client-Cleanup trotz wiederholter Cancellation oder
  Fehler im ersten Close;
- maximal 4096 retained Tool-Call-IDs, 512 Bytes je ID und Einrechnung in die
  State-Byte-Grenze;
- `store=false` und Ablehnung von Conversations,
  `previous_response_id`, `background` und `extra_body`-Umgehungen;
- OAuth-Origin und Single-Credential-Pilot;
- Capability-Fehler ohne stillen Fallback;
- unveränderte zustandslose Legacy-Aufrufe.

## 5. Frische Testevidenz

### 5.1 Neue Suiten

```text
102 passed, 4 warnings
```

Bewertung: Kandidaten-Gate PASS; keine Skips oder Xfails.

### 5.2 Core-Kombination

Enthalten:

- beide neuen Suiten;
- `tests/unit/test_codex_vlm.py`;
- `tests/models/vlm/test_timeout_config.py`.

```text
132 collected
131 passed
1 failed
4 warnings
```

Einziger Fehler:

```text
tests/unit/test_codex_vlm.py::test_vlm_config_default_provider_resolves_codex
```

Der unveränderte Basis-Checkout liefert für die betroffene Suite 29 PASS und
denselben einen Fehler. Das ist keine Kandidatenregression, aber ein Legacy-HOLD.

### 5.3 Erweiterte Kombination

Zusätzlich enthalten: `tests/unit/test_stream_config_vlm.py`.

```text
152 collected
140 passed
12 failed
4 warnings
```

Elf Stream-Config-Fehler reproduzieren auf der Basis mit 9 PASS und 11 FAIL.
Zusammen mit dem bekannten Codex-Config-Fehler sind alle zwölf Fehler als
vorbestehend bestätigt. Die Legacy-Suite ist trotzdem nicht vollständig grün.

### 5.4 Statische Prüfungen

```text
ruff check:        PASS
ruff format --check: 8 files already formatted
python -m compileall -q: PASS
git diff --check:  PASS
```

## 6. MCP- und Live-Grenze

Der gemeinsam genutzte OpenViking-Dienst auf `127.0.0.1:1933` bestand einen
Health-Check und einen read-only `search_experience`-Aufruf. Es erfolgte kein
Restart. Das beweist den MCP-Zugriff, nicht die Codex-Responses-Capability.

Nicht ausgeführt:

- Capability-Probe für `context_management` am exakten Codex-Endpunkt;
- Live-Nachweis von Compaction-Items und Replay;
- Canary mit echter Chain;
- A/B-Matrix mit 20 realen und 10 synthetischen Szenarien.

Der Capability-Probe kann einen Provider-Request und damit Kosten auslösen. Er
bedarf ausdrücklicher Genehmigung.

## 7. Test-Simulation

Vor Implementierung wurde die Teststrategie anhand der Kriterien
Vollständigkeit, Determinismus, Isolation, Security, Mutation-Sensitivität,
Async/Sync-Parität, Legacy-Schutz und Diagnosefähigkeit bewertet.

| Kriterium | Wert |
|---|---:|
| Vertragsvollständigkeit | 98 % |
| Determinismus | 98 % |
| Isolation | 97 % |
| Security | 98 % |
| Mutation-Sensitivität | 96 % |
| Async/Sync-Parität | 97 % |
| Legacy-Schutz | 96 % |
| Diagnosefähigkeit | 98 % |
| **Aggregiert** | **97,2 %** |

Damit sind mindestens 95 % aggregiert und mindestens 90 % je Kriterium erreicht.
Die Simulation ersetzt keine Live- oder unabhängige Security-Evidenz.

## 8. Freigabebewertung

| Gate | Status |
|---|---|
| Neue kritische Tests 100 % | PASS |
| Null Cross-Chain-Leaks in Offline-Tests | PASS |
| Kandidatenregression in geprüften Suites | Keine nachgewiesen |
| Legacy vollständig grün | HOLD |
| 20+10 A/B-Corpus | HOLD |
| Exakter Endpoint-Probe und Canary | HOLD |
| Qualitäts-/Token-/Latenz-/Fehler-Promotion | HOLD |

Gesamturteil: **Implementierter Offline-Kandidat, nicht aktiviert und nicht zur
Default-Promotion freigegeben.**

Der frühere Security-Re-Review Revision 2 des Responses-State-Kandidaten meldet
keine offenen Critical-/High-Befunde und hebt dessen Offline-Veto auf. Score:
95,6 % aggregiert, mindestens
91 % je Kriterium. Die Bewertung ist vorläufig, weil das geforderte aktuelle
Claude Opus nicht verfügbar war und Codex als Ersatzmodell diente. Die drei
Medium-Restbefunde wurden im Follow-up-Commit
`325e5cff3895036a2fc0e8a0a93131e77f7c9d0d` geschlossen; der Randfall aus
Cancellation plus Close-Fehler wurde mit `0556a9aac049d2563893e1abe4068c0260024542`
ergänzt. Die formale Bewertung bleibt wegen des
Ersatzreviewers vorläufig.

Diese frühere Bewertung ist vom späteren Legacy-VLM-H3-Security-Review getrennt.

## 9. Reproduktion

Die verwendete Testumgebung nutzt den OpenViking-Python-Interpreter und einen
isolierten Offline-Dependency-Pfad:

```bash
PYTHONPATH=/tmp/openviking-codex-responses-test-deps-20260731 \
  /Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest \
  -q -o addopts= \
  tests/unit/test_codex_compaction_hook.py \
  tests/unit/test_codex_responses_state.py
```

Der temporäre Dependency-Pfad enthält keine produktive Aktivierung.

## 10. Verknüpfte Artefakte

- [Implementation Dossier](../dossiers/2026-07-31-codex-compaction-openviking-responses-id.md)
- [TRD](../dossiers/2026-07-31-codex-compaction-openviking-responses-trd.md)
- [Open Items](../sessions/2026-07-31-codex-compaction-openviking-responses-open-items.md)

## 11. Legacy-VLM-H3 Testdefinition — 2026-07-31

Status: **Testdefinition vor Implementierung.** Dieser Abschnitt setzt ID §9
Revision 3 in deterministische RED-/GREEN-Verträge um. Er ist keine neue
Testergebnis-Evidenz und ändert die historischen Ergebnisse in §5 nicht.

### 11.1 Gefrorene Baseline und Testscope

| Matrix | Exaktes Ausgangsergebnis | Fehlerklassifikation |
|---|---:|---|
| gezielt | 46 gesammelt; 33 PASS; 13 FAIL | 2 stale exakte Provider-`Dict`-Assertions und 11 Streaming-Fehler |
| breit | 216 gesammelt; 195 PASS; 21 FAIL | dieselben 13 plus 8 separate, vorbestehende VolcEngine-Konstruktor-Testfehler |

Die acht VolcEngine-Konstruktorfehler sind weder Reparaturziel noch zulässiger
Grund, die breite Matrix pauschal grün oder rot dem H3-Follow-up zuzurechnen. Sie
werden nach Test-ID separat berichtet. Produktionsänderungen an `VLMConfig` oder
am VolcEngine-Konstruktor sind durch diese Testdefinition nicht erlaubt.

Exakt diese sechs bestehenden Testdateien werden im H3-Follow-up geändert:

1. `tests/unit/test_codex_vlm.py`;
2. `tests/unit/test_kimi_glm_vlm.py`;
3. `tests/unit/test_stream_config_vlm.py`;
4. `tests/unit/test_model_retry.py`;
5. `tests/unit/test_vlm_failover.py`;
6. `tests/unit/test_vikingbot_vlm_adapter_retry.py`.

Keine siebte Testdatei, kein Produktionsfile und kein anderes Dossier gehört zum
Testdefinitionsdiff.

### 11.2 RED-Ausgangserwartung

Der erste TDD-Schnitt ist absichtlich zweigeteilt:

- In `test_codex_vlm.py` und `test_kimi_glm_vlm.py` werden nur die zwei stale
  Voll-`Dict`-Vergleiche durch semantische Feldassertions ersetzt. Danach müssen
  genau diese beiden Provider-Tests **GREEN** sein, ohne Produktionsänderung.
- Die bestehenden elf Streaming-Verträge und die neu definierten Streaming-,
  Cleanup-, Marker-, Wrapper- und Adapterverträge müssen vor der
  Produktionsimplementierung **RED** sein. Ihr RED muss auf exakt fehlende
  Produktion zurückgehen, etwa fehlende Stream-Reducer, fehlenden lokalen
  Preflight, fehlende Marker-Helper oder fehlende Catch-Phase-Prüfung. Import-,
  Fixture-, Dependency-, Syntax- oder Test-Harness-Fehler sind kein gültiges RED.

Nach jeder RED-Aufnahme wird die konkrete Assertion, der erwartete
Produktionsvertrag und der beobachtete Failure-Trace festgehalten. Ein Test, der
bereits ohne die beabsichtigte Produktion grün ist, muss mutationssensitiv
verschärft werden; er darf nicht als Implementierungsbeweis gezählt werden.

### 11.3 Semantische Provider-Assertions

`test_vlm_config_default_provider_resolves_codex` prüft nach der Korrektur:

- `provider_name == "openai-codex"`;
- die fachlich relevanten, normalisierten Codex-Felder besitzen die erwarteten
  Werte beziehungsweise Defaults;
- fremde Providerwerte, insbesondere der OpenAI-Test-Key, werden nicht als
  Codex-Credential übernommen.

Der betroffene GLM/Kimi-Test prüft entsprechend Providername, `api_key` und nur
die vertraglich relevanten normalisierten Felder. Beide Tests dürfen weder die
gesamte interne Mapping-Struktur noch die Abwesenheit neuer Defaultfelder
assertieren. Ein Production-Diff an
`openviking_cli/utils/config/vlm_config.py` lässt diesen Slice unabhängig vom
Testergebnis scheitern.

### 11.4 Deterministische Fakes und Spies

Die Tests verwenden kleine lokale Fakes statt Provider- oder Netzwerkzugriff:

| Fake/Spy | Verbindliche Fähigkeit |
|---|---|
| `FakeUsage` | Prompt-/Completion-/Total-Tokens sowie verschachtelte Cached- und Reasoning-Token-Details; jede Instanz ist eindeutig identifizierbar |
| `FakeChunk` | frei kombinierbare Choices, String-Content und Usage; contentless, komplett leer und malformed möglich |
| `FakeSyncStream` | skriptbare Events und Iteratorfehler; `read_count`, `close_count`, gespeicherte Close-Exception und Objektidentität |
| `FakeAsyncStream` | skriptbarer Async-Iterator mit Barrieren vor Event, Fehler und Cleanup; zählt `__anext__`, `close`, `aclose` und erzeugte Cleanup-Tasks |
| Close-Varianten | awaitbares `close`, nicht-awaitbares `close`, nur `aclose`, beide Methoden vorhanden, sowie Cleanup-Fehler mit Sentinel-Payload |
| Client-/Create-Spy | getrennte Zähler für Clientfactory und `chat.completions.create`; kann Erstellungsfehlerfolgen liefern, ohne Iteratorfehler als Creation zu maskieren |
| Credential-Spy | zählt Resolver-/Refresh-/Dateizugriffe und schlägt bei unerwartetem Zugriff laut fehl |
| Vision-I/O-Spy | Path-/Dateiobjekt, dessen `open`, `read`, Encoding oder Metadatenzugriff zählt und vor Preflight laut fehlschlägt |
| Exception-Graph-Builder | baut direkte Marker, beide Cause-/Context-Kanten, Zyklen und verschachtelte `AllCredentialsFailedError`-Kinder unter Erhalt der Objektidentität |
| Retry-Spies | getrennte Callback-, Logger-, Delay-, Sleep- und Operation-Zähler |
| Wrapper-Mutator-Spies | Snapshots und Aufrufzähler für Annotation, Klassifikation, Aggregation, Switcher und Credentialmutation nach Eintritt in die Catch-Phase |
| VikingBot-Event-Fake | native content-, reasoning-only-, tool-only-, usage-only- und vollständig leere Events plus danach injizierbaren Fehler |

Fakes dürfen keine echte Credentialquelle lesen, keine Zeit schlafen und kein
Netzwerk öffnen. Barrieren werden mit `asyncio.Event` oder kontrollierten
Futures gesteuert; Timing-only-Assertions sind unzulässig.

#### 11.4.1 Pre-change SHA-256 manifest

Die Testdefinition wiederholt die in ID §9.4.1 verifizierte Rollback-Baseline
für exakt vier Produktions- und sechs Testdateien:

| Datei | Pre-change SHA-256 |
|---|---|
| `openviking/models/vlm/backends/openai_vlm.py` | `0603fb14f432e2f95e2352d3417ea95152011a6ab8360e1ab5446b45c90d912c` |
| `openviking/utils/model_retry.py` | `98d93ae30a3f2752950bc54dff0c756eeb2b86a77e2cb04e89f141c6d7585839` |
| `openviking/models/vlm/base.py` | `799ddd6b3e689da4afabcd54d990be387baae13c0816eeb6098fb29de6ef7ca3` |
| `bot/vikingbot/providers/vlm_adapter.py` | `1fe538363f1f9e412089a3a8fe3efa6b7fd88643065f616d207b5a9b14c62385` |
| `tests/unit/test_codex_vlm.py` | `1c95a8b397f023a6e8edfc3a4e791ef190f28272312cd7046a1df3d7057c2d88` |
| `tests/unit/test_kimi_glm_vlm.py` | `19ed1576026da1e8724940e5dad20331b3e241871b2627ca710c0bfb8ede855b` |
| `tests/unit/test_stream_config_vlm.py` | `5756bdd5597a4610bfa9b94f0a9e8a62f2c1a742fd953fac4b7a429eceed446a` |
| `tests/unit/test_model_retry.py` | `b4344eb4e857ee9484a072e0287ab2ca8fa52564c9cec186b71dbb41bb695f08` |
| `tests/unit/test_vlm_failover.py` | `2d115b829353a4d93141bf0c0556a86131b92868ccaf97083f2ec9201a240224` |
| `tests/unit/test_vikingbot_vlm_adapter_retry.py` | `43f3d3a815d421b925e60bd3264a7f79ca7a2df3a2ee631888a116b9b5142569` |

Jeder spätere Rollback-Test vergleicht alle zehn Werte; ein einzelner Drift ist
ein fehlgeschlagenes Rollback-Gate.

### 11.5 OpenAI-kompatibler Request- und Reducer-Vertrag

#### 11.5.1 Vier lokale Tool-Stream-Preflights

Je ein Test deckt Text Sync, Text Async, Vision Sync und Vision Async mit
`stream=True` plus nichtleeren `tools` ab. Jeder erwartet
`NotImplementedError`. Vor diesem Fehler müssen alle Zähler exakt null sein:

- Request-Builder;
- Sync-/Async-Clientfactory;
- Credentialresolver, Refresh und Credential-Datei-I/O;
- Provider-Create und Netzwerk;
- für beide Visionpfade zusätzlich Bild-`open`, `read`, Encoding und sonstige
  Vision-Datei-/Metadaten-I/O.

Damit beweist der Test nicht nur Fehlerart, sondern auch die vorgeschriebene
Reihenfolge des Guards.

#### 11.5.2 Explizites `stream`-Flag

Text und Vision werden jeweils für Sync und Async mit `stream=False` und
`stream=True` parametrisiert: acht Kombinationen. Der Create-Spy muss das
explizite boolesche Feld in jeder Anfrage sehen. `False` bewahrt den bisherigen
Non-Streaming-Rückgabetyp und die Tool-Semantik; `True` ist nur ohne Tools
zulässig und verwendet den passenden Iterator.

#### 11.5.3 Content und Usage

Reducer-Tests kodieren:

- nur String-Content wird in Providerreihenfolge genau einmal verkettet;
- `None`, contentless Events und vollständig leere Events beenden den Stream
  nicht und erzeugen keinen Text;
- usage-only Events sind gültig;
- von mehreren Usage-Werten wird nur der letzte belastbare Wert genau einmal an
  den lokalen Usage-Tracker übergeben;
- Cached- und Reasoning-Token-Details dieses letzten Werts bleiben erhalten;
- es gibt ausdrücklich keinen Test, der Reasoning-Text-Deltas im OpenAI-VLM-
  Reducer aggregiert.

Sync und Async verwenden dieselben Ereignisskripte und müssen denselben String
und dieselbe einzelne Usage-Aktualisierung liefern.

### 11.6 Retry- und Marker-Vertrag

#### 11.6.1 Creation-only Retry

Der Client-Create-Fake liefert zunächst einen klassifizierten transienten
Erstellungsfehler und danach einen Stream. Der Test beweist Retry nur innerhalb
des konfigurierten Creation-Budgets. Sobald ein Streamobjekt zurückgegeben wurde,
werden Iterator-, Parser- und Cleanup-Fehler lokal nie wiederholt.

Ein Iteratorfehler vor dem ersten Event bleibt unmarkiert, ist lokal unretried
und darf erst außerhalb `OpenAIVLM` klassifiziert werden. Ein Fehler nach einem
beliebigen gelesenen Event ist markiert; der lokale Provider-Create-Call-Count
bleibt exakt eins.

#### 11.6.2 Marker-Graph

`tests/unit/test_model_retry.py` deckt mindestens ab:

- direkt markierte Exception;
- Marker nur in `__cause__`;
- Marker nur in `__context__`;
- verschiedene Markerpfade gleichzeitig;
- zyklische Cause-/Context-Graphen ohne Endlosschleife;
- Marker in jedem Kind und in verschachtelten Kindern eines
  `AllCredentialsFailedError`;
- nicht markierter Graph als negative Kontrolle.

Die Suche folgt an jedem Knoten beiden Kanten, verwendet Objektidentität als
Visited-Key und terminiert auch bei Selbst- und Mehrknotenzyklen.

#### 11.6.3 Boolesche Classifier und Retry-Wrapper

`is_retryable_api_error()` und `is_retryable_rate_limit_error()` liefern für
jeden erreichbaren Marker `False`; `classify_api_error()` bleibt unverändert.
`retry_sync()` und `retry_async()` werfen markierte Fehler identisch erneut,
bevor ein Default- oder Custom-Callback ausgeführt wird. Pro Fall werden
Callback-, Logger-, Delay-, Sleep- und zweiter Operation-Call-Count mit exakt
null assertiert.

### 11.7 Failover-, MultiCredential- und VikingBot-Vertrag

#### 11.7.1 Wrapper Catch-Phase

Für `FailoverVLM` und `MultiCredentialVLM`, jeweils Sync und Async, injiziert
der Provider eine markierte Exception. Unmittelbar nach Eintritt in die
Catch-Phase muss die Markerprüfung erfolgen und dasselbe Exceptionobjekt erneut
geworfen werden. Dafür wird keine neue test-only Wrapper-Abstraktion und kein
Spy auf die lokale `aggregated_errors`-Liste eingeführt. Stattdessen nimmt ein
Provider-Side-Effect unmittelbar beim Provideraufruf einen Pre-Catch-Snapshot
aller beobachtbaren Catch-relevanten Zustände. Mutator-Spies beweisen danach
exakt null Aufrufe für:

- Fehlerannotation;
- `classify_api_error()`;
- Fehleraggregation;
- Switcher-Failure-/Success-Mutation;
- Credentialindex- oder Credentialwechsel;
- zweiten Provideraufruf.

Das identische Exceptionobjekt wird erneut geworfen; der nach Catch beobachtete
Zustand muss dem beim Provider-Side-Effect aufgenommenen Pre-Catch-Snapshot
entsprechen. Planmäßige Selection-/Failback-Aktionen vor dem Provideraufruf
werden dadurch nicht fälschlich als Catch-Phase-Verstoß bewertet. Negative
Kontrollen belegen, dass ein unmarkierter Fehler vor dem ersten Streamevent den
bisherigen äußeren Failoverpfad weiterhin verwenden kann.

#### 11.7.2 VikingBot

`VLMProviderAdapter.chat()` erhält eine markierte rate-limit-ähnliche Exception
und muss sie vor `is_retryable_rate_limit_error()`, Logging, Sleep oder zweitem
Aufruf stoppen.

Für `_chat_stream_volcengine()` werden fünf getrennte native Eventformen
getestet: leer, usage-only, reasoning-only, content und tool-only. Direkt nach
jedem Eintritt in den `async for`-Body muss `saw_event` gesetzt sein, bevor die
Form geprüft wird. Ein unmittelbar danach injizierter Fehler ist in allen fünf
Fällen markiert; Provider-Create bleibt eins und der Adapter replayt nicht.
Bestehende sichtbare Reasoning-, Content- und Tool-Eventemission wird lediglich
regressiert, nicht in den OpenAI-VLM-String-Reducer übertragen.

Ein zusätzlicher Reihenfolgetest liefert ein Event, dessen erster Zugriff auf
`usage` oder eine beliebige andere Property sofort eine Sentinel-Exception
wirft. Obwohl die Eventauswertung damit bei der ersten Property scheitert, muss
der Fehler markiert sein: `saw_event = True` war bereits die erste Anweisung des
Loop-Bodys. Rate-limit-Classifier, Logger, Sleep und zweiter Provideraufruf
bleiben bei Count null.

### 11.8 Cleanup- und Outcome-Matrix

#### 11.8.1 Sync

Jeder erfolgreich erstellte Sync-Stream erhält bei normalem Ende,
Iteratorfehler, Parserfehler und Usage-Fehler exakt einen `close()`-Aufruf. Ein
zweiter Close ist auch dann verboten, wenn Close selbst fehlschlägt. Nach einem
gelesenen Event wird ein Cleanup-only-Fehler vor Propagation markiert.

#### 11.8.2 Async Close-Auswahl

Jeder erfolgreich erstellte Async-Stream erzeugt genau einen Cleanup-Task:

- vorhandenes awaitbares `close()` wird einmal aufgerufen und awaited;
- vorhandenes nicht-awaitbares `close()` beendet Cleanup; ein ebenfalls
  vorhandenes `aclose()` bleibt bei Count null;
- nur bei fehlendem `close` wird `aclose()` einmal aufgerufen und awaited;
- der eine Task wird in einer `shield`-Schleife bis `done()` abgewartet;
- eine zweite oder weitere Cancellation wird erfasst, erzeugt aber weder einen
  zweiten Task noch einen weiteren Close.

Ein Barrier-Fake lässt Cancellation gezielt während Cleanup nach erfolgreichem
Body eintreten. Cleanup muss zuerst vollständig enden; erst danach wird die erste
Wartephasen-Cancellation propagiert.

Der Repeated-Cancellation-Test verwendet keine Sleeps, sondern getrennte
`asyncio.Event`-/Future-Barrieren für (a) Eintritt in Cleanup, (b) erste
Cancellation beobachtet, (c) zweite Cancellation beobachtet und (d) Freigabe
des Close. Beide Cancellation-Signale müssen denselben Cleanup-Task betreffen;
Task-Identität und Close-Count eins werden explizit assertiert. Body-Cancellation
und Waiting-Cancellation verwenden unterscheidbare Exceptionobjekte oder
Nachrichten, sodass Identität und Priorität nicht nur über den Typ geprüft werden.

Der pytest-Harness startet ausschließlich den zu prüfenden Subject-Coroutine mit
`asyncio.create_task()` und cancelt nur diese Subject-Task, niemals den laufenden
pytest-Task. Nach dem Signal gilt `subject.cancelling() >= 1`. Anschließend wird
das Subject gezielt entweder unter `pytest.raises(asyncio.CancelledError)` oder
unter Erwartung der identischen Primärexception awaited. Jede Hilfstask wird
kontrolliert abgeschlossen und eingesammelt; damit darf keine unhandled Runner-
Cancellation aus dem Test entweichen.

#### 11.8.3 Vollständige Fehler- und Identitätsmatrix

| Body | Cleanup | Cancellation beim Cleanup-Wait | Assertion |
|---|---|---|---|
| Erfolg | Erfolg | keine | identisches Ergebnis; ein Cleanup-Task |
| Primärfehler | Erfolg | keine | identisches Primärfehlerobjekt |
| Body-`CancelledError` | Erfolg | keine oder weitere | identischer ursprünglicher `CancelledError` nach Cleanup |
| Erfolg | Cleanupfehler | keine | Cleanupfehler; nach Event markiert |
| Primärfehler | Cleanupfehler | keine | identischer Primärfehler; Cleanup nur redigiert geloggt |
| Body-`CancelledError` | Cleanupfehler | keine oder weitere | identischer Body-`CancelledError`; Cleanup nur redigiert geloggt |
| Erfolg | Erfolg oder Cleanupfehler | eine oder mehrere | erste Wartephasen-Cancellation nach abgeschlossenem Cleanup; kein zweiter Task/Close |
| Primärfehler | Erfolg oder Cleanupfehler | eine oder mehrere | identischer Primärfehler; Pending-Cancellation bleibt erhalten; Cleanupfehler redigiert |

Für jede Zeile mit Cleanupfehler enthält dessen Nachricht ein eindeutiges
Sentinel-Secret. Der Log-Capture muss den festen Cleanup-Hinweis enthalten und
das Sentinel, Exception-`repr`, Prompt, Credential sowie Streamobjekt vollständig
ausschließen. Identitätsassertions verwenden `is`, nicht nur Typ oder Nachricht.

### 11.9 Testbefehle und Gates

#### 11.9.1 Zwei semantische Provider-Tests

```bash
PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  .venv/bin/python -m pytest -q -o addopts= \
  tests/unit/test_codex_vlm.py::test_vlm_config_default_provider_resolves_codex \
  tests/unit/test_kimi_glm_vlm.py::test_vlm_config_uses_canonical_provider_names
```

Erwartung nach dem reinen Assertionfix: die zwei vormals stale Assertions sind
GREEN; es gibt keinen Produktionsdiff an `vlm_config.py`.

#### 11.9.2 Gefrorene 46er-Matrix

```bash
PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  .venv/bin/python -m pytest -q -o addopts= \
  tests/unit/test_codex_vlm.py \
  tests/unit/test_kimi_glm_vlm.py \
  tests/unit/test_stream_config_vlm.py
```

Vor Produktionsimplementierung ist die Referenz `33 PASS / 13 FAIL`. Nach dem
Assertionfix, aber vor Streaming-Produktion, müssen die zwei Providerfälle grün
und die elf Streamingfälle aus fehlender Produktion weiterhin rot sein. Das
finale H3-Gate verlangt 46/46 ohne Skip oder Xfail.

#### 11.9.3 Alle sechs H3-Testdateien

```bash
PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  .venv/bin/python -m pytest -q -o addopts= \
  tests/unit/test_codex_vlm.py \
  tests/unit/test_kimi_glm_vlm.py \
  tests/unit/test_stream_config_vlm.py \
  tests/unit/test_model_retry.py \
  tests/unit/test_vlm_failover.py \
  tests/unit/test_vikingbot_vlm_adapter_retry.py
```

#### 11.9.4 Exakte breite 216er-Matrix

```bash
PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  /Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest -q -o addopts= \
  tests/unit/test_codex_vlm.py \
  tests/unit/test_kimi_glm_vlm.py \
  tests/unit/test_stream_config_vlm.py \
  tests/unit/test_extra_headers_vlm.py \
  tests/unit/test_litellm_vlm_gemini_cache.py \
  tests/unit/test_model_retry.py \
  tests/unit/test_vlm_failover.py \
  tests/unit/test_vlm_reasoning_models.py \
  tests/unit/test_vlm_response_formats.py \
  tests/unit/test_vlm_thinking_param.py \
  tests/models/vlm/test_timeout_config.py \
  tests/models/vlm/test_volcengine_cache.py
```

Gefrorene Ausgangserwartung: vorerst `216 collected`, `195 PASS`, `21 FAIL`.
Davon sind 13 H3-Fälle und acht separate vorbestehende VolcEngine-Konstruktor-
Testfehler. Jede spätere Abweichung der Collection-Zahl wird vor inhaltlicher
Bewertung als Testinventar-Drift aufgeklärt.

#### 11.9.5 TD-Simulationsverlauf Revision 3

| Revision | Aggregiert | Niedrigstes Einzelkriterium | Urteil |
|---|---:|---:|---|
| Revision 1 | 91.8% | 88% | HOLD |
| Revision 2 | 95.4% | 93% | Gate rechnerisch erreicht; Präzisionsreview offen |
| Revision 3 | 97.1% | 96% | PASS für Testdefinitionsübergabe |

Revision 3 erfüllt mindestens 95% aggregiert und mindestens 90% je Kriterium.
Die tatsächliche Testausführung bleibt dennoch **HOLD**, bis die Tests im
vorgeschriebenen RED-/GREEN-Zyklus geschrieben und ausgeführt werden. Die
Simulation ist weder Testergebnis noch Live-Evidenz.

#### 11.9.6 Mutationstestziele

Die kritischen Verträge müssen mindestens gegen folgende gezielte Mutationen
empfindlich sein:

- Verschieben des Tool-Stream-Guards hinter Requestbau, Client-, Credential- oder
  Vision-I/O;
- Weglassen eines expliziten `stream=False` oder `stream=True`;
- Aggregation von Reasoning-Text oder Verwendung der ersten statt letzten Usage;
- zweites Usage-Tracker-Update oder Verlust von Cached-/Reasoning-Token-Details;
- lokaler Retry eines Iterator-, Parser- oder Cleanupfehlers;
- Setzen von `saw_event` erst nach dem ersten Property-Zugriff;
- Traversieren nur von `__cause__` oder nur von `__context__`, fehlende
  Zyklussicherung oder Überspringen eines `AllCredentialsFailedError`-Kindes;
- Aufruf von Custom-Callback, Logger oder Sleep trotz Marker;
- Catch-Phase-Annotation, Klassifikation, Aggregation, Switcher-/Credential-
  Mutation oder zweiter Provideraufruf vor dem identischen Rethrow;
- zweiter Cleanup-Task, zweiter Close, `aclose()` nach vorhandenem
  nicht-awaitbarem `close()` oder Abbruch des Cleanup durch wiederholte
  Cancellation;
- Ersetzen der Primärexception, Verlust der Cancellation-Priorität oder Leaken
  eines Sentinel-Secrets in Cleanup-Logs;
- Cancellation des pytest-Runner-Tasks statt ausschließlich der Subject-Task.

Jede dieser Mutationen muss mindestens einen klar zugeordneten Test rot machen.
Ein überlebender kritischer Mutant ist ein Testdefinitions-HOLD.

#### 11.9.7 Freigabekriterien

- Testdefinitionssimulation mindestens 95% aggregiert und mindestens 90% je
  Einzelkriterium; die bestehende globale TD-Simulation ersetzt keine neue
  H3-spezifische Bewertung.
- 100% der kritischen H3-Verträge bestehen; keine Skips, Xfails oder still
  ausgelassenen Parametrisierungen.
- Gezielte Matrix 46/46.
- Breite 216er-Matrix ohne Fehler aus den 13 H3-Fällen; die acht separaten
  VolcEngine-Konstruktorfälle werden mit ihren exakten Test-IDs ausgewiesen und
  nicht durch Xfail oder Filter verborgen.
- Non-Streaming- und bestehende Legacy-Semantik unverändert; null unerwartete
  Client-, Credential-, Vision-I/O-, Callback-, Logger-, Sleep-, Mutator- oder
  Replay-Aufrufe.
- Bei einem nicht eindeutig aus fehlender Produktion erklärbaren RED, einem
  instabilen Timing-Test oder einem Scope-Diff außerhalb der sechs Testdateien:
  STOP und Testdefinition korrigieren, bevor Produktion geändert wird.

### 11.10 Live- und Betriebsgrenze

Provider-Live-Tests bleiben **HOLD**. Es wird kein API-Key angefordert, gelesen
oder erzeugt und kein OpenAI-, Codex- oder VolcEngine-Providerrequest gesendet.
Ein Offline-GREEN ist kein Live-Capability-Beweis. Diese Testdefinition
autorisiert weder Restart, Aktivierung, Push, PR, Merge, Canary noch Promotion.

## 12. Security RED Revision 1 — 2026-07-31

### 12.1 VETO und RED-Status

Security Revision 1 bewertet die Definition mit **78/100, 0 Critical, 5 High,
1 Medium** und setzt ein VETO. Source und Testimplementierung sind gesperrt. Die
folgenden Fälle sind die nächste Test-RED-Revision; sie wurden noch nicht
geschrieben oder ausgeführt und dürfen nicht als RED-Evidenz bezeichnet werden.

Ein späterer gültiger RED-Lauf muss aus exakt fehlender H1–H5-Produktion
scheitern, nicht aus Import-, Fixture-, Dependency-, Timing- oder Harnessfehlern.

### 12.2 H1 — Keine markierten Fehlerdaten in Senken

Ein markierter Fehler trägt unterschiedliche Sentinel-Secrets in Nachricht,
Args, `repr` und verkettetem Fehler. Für VikingBot `chat` und nativen Stream
werden Responseinhalt, strukturierte Fehlerkategorie, alle Logger-Records und der
vollständige Langfuse-Payload erfasst. Assertions:

- ausschließlich fester redigierter Text und feste Kategorie;
- kein Sentinel, `str`, `repr`, Traceback, Prompt, Credential oder Eventinhalt;
- Rate-limit-Classifier, Sleep, Retry und zweiter Provideraufruf jeweils null.

### 12.3 H2 — Begrenzter Markergraph

Parametrisierte Graph-Fakes erzeugen:

- breite Graphen an und über 256 Nodes beziehungsweise 512 Edges;
- tiefe Cause-/Context-Ketten, beide Kanten je Node und Zyklen;
- Aggregate an und über 256 Kindern sowie verschachtelte Aggregate;
- Attribute, die beim Lesen werfen, nichtiterierbare/malformed `errors` und
  Kinder mit unlesbaren Feldern.

Innerhalb des Budgets wird jeder erreichbare Marker gefunden. Der erste
Budgetüberlauf oder jede unreadable/malformed Struktur liefert `True`. Zähler
beweisen höchstens 256 besuchte Nodes, 512 untersuchte Edges und 256 gelesene
Aggregate-Kinder; Laufzeit- oder Sleep-basierte Grenzen sind unzulässig.

### 12.4 H3 — Wrapper-Level Tool-Stream-Preflight

Für Failover und MultiCredential werden Text/Vision Sync/Async mit nichtleeren
Tools parametrisiert. Zielbäume enthalten einheitlich `stream=False`, ein
mögliches `stream=True`, heterogene Streammodi, verschachtelte Wrapper sowie
unlesbare/malformed Ziele. Unsichere oder uneindeutige Fälle erwarten lokalen
`NotImplementedError` aus `_validate_stream_request(tools)`.

Die Prüfung muss vor `should_try_primary()`, `maybe_failback()`, Provider,
Credential-/Switcher-State, Requestbau und Vision-I/O liegen. Spies für alle
diese Grenzen bleiben bei null. Eine Mutation, die den Guard hinter eine einzige
Grenze verschiebt oder nur das aktuell aktive Ziel prüft, muss den Test rot
machen.

### 12.5 H4 — Fast-Fail-Szenariomatrix

Die Matrix kreuzt Text/Vision, Sync/Async mit:

1. markiertem Primary-Fehler;
2. markiertem Fehler des aktuell aktiven Backup;
3. `MultiCredentialVLM` mit aktivem Index ungleich null;
4. failback-due Auswahl.

Der Provider-Side-Effect nimmt unmittelbar beim Provideraufruf den Snapshot nach
zulässiger Pre-call-Selection. Nach Catch muss exakt dasselbe Exceptionobjekt
propagieren und der Snapshot unverändert sein. Annotator, beide booleschen Retry-
Classifier, Logger, Switcher-/Credential-Mutatoren und nächster Provider haben
Count null. Es wird keine test-only Wrapperabstraktion und kein lokaler
`aggregated_errors`-Spy eingeführt.

### 12.6 H5 — Deterministische Cancellation-Matrix

`asyncio.shield` wird durch einen kontrollierten Fake ersetzt. Vor Teststart
existieren getrennte `CancelledError`-Objekte für Body, erste und zweite
Wait-Cancellation. Events/Futures signalisieren First-observation,
Second-observation und Cleanup-release; Sleeps sind verboten. Ein Spy um
`asyncio.create_task` erfasst exakt einen Cleanup-Task und seine Identität. Der
Close-Count bleibt eins.

Verbindliche Fälle:

| Body | Cleanup | Wait-Cancellation | Erwartung |
|---|---|---|---|
| Erfolg | Fehler | erste und zweite | identisches Objekt der ersten Wait-Cancellation |
| identischer Primärfehler | Fehler | erste und zweite | identischer Primärfehler |
| vorerzeugte Body-Cancellation | beliebig | weitere Wait-Cancellations | identische Body-Cancellation |

Nur eine per `create_task` gestartete Subject-Task wird gesteuert; der pytest-
Task wird nie gecancelt. Am Ende sind Subject, Cleanup-Task und Helfer-Futures
done und awaited; `asyncio.all_tasks()` zeigt gegenüber dem Baseline-Snapshot
keine neue pending Task und der Runner meldet keine unhandled Exception.

### 12.7 M1 — Live-Gate-Testdefinition

Live-Ausführung bleibt HOLD, bis ein unveränderlicher Evidence Record enthält:

- exakter HTTPS-Allowlist-Origin
  `https://chatgpt.com/backend-api/codex`;
- genau ein Credential-Slot-Fingerprint;
- festes Modell, Visionmodus und Capabilitymenge;
- feste numerische Caps für Gesamtrequests, maximale Output-Tokens,
  Bildbytes und Gesamtkosten.

Der Harness prüft vor dem ersten Request alle Felder und setzt Provider-Retry und
Failover auf null. Fehlt ein Feld, ist das Ergebnis lokaler HOLD ohne Request.
MCP-Handshake/read-only Tool-Call laufen in einem separaten Test und erfüllen
kein M1-Kriterium.

### 12.8 Re-Review- und Ausführungsgate

Die finale Security Revision 3 muss vor Source-Unlock `0 Critical`, `0 High` und
mindestens 90/100 erreichen. Erst danach dürfen die Security-Tests geschrieben, als RED
verifiziert und im TDD-Zyklus zur Produktion geführt werden. Keine Skips, Xfails
oder still ausgelassenen Matrixzellen. Live, Restart, Merge, Push, Canary,
Aktivierung und Promotion bleiben gesondert gesperrt.

## 13. Security RED Revision 3 — Finale Testpräzisierung

### 13.1 Revisionsstatus

| Revision | Score | Befunde | Status |
|---|---:|---:|---|
| 1 | 78/100 | 0C/5H/1M | VETO |
| 2 | 84/100 | 0C/3H/1M | VETO; H4/H5 definitionsseitig geschlossen |
| 3 | 89/100 | 0C/1H/1M | HOLD; H2–H5 geschlossen, H1 offen |

Source und Tests bleiben gesperrt. Die folgenden H1–H3-Fälle sind Definition,
nicht ausgeführte RED-Evidenz. Revision 3 verfehlte `0C/0H` und mindestens
90/100; daher folgt HOLD ohne Revision 4.

### 13.2 H1 — Exact-constant Senkentests

Für einen markierten Fehler verwenden `chat()` und nativer Stream exakt:

| Senke | Exakter Erwartungswert |
|---|---|
| sichtbarer Response-Text | `VLM response interrupted after partial output.` |
| Langfuse-Kategorie | `partial_stream_non_retryable` |
| Logger-Aufruf | `VLM adapter stopped a non-retryable partial stream.` |

Je Pfad werden `LLMResponse`, Logger-Call und vollständige Langfuse-Ausgabe
einschließlich `output` und `metadata` exakt assertiert. Ein Fehlerfake verteilt
unterschiedliche Sentinels über Nachricht, Args, `repr`, Cause und Context; kein
Sentinel darf in einer Senke erscheinen. Der Test verbietet variable Sanitizer-
Aufrufe. Ein unmarkierter Legacy-Kontrollfall beweist unverändertes Altverhalten
und verhindert eine versehentliche globale Redigierung.

Mutationstestziele: Austausch eines Satzzeichens/einer Kategorie, dynamisches
`str`/`repr`, Sentinel in Langfuse `output` oder `metadata`, variabler Sanitizer
und Anwendung der Konstanten auf den unmarkierten Kontrollfall.

### 13.3 H2 — Exact-bound Graphfälle

Das Budget lautet exakt 256 Nodes, 512 erreichbare Edges und 256 Aggregate-
Kinder. Parametrisierte Fakes prüfen:

- exakt 256 wohlgeformte Aggregate-Kinder erlaubt; Kind 257 liefert `True`;
- exakt 512 erreichbare Edges erlaubt; ein Graph mit mehr als 512 erreichbaren
  Edges aus kombinierten Aggregate-, Cause- und Context-Verweisen liefert
  fail-closed `True`;
- werfender `__cause__`-Getter, werfender `__context__`-Getter und werfender
  `errors`-Getter jeweils `True`;
- malformed Aggregate-Tupel und Nicht-`BaseException`-Kind jeweils `True`;
- Marker vor dem Budgetende wird gefunden; markerloser wohlgeformter Graph
  innerhalb aller Limits bleibt `False`.

Node-Visit-, Edge- und Aggregate-Child-Instrumentierung assertiert harte
Obergrenzen von 256/512/256. Wide-, Deep- und Zyklusfälle verwenden keine
Laufzeitschwelle. Mutationen `512 → unbounded`, nur Cause, nur Context,
stilles Überspringen malformed Daten oder Lesen von Kind 257 müssen rot werden.

### 13.4 H3 — Rekursiver Identity-Graph

Die Matrix läuft für Text/Vision Sync/Async und prüft:

1. Deep all-safe: mehrere verschachtelte Wrapper und Targets sind
   `stream=False`; Validierung erlaubt und der aktive Provider wird genau einmal
   aufgerufen.
2. Deep heterogeneous: unter mehreren safe Wrappern liegt ein tiefer
   `stream=True`-Child; lokaler `NotImplementedError` vor jeder Selection/I-O.
   Eine nur flache `[False, True]`-Probe ist nicht ausreichend.
3. Cyclic all-safe: ein identity-zyklischer `stream=False`-Graph terminiert,
   erlaubt und ruft den aktiven Provider genau einmal.
4. Fail-closed: unreadable `stream`, unreadable oder werfender Validator,
   malformed Target und Target 257 eines mehr als 256 Objekte großen Graphen
   liefern lokalen `NotImplementedError`.

Jeder Reject-Fall assertiert null `should_try_primary`, `maybe_failback`,
Provider, Requestbau, Switcher-/Credential-Mutation sowie Bild-/Datei-I/O. Ein
Provider-Side-Effect im Allow-Fall beweist Call-Count eins. Mutationen wie
Validierung nur des aktiven/shallow Targets, fehlendes Visited-Set, Selection vor
Validation oder fail-open bei Getterfehlern müssen rot werden.

### 13.5 Geschlossene und unveränderte Gates

H4 und H5 bleiben in Revision 2 definitionsseitig geschlossen; ihre bestehenden
Fast-Fail-, Identitäts-, Cancellation- und Orphan-Tests werden nicht gelockert.
M1 bleibt konkret HOLD: exakter HTTPS-Allowlist-Origin, ein Credential-Slot-
Fingerprint, fixes Modell/Vision/Capabilities und numerische Gesamtrequest-,
Output-Token-, Bildbyte- und Kostencaps; Retry/Failover null, MCP separat.

Keine Test- oder Sourceausführung, kein Restart, Merge, Push, Canary, Aktivierung
oder Promotion ist durch diese letzte Definitionsrevision autorisiert.

## 14. Finales Test- und Security-Gate

Security Revision 3 endet bei **89/100, 0C/1H/1M**. H2–H5 sind geschlossen; H1
bleibt exakt offen. Die final ausgeführte sechs-Dateien-Matrix ergab
`266 collected = 129 PASS + 137 fachliche RED`; Collection, Imports und Harness
waren fehlerfrei. Die vorhandene native Stream-Senke prüft jedoch weiterhin
nicht den exakt erlaubten Langfuse-Update-Payload. Wegen dieser H1-Lücke gibt es
keine vierte Security-Revision und keine Produktionsimplementierung.
Gesamtstatus: **HOLD**.

OpenViking MCP Health und echter read-only `search_experience`-Aufruf sind PASS,
aber kein Provider-Capability-Test. Der User hat Live-Provider-Tests vertagt.
Keine Skips/Xfails werden als Ersatzbeleg verwendet; M1 bleibt HOLD.

## 15. Neuer HOLD-Lift-Testzyklus

| Gate | Ergebnis |
|---|---|
| Architektur | 97 Design / 96 Interface / 100 Scope, PASS |
| H1 test-first | direktes RED |
| Pre-Source Security | 93/100, 0C/0H, PASS |
| Implementierungssimulation | 96,6%, Minimum 95%, PASS |
| erster Source-Stand | 267/267 |
| Security Rev1 | 86/100, 0C/1H/2M, VETO H6 |
| H6 test-first / nach Source | 5 RED → 5/5 PASS |
| relevanter Ausschnitt | 189/189 PASS |
| finale sechs Dateien | 272/272, 0 Fail/Skip/Xfail |
| Testsimulation | 98%, Minimum 96%, PASS |
| Security Rev2 | 96/100, 0C/0H/1M, PASS |

H6 prüft nicht instanzmarkierbare Exceptions: Ein opaker, klassenmarkierter
Wrapper bindet das identische Original als `__cause__`. M2 erzwingt den
fail-closed Stopp beim 257. Aggregate-Kind, bevor Kind 258 gelesen wird. Ein
initialer Test erwartete für einen `AllCredentialsFailedError` fälschlich
`RuntimeError`, obwohl zugleich Objektidentität verlangt wurde; nur die
erwartete konkrete Exceptionklasse wurde korrigiert, nicht der Klassifizierer.

Alle Läufe nutzten:

```text
PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
/Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest -q -o addopts=
```

Vier bekannte Pydantic-Warnungen blieben sichtbar. Worker und Supervisor
reproduzierten den finalen 13-Dateien-Scope mit 364 PASS plus exakt acht
vorbestehenden VolcEngine-Konstruktorfehlern. Keine Breitsuite wird als
vollständig grün und keine numerische Coverage oder Mutation-Coverage
behauptet. **Offline Legacy-VLM HOLD aufgehoben; Live M1 bleibt HOLD.**

## 16. Aktuelles Testinventar und finales Follow-up-Gate — 2026-08-01

Die historische 216er-/364-plus-8-Matrix bleibt Entstehungsevidenz, ist aber
kein aktueller Reproduktionsvertrag. `tests/models/vlm/test_volcengine_cache.py`
ist entfernt; aktuell ist
`tests/models/vlm/test_volcengine_chat_completions.py` mit drei
Factory-/Sync-/Async-Chat-Completions-Vertragstests.

| Paket | Verifizierter Stand |
|---|---:|
| State + Hook | 102/102 PASS |
| VolcEngine gezielt | 129 PASS |
| breite VLM-Matrix vor spaeteren Follow-up-Aenderungen | 348 PASS |
| Streamdatei / VLM-Matrix | 50/50 / 274/274 PASS |
| WatchTask unter Pydantic Warning-as-error | 7/7 PASS |
| Resource-Fixtures / Service-Fixtures | 37/37 PASS / 0 Setupfehler |
| Recovery/Scheduler / Connector | 19/19 / 50/50 PASS |
| Watch-Service / Feishu-Queue | 21/21 / 23/23 PASS |
| konsolidierte 18-Dateien-Matrix, Pydantic Warning-as-error | 500/500 PASS |
| finale Watch-Matrix nach Ruff-Format | 150/150 PASS |
| Ruff check / Ruff format / diff-check | PASS |

Die Deferred-Matrix prueft `defer_post_processing=True`, genau einen Enqueue,
`QueueManager.ADD_RESOURCE` und den Prepared-Payload. Sie verwendet kein
`wait=True`; fehlender Payload bleibt produktiv fail-loud. L1-L3 pruefen
begrenzte redigierte Senken, Marker-Rueckgabewert und Cleanup sowie die
Built-in-Cancellation nach dem ersten Event.

H1 hat noch keinen ausfuehrbaren Live-Test: Modell, numerische Limits,
Fixture-/Tree-Hashes, Preisbasis und Credential-Policies sind nicht genehmigt.
H2 bleibt bis H1 PASS und Datenfreigabe gesperrt. Kein Skip/Xfail, Offline-Test
oder MCP-Read ersetzt diese Gates. Der `agy`-Review war wegen
Headless-Command-Berechtigung UNAVAILABLE und wird nicht als PASS gezaehlt.

Die beiden finalen Follow-up-Matrizen sind exakt so reproduzierbar:

```bash
PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  /Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest \
  -W error::pydantic.warnings.PydanticDeprecatedSince20 \
  -q -o addopts= -p no:cacheprovider --no-cov \
  tests/unit/test_codex_vlm.py \
  tests/unit/test_kimi_glm_vlm.py \
  tests/unit/test_stream_config_vlm.py \
  tests/unit/test_extra_headers_vlm.py \
  tests/unit/test_litellm_vlm_gemini_cache.py \
  tests/unit/test_model_retry.py \
  tests/unit/test_vlm_failover.py \
  tests/unit/test_vlm_reasoning_models.py \
  tests/unit/test_vlm_response_formats.py \
  tests/unit/test_vlm_thinking_param.py \
  tests/models/vlm/test_timeout_config.py \
  tests/models/vlm/test_volcengine_chat_completions.py \
  tests/resource/test_watch_manager.py \
  tests/resource/test_watch_scheduler.py \
  tests/service/test_watch_recovery.py \
  tests/service/test_resource_service_watch.py \
  tests/parse/test_feishu_parser_api.py \
  tests/service/test_resource_service_connector.py

PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  /Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest \
  -q -o addopts= -p no:cacheprovider --no-cov \
  tests/resource/test_watch_manager.py \
  tests/resource/test_watch_scheduler.py \
  tests/service/test_watch_recovery.py \
  tests/service/test_resource_service_watch.py \
  tests/parse/test_feishu_parser_api.py \
  tests/service/test_resource_service_connector.py
```

## 17. Publikations- und Online-Evidenz — 2026-08-01

Der verifizierte Implementierungs-HEAD
`de9f6e3cc8ee3dcb9f6d64c2ed9fd3ec4865d369` wurde ueber Fork-PR
`manni07/OpenViking#3` gemergt. Alle fuer den PR ausgefuehrten Gates bestanden:

- API & CLI Integration Tests auf Ubuntu: PASS in 23m07s;
- Plugin-Tests: PASS;
- Docs-Build: PASS;
- Dependency-Check: PASS.

Build und cuVS waren wegen unveraendertem Scope vom Workflow uebersprungen und
werden nicht als PASS ausgewiesen. Ein zusaetzlicher Root-`pytest tests`-Versuch
war kein gueltiger Vollsuite-Beleg: Nach isolierter Ergaenzung von `pytest-html`
brach die Sammlung an 20 unveraenderten optionalen Dependency-/Subprojekt-
Problemen ab (`psutil`, `google.genai`, `scrapy`, `mcp` und `oc2ov_test`-
Importroots). Die offizielle `_test_full.yml` ersetzt die volle Unit-Suite
derzeit ebenfalls explizit durch den Lite-Integrationstest. Diese Collection-
Probleme wurden nicht als Kandidatenregression oder als gruene Tests umgedeutet.

Merge-Commit in `origin/main`:
`ed77c27ef1af17fd555ffb59d413b0b909c2ec11`. Es gab keinen Codex-Live-Probe,
Canary, Credential-Aufruf, Restart oder Default-Promotion.
