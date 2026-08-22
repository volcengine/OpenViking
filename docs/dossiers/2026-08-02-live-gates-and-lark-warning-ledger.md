# Evidence Dossier — Live-Gates und Lark-Upstream-Warnungen

**Stand:** 2026-08-02
**Workflow:** `$tccode` (`thorough`, `critical`) innerhalb Agent-Workflow-v4
**Arbeitsstand:** `05fda173` (`agent-workflow/20260801-open-items-completion`)
**Fork:** `manni07/OpenViking`
**Live-Status:** `HOLD / NOT RUN`

## Ergebnis

Die offline behebbaren WebSocket-Kompatibilitätsfehler sind geschlossen:
`lark-oapi 1.7.1`, `uvicorn 0.52.1` und `websockets 15.0.1` sind gelockt,
und die OpenViking-/VikingBot-Serverpfade wählen den SansIO-Adapter explizit.
Ein frischer Importtest bestätigt daneben genau zwei bekannte Warnungen aus
dem unveränderten Drittanbieterpaket. Sie werden weder global gefiltert noch in
`site-packages` gepatcht.

PR #8 im Fork wurde am 2026-08-02 als Merge-Commit
`373aa383511a62a8178208511c60b655ea406dfa` in `manni07/OpenViking:main`
übernommen. Die Live-Gates sind davon unabhängig und bleiben bewusst
`HOLD / NOT RUN`.

Der lokale Runner `/Volumes/ExtremePro/projects/local-ci-gate` meldet alle
fünf Checks `PASS`: Root `6165 passed, 246 skipped, 1` bekannter
Lark-Upstream-Warnung und Bot `271 passed, 2` bekannte Lark-Upstream-Warnungen.
Die Warnungsanzahl ist damit klassifiziert, nicht versteckt.

## Gate-Ledger

| Gate | Status | Fehlender Nachweis / sichere nächste Aktion |
|---|---|---|
| H1 Codex Capability | `HOLD / NOT RUN` | Freigegebenes Approval-Manifest mit exakt erlaubtem Origin/Modell, OAuth-Policy, Capability-Scope, Hashes, Limits, Preisbasis und `retry=0` fehlt; Manifest muss vor Credential-/Client-/Netzwerk-I/O validiert werden. |
| H2 Responses Benchmark | `HOLD / NOT RUN` | Erst nach H1-PASS und separater Freigabe; 20 reale plus 10 synthetische Szenarien, Wiederholungen und Kosten-/Datengrenzen sind noch nicht freigegeben. |
| OpenClaw P0/Service | `HOLD / NOT RUN` | Harness ist nicht als disposable/read-only bewiesen: Hostpfade, fehlende `settings.py`, feste 1933-/Home-Annahmen und rohe Logausgaben müssen isoliert bzw. redigiert werden; Prozess-/Containeridentität fehlt. |
| Provider-/Feishu-Live | `HOLD / NOT RUN` | Token-Art, exakte HTTPS-Domain/Fixture, App-Berechtigungen, Retention-/Verschlüsselungsentscheidung, Timeout-, Kosten- und Rollback-Grenzen fehlen. |

Keines dieser Gates wird durch Offline-Mocks, einen Health-Endpunkt oder die
erfolgreiche Root-/Bot-Suite als bestanden bewertet. Es gab in dieser Runde
keinen Provider-, Feishu-, OpenClaw- oder Codex-Live-Aufruf und keinen Restart.

## Lark-Warning-Ledger

Die folgende Tabelle ist ein absichtlicher Maintenance-Restbefund, kein
lokaler Abnahmefehler:

| Signatur | Provenienz | Bewertung | Behebung/Exit-Kriterium |
|---|---|---|---|
| `datetime.datetime.utcfromtimestamp() is deprecated ...` | Vendorter Protobuf-Code unter `lark_oapi/ws/pb/google/protobuf/internal/well_known_types.py` (Import in `lark-oapi 1.7.1`) | Veraltet, aber nicht aus OpenViking-Datenpfaden; ein Upgrade der separaten Projekt-Abhängigkeit `protobuf` erreicht diesen eingebetteten Code nicht. | Auf eine verifizierte Lark-Version warten, die den vendorten Ausdruck ersetzt. Danach Lock-Hash, Import- und Protobuf-Regressionslauf aktualisieren. |
| `There is no current event loop` | Modul-Import `lark_oapi/ws/client.py`; der SDK-Client hält anschließend einen globalen Loop und wird im Feishu-Kanal aus einem separaten Thread gestartet. | Lifecycle-/Verfügbarkeitsrisiko, nicht nur Kosmetik. Ein vorab erzeugter Loop oder ein Warning-Filter würde die Ownership-/Cleanup-Frage verdecken. | Upstream muss Loop-Eigentum, Thread-Bindung und Stop/Cleanup explizit machen. Danach Sync-/Async-, Multi-Thread- und Cleanup-Tests ergänzen. |

Der Regressionstest
`tests/test_lark_websockets_compat.py::test_lark_upstream_warning_ledger_is_explicit`
importiert das SDK in einem frischen Subprozess und prüft die beiden
Signaturen samt Dateipfad. Ein neuer oder verschwundener Befund stoppt den
Dependency-Review, statt stillschweigend unterdrückt zu werden. Der Test
verändert weder Warning-Filter außerhalb seines lokalen Capture-Blocks noch
installierte Dateien.

## Freigabe-/Stop-Regeln

Live darf erst beginnen, wenn die in
[`docs/plans/2026-08-01-live-gates-h1-h2-openclaw.md`](../plans/2026-08-01-live-gates-h1-h2-openclaw.md)
genannten Freigabefelder vollständig und schriftlich vorliegen. Fehlende
Origin-, OAuth-, Preis-, Deadline-, Retention- oder Prozessdaten bleiben
`HOLD`; es gibt keinen automatischen Fallback, Retry, Failover oder Restart.
