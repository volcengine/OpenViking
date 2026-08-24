# TD — Root-Fixture-Isolation

## Kritische Regressionen

| Test | Zweck | Ergebnis |
|---|---|---|
| `test_embedded_path_is_applied_before_host_storage_validation` | Embedded-Workspace und abhängige AGFS/VectorDB-Pfade werden vor Host-Validierung gesetzt; Host-Sentinel bleibt unangelegt | PASS |
| `test_embedded_path_does_not_touch_container_workspace` | `/app` wird über einen `Path.mkdir`-Guard als verbotener Seiteneffekt behandelt | PASS |
| `test_root_fixture_uses_function_scoped_offline_config` | Root-Fixture übernimmt weder Host-Pfad noch Endpoint/Credentials und setzt eine per-Test-`ov.conf` | PASS |
| `test_root_fixture_safe_file_contains_no_provider_endpoints` | Native AGFS sieht keine Provider-URL oder Credentials | PASS |
| `test_root_fixture_uses_deterministic_fake_embedder` | Embedder-Aufruf bleibt lokal und liefert die feste Dimension | PASS |
| `test_root_fixture_uses_deterministic_fake_vlm` | VLM-Aufruf bleibt lokal und liefert die feste Antwort | PASS |
| `test_workspace_override_rejects_non_object_config` | Ungültige Top-Level-Config schlägt fail-closed fehl | PASS |
| `test_workspace_override_rejects_non_object_storage` | Ungültiger Storage-Block erzeugt keinen Fallback-Pfad | PASS |
| `test_empty_embedded_path_preserves_legacy_no_override` | Leerer Pfad bleibt mit dem bestehenden No-Override-Vertrag kompatibel | PASS |
| `test_cached_config_is_still_resynchronized_for_embedded_path` | Bereits sichere Singleton-Configs synchronisieren AGFS/VectorDB weiter | PASS |
| `test_root_temp_dir_is_not_shared_project_state` | `tmp_path` statt globalem Repository-Verzeichnis | PASS |

## Legacy-Regressionen

```text
PYTHONPATH="$PWD" .venv/bin/python -m pytest \
  tests/test_root_fixture_isolation.py tests/test_config_loader.py \
  -q -o addopts= -p no:cacheprovider
```

Ergebnis am 2026-08-01: `40 passed in 0.13s` (11 neue Isolationstests plus
29 Config-Legacy-Tests).

Zusätzlich:

```text
PYTHONPATH="$PWD" .venv/bin/python -m pytest \
  tests/test_test_suite_boundaries.py -q -o addopts= -p no:cacheprovider
```

Ergebnis: `3 passed, 1 pre-existing qdrant warning`.

## Root-Collection-Gate

```text
PYTHONPATH="$PWD" .venv/bin/python -m pytest \
  --collect-only -q -o addopts= -p no:cacheprovider
```

Ergebnis: 6302 Tests gesammelt, aber FAIL bei
`tests/unit/test_vikingbot_vlm_adapter_retry.py`, weil das eigenständige
optionale `vikingbot`-Subprojekt im aktuellen Test-Venv nicht installiert ist.
Zusätzlich bleiben 15 bekannte Collection-Warnungen (11 `cli_remote`, 1
`qdrant`, 3 Hilfsklassen). Dieses Gate wird deshalb nicht als Root-Vollsuite-
PASS gewertet.

## Native-Lifecycle-Gate

Der repräsentative Lifecycle-Test erreicht nach der Isolation nicht mehr den
`/app`-Fehler, sondern bricht beim fehlenden nativen Modul ab:
`ImportError: ragfs_python native library is not available: Rust binding not
available`. Das ist eine lokale Build-/Dependency-HOLD; kein Service- oder
Provider-Live-Test wurde gestartet.

## Testsimulations-Gate

Die agent-workflow-v4-Implementierungssimulation erreichte C1–C5 =
98/97/95/95/97, aggregiert 96,4 %. Die Revisionen (Host-Sentinel,
native-AGFS-sichere Config-Datei, lokale Embedder-/VLM-Fakes) wurden umgesetzt.
Die Testsimulation muss vor Promotion separat mindestens 95 % aggregiert und
90 % je Kriterium bestätigen; nicht ausgeführte Live-/Native-Gates bleiben
HOLD.

Die Gate-Zuordnung und die unveränderliche Übergabeprüfung werden im STP in
der Evidence-Matrix geführt.
