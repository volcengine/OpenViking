# Open-Item-Bericht nach Fork-Merge

**Stand:** 2026-08-01
**Status:** Offline-Follow-up abgeschlossen; H1/H2 fail-closed auf HOLD
**Session Transfer:**
[STP](2026-07-31-codex-compaction-openviking-responses-stp.md)

Der historische Inventarumfang bleibt nachvollziehbar bei exakt drei High-,
drei Medium- und drei Low-Kennungen. Nur H1 und H2 sind noch aktiv. Geschlossene
Punkte stehen bewusst ausserhalb der aktiven Tabelle, damit sie weder als
Restarbeit noch als neue Freigabe erscheinen.

## Aktive Open Items

| ID | Prioritaet | Massnahme | Owner | Fehlende Freigabe / Abschlusskriterium | Next command |
|---|---|---|---|---|---|
| H1 | High | Offline-Preflight-Vertrag und spaeteren Capability-Probe freigeben | `mcp_coordinator_agent`, `security_agent` | Vor jeder Credential-, Client- oder Netzwerkoperation muessen das exakte Modell, alle numerischen Request-/Input-/Output-/Bild-/Timeout-/Kosten-/Compaction-Limits, freigegebene Fixture-/Tree-Hashes, Preisbasis sowie Credential-Slot-, Fingerprint-, Mindestgueltigkeits- und Refresh-Policy genehmigt sein. Unvollstaendige oder unbekannte Felder bleiben fail-closed. | Kein Live- oder Probe-Command; Policies sind nicht freigegeben |
| H2 | High | Canary, A/B-Corpus und Promotionsevidenz erheben | `simulation_agent`, `test_unit_agent` | Erst nach H1-PASS und separater Freigabe der 20 sanitisierten realen sowie 10 synthetischen Szenarien; keine Qualitaetsverschlechterung, mindestens 20% weniger mediane Output-Tokens, p95 hoechstens 10% schlechter, keine hoehere Fehlerrate und null Cross-Chain-Leaks | Kein Command vor H1-PASS und Datenfreigabe |

## Geschlossenes 3/3/3-Inventar

| ID | Prioritaet | Abschluss und verifizierte Evidenz |
|---|---|---|
| H3 | High | Die acht verwaisten `test_volcengine_cache.py`-Faelle wurden entfernt und durch drei aktuelle Factory-/Sync-/Async-Chat-Completions-Vertragstests ersetzt. Gezielter Stand: 129 PASS; breite Matrix vor den spaeteren Follow-up-Aenderungen: 348 PASS. Keine VolcEngine-Produktionsaenderung. |
| M1 | Medium | Der richtige Fork-PR `manni07/OpenViking#2` ist in `origin/main` als `c4e3cc52272c086843f3dc64808ed1e8956abede` gemergt. Der irrtuemliche Upstream-PR `volcengine/OpenViking#3667` ist geschlossen. Daraus folgt keine Aktivierung oder Live-Freigabe. |
| M2 | Medium | `WatchTask` nutzt den Pydantic-v2-Vertrag ohne die klassifizierten Deprecations. Evidenz: 7 WatchTask-Faelle sowie 274 VLM-Faelle mit den betroffenen Warnungen als Fehler. |
| M3 | Medium | Stream-Fakes wurden in ein nicht sammelbares Supportmodul ausgelagert. Der Verifikationscheckpoint lag bei 914 Zeilen, 50/50 Dateitests und 274/274 breiter VLM-Matrix. Nach den spaeter ergaenzten Vertragsfaellen umfasst die Datei am Dokumentationscheckpoint 922 physische Zeilen und bleibt deutlich unter 1000. |
| L1 | Low | Die festen redigierten Senken bleiben durch vorhandene Sentinel-Captures und 16 gezielte Fail-fast-Faelle begrenzt; keine variable Providerexception gelangt in die geschuetzten Response-/Logger-/Langfuse-Senken. |
| L2 | Low | Alle aktuellen Aufrufer behalten das von `mark_vlm_error_non_retryable()` gelieferte Objekt; der Assignment-Rejection-/Cleanup-Vertrag ist gezielt getestet. |
| L3 | Low | Built-in `asyncio.CancelledError` nach dem ersten Streamereignis bewahrt Identitaet, fuehrt genau ein Cleanup aus und hinterlaesst keinen Orphan-Task. Der hypothetische providerfremde Sondertyp wurde nicht in Produktion modelliert. |

Die fruehere doppelte Kennung `M2` ist aufgeloest: Der aktuelle Pydantic-Punkt
bleibt `M2`; der bereits im vorherigen Security-Zyklus geschlossene
Aggregate-`len()`-Befund heisst ab jetzt **SEC-M2**. SEC-M2 stoppt beim 257.
Aggregate-Kind fail-closed, bevor Kind 258 gelesen wird. Der ebenfalls
historische H6-Fallback-Wrapper bleibt geschlossen.

## Geschlossene Legacy-Testisolierung

| Paket | Verifizierter Stand |
|---|---|
| Resource-/Service-Fixtures | Resource 37/37 PASS; Service-Fixtures ohne Setupfehler. Nachgelagerte echte Fehler wurden getrennt klassifiziert. |
| Connector-Config-Isolation | Recovery/Scheduler 19/19 PASS; Connector-Vertrag 50/50 PASS. Keine globale Config-Datei ist Testvoraussetzung. |
| Deferred-Watch-Mock | Watch-Service 21/21 PASS; Feishu-/Queue-Vertrag 23/23 PASS. Der Mock bildet nur bei `defer_post_processing=True` den Payload ab; `wait=True` wurde nicht erzwungen und der produktive Missing-Payload-Guard blieb unveraendert. |

## Verbindliche Stopregel

H1 und H2 bleiben HOLD. Es gab in diesem Follow-up keinen Live-Provider-,
Capability- oder Canary-Aufruf, keine Aktivierung oder Default-Promotion und
keinen Rechner-, Server-, Runtime-, Container-, Service- oder Prozess-Restart.
Ein spaeterer Live-Schritt benoetigt eine neue ausdrueckliche Freigabe und alle
in H1 genannten Werte; fehlende Evidenz bleibt fail-closed.
