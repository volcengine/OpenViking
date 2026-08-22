# Development Diary v000

## 2026-07-27 — Synchronisierte Main-Fassung: Security Hardening

**Auslöser.** Nach sicherer Synchronisation der Main-Basis wurde ein Sicherheitsaudit erstellt. Die Umsetzung folgt dem durch `$tccode` geführten Phasenmodell mit `agent-workflow-v4` in thorough/critical-Ausprägung und gestaffelten Rollen.

**Entscheidungen.**

- Lokale WebDAV-Uploads erhalten einen sicheren Default von 16 MiB; Überschreitungen müssen vor einem Schreibpfad mit HTTP 413 enden.
- Öffentliche Bindungen sind fail-closed: keine CORS-Wildcard, konkrete Origin-Allowlist und explizite HTTPS-`public_base_url`; lokale Defaults bleiben ohne CORS-Wildcard gültig.
- Markdown-Links werden im Web Studio und der Graph-HTML-Ausgabe anhand enger Schema-Allowlisten behandelt; gesperrte Links erhalten keinen klickbaren Zielwert.
- Abhängigkeitsupdates bleiben auf kompatible Versionen begrenzt. Nicht kompatible Starlette-, Rust-, shadcn- und Bot-Pfade werden nicht stillschweigend durch Major-Upgrades ersetzt.
- Der neue CI-Entwurf führt Python-, Cargo- und npm-Audits aus und erlaubt nur paketpfadgenaue, ablaufende Baseline-Befunde.

**Arbeitsartefakte.** ARD, TRD, PD, ID, Simulation und Testdossier liegen in `docs/dossiers/`, `docs/plan/` und `docs/tests/`. Der Ausgangsbefund liegt in `docs/audit/2026-07-27-security-audit-main.md`; der Reststatus in `docs/openitem/Security_Hardening_Open_Items_2026-07-27.md`.

**Validierungsstand.** 13 fokussierte Python-Regressionen (Public-URL, exakte und überschreitende WebDAV-Streamgrenze, Baseline-Verifier), 16 Vitest-Fälle sowie Studio-Lint/Build sind grün. Der nachgelagerte Konfigurationsabgleich bestand zusätzlich mit 37 Konfigurations-/Public-URL-Tests. Die CI des ersten Härtungs-Commits bestand vollständig, einschließlich API/CLI-Integration, Plattform-Builds, Dokumentationsbuild und Dependency-Audit. Anschließend wurden noch Helm- und Beispielkonfigurationen auf sichere öffentliche Defaults ausgerichtet; deren PR-Check bleibt vor dem Merge erneut abzuwarten. Nicht als bestanden behauptet werden: ein lokaler vollständiger Python-Audit (Subprozess `SIGABRT`), eine vollständige Suite ohne gültige `ov.conf`, Browser-E2E/Serverstart oder ein Deployment. Der angeforderte `agy`-Review war im Headless-Modus nicht berechtigt; dies bleibt ein dokumentierter externer Review-Blocker.

**Restarbeit.** Vor Merge die PR-Checks des sicheren Konfigurationsnachtrags abwarten; jede der 74 Baseline-Ausnahmen vor dem 2026-08-27 entfernen oder erneut explizit bewerten. Eine reale Bereitstellung verlangt einen konkret benannten Ziel-Host/-Cluster und dessen nicht-sekrete Konfigurationsreferenzen; lokal ist kein Docker-Daemon und kein Kubernetes-Kontext verfügbar. Kein Rechner- oder Serverneustart wurde ausgeführt oder ist für diese Arbeit angeordnet.

**Betroffener Git-Kontext.** Basis `60ef45d4c3a7d07ceb1df4e9d7dde7a14449ac50`; Arbeitszweig `agent-workflow/20260727-security-hardening` im isolierten Worktree. Draft-PR #1 ist im Fork `manni07/OpenViking` eröffnet; Push/Merge erfolgen nur nach Status-, Diff- und Checkprüfung.

## 2026-08-01 — Root-Fixture-Isolation

**Auslöser.** Der Root-Testpfad lud bei Embedded-Clients zuerst die Host-
`~/.openviking/ov.conf`; `StorageConfig` versuchte dadurch bereits vor dem
`path`-Override `/app/.openviking/data` anzulegen. Zusätzlich löschte die
function-scoped Fixture ein globales `test_data/tmp` und war nicht worker-sicher.

**Umsetzung.** In einem frischen Fork-Worktree auf `22919c33` wurde der
Workspace-Override vor `OpenVikingConfig.from_dict()` gelegt. Die Root-Fixtures
erhalten eine sichere per-Test-`ov.conf`, einen Bootstrap-Pfad vor eager Imports,
lokale Embedder-/VLM-Fakes, sichere Singleton-Resets und `tmp_path`-Isolation.
Die Änderung bleibt auf Root-/Embedded-Tests begrenzt; OpenClaw-P0/Service,
H1/H2, Provider-Live und native Builds wurden nicht gestartet.

**Nachweis.** TDD-RED reproduzierte den `/app`-Fehler. Danach bestanden 40
fokussierte Tests (11 Isolation plus 29 Config-Legacy-Fälle);
die Boundary-Suite bestand mit 3 Tests und einer bekannten qdrant-Warnung. Die
Root-Collection sammelte 6302 Tests, bleibt aber wegen des nicht installierten
optionalen `vikingbot`-Subprojekts und 15 vorbestehender Warnungen FAIL/HOLD.
Der Lifecycle-Test erreicht nun den `/app`-Fehler nicht mehr, bleibt aber wegen
fehlendem `ragfs_python` HOLD.

**Übergabe.** ARD, TRD, ID, PD, QWF, TD, STP, Manual, Open-Item-Bericht und
Proposal liegen in den entsprechenden `docs/`-Ordnern. Ein gezielter Commit,
Push und Draft-PR gegen `manni07/OpenViking` stehen noch aus; Merge und jede
Live-Aktivierung sind ausdrücklich ausgeschlossen.
## Codex-Compaction und OpenViking Responses State

Datum: 2026-07-31
Status: Offline Legacy-VLM HOLD aufgehoben; Live M1 und Promotion auf HOLD

## Ausgangslage

Die Umsetzung startete in einem isolierten OpenViking-Worktree auf Commit
`60ef45d4`. Ungetrackte Dateien des Haupt-Checkouts blieben unangetastet. Vor
potenziellen globalen Codex-Änderungen wurden `config.toml`, `hooks.json` und das
bestehende Hook-Skript auf einem Backup-Volume gesichert, gehasht und mit
restriktiven Rechten versehen.

## Arbeitschronik

### 1. Architektur und Stop-Regeln

ARD, TRD und PD definierten einen additiven State-Pfad, einen nicht installierten
Hook-Kandidaten und fail-closed Live-Gates. `VLMBase`, andere Provider, globale
Codex-Dateien und laufende Dienste wurden aus dem Änderungsumfang ausgeschlossen.

### 2. Testvertrag

Die neuen Tests wurden auf Kontinuitäts- und Sicherheitsgründe ausgerichtet:
keine Prompt-Injection aus Hook-Eingaben, keine unsicheren Symlinkpfade, kein
State-Commit nach Teil-Streams, keine Cross-Chain- oder Tool-Replays und kein
stiller Capability-Fallback.

### 3. Hook-Implementierung

Der Hook-Kandidat erhielt private Rechte, sichere atomare Dateien,
komponentenweise Symlink- und Eigentümerprüfungen, Eingabe-/Zeitlimits und feste
Prompts. Eine abschließende Pfadprüfung führte zu einem zusätzlichen
Parent-Symlink-Test. Im Offline-Sicherheits-Follow-up wurden alle Dateioperationen
an Directory-FDs verankert, die Deadline erzwungen und TTL-/Anzahl-/Scan-Limits
für die Retention ergänzt. Ergebnis: 30 Hook-Tests bestanden.

### 4. Responses-Implementierung

Der Adapter erhielt einen frozen State, kanonische vollständige Output-Items,
Compaction-Reduktion, commit-on-complete, native Async-Streams, Tool exactly once,
Bindings, Integrität und harte Limits. `CodexVLM` exponiert additive
State-Methoden und einen expliziten Probe. Nach einem Security-Veto wurden
Trace-Redaction, Adapter-Initialisierungsrace, Credential-I/O im Event-Loop und
unbegrenzte retained Call-ID-Metadaten TDD-geführt gehärtet. Das Follow-up band
Credentials stabil an ihren persistenten Slot, auch ohne `client_id`, und
schirmte Stream-/Client-Cleanup gegen wiederholte Cancellation und Close-Fehler
ab. Ein ergänzender Test stellt sicher, dass ein Close-Fehler die ursprüngliche
Cancellation nicht verdeckt. Ergebnis: 72 State-/Adaptertests bestanden.

### 5. Konfiguration

`responses_state_enabled` und `responses_compact_threshold` wurden opt-in
ergänzt. Threshold ohne State und State ohne genau ein `openai-codex`-Credential
werden abgelehnt. Der Default bleibt aus.

### 6. Verifikation

Frische Kandidatensuiten: 102/102 PASS. Die Core-Kombination lieferte 131 PASS und
einen Fehler; die erweiterte Kombination 140 PASS und zwölf Fehler. Der einzelne
Codex-Config-Fehler sowie elf Stream-Config-Fehler wurden auf der unveränderten
Basis reproduziert. Ruff, Format, Compileall und `git diff --check` bestanden.

Der gemeinsame OpenViking-MCP bestand Health und eine read-only Suche ohne
Restart. Globale Codex-Dateien blieben identisch zu den SHA-256-verifizierten
Backups.

Der separate Legacy-VLM-H3-Sicherheitsreview durchlief die maximal drei
Revisionen: 78/100 (`0C/5H/1M`), 84/100 (`0C/3H/1M`) und final 89/100
(`0C/1H/1M`). H2–H5 wurden geschlossen; H1, der exakte Konstantenvertrag für
markierte VikingBot-Fehler, blieb offen. Damit wurden `0H` und 90/100 verfehlt.
Source-Unlock wurde verweigert; es erfolgte keine Produktionscodeänderung und
keine vierte H1-Schließungsrevision. Die sechs ergänzten Vertrags-Testdateien
wurden final mit `266 collected = 129 PASS + 137 fachliche RED` ausgeführt.

OpenViking MCP Health und ein echter read-only `search_experience`-Aufruf waren
PASS, ohne Restart. Diese Evidenz beweist MCP-Zugriff, nicht die Responses-/
Compaction-Fähigkeit des Providers. Der User vertagte den Live-Provider-Test.

## Entscheidungen

- 206720 bleibt unveränderte Baseline; 175k wurde nicht übernommen.
- Capability wird nicht vermutet, sondern muss am exakten Endpoint geprüft
  werden.
- Der Probe wurde nicht ausgeführt, weil er potenziell kostenpflichtig ist und
  ausdrückliche Genehmigung benötigt.
- Ohne 20 reale und 10 synthetische Szenarien gibt es keine A/B-Siegerwahl.
- Vorbestehende Legacy-Fehler werden sichtbar als HOLD geführt.
- Der finale H3-Security-HOLD bleibt bestehen; H1 wird in diesem Lauf nicht
  weiter revidiert oder implementiert.
- Der bestandene MCP-Read ist kein Ersatz für einen Provider-Capability-Probe.
- Der Live-Test wurde auf Wunsch des Users vertagt.
- Keine Installation, Aktivierung, Promotion, kein Restart und kein Git-Publish.

## Ergebnis

Der Worktree enthält einen offline verifizierten, opt-in Kandidaten und die
zugehörige Übergabedokumentation. Der Legacy-VLM-H3-Follow-up bleibt wegen H1
bei verweigertem Source-Unlock auf HOLD. Live-Capability, A/B-Effekt und
Default-Promotion sind ausdrücklich nicht bewiesen.

## Verweise

- [Implementation Dossier](../dossiers/2026-07-31-codex-compaction-openviking-responses-id.md)
- [Test Dossier](../tests/2026-07-31-codex-compaction-openviking-responses-td.md)
- [Lessons Learned](../lessons/2026-07-31-codex-compaction-openviking-responses-lessons-learned.md)
- [Open Items](../sessions/2026-07-31-codex-compaction-openviking-responses-open-items.md)

## Nachtrag: user-autorisierter Offline-HOLD-Lift

Nach dem alten finalen HOLD autorisierte der User einen neuen Offline-Zyklus.
Architektur 97/96/100, H1 direkt RED, Pre-Source Security 93/100 bei 0C/0H und
Implementierungssimulation 96,6 Prozent bei Minimum 95 öffneten den engen
Sourceumfang. Der erste Stand bestand 267/267, wurde wegen H6 in Security Rev1
aber bei 86/100, 0C/1H/2M erneut gesperrt.

Fünf neue H6-Tests waren zunächst RED. Die Korrektur verwendet für nicht
instanzmarkierbare Exceptions einen opaken, klassenmarkierten Wrapper mit dem
identischen Original als `__cause__`; M2 stoppt beim 257. Aggregate-Kind
fail-closed, bevor Kind 258 gelesen wird. Ein Test erwartete für einen
`AllCredentialsFailedError` fälschlich `RuntimeError`; korrigiert wurde nur die
erwartete konkrete Exceptionklasse. Danach bestanden 5/5, 189/189 und 272/272
ohne Fail, Skip oder Xfail. Testsimulation 98 Prozent, Minimum 96; Security Rev2
96/100, 0C/0H/1M, PASS.

Vier bekannte Pydantic-Warnungen blieben sichtbar. Der finale breite Lauf wurde
vom Worker und vom Supervisor mit 364 PASS plus exakt acht vorbestehenden
VolcEngine-Konstruktorfehlern reproduziert. Keine breite Vollgrün- oder
Coveragebehauptung. Die bestehende venv lief mit dem Overlay
`PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731`.

**Offline Legacy-VLM HOLD aufgehoben; Live M1 bleibt HOLD.** Kein Live-Test,
keine Aktivierung, Promotion, kein Merge oder Restart.

## 2026-08-01 — Open-Items-Follow-up nach Fork-Merge

**Ausloeser.** Der richtige Fork-PR `manni07/OpenViking#2` war als
`c4e3cc52272c086843f3dc64808ed1e8956abede` in `origin/main` gemergt; der
irrtuemliche Upstream-PR #3667 war geschlossen. Der neue isolierte Worktree
`20260801-open-items-follow-up` sollte die danach noch offline schliessbaren
Open Items und veralteten Testvertraege bereinigen.

**Entscheidungen und Umsetzung.** Acht verwaiste VolcEngine-Cachetests wurden
durch drei aktuelle Factory-/Sync-/Async-Chat-Completions-Vertragstests ersetzt.
Streamfakes wurden in ein nicht sammelbares Supportmodul verschoben; die
redigierten Senken, Marker-Rueckgabe und Built-in-Post-Event-Cancellation wurden
gezielt abgesichert. `WatchTask` wechselte minimal auf den Pydantic-v2-Vertrag.
Versehentlich verlorene lokale Watch-Fixtures, unbeabsichtigte Config-I/O und
der veraltete Deferred-Testdouble wurden getrennt repariert. Der produktive
Missing-Payload-Guard blieb unveraendert; kein `wait=True` kaschiert den
asynchronen Queuevertrag.

**Verifikation.** Fokussierte Zwischenstaende: VolcEngine 129 PASS und 348
breit vor Folgeaenderungen; Stream 50 und VLM 274; WatchTask 7 unter
Warning-as-error; Resource 37; Recovery/Scheduler 19; Connector 50; Watch 21;
Feishu/Queue 23. Der finale Orchestrator-Lauf bestand State/Hook 102/102, die
konsolidierte 18-Dateien-Matrix 500/500 unter Pydantic Warning-as-error und die
Watch-Matrix 150/150 nach Ruff-Format. Ruff check, Ruff format und diff-check
waren gruen. Keine Skips oder Xfails wurden als Ersatzbeleg verwendet.

**Reststatus.** H3, M1-M3 und L1-L3 sind geschlossen; der historische
Aggregate-Befund heisst eindeutig `SEC-M2`. H1 bleibt vor Credential-/Client-/
Netzwerk-I/O gesperrt, weil Modell, numerische Limits, Fixture-/Tree-Hashes,
Preisbasis und Credential-Policies nicht freigegeben sind. H2 wartet auf H1
PASS und separate Datenfreigabe. Der `agy`-Reviewversuch war wegen fehlender
Headless-Command-Berechtigung UNAVAILABLE, nicht PASS.

In Task 8 wurden nur Dokumente geaendert. Es erfolgten keine Source-/Testedits,
externen Calls, Commits, Pushes, Live-Tests, Aktivierungen oder Restarts.

## 2026-08-01 — Publikationsabschluss des Open-Items-Follow-ups

Der Follow-up wurde in vier getrennten Commits auf
`agent-workflow/20260801-open-items-follow-up` veroeffentlicht. Fork-PR
`manni07/OpenViking#3` bestand API & CLI Integration auf Ubuntu in 23m07s,
Plugin-Tests, Docs-Build und Dependency-Check. Build und cuVS waren durch den
unveraenderten Pfadfilter uebersprungen und wurden nicht als PASS gezaehlt.

GitHub mergte den gebundenen Head
`de9f6e3cc8ee3dcb9f6d64c2ed9fd3ec4865d369` als
`ed77c27ef1af17fd555ffb59d413b0b909c2ec11` in den Fork. Der Branch und der
isolierte Worktree blieben erhalten. Ein Root-Vollsuiteversuch blieb wegen 20
vorbestehenden optionalen Dependency-/Subprojekt-Collectionfehlern ohne
Testurteil; dies ist im Testdossier fail-loud erfasst. H1/H2 bleiben trotz des
Publikationsabschlusses HOLD. Kein Live-Probe, Canary, Credential-Aufruf,
Restart oder Upstream-Schreibzugriff erfolgte.

## 2026-08-01 — Root-Collection-Fehler geschlossen

**Ausloeser.** Die 20 Fehler wurden erneut in acht Environment-Fehler, elf
Suite-Ownership-Fehler und einen optionalen Gemini-Importfehler zerlegt. Der
Agent-Workflow-v4-Orchestrator gab den reduzierten Plan mit 98,4 Prozent
Architektur-, 97,0 Prozent Test- und 96 Prozent Security-Score frei.

**Umsetzung.** Ein frischer Worktree von Fork-`main` erhielt eine eigene,
gefrorene uv-Umgebung. Der Root-Collector ignoriert nur `api_test` und
`oc2ov_test`. Der Gemini-E2E-Test importiert das optionale Backend erst in den
beiden Laufzeitpfaden. Ein neues Regressionstestmodul prueft die exakte Grenze,
providerfreie Collection und den lauten Missing-Extra-Fehler.

**TDD und Verifikation.** Vor dem Fix schlugen alle drei neuen Tests aus den
beabsichtigten Gruenden fehl. Danach bestanden 3/3. uv 0.8.20 baute mit Python
3.12.11 die lockfile-identische `--extra test`-venv; `mcp` 1.28.1 und `scrapy`
2.16.0 sind importierbar. Gemini sammelt ohne Key und ohne `google.genai` fuenf
Tests. Die vollstaendige Root-Collection sammelte 6382 Tests in 18,07 Sekunden
mit Exit 0 und null Collection-Fehlern. Ein zweiter Node-Grenzcheck bestaetigte,
dass keine Standalone-Node-ID enthalten ist und ein Root-Sentinel vorhanden
bleibt. `git diff --check` ist gruen; `pyproject.toml` und `uv.lock` sind
unveraendert.

**Reststatus.** Elf `cli_remote`-, eine `qdrant`- und drei Hilfsklassen-
Warnungen bleiben offen und wurden nicht als behoben gewertet. Die beiden
Standalone-Harnesses wurden nicht ausgefuehrt und erhalten kein PASS. H1/H2,
Live-Provider, Credentials, Services und Restarts blieben unberuehrt.
