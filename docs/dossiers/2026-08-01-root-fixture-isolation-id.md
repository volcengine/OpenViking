# ID — Root-Fixture-Isolation

## Implementierter Umfang

### Konfigurationsreihenfolge

`OpenVikingConfigSingleton.get_instance()` akzeptiert optional
`workspace_override`. Beim Datei-Laden wird der dekodierte JSON-Block geprüft,
der `storage`-Block validiert und dessen `workspace` vor
`OpenVikingConfig.from_dict()` ersetzt. Damit läuft
`StorageConfig.resolve_paths()` nur über den Embedded-Pfad. Aufrufer ohne
Override behalten die bisherige Auflösungskette.

`initialize_openviking_config(path=...)` reicht den nichtleeren Pfad an diesen
frühen Hook weiter und synchronisiert danach wie bisher User, AGFS- und
VectorDB-Pfade.

### Root-Testgrenze

`tests/conftest.py` setzt vor eager OpenViking-Imports einen kurzlebigen
Bootstrap-Config-Pfad. Die function-scoped Fixture `root_openviking_config`
schreibt zusätzlich eine sichere per-Test-`ov.conf`, bindet die
`OPENVIKING_CONFIG_FILE`-Variable daran, entfernt Provider-Umgebungen und
installiert deterministische Embedder-/VLM-Fakes. `client` und
`uninitialized_client` hängen von dieser Fixture ab und verwenden
`finally`-sichere Resets.

`temp_dir` verwendet `tmp_path / "root"`; die bisherige globale
`test_data/tmp`-Löschung entfällt.

## TDD-Evidenz

- RED vor der Änderung: Host-Config mit `/app/.openviking/data` ergab
  `RuntimeError: Failed to load config file ... Read-only file system: '/app'`;
  fehlende `root_openviking_config`-Fixture und die gemeinsame `test_data/tmp`
  ließen die beiden zusätzlichen Regressionen ebenfalls fehlschlagen.
- GREEN nach der Änderung: 40 fokussierte Regressionen (11 Isolation plus 29
  Config-Legacy-Fälle) bestanden.

## Simulation und Gate

Die agent-workflow-v4-Implementierungssimulation (Rolle
`simulation_agent`, thorough/critical, read-only) bewertete C1–C5 mit
98/97/95/95/97, aggregiert 96,4 %. Damit war das Gate >=95 % aggregiert und
>=90 % je Kriterium nach einer Revision (Host-Sentinel, sichere AGFS-Datei,
lokale Fakes) erfüllt. Diese Simulation ersetzt weder pytest-Ausführung noch
die offenen Native-/Live-Gates.

## Änderungsmanifest

| Datei | Zweck |
|---|---|
| `openviking_cli/utils/config/open_viking_config.py` | früher Embedded-Override |
| `tests/conftest.py` | Bootstrap, sichere Root-Config, tmp_path, Cleanup |
| `tests/test_root_fixture_isolation.py` | 11 Offline-Verträge |
| `docs/dossiers/`, `docs/plan/`, `docs/tests/`, `docs/sessions/`, `docs/manuals/`, `docs/openitem/`, `docs/proposals/`, `docs/vision/` | tccode-Nachweise |

Der bestehende `_patch_agfs_grep_if_missing()`-Workaround im Root-Conftest ist
vorbestehend und wurde in diesem Change nicht semantisch verändert.

Die verbindliche Status- und Nachweismatrix steht im STP unter „Evidence
matrix“; sie trennt Offline-PASS von Root-Collection-FAIL/HOLD und nicht
ausgeführten Native-/Live-Gates.

## Bewusste Grenzen

- Native `ragfs_python`-Lifecycle-Ausführung ist in diesem Worktree nicht
  vorhanden und bleibt HOLD; sie wird nicht als PASS maskiert.
- Direkte Tests ohne Root-Fixture erhalten nur die sichere Collection-
  Bootstrap-Datei; Integrations-/Server-Fixtures mit eigener Config bleiben
  separate Testgrenzen.
- Keine Provider-, MCP-, OpenClaw- oder Service-Live-Aufrufe und kein Neustart.
