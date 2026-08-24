# ARD — Root-Fixture-Isolation

## Auftrag und Gate

Der Root-Testlauf darf keine Konfiguration aus dem Host-
`~/.openviking/ov.conf` übernehmen, wenn ein eingebetteter Client einen
eigenen `path` erhält. Der aktuelle Host-Wert `storage.workspace=/app/...`
wird bereits während der Pydantic-Validierung angelegt; der bisherige
nachgelagerte `path`-Override kommt daher zu spät.

In diesem Change werden ausschließlich die Root-Fixtures, die
Konfigurationsreihenfolge und deterministische Offline-Regressionstests
bearbeitet. OpenClaw-P0/Service, H1/H2 und Provider-Live-Tests bleiben HOLD.
Es gibt keinen Start, Neustart oder Kill eines Dienstes, Servers oder Rechners.

## Ist-Befund

- `OpenVikingService.__init__` ruft `initialize_openviking_config(path=...)` auf.
- Diese Funktion rief zuerst `get_openviking_config()` auf.
- `OpenVikingConfigSingleton._load_from_file()` baute dabei
  `StorageConfig` mit `/app` und scheiterte auf dem schreibgeschützten Host.
- `tests/conftest.py` löschte und erzeugte für jede Funktion denselben
  `PROJECT_ROOT/test_data/tmp`; parallele Worker konnten sich gegenseitig
  löschen.
- `AsyncOpenViking.reset()` setzt den Config-Singleton nicht zurück.

Der Fehler ist mit einem sanitisierten Host-Config-Test reproduziert:
vor der Änderung: `OSError: [Errno 30] Read-only file system: '/app'`.

## Zielarchitektur

1. `OpenVikingConfigSingleton.get_instance(workspace_override=...)` wendet den
   normalisierten Embedded-Workspace auf das dekodierte JSON an, bevor
   `OpenVikingConfig.from_dict()` und `StorageConfig.resolve_paths()` laufen.
2. `initialize_openviking_config(path=...)` verwendet diesen frühen Override;
   der bestehende Pfad-/User-Override bleibt als nachgelagerte Konsistenzsicherung
   erhalten.
3. Die Root-Fixture `root_openviking_config` initialisiert pro Funktion einen
   direkten, endpoint- und credential-freien Offline-Config-Dict, schreibt
   zusätzlich eine sichere temporäre `ov.conf` und bindet
   `OPENVIKING_CONFIG_FILE` daran (auch der native AGFS-Pfad bleibt damit
   isoliert). Provider-Umgebungsvariablen werden entfernt und Embedder/VLM
   durch lokale Fakes ersetzt. Client sowie Singleton werden in einem
   `finally`-Block zurückgesetzt. Bereits beim Conftest-Import wird ein
   disposable Bootstrap-Config-Pfad gesetzt, damit Logger-Imports den Host
   nicht lesen.
4. `temp_dir` basiert auf Pytests `tmp_path`; kein globales `rmtree` und keine
   Worker-Kollision.

## Sicherheits- und Nichtziele

- Keine Host-Datei wird verändert, gelesen oder in Logs ausgegeben.
- Keine Netzwerk-/Provider-Anfrage ist Teil der Regressionstests.
- Keine automatische Behandlung der fehlenden `ragfs_python`-Native-Bindung;
  deren echte Lifecycle-Ausführung bleibt als lokale Build-/Umgebungsgrenze
  separat auszuweisen.
- Keine Änderungen an `VLMBase`, OpenClaw-Harnesses oder Live-Konfiguration.

## Abnahmekriterien

- RED-Test reproduziert `/app` vor der Implementierung; GREEN-Test bestätigt
  den Ziel-Workspace nach der Implementierung.
- Root-Fixture hat keine Host-Endpunkte, Credentials oder `/app`-Pfade.
- `temp_dir` ist pro Test und Worker eindeutig.
- Konfigurations-Regressionen und fokussierte Root-Tests sind grün; native
  Lifecycle-Tests werden nicht als grün behauptet, wenn die Bindung fehlt.
- `git diff --check` und Legacy-Konfigurationssuite bleiben grün.
