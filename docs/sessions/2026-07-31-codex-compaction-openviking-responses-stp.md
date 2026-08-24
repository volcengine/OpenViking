# Session Transfer Protocol: Open-Items-Follow-up

**Stand:** 2026-08-01
**Status:** OFFLINE-FOLLOW-UP PASS / H1 UND H2 HOLD
**Workflow:** `$tccode`, `thorough`, `critical`; Agent Workflow v4, Team 10
**Open Items:**
[Open-Item-Bericht](2026-07-31-codex-compaction-openviking-responses-open-items.md)

Dieses Dokument ist der normative, restartbare Transferstand. Historische
Baselines in den Dossiers bleiben als Entstehungsevidenz erhalten; fuer eine
Fortsetzung gelten die Identitaet, das Testinventar und die Gates hier.

## 1. Repository- und Autoritaetsstatus

| Feld | Exakter Wert |
|---|---|
| Repository/Fork | `manni07/OpenViking` (`origin`) |
| Upstream | `volcengine/OpenViking` (`upstream`) |
| Worktree | `/Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-open-items-follow-up` |
| Aktueller Publication-Record-Branch | `agent-workflow/20260801-open-items-publication-record` |
| Publication-Record-Startbasis | `ed77c27ef1af17fd555ffb59d413b0b909c2ec11`; danach ausschliesslich dieser Dokumentationsabschluss |
| Implementierungsbranch | `agent-workflow/20260801-open-items-follow-up` (retained) |
| Implementierungsbasis | `c4e3cc52272c086843f3dc64808ed1e8956abede` |
| Implementierungs-HEAD | `de9f6e3cc8ee3dcb9f6d64c2ed9fd3ec4865d369` |
| Follow-up-Merge | Fork-PR `manni07/OpenViking#3`, Merge-Commit `ed77c27ef1af17fd555ffb59d413b0b909c2ec11` |
| Vorheriger Merge | Fork-PR `manni07/OpenViking#2`, Commit `c4e3cc52272c086843f3dc64808ed1e8956abede` |
| Falscher PR | Upstream-PR `volcengine/OpenViking#3667`, geschlossen |
| Aktivierung/Live | nicht erfolgt |

Die Implementierung ist committed, zum Fork gepusht und ueber PR #3 in
`origin/main` gemergt. Der Implementierungsbranch und der isolierte Worktree
wurden nicht geloescht. Der aktuelle Publication-Record-Branch ist ein reiner
Dokumentationsnachfahre der angegebenen Startbasis; sein finaler Commit kann
nicht selbstreferenziell im selben Commit fixiert werden. Vor Fortsetzung sind
`git status`, Branch, `git rev-parse HEAD` und das aktuelle `origin/main`
read-only zu pruefen. Keine fremden Aenderungen verwerfen und kein Rebase,
Reset, Merge, Commit, Push oder PR ohne neue Autorisierung.

## 2. Abschlussklassifikation

Aktiv offen bleiben nur:

- **H1:** Offline-Preflight und spaeterer Capability-Probe. HOLD, weil exaktes
  Modell, numerische Limits, Fixture-/Tree-Hashes, Preisbasis und Credential-
  Lifecycle-Policies nicht freigegeben sind.
- **H2:** Canary/A-B. HOLD bis H1 PASS und die 20 realen plus 10 synthetischen
  Szenarien separat freigegeben sind.

Geschlossen sind H3, M1-M3 und L1-L3. Der historische Aggregate-Befund heisst
eindeutig **SEC-M2**, nicht mehr M2. Details und Abschlussbelege stehen im
Open-Item-Bericht.

## 3. Aenderungsscope des Follow-ups

Produktionsdatei:

- `openviking/resource/watch_manager.py`: Pydantic-v2-konforme
  `WatchTask`-Konfiguration ohne Vertragsaenderung.

Testdateien und Testsupport:

- `tests/models/vlm/test_volcengine_cache.py`: verwaistes Inventar entfernt;
- `tests/models/vlm/test_volcengine_chat_completions.py`: drei aktuelle
  Factory-/Sync-/Async-Vertragstests;
- `tests/unit/test_stream_config_vlm.py` und
  `tests/unit/_streaming_support.py`: Streamtest-Aufteilung und L1-L3-Vertraege;
- `tests/resource/test_watch_manager.py`: WatchTask- und lokale Fixtures;
- `tests/service/test_resource_service_watch.py`: Fixture-, Config- und
  Deferred-Queue-Isolation;
- `tests/service/test_watch_recovery.py`: lokale Fixture-/Config-Isolation.

Der produktive Deferred-Payload-Guard in `resource_service.py`, VLM-
Produktionspfade, Providerkonstruktoren und globale Konfiguration wurden in
diesem Follow-up nicht geaendert.

## 4. Finale Offline-Evidenz

| Gate | Ergebnis |
|---|---:|
| Responses-State + Hook | 102/102 PASS |
| konsolidierte 18-Dateien-Matrix, Pydantic-Warnungen als Fehler | 500/500 PASS |
| finale Watch-Matrix nach Ruff-Format | 150/150 PASS |
| Fork-PR #3 API & CLI Integration (Ubuntu) | PASS in 23m07s |
| Fork-PR #3 Plugin-/Docs-/Dependency-Checks | PASS |
| Ruff check / Ruff format / `git diff --check` | PASS |
| Skip/Xfail | keine als Ersatzbeleg verwendet |

Zusatzbelege aus den fokussierten Zwischenstufen:

```text
VolcEngine neu:              3 Vertragstests
VolcEngine gezielt:          129 PASS
VolcEngine breit vor Folgeaenderungen: 348 PASS
Stream-Datei:                50 PASS
VLM Warning-as-error:        274 PASS
WatchTask:                   7 PASS
Resource-Fixtures:           37 PASS
Service-Fixtures:            0 Setupfehler
Recovery/Scheduler:          19 PASS
Connector:                   50 PASS
Watch-Service Deferred:      21 PASS
Feishu/Queue:                23 PASS
```

Die alte `364 PASS + 8 FAIL`-Matrix und `test_volcengine_cache.py` sind nur
historische Ausgangsevidenz. Sie sind kein aktueller Reproduktionsbefehl oder
Restfehlerbestand. Der Streamtest lag beim M3-Verifikationscheckpoint bei 914
Zeilen; nach spaeteren Vertragsfaellen umfasst er am Dokumentationscheckpoint
922 physische Zeilen und bleibt deutlich unter 1000.

## 5. Sichere lokale Reproduktion

Voraussetzung ist die bestehende lokale venv und das dokumentierte Overlay:

```bash
cd /Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-open-items-follow-up
git status --short --branch
git branch --show-current
git rev-parse HEAD
git diff --check

PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  /Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest \
  -q -o addopts= -p no:cacheprovider --no-cov \
  tests/unit/test_codex_compaction_hook.py \
  tests/unit/test_codex_responses_state.py

PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  /Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest \
  -W error::pydantic.warnings.PydanticDeprecatedSince20 \
  -q -o addopts= -p no:cacheprovider --no-cov \
  tests/unit/test_codex_vlm.py \
  tests/unit/test_kimi_glm_vlm.py \
  tests/unit/test_stream_config_vlm.py \
  tests/unit/test_extra_headers_vlm.py \
  tests/unit/test_litellm_vlm_gemini_cache.py \
  tests/unit/test_model_retry.py \
  tests/unit/test_vlm_failover.py \
  tests/unit/test_vlm_reasoning_models.py \
  tests/unit/test_vlm_response_formats.py \
  tests/unit/test_vlm_thinking_param.py \
  tests/models/vlm/test_timeout_config.py \
  tests/models/vlm/test_volcengine_chat_completions.py \
  tests/resource/test_watch_manager.py \
  tests/resource/test_watch_scheduler.py \
  tests/service/test_watch_recovery.py \
  tests/service/test_resource_service_watch.py \
  tests/parse/test_feishu_parser_api.py \
  tests/service/test_resource_service_connector.py

PYTHONPATH=.:bot:/tmp/openviking-codex-responses-test-deps-20260731 \
  /Volumes/ExtremePro/projects/OpenViking/.venv/bin/python -m pytest \
  -q -o addopts= -p no:cacheprovider --no-cov \
  tests/resource/test_watch_manager.py \
  tests/resource/test_watch_scheduler.py \
  tests/service/test_watch_recovery.py \
  tests/service/test_resource_service_watch.py \
  tests/parse/test_feishu_parser_api.py \
  tests/service/test_resource_service_connector.py
```

Die drei Befehle sind der exakte aktuelle Reproduktionsvertrag fuer 102, 500
und 150 Tests. Eine veraenderte Collection-Zahl ist zuerst als Inventardrift zu
klassifizieren.

## 6. H1/H2 und Betriebsgrenze

Vor H1 duerfen weder Credential-Resolver noch Client-Factory oder Netzwerk
aufgerufen werden. Erforderlich sind mindestens:

- exakter freigegebener HTTPS-Origin und fixes Modell;
- feste Capability-/Vision-Menge;
- genehmigte Fixture- und Repository-Tree-Hashes;
- numerische Request-, Input-, Output-, Bildbyte-, Timeout-, Kosten- und
  Compaction-Limits;
- Preisbasis und Kostenberechnung;
- genau ein Credential-Slot/Fingerprint sowie Mindestgueltigkeits-, Refresh-
  und OAuth-Policy;
- Retry und Failover `0`.

H2 benoetigt zusaetzlich H1 PASS und eine separate Datenfreigabe. Offline-,
MCP- oder Testsuite-Evidenz ersetzt weder Capability-Probe noch Canary.

## 7. Review- und Stopstatus

Der angeforderte `agy`-Reviewversuch war wegen fehlender Berechtigung fuer den
Headless-Command nicht verfuegbar. Er ist **UNAVAILABLE**, nicht PASS. Die
lokalen Spec-, Codequalitaets- und Re-Reviews wurden dokumentiert; daraus folgt
keine externe Review-Freigabe.

Es erfolgten in Task 8 keine Source-/Testaenderung, kein externer Call, Commit,
Push, PR, Live-Test, Canary, keine Aktivierung oder Default-Promotion und kein
Rechner-, Server-, Runtime-, Container-, Service- oder Prozess-Restart.

Bei Hash-, Scope-, Test-, Credential- oder Policy-Drift gilt fail-loud HOLD.
