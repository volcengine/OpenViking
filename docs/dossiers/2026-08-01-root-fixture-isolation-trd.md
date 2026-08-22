# TRD — Root-Fixture-Isolation

## Betroffene Dateien

| Bereich | Datei | Änderung |
|---|---|---|
| Konfigurationskern | `openviking_cli/utils/config/open_viking_config.py` | optionaler früher Workspace-Override beim Datei-Decode |
| Root-Fixtures | `tests/conftest.py` | tmp_path-Isolation, explizite Offline-Config, sichere Resets |
| Regression | `tests/test_root_fixture_isolation.py` | Host-`/app`, Provider-Leakage und Parallelitätsvertrag |

## Vertrag des frühen Overrides

`get_instance(workspace_override=None)` bleibt für alle bestehenden Aufrufer
semantisch unverändert. Bei einem Embedded-Pfad wird nur
`config_data["storage"]["workspace"]` vor der Modellvalidierung ersetzt. Der
Wert wird mit `expanduser().resolve()` normalisiert; `StorageConfig` erzeugt
danach ausschließlich diesen Zielpfad und synchronisiert AGFS/VectorDB.
Ungültige JSON-Top-Level- oder `storage`-Typen schlagen explizit fehl.

## Root-Fixture-Vertrag

`root_openviking_config` ist function-scoped, setzt zuerst Client- und
Config-Singleton zurück, schreibt eine temporäre sichere `ov.conf`, setzt
`OPENVIKING_CONFIG_FILE` darauf, initialisiert einen lokalen LiteLLM-Test-Dummy
(`test-offline`, Dimension 3, kein `api_base`) und setzt beide Zustände auch bei
Fehlern im `finally` zurück. Provider-Umgebungen werden entfernt und Embedder/
VLM auf lokale Fakes gepatcht. Ein disposable Bootstrap-Config-Pfad wird
bereits vor den eager OpenViking-Imports gesetzt. Die Fixture initialisiert den
Singleton absichtlich direkt aus dem bereinigten Dict; die sichere Datei ist
zusätzlich der Pfad, den native AGFS-Bindings über `OPENVIKING_CONFIG_FILE`
auflösen. Die Fixture erzeugt keine echte Modell- oder Provider-Anfrage.

Der frühe `workspace_override` gilt beim ersten Singleton-Laden. Die
Collection-Bootstrap-Config und der Fixture-Reset stellen sicher, dass Root-
Embedded-Clients diesen ersten Ladevorgang kontrollieren; ein bereits von
einem anderen Test absichtlich vorinitialisierter Singleton wird nicht
stillschweigend neu geladen.

Die bestehenden `client`- und `uninitialized_client`-Fixtures hängen davon ab;
ihre Close-/Reset-Pfade sind ebenfalls `finally`-sicher.

## Testausführung

```text
PYTHONPATH="$PWD" .venv/bin/python -m pytest \
  tests/test_root_fixture_isolation.py tests/test_config_loader.py \
  -q -o addopts= -p no:cacheprovider
```

Für native Lifecycle-/Service-Tests muss `ragfs_python` separat gebaut und
verifiziert werden; ein Fehlen dieser Bindung ist ein HOLD und kein stiller
Skip der Root-Isolationsregression.

## Rollback

Der Change ist auf die drei oben genannten Source-/Testdateien begrenzt. Bei
einem Gate-Fehler wird ausschließlich der gezielte Commit zurückgenommen; der
Haupt-Checkout und ungetrackte Dateien bleiben unangetastet.
