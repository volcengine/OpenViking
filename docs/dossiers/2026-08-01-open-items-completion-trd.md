# TRD — Open Items Completion (2026-08-01)

## Vertragsänderungen

### Root-/Bot-Testumgebung

`[tool.pytest.ini_options]` ist die einzige Root-Markerquelle und setzt
`pythonpath = ["."]`. `api_test` und `oc2ov_test` sind per `collect_ignore`
außerhalb des Root-Scope. Die VikingBot-Suite ist mit `bot/pytest.ini`
eigenständig und nutzt `pythonpath = [".", ".."]`, ein eigenes Manifest und
eigene Marker. Root sammelt keine Bot-Module aus dem Bot-Pythonpfad.

### Collection-Warnungen

Hilfsmodelle und Testhelfer werden nicht mehr als Testklassen gesammelt. Der
Contract-Test führt Root-Collection mit `--strict-markers` und
`-W error::pytest.PytestCollectionWarning` aus. Verbleibende
Drittanbieter-`DeprecationWarning`s werden nicht durch Warnungsfilter
versteckt und nicht als lokaler Fehler klassifiziert.

### Offline-Sicherheitsvertrag

Der Root-Bootstrap bleibt vor den OpenViking-Imports aktiv. Direkte
`AsyncOpenViking`-/`OpenVikingService`-Konstruktionen erhalten einen
pytest-eigenen Workspace oder sind als separater Boundary-Test markiert.
Provider-Umgebungsvariablen werden vor den Collection-Checks entfernt.
Root- und Bot-Fixtures setzen private temporäre Config-Dateien, damit weder
`/app` noch `~/.openviking/ov.conf`/`ovcli.conf` in Offline-Tests gelesen wird.

### Native-Lifecycle-Vertrag

Der Build-/Installationspfad verwendet `crates/ragfs-python`, eine isolierte
Testumgebung und einen nachvollziehbaren Wheel-/Import-Nachweis. Der lokale
Smoke-/Lifecycle-Test belegt Read/Write und Cleanup; ein externer Mount oder
Provider wird nicht vorausgesetzt.

### Legacy-API-Verträge

- `OpenGaussCollection.update_data` validiert IDs/Feldtypen und führt partielle
  Updates durch.
- Account-Root-Listing ist auf `resources` und `user` begrenzt; Agent-Daten
  werden nicht tenantübergreifend sichtbar.
- Embedder-/Gemini-/Rerank-Kompatibilität bleibt optional und offline
  prüfbar; optionale SDK-Imports skippen nur die tatsächlich providergebundene
  Teilmenge.
- Legacy-Namespace-, Memory-Link-, Prompt- und Bot-Retention-Verträge bleiben
  rückwärtskompatibel und sind regressionsgetestet.

## Fehlerbehandlung

| Fehlerklasse | Verhalten |
|---|---|
| unbekannter Marker oder lokale Collection-Warning | Sammlung FAIL, kein Filter-Workaround |
| fehlendes VikingBot-Extra | separater Bot-Job HOLD; Root-Result nicht künstlich grün |
| fehlendes Native-Wheel/ABI | Native-Gate HOLD mit Build-/Importnachweis |
| stale/destruktives Harness-Skript | Skript nicht ausführen; direkte aktuelle Tests verwenden |
| OAuth-Origin/Capability nicht exakt freigegeben | vor Netzwerk FAIL-CLOSED |
| Teilstream/Timeout/Providerfehler | vorherigen State unverändert lassen |

## Observability und Aufbewahrung

Artefakte enthalten Testzahlen, Exit-Codes, Versionen, Hashes und
sanitisierte Fehlertexte. Tokens, Authorization-Header, Cookie-Werte,
vollständige Config-Inhalte und Responses-State werden weder geloggt noch in
Dossiers/Telemetrie gespeichert. Temporäre Configs und Testdaten sind privat
und werden nach dem Testlauf entfernt.
