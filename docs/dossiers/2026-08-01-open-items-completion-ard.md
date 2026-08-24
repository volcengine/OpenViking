# ARD — Open Items Completion (2026-08-01)

## Auftrag und Abgrenzung

Dieses Dossier schließt die lokal reproduzierbaren Restpunkte des Root-
Testpfads, der eigenständigen VikingBot-Harness und des Native-Lifecycle-
Gates. Die Änderungen gehen ausschließlich in den Fork `manni07/OpenViking`;
der Haupt-Checkout bleibt unangetastet.

Der echte OpenClaw-P0-/Service-Lauf, H1-Capability, H2-Benchmark und Provider-
Live-Tests sind eigene, weiterhin angehaltene Gates. OAuth ist für diese
Offline-Arbeit nicht verwendet worden. Ein Live-Gate darf erst nach seinem
eigenen Nachweis als PASS bezeichnet werden.

## Revalidierter Befund

- Worktree: `/Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-open-items-completion`.
- Branch: `agent-workflow/20260801-open-items-completion`.
- Fork-Remote: `https://github.com/manni07/OpenViking.git`.
- Referenz-Remote: `https://github.com/volcengine/OpenViking.git`.
- Ausgangs-HEAD der Arbeitsrunde: `fdccb0b3`.
- Root-Collection: `6359 tests collected`, strict markers und
  `PytestCollectionWarning` als Fehler ohne Collection-Fehler.
- Root-Ausführung: `6129 passed, 232 skipped, 4 warnings` in `720.40s`.
- Standalone VikingBot: `271 passed, 4 warnings` in `7.44s`.
- Fokussierte Legacy-/Config-/Boundary-Suiten: `268 passed, 3 skipped` sowie
  `420 passed, 2 skipped`; Storage vollständig `395 passed, 2 skipped`.
- Native AGFS: fokussierter Smoke-Lauf `5 passed`, Lifecycle `2 passed`.

Die vier Root- und vier Bot-Warnungen stammen aus `lark_oapi` und
`websockets` (veraltete Drittanbieter-APIs bzw. fehlende Event-Loop-Erkennung).
Es gibt keine verbleibende OpenViking-`PytestCollectionWarning` und keinen
fehlgeschlagenen Offline-Test. Drittanbieter-Code wird nicht vendored oder
unautorisiert gepatcht; ein Upgrade wird als separater Wartungspunkt geführt.

## Zielarchitektur und umgesetzte Behebung

1. Root-Pytest besitzt nur den Root-Pythonpfad; `api_test` und `oc2ov_test`
   bleiben eigene Projekte. VikingBot hat mit `bot/pytest.ini` einen eigenen
   Manifest-/Pythonpfad und eine eigene Fixture-Grenze.
2. Root- und Bot-Fixtures verwenden private, temporäre Konfigurationen,
   Funktions-Workspaces, Reset der Singletons und deterministische Offline-
   Modelle. Die Bot-Harness überschreibt standardmäßig die Host-
   `OPENVIKING_CLI_CONFIG_FILE` mit einer privaten leeren Testdatei.
3. Direkte Client-/Service-Konstruktoren bleiben durch Regressionstests gegen
   `/app`- und Host-Konfigurationsleaks geschützt.
4. Native AGFS wird aus `crates/ragfs-python` in der isolierten Testumgebung
   verwendet; Import, Read/Write und Cleanup sind belegt.
5. Die Legacy-Verträge für Credential-Bindung, URI-Scope, Embedding-/Rerank-
   Defaults, Prompt-/Memory-Rendering, OpenGauss-Updates und Bot-Retention
   sind durch gezielte Tests abgedeckt.

## Sicherheits- und Nichtziele

- Keine Host-Konfiguration, Credential-Datei oder Provider-Variable wurde als
  Testkonfiguration verwendet oder in Artefakte geschrieben.
- Kein Rechner-, Server-, Runtime-, Container-, Service- oder Prozess-Restart
  wurde ausgeführt.
- Kein OAuth-Live-Aufruf, kein Provider-Live-Aufruf und kein OpenClaw-Service-
  Lauf wurde simuliert oder als PASS ausgegeben.
- Kein stiller Fallback von OAuth zu API-Key/LiteLLM und keine Aktivierung eines
  Responses-/Compaction-Defaults wurde vorgenommen.

## Abnahmekriterien und Reststatus

Die Offline-Abnahmekriterien sind PASS: Root-Collection/-Suite, separate
Bot-Suite, Legacy-Regressionen, Native-Smoke und Security-/Boundary-Tests.
Die folgenden Gates bleiben HOLD/NOT RUN und benötigen eine separate
Freigabe, Credentials, Kosten-/TTL-Limits und eigene Evidenz:

- OpenClaw-P0-/Service-Handschlag und read-only MCP-Aufruf;
- H1 Capability-Probe am exakt freigegebenen Codex-Endpunkt;
- H2 Benchmarkmatrix einschließlich Promotionkriterien;
- Provider-Live-Tests.

Die vier Drittanbieter-Warnungen sind dokumentiert, aber kein lokaler
Abnahmefehler. Ein Fork-PR wird nach dem finalen Diff-/Testcheck aktualisiert;
Merge oder Aktivierung erfolgen nicht automatisch.

## Nachtrag 2026-08-02 — aktueller Fork- und Warning-Stand

Die historische Warnungszählung oben bleibt als damalige Evidence erhalten.
Der aktuelle frische Import-Ledger weist genau zwei Signaturen aus
`lark-oapi 1.7.1` nach: vendortes `utcfromtimestamp` und der
Import-time-Aufruf `asyncio.get_event_loop`. Beide sind im unveränderten
Drittanbieterpaket verankert und werden weder lokal gefiltert noch in
`site-packages` gepatcht; siehe
[`2026-08-02-live-gates-and-lark-warning-ledger.md`](2026-08-02-live-gates-and-lark-warning-ledger.md).

Der Fork-PR #8 ist inzwischen als Merge-Commit
`373aa383511a62a8178208511c60b655ea406dfa` in `manni07/OpenViking:main`
übernommen. H1/H2, OpenClaw-P0/Service und Provider-/Feishu-Live bleiben
davon unabhängig `HOLD / NOT RUN`.
