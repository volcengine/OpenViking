# Planning Document

## Open-Items-Follow-up nach Fork-Merge

**Stand:** 2026-08-01
**Basis:** `origin/main` bei `c4e3cc52272c086843f3dc64808ed1e8956abede`
**Modus:** `$tccode` (`thorough`, `critical`) mit Agent-Workflow-v4-Rollen
**Betriebsgrenze:** keine Restarts, keine Live-Provider-Aufrufe, keine
Aktivierung oder Default-Promotion ohne eigenes bestandenes Gate
**Ausfuehrungsstand:** Tasks 1-6 und 8 offline abgeschlossen; Task 7/H1 sowie
Task 9 Live/Veroeffentlichung bleiben HOLD

## 1. Ziel und Erfolgskriterien

Die nach dem Offline-HOLD-Lift dokumentierten Punkte werden nach aktuellem
Fork-Stand neu klassifiziert und die offline abschliessbaren Punkte chirurgisch
geschlossen. Widersprüchliche oder veraltete Testverträge werden nicht durch
neue Produktions-Kompatibilität konserviert.

Erfolg ist erreicht, wenn:

1. die acht verwaisten VolcEngine-Cachetests durch aktuelle
   Chat-Completions-Vertragstests ersetzt sind, ohne Produktionsänderung;
2. `WatchTask` ohne Pydantic-v2-Deprecations und mit unverändertem
   Serialisierungs-/Extra-Feld-Vertrag arbeitet;
3. der Streamtest deutlich unter 1000 Zeilen liegt, ohne verlorene Node-IDs;
4. die Marker-Rückgabe, redigierten Senken und Cancellation nach dem ersten
   Streamereignis gezielt regressionsgeschützt sind;
5. die gezielten und breiten Offline-Suiten vollständig ohne Skip/Xfail
   bestehen;
6. H1/H2 nur mit vollständigen Live-Parametern und separater Datenfreigabe
   fortgesetzt werden.

## 2. QWF und Scope-Trennung

| Rang | Paket | Begründung | Gate |
|---:|---|---|---|
| 1 | H3 VolcEngine-Testvertrag | Acht reproduzierbare Baselinefehler; kein gültiger Produktionsdefekt | verwaiste Tests entfernt, aktuelle Factory-/Sync-/Async-Routingverträge grün |
| 2 | M3 und L1-L3 | test-only und dokumentarisch; schafft Platz für fehlende Vertragsfälle | unveränderte Sammlung plus neue gezielte Tests; sechs-Dateien-Matrix grün |
| 3 | M2 Pydantic | kleine Produktionsänderung ausserhalb des VLM-Scopes | Warning-as-error und WatchManager-Suite grün |
| 4 | Legacy Watch-Fixtures | 42 erhaltene Tests referenzieren im selben Refactor versehentlich gelöschte lokale Fixtures | lokale Fixtures ohne entfernte Produktions-Cleanup-API; alle Consumer laufen wirklich |
| 5 | Watch-Test-I/O-Isolation | drei Watch-Tests laden unbeabsichtigt globale Connector-Config unter `/app` | nur Config-Lookup lokal patchen; echte Routing-/Schedulerkette bleibt aktiv |
| 6 | Watch-Deferred-Mockvertrag | 20 Service-Tests liefern nach Queue-Vertragsänderung keinen Deferred-Payload | echten `wait=False`-Vertrag im Mock abbilden; Fail-loud-Produktion unverändert |
| 7 | H1 Offline-Preflight | Capability-Probe prüft den Origin aktuell erst nach möglichem Credential-I/O | jedes unvollständige Manifest stoppt vor Credential-, Client- und Netzwerkzugriff |
| 8 | Dossiers und Transfer | Drift bei PR, Worktree, Warnungszahl und Befund-ID beseitigen | aktueller Fork-/Merge-Stand und verbleibende Gates eindeutig |
| 9 | H1 Live-Probe | externer Egress und Credential-Nutzung | nur nach vollständigen numerischen Limits und ausdrücklicher Wiederaufnahme |
| 10 | H2 Canary/A-B | reale Sitzungsdaten und Kosten | nur nach H1 PASS und separater Corpusfreigabe |

H3/M3/L1-L3 und M2 werden in getrennten Commits gehalten. Falls Review oder
CI eine saubere Scope-Trennung erfordert, wird M2 in einen eigenen Branch/PR
abgespalten. Keine VolcEngine-Produktionsdatei wird für H3 geändert.

## 3. Architekturentscheidungen

- Der gültige Konstruktor bleibt `VolcEngineVLM(config: dict)`; es gibt keinen
  Keyword-/`**kwargs`-Kompatibilitätspfad.
- Der bewusst entfernte Responses-Prefix-Cache, `previous_response_id` und
  Provider-Response-ID-State werden nicht wieder eingeführt.
- Der VolcEngine-Vertrag schützt Factory-Erzeugung, unveränderte Eingabeconfig
  sowie Sync-/Async-Routing ausschliesslich über `chat.completions.create`.
- `WatchTask.to_dict()` bleibt der persistente ISO-8601-Vertrag. Die redundante
  Pydantic-v1-`json_encoders`-Konfiguration wird nicht ersetzt.
- Der opake Non-Retryable-Wrapper behält sein Original als `__cause__`.
  OpenViking garantiert Redaction an seinen eigenen Senken, nicht bei
  beliebigen externen Traceback-Renderern.
- Ein hypothetischer, nicht markierbarer Cancellation-Sondertyp erhält ohne
  reproduzierbaren Providerfall keinen Produktionspfad.

## 4. Agent-Workflow-v4-Rollen

Die zehn Rollen bleiben als Prüfverantwortung erhalten; die verfügbare
Parallelität ist technisch auf vier aktive Agenten begrenzt.

1. `master_orchestrator`: Gates, Scope und Freigaben
2. `documentation_agent`: Dossier-/STP-Drift und Live-Gates
3. `session_transfer_agent`: restartbarer Abschlussstand
4. `architecture_agent`: VolcEngine-Vertragsentscheid
5. `code_quality_api_agent`: M3/L2 und Dateigrenze
6. `security_agent`: L1-Redaction und Veto
7. `simulation_agent`: Implementierungs-/Testscores
8. `test_unit_agent`: RED/GREEN und breite Matrix
9. `mcp_coordinator_agent`: H1, aktuell fail-closed
10. `devops_agent`: Fork-Branch, CI, PR und Merge-Evidenz

## 5. Implementierungsaufgaben

### Task 1: VolcEngine-Vertrag test-first bereinigen

- Exakt acht historische Fehler auf der Basis reproduzieren.
- `tests/models/vlm/test_volcengine_cache.py` entfernen.
- Neue, gegen den heutigen Code geschriebene Factory-/Routingtests ergänzen.
- Einen Zugriff auf `responses.create` in Sync und Async fail-loud machen.
- Gezielte VolcEngine-/VLM-Suite ausführen.

### Task 2: Streamtests teilen und L1-L3 absichern

- Sammlung und Node-IDs vor der Änderung sichern.
- Nur lokale Fakes in ein nicht sammelbares Supportmodul verschieben.
- Marker-Rückgabe bei nicht markierbarem Cleanupfehler testen.
- Redaction in Failover und VikingBot-Senken mit Sentinel-Secrets testen.
- Cancellation nach dem ersten Event auf Identität, genau ein Cleanup und
  fehlende Orphan-Tasks prüfen.
- Sammlung, gezielte Datei und sechs-Dateien-Matrix erneut ausführen.

### Task 3: Pydantic-Warnungen test-first entfernen

- Die aktuell exakt zwei Warnungen mit `PydanticDeprecatedSince20` als Fehler
  reproduzieren.
- Verhaltenstest für unbekannte Felder und Datetime-JSON hinzufügen und RED
  beobachten.
- `WatchTask` minimal auf `ConfigDict(extra="ignore")` umstellen und die
  redundanten `json_encoders` entfernen.
- WatchManager-Suite mit Warning-as-error und relevante breite Matrix
  ausführen.

### Task 4: Versehentlich gelöschte Watch-Fixtures wiederherstellen

- Die 23 Resource- und 19 Service-Fixture-Setupfehler auf der unveränderten
  Baseline als RED sichern.
- Je Testmodul nur die zuvor vorhandene lokale, function-scoped Fixture
  wiederherstellen; keinen globalen `conftest.py`-Vertrag erzeugen.
- Die entfernte Produktionsmethode `clear_all_tasks()` nicht wieder einführen;
  die temporäre Mock-FS- bzw. In-Memory-Isolation benötigt keinen Teardown.
- Beide Consumerdateien vollständig ausführen und jeden nachgelagerten echten
  Testfehler getrennt klassifizieren.
- Angrenzende Watch-/Scheduler-/Recovery-Suiten ausführen; ungemockte
  Connector-Config-I/O bleibt ein eigener test-first Scope und wird nicht als
  Fixture-Fix umetikettiert.

### Task 5: Watch-Tests von globaler Connector-Config isolieren

- Die drei exakten RED-Fälle sichern: zwei in `test_watch_recovery.py` und der
  Feishu-User-Token-Fall in `test_resource_service_watch.py`.
- Nur in diesen Tests den Config-Lookup von `code_hosting_utils` auf eine
  harmlose Konfiguration mit leeren Code-Hosting-Domainlisten patchen.
- Weder `_should_use_connector` noch `is_git_repo_url` selbst patchen, damit
  die echte Routingentscheidung weiter ausgeführt wird.
- Keine Produktionsänderung und kein globaler Config-Singleton-Reset.
- Die drei Nodes, Watch-Recovery/Scheduler und die Connector-Vertragssuite
  vollständig ausführen.

### Task 6: Watch-Testdouble an den Deferred-Queuevertrag anpassen

- Den 20er-RED-Lauf sichern und den produktiven Fail-loud-Guard unverändert
  lassen.
- `MockResourceProcessor` soll für `defer_post_processing=True` einen
  vollständigen `_post_process`-Payload und einen No-op-Resource-Lock liefern.
- Gemeinsame und manuell erzeugte `ResourceService`-Instanzen erhalten nur im
  Test einen lokalen `_enqueue_add_resource_job`-AsyncMock mit stabiler
  `task_id`; kein Wechsel auf `wait=True`.
- Mindestens ein Watch-Test prüft explizit `defer_post_processing=True` und die
  Queueparameter. Fehlende Payload bleibt durch bestehende Contract-Tests
  fail-loud.
- Watch-Service, den einschlägigen Feishu-/Queue-Vertrag und angrenzende
  Service-Tests vollständig ausführen.

### Task 7: H1-Preflight vor Credential-I/O härten

- Einen strikt schema-validierten, offline ausführbaren Manifest-/Approval-
  Vertrag definieren; fehlende und unbekannte Felder schlagen fehl.
- Origin, Modell, Capabilitymenge, Fixture-/Tree-Hashes, Request-/Input-/Output-
  /Bild-/Timeout-/Kostenlimits und Compaction-Threshold vollständig prüfen,
  bevor ein Credential-Resolver oder eine Client-Factory aufgerufen wird.
- `probe_compaction_capability()` selbst am Methodeneingang an den exakt
  freigegebenen Origin binden.
- OAuth-Refresh im Probe verbieten; abgelaufene oder zu knapp gültige Tokens
  bleiben HOLD.
- Für jedes fehlende Feld sowie falschen Origin und fehlende Preisbasis
  `credential_loader.calls == client_factory.calls == network_calls == 0`
  test-first belegen.
- Keine ausführbare Live-Anleitung und keine Manifestwerte mit Credentials in
  das Repository aufnehmen.

### Task 8: Dossiers, Open Items und STP aktualisieren

- M1 (alter Draft-PR) als durch Fork-PR #2/Merge erledigt dokumentieren.
- H3, M2, M3 und L1-L3 mit verifizierter Evidenz schliessen oder begrenzt
  reklassifizieren.
- Die doppelte Kennung `M2` beim Aggregate-Befund beseitigen.
- Veraltete Upstream-PR-/Worktree-/Testinventar-Angaben korrigieren.
- H1/H2 samt fehlenden Parametern und Stopregel unverändert fail-closed halten.

### Task 9: Review, Simulation und Veröffentlichung

- Architektur-, Codequalitäts-, Security- und Testreview gegen den finalen Diff.
- Implementierungs- und Testsimulation jeweils mindestens 95 Prozent
  aggregiert und mindestens 90 Prozent pro Einzelkriterium.
- Getrennte, absichtliche Commits; Push und PR nur zum Fork
  `manni07/OpenViking`.
- Kein automatischer Live-Test, Restart, Canary, Default-Promotion oder
  Account-/Provider-Failover.

## 6. Verifikationsgate

Pflichtläufe werden mit dem bestehenden lokalen Python und explizitem
`PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731`
ausgeführt. Ein Ergebnis ist nur PASS, wenn keine Tests übersprungen oder als
XFail umgedeutet wurden. Neue oder verbleibende Warnungen werden gezählt und
klassifiziert. Bei Produktionsregression, unerwartetem Netzwerkzugriff,
Credential-Bedarf oder Scope-Drift gilt STOP.

## 7. Live-HOLD

H1 benötigt vor jedem Request mindestens: exakten zugelassenen HTTPS-Origin,
einen Credential-Slot/Fingerprint, ein fixes Modell, Capability-/Vision-Scope,
genehmigte Fixture- und Repository-Tree-Hashes, numerische Caps fuer Requests,
Input- und Output-Tokens, Bildbytes, Timeout, Kosten und Compaction-Threshold,
eine gueltige Preisbasis und Kostenberechnung sowie Mindestgueltigkeits-,
Refresh- und OAuth-Policy des Credentials. Retry und Failover sind null. H2 benötigt zusätzlich ein
positives H1-Gate und eine separate Freigabe der 20 sanitisierten realen plus
10 synthetischen Szenarien. Bis dahin sind beide Punkte absichtlich offen und
nicht durch Offline-Simulation ersetzbar.

## 8. Focused Follow-up-Abschluss — 2026-08-01

| Task | Status | Evidenz |
|---|---|---|
| 1 / H3 | CLOSED | verwaiste Cachetests entfernt; drei aktuelle VolcEngine-Vertragstests; 129 gezielt und 348 breit vor spaeteren Follow-up-Aenderungen |
| 2 / M3, L1-L3 | CLOSED | Streamtest-Checkpoint 914 Zeilen, spaeter 922 physische Zeilen; 50 Dateitests und 274 VLM-Tests; Sentinel-Senken plus 16 Fail-fast-Faelle; Marker-Rueckgabe und Built-in-Post-Event-Cancellation abgesichert |
| 3 / M2 | CLOSED | WatchTask 7/7; VLM 274/274 mit Pydantic-Warnungen als Fehler |
| 4 | CLOSED | Resource-Fixtures 37/37; Service-Fixtures ohne Setupfehler |
| 5 | CLOSED | Recovery/Scheduler 19/19; Connector 50/50 |
| 6 | CLOSED | Watch 21/21; Feishu/Queue 23/23; kein `wait=True`, kein Produktionsguard-Diff |
| 7 / H1 | HOLD | Modell, numerische Limits, Fixture-/Tree-Hashes, Preisbasis und Credential-Policies nicht freigegeben |
| 8 | CLOSED | Bestehende TCCODE-Artefakte auf Fork-Merge und aktuelles Inventar abgeglichen |
| 9 | CLOSED (offline publication) | vier getrennte Commits; Fork-PR #3; alle ausgefuehrten PR-Gates PASS; Merge-Commit `ed77c27ef1af17fd555ffb59d413b0b909c2ec11` |

Finale konsolidierte Orchestrator-Evidenz: State/Hook 102/102,
18-Dateien-Matrix 500/500 unter Pydantic Warning-as-error und finale
Watch-Matrix 150/150 nach Ruff-Format. Ruff check, Ruff format und
`git diff --check` sind PASS. Die fruehere `364 PASS + 8 FAIL`-Matrix ist
historische Baseline, kein aktueller Restfehlerbestand.

Der Follow-up-Head `de9f6e3cc8ee3dcb9f6d64c2ed9fd3ec4865d369`
wurde ueber Fork-PR `manni07/OpenViking#3` als Merge-Commit
`ed77c27ef1af17fd555ffb59d413b0b909c2ec11` in `origin/main` aufgenommen.
Der vorherige richtige Merge bleibt Fork-PR #2; der irrtuemliche Upstream-PR
`volcengine/OpenViking#3667` ist geschlossen. Der Implementierungsbranch und
der isolierte Worktree wurden zur Nachvollziehbarkeit nicht geloescht.

Die PR-Gates bestanden: API & CLI Integration auf Ubuntu in 23m07s sowie
Plugin-, Docs- und Dependency-Checks. Build und cuVS wurden vom unveraenderten
Pfadfilter erwartungsgemaess uebersprungen, nicht als PASS gezaehlt.

Der angeforderte `agy`-Review war wegen Headless-Command-Berechtigung nicht
verfuegbar und wird nicht als PASS gewertet. H1/H2 bleiben fail-closed; es gab
keinen Live-Aufruf, Canary, Restart oder Aktivierung.

### Simulation des finalen Offline-Diffs

| Implementierungskriterium | Wert | Testkriterium | Wert |
|---|---:|---|---:|
| Scope-Treue | 98 % | Vertragsvollstaendigkeit | 98 % |
| Einfachheit | 97 % | Determinismus | 98 % |
| Codebase-Konformitaet | 97 % | Isolation | 98 % |
| Fail-loud-Verhalten | 98 % | Security | 98 % |
| Rueckwaertskompatibilitaet | 97 % | Mutation-Sensitivitaet | 96 % |
| Reviewbarkeit | 98 % | Diagnosefaehigkeit | 97 % |
| **Aggregiert** | **97,5 %** | **Aggregiert** | **97,5 %** |

Beide Simulationen erreichen mindestens 95 Prozent aggregiert und mindestens
96 Prozent je Einzelkriterium. Sie bewerten den Offline-Diff und ersetzen weder
den ausstehenden H1-Live-Probe noch H2-Canary-Evidenz.

## 9. Root-Collection-Follow-up — 2026-08-01

Der eng begrenzte Offline-Follow-up beseitigt die zuvor dokumentierten 20
Collection-Fehler ursachengerecht. Eine frische Worktree-venv aus `uv.lock`
mit uv 0.8.20 und Python 3.12.11 stellt die verpflichtenden Abhaengigkeiten
`mcp` und `scrapy` bereit. Die Root-Suite nimmt die eigenstaendigen Live-
Harnesses `tests/api_test` und `tests/oc2ov_test` nicht mehr in ihren Prozess
auf. Der Gemini-E2E-Test importiert sein optionales Provider-Modul erst bei
tatsaechlicher Testausfuehrung und bleibt bei fehlendem Extra fail-loud.

Das neue TDD-Paket war vor der Aenderung mit 3/3 erwarteten Fehlern RED und ist
danach mit 3/3 PASS GREEN. Der Gemini-E2E-Baum sammelt ohne API-Key und ohne
`google.genai` fuenf Tests. Die vollstaendige Root-Collection endet mit 6382
gesammelten Tests, Exit 0 und null Collection-Fehlern. Die elf unbekannten
`cli_remote`-Marker, ein unbekannter `qdrant`-Marker und drei bekannte
Hilfsklassen-Warnungen bleiben sichtbar ausserhalb dieses atomaren Pakets.
Die beiden Standalone-Harnesses wurden nicht ausgefuehrt und sind daher weder
PASS noch Teil dieses Root-Beweises. H1/H2 bleiben HOLD; kein Provider-,
Credential-, Service- oder Restart-Pfad wurde ausgefuehrt.

Der TCCODE-`agy`-Review wurde erneut versucht. Der erste Aufruf war wegen einer
abweichenden CLI-Argumentauswertung off-target und wurde verworfen; der anhand
von `agy --help` korrigierte read-only Headless-Aufruf wurde mangels
`command`-Berechtigung automatisch abgelehnt. Es wurde kein
`--dangerously-skip-permissions` verwendet. Damit ist `agy` fuer dieses Paket
UNAVAILABLE und kein PASS; es gab kein verwertbares Feedback einzuarbeiten.

Das unabhaengige finale Source-/Testreview ergab PASS ohne Veto: Security
98/100, Code/API 97/100 und Tests 95/100. Der verifizierte
Implementierungscommit ist `9a2bcd130e47ef0e9109ba7902e0335c537a690b`.
