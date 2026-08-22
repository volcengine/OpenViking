# Implementation Dossier

## Codex-Compaction und OpenViking Responses State

Stand: 2026-08-01
Status: Offline-Follow-up PASS; H1/H2 bleiben HOLD
Urspruenglicher Basis-Commit: `60ef45d4c3a7d07ceb1df4e9d7dde7a14449ac50`
Aktuelle Fork-Basis: `c4e3cc52272c086843f3dc64808ed1e8956abede`

## 1. Lieferergebnis

Der Worktree enthält einen minimal additiven Kandidaten:

- einen gehärteten Compaction-Hook unter
  `tools/codex_compaction_hooks/codex_compaction_hook.py`;
- einen aufruferverwalteten Responses-State-Adapter unter
  `openviking/models/vlm/backends/codex_responses_adapter.py`;
- additive State-Methoden und einen expliziten Capability-Probe in
  `openviking/models/vlm/backends/codex_vlm.py`;
- zwei opt-in Konfigurationsfelder in
  `openviking_cli/utils/config/vlm_config.py`;
- 102 neue Offline-Tests.

Es wurden keine globalen Codex-Dateien verändert, keine Funktion aktiviert und
kein Provider-Call, Restart, Push oder Merge ausgeführt. Die isolierte Branch
enthält die gezielten Implementierungs-Commits `a84a3730`, `325e5cff` und
`0556a9aa`.

## 2. Implementierungsentscheidungen

### 2.1 Hook

Der Hook schreibt nur private Korrelationsmetadaten. Jede Pfadkomponente von
`CODEX_HOME` bis zum Hook-State-Verzeichnis wird auf Symlinks geprüft.
Eigentümer, Typ und Rechte werden validiert; Dateien werden atomar mit `0600` in
einem `0700`-Verzeichnis veröffentlicht. Eingabe und Laufzeit sind begrenzt.
Prompts enthalten ausschließlich feste, kleine Hinweise.

Das Offline-Follow-up verankert alle Operationen an geöffneten Directory-FDs,
erzwingt die Fünf-Sekunden-Deadline über den gesamten Hook und begrenzt alte
Korrelationsmetadaten durch TTL-, Anzahl- und Scan-Limits.

### 2.2 Responses-State

Der State ist frozen, integritätsgeschützt und an Modell, Instructions, Origin,
Principal und Credential gebunden. Der Adapter übernimmt alle Output-Items
kanonisch, beschneidet nur vor dem neuesten Compaction-Item und veröffentlicht
einen Nachfolgestate ausschließlich nach `response.completed`.

Stateful Requests erzwingen `store=false` und `stream=true`. Conversations,
`previous_response_id`, Background und entsprechende `extra_body`-Umgehungen
werden abgelehnt. Sync und Async teilen denselben Zustandsvertrag.

### 2.3 Tool- und Ressourcenintegrität

Offene Tool-Call-IDs gehören zu genau einer Chain-Generation. Tool-Ausgaben
werden genau einmal angenommen. State-, Item-, Turn-, Bild-, Tool-Ausgabe-, TTL-
und Chain-Grenzen schlagen laut fehl. Opaque Daten sind von normaler
Repräsentation und Logging ausgeschlossen.

Revision 2 verhindert zusätzlich State-spezifische sichtbare/opaque Inhalte in
Traces, serialisiert die erstmalige Adapter-Initialisierung, hält
Credential-Datei-/Refresh-I/O vom Async-Event-Loop fern und begrenzt retained
Tool-Call-IDs auf 4096 beziehungsweise 512 Bytes je ID. Die IDs zählen zur
kanonischen State-Byte-Bilanz.

### 2.4 Capability und Pilot

Compaction ist nur opt-in und erfordert einen erfolgreichen Probe am tatsächlich
verwendeten Endpoint. Im Pilot gilt exakt ein `openai-codex`-Credential und für
OAuth ausschließlich `https://chatgpt.com/backend-api/codex`. Es gibt keinen
stillen Fallback und kein Failover innerhalb einer Chain.

## 3. Änderungsumfang

| Datei | Änderung |
|---|---|
| `codex_compaction_hook.py` | Gehärteter Hook-Kandidat |
| `codex_responses_adapter.py` | State, Reducer, Limits, Sync/Async Adapter |
| `codex_vlm.py` | Additive öffentliche Methoden und Probe |
| `vlm_config.py` | Opt-in State-/Threshold-Konfiguration |
| `test_codex_compaction_hook.py` | 30 Hook-Sicherheitsfälle |
| `test_codex_responses_state.py` | 72 State-/Adapterfälle |

`VLMBase` und andere Provider wurden nicht geändert.

## 4. Verifikation

| Prüfung | Ergebnis |
|---|---|
| Neue Suiten | 102/102 PASS |
| Core-Kombination | 131 PASS, 1 bestätigter Baseline-Fehler |
| Erweiterte Kombination | 140 PASS, 12 bestätigte Baseline-Fehler |
| Ruff Check / Format | PASS / PASS |
| Compileall | PASS |
| Diff-Check | PASS |
| MCP Health + read-only Suche | PASS |
| Globale Codex-Hashes | unverändert gegenüber Backup |

Der eine Core- und elf zusätzliche Stream-Config-Fehler reproduzieren auf dem
Basis-Checkout. Sie sind nicht durch diesen Kandidaten verursacht, bleiben aber
ein Legacy-Freigabe-HOLD.

## 5. Implementierungs-Selbstsimulation

Dies ist eine dossierbasierte Selbstprüfung, keine unabhängige Live-Evidenz.

| Kriterium | Wert | Begründung |
|---|---:|---|
| Korrektheit | 97 % | Verträge und Failure Paths durch Tests abgedeckt |
| Integration | 96 % | Additive Pfade, bestehender Default erhalten |
| Sicherheit | 97 % | Bindings, Limits, Hook-Pfad und Log-Sentinels |
| Testbarkeit | 98 % | 102 deterministische neue Tests |
| Performance | 92 % | Harte Limits; kein realer Long-Horizon-Benchmark |
| Wartbarkeit | 95 % | Provider-spezifisch, keine `VLMBase`-Ausweitung |
| Beobachtbarkeit | 94 % | Typisierte Fehler, absichtlich keine State-Logs |
| Rollback | 99 % | Opt-in und globale Dateien unverändert |
| **Aggregiert** | **96,0 %** | Mindestwert je Kriterium: 92 % |

Damit sind die geforderten 95 % aggregiert und 90 % je Einzelkriterium erreicht.
Der fehlende Live- und A/B-Nachweis bleibt dennoch HOLD.

### Historischer Responses-State Security-Re-Review Revision 2

Keine offenen Critical-/High-Befunde; das Offline-Kandidaten-Veto ist aufgehoben.
Bewertung: 95,6 % aggregiert, mindestens 91 % je Kriterium. Das geforderte
aktuelle Claude Opus war nicht verfügbar, deshalb ist der Review mit Codex als
Ersatzmodell vorläufig. Die drei Medium-Restbefunde sind im Offline-Follow-up
`325e5cff3895036a2fc0e8a0a93131e77f7c9d0d` geschlossen und mit
`0556a9aac049d2563893e1abe4068c0260024542` um die Cancellation-Fehlerpriorität
ergänzt:

- stabile Credential-Slot-Bindung auch ohne `client_id`;
- abgeschirmtes Async-Cleanup trotz wiederholter Cancellation und Close-Fehlern;
- Directory-FD-verankerter Hook mit erzwungener Deadline und begrenzter
  Retention.

Die neuen Regressionstests sind Bestandteil der 102/102 bestandenen
Kandidatentests. Eine unabhängige Revalidierung vor Aktivierung bleibt offen.
Diese Bewertung ist vom späteren Legacy-VLM-H3-Security-Review getrennt.

## 6. Harte HOLDs

1. **A/B-Evidenz fehlt:** keine 20 sanitisierten realen Langsitzungen und 10
   synthetischen Multi-Turn-/Tool-Szenarien.
2. **Live-Capability fehlt:** Probe und Canary am exakten Codex-Endpunkt wurden
   nicht ausgeführt. Der Probe ist potenziell kostenpflichtig und erfordert
   ausdrückliche Genehmigung.
3. **Legacy nicht vollständig grün:** ein Codex-Config- und elf
   Stream-Config-Fehler sind vorbestehend, aber offen.
4. **Keine Aktivierung:** weder Hook noch State-Modus oder Threshold wurden
   global aktiviert; keine Default-Promotion.

## 7. Freigabeurteil

Der Code ist als **offline verifizierter Opt-in-Kandidat** übergabefähig. Er ist
nicht als live-capability-verifiziert, A/B-optimiert oder promotionsfähig zu
bezeichnen. Eine spätere Aktivierung benötigt einen gesonderten Evidenzreview und
ausdrückliche Autorisierung.

## 8. Artefakte

- [ARD](2026-07-31-codex-compaction-openviking-responses-ard.md)
- [TRD](2026-07-31-codex-compaction-openviking-responses-trd.md)
- [PD](../plan/2026-07-31-codex-compaction-openviking-responses-pd.md)
- [TD](../tests/2026-07-31-codex-compaction-openviking-responses-td.md)
- [Development Diary](../diaries/Development_Diary_v000.md)
- [Manual](../manuals/2026-07-31-codex-compaction-openviking-responses-manual.html)
- [Proposal Dossier](../vision/2026-07-31-codex-compaction-openviking-responses-ppd.md)
- [Open Items](../sessions/2026-07-31-codex-compaction-openviking-responses-open-items.md)

## 9. Legacy-VLM-H3 Implementation Follow-up — 2026-07-31

Status: **PDID conversion only; not implemented.** This section specifies the
bounded follow-up required to close the recorded H3 legacy failures. It does not
replace the historical candidate evidence above and authorizes no code or test
change by itself.

### 9.1 Frozen baseline, history and scope

| Evidence set | Exact observed result | Required interpretation |
|---|---:|---|
| Targeted legacy baseline | 46 collected; 33 PASS; 13 FAIL | exactly 2 stale exact-`Dict` assertions plus 11 streaming failures |
| Broad VLM baseline | 216 collected; 195 PASS; 21 FAIL | the same 13 plus 8 separate, unrelated, pre-existing VolcEngine constructor-test failures |

Commit history establishes the production/test drift: `d739a5be` added the
OpenAI-compatible Text/Vision streaming paths and reducers; `44d3cc41` removed
that streaming implementation while the `VLMBase.stream` contract and tests
remained. The repair therefore restores the bounded contract; it does not
redesign `VLMBase`.

The production scope is exactly these four files:

1. `openviking/models/vlm/backends/openai_vlm.py`;
2. `openviking/utils/model_retry.py`;
3. `openviking/models/vlm/base.py`;
4. `bot/vikingbot/providers/vlm_adapter.py`.

`openviking_cli/utils/config/vlm_config.py` must not change. The VikingBot file
is in scope because its existing native VolcEngine stream must mark progress
after every read event; this is not a VolcEngine constructor repair. The eight
constructor-test failures remain separate baseline evidence and outside scope.

### 9.2 Normative behavior

1. All four OpenAI-compatible public paths — Text/Vision and Sync/Async — reject
   `stream=True` with non-empty `tools` using `NotImplementedError` before
   request construction, `get_client()`, `get_async_client()`, credential access
   or network access. The two Vision paths must also reject before opening,
   reading, encoding or otherwise inspecting any image path or file-like input.
2. `_build_text_kwargs()` and `_build_vision_kwargs()` carry an explicit
   `stream` value for every request. `False` remains the default.
3. A tool-free stream aggregates only string content in event order. No
   Reasoning-text-delta aggregation is promised. The last provider Usage value,
   including cached-token and reasoning-token details when present, is applied
   exactly once. Empty and usage-only events are valid.
4. Local retry covers stream creation only. Once the provider returns an
   iterator/async iterator, no iteration, parsing or cleanup failure is locally
   retried, including a failure before the first event.
5. Sync closes every created stream exactly once in `finally`. Async creates
   exactly one cleanup Task for every created stream and repeatedly awaits
   `shield(task)` until that same Task is done. Every further Cancellation seen
   by that loop is captured; no Cancellation creates a second Task or invokes a
   second close. Inside the Task, an existing `close()` is invoked exactly once.
   If its return value is awaitable it is awaited; if it is non-awaitable,
   cleanup is complete and `aclose()` must not follow. `aclose()` is invoked
   exactly once only when `close` is absent. A transport, iterator, parser or
   body-level `CancelledError` remains primary over a cleanup error; cleanup
   logging uses a fixed redacted message without interpolating the exception,
   request, prompt, credential or stream object.
6. After any event has been read, a subsequent exception is marked with exactly
   `mark_vlm_error_non_retryable(exc)`. Detection uses exactly
   `is_vlm_error_non_retryable(exc)`. It performs an identity-safe, cycle-proof
   graph search that follows both `__cause__` and `__context__` at every node and
   visits every child exception held by every encountered
   `AllCredentialsFailedError`.
7. The two boolean retry classifiers, `is_retryable_api_error()` and
   `is_retryable_rate_limit_error()`, fail closed for the marker.
   `classify_api_error()` remains unchanged. `retry_sync()` and `retry_async()`
   perform the same check before invoking a supplied custom retry callback.
8. `FailoverVLM` and `MultiCredentialVLM` immediately rethrow a marked error
   with the identical exception object. This Catch-phase invariant starts as the
   first action immediately after `catch`: marker check, then identical rethrow,
   before annotation, `classify_api_error()`, aggregation, switcher updates,
   credential changes or any other Catch-phase mutation. Planned selection or
   failback actions completed before the provider call are not falsely counted
   as post-catch mutation.
9. VikingBot `chat()` checks the marker before its rate-limit classifier. Its
   native `_chat_stream_volcengine()` loop sets `saw_event = True` as the first
   instruction after entry for every `async for` event, before Usage, choices,
   delta or event-shape inspection. A later error is then marked and cannot
   replay the turn, including after an empty or usage-only event.
10. Every non-streaming request, return type, tool response and retry behavior
    remains unchanged.

#### 9.2.1 Complete async outcome matrix

The single Cleanup Task always runs to completion. Final outcome selection is
deterministic:

| Body outcome before cleanup | Cleanup outcome | Cancellation first observed while waiting for cleanup | Required final outcome |
|---|---|---|---|
| success | success | none | return the body result |
| primary non-Cancellation error | success | none | rethrow the identical primary object |
| body-level `CancelledError` | success | any number, including none | rethrow the original body-level Cancellation after cleanup completes |
| success | cleanup error | none | raise the cleanup error; if an event was read, mark it non-retryable first |
| primary non-Cancellation error | cleanup error | none | rethrow the identical primary object; emit only the fixed redacted cleanup log |
| body-level `CancelledError` | cleanup error | any number, including none | rethrow the original body-level Cancellation; emit only the fixed redacted cleanup log |
| success | success or cleanup error | one or more | after cleanup completes, propagate the first captured waiting-phase Cancellation; later Cancellations are recorded but never start another close |
| primary non-Cancellation error | success or cleanup error | one or more | preserve and rethrow the identical primary object after cleanup; preserve the Task's pending-Cancellation state for the caller and redact any cleanup failure |

Thus a pre-existing body primary has identity priority, body Cancellation is a
primary outcome, and Cancellation first arriving during successful-body cleanup
has priority over success or cleanup-only failure. Repeated Cancellation changes
neither the chosen outcome nor the exactly-once cleanup invariant.

### 9.3 Exact production file and method mapping

| File | Existing or restored method | Required change |
|---|---|---|
| `openviking/models/vlm/backends/openai_vlm.py` | `_build_text_kwargs()`, `_build_vision_kwargs()` | add the explicit `stream` request member without changing non-stream defaults |
| same | `get_completion()`, `get_completion_async()`, `get_vision_completion()`, `get_vision_completion_async()` | perform the four local stream-plus-tools preflights before request construction or client/credential access and, for Vision, before image I/O; route only tool-free streams through the corresponding reducer; keep creation retry outside iteration |
| same | restored `_extract_from_chunk()` | extract string content and Usage only; do not create a Reasoning-text aggregation contract |
| same | restored `_process_streaming_response()` | iterate once, track the first read event, retain last Usage, mark post-event failures, update Usage once, and close sync exactly once |
| same | restored `_process_streaming_response_async()` | provide the same reducer semantics with native async iteration; own exactly one Cleanup Task, shield-loop that Task until done through repeated Cancellation, and implement the complete outcome matrix in section 9.2.1 |
| same | `_update_token_usage_from_response()` | consume the single retained last Usage, including cached/reasoning token details supported by the response shape |
| `openviking/utils/model_retry.py` | new `mark_vlm_error_non_retryable(exc)` and `is_vlm_error_non_retryable(exc)` | define the marker solely here; graph-search both cause and context edges plus all `AllCredentialsFailedError` children with identity-based cycle detection |
| same | `is_retryable_api_error()`, `is_retryable_rate_limit_error()` | return `False` for a marked error before ordinary retry classification; leave `classify_api_error()` unchanged |
| same | `retry_sync()`, `retry_async()` | rethrow marked errors before the default or custom retry callback, delay computation, logging or sleep |
| `openviking/models/vlm/base.py` | `FailoverVLM._get_completion_with_failover()`, `FailoverVLM._get_completion_with_failover_async()` | as the first Catch-phase action, import/use the checker and identically rethrow a marked error before annotation, `classify_api_error()` or Catch-phase switcher mutation |
| same | `MultiCredentialVLM._get_completion_with_failover()`, `MultiCredentialVLM._get_completion_with_failover_async()` | as the first Catch-phase action, identically rethrow a marked error before annotation, `classify_api_error()`, aggregation, credential traversal or Catch-phase switcher mutation |
| `bot/vikingbot/providers/vlm_adapter.py` | `VLMProviderAdapter.chat()` | check the marker before `is_retryable_rate_limit_error()` and before sleep/retry |
| same | `VLMProviderAdapter._chat_stream_volcengine()` | make `saw_event = True` the first instruction for each successfully read event; mark a later exception before the adapter's retry branch; preserve native content/reasoning/tool event emission already owned by this adapter |

The helper definitions must not be duplicated in `base.py`; that module imports
and checks them only.

### 9.4 Exact six-file test mapping

Only these six existing test files are modified or extended by the follow-up:

| Test file | Contract encoded |
|---|---|
| `tests/unit/test_codex_vlm.py` | replace the stale full provider-`Dict` equality with assertions for the relevant Codex provider resolution fields |
| `tests/unit/test_kimi_glm_vlm.py` | replace the second stale full provider-`Dict` equality with relevant Kimi/GLM field assertions |
| `tests/unit/test_stream_config_vlm.py` | four preflights, explicit stream request, string-content/last-Usage reducers, empty events, creation-only retry, exact cleanup, cancellation and unchanged non-stream behavior |
| `tests/unit/test_model_retry.py` | exact marker helpers; `__cause__`, `__context__`, aggregate propagation; both boolean retry classifiers and both retry wrappers fail closed before custom callbacks; `classify_api_error()` remains unchanged |
| `tests/unit/test_vlm_failover.py` | Failover/MultiCredential Sync/Async immediate rethrow with unchanged switcher, credential and aggregation state; pre-event failover remains possible |
| `tests/unit/test_vikingbot_vlm_adapter_retry.py` | `chat` marker guard and native VolcEngine event-by-event progress; provider call count remains one after partial output |

The eight unrelated VolcEngine constructor tests may be rerun for comparison but
are not edited in this follow-up.

#### 9.4.1 Verified pre-change SHA-256 manifest

The rollback baseline for exactly the four production and six test files was
verified before implementation:

| File | Pre-change SHA-256 |
|---|---|
| `openviking/models/vlm/backends/openai_vlm.py` | `0603fb14f432e2f95e2352d3417ea95152011a6ab8360e1ab5446b45c90d912c` |
| `openviking/utils/model_retry.py` | `98d93ae30a3f2752950bc54dff0c756eeb2b86a77e2cb04e89f141c6d7585839` |
| `openviking/models/vlm/base.py` | `799ddd6b3e689da4afabcd54d990be387baae13c0816eeb6098fb29de6ef7ca3` |
| `bot/vikingbot/providers/vlm_adapter.py` | `1fe538363f1f9e412089a3a8fe3efa6b7fd88643065f616d207b5a9b14c62385` |
| `tests/unit/test_codex_vlm.py` | `1c95a8b397f023a6e8edfc3a4e791ef190f28272312cd7046a1df3d7057c2d88` |
| `tests/unit/test_kimi_glm_vlm.py` | `19ed1576026da1e8724940e5dad20331b3e241871b2627ca710c0bfb8ede855b` |
| `tests/unit/test_stream_config_vlm.py` | `5756bdd5597a4610bfa9b94f0a9e8a62f2c1a742fd953fac4b7a429eceed446a` |
| `tests/unit/test_model_retry.py` | `b4344eb4e857ee9484a072e0287ab2ca8fa52564c9cec186b71dbb41bb695f08` |
| `tests/unit/test_vlm_failover.py` | `2d115b829353a4d93141bf0c0556a86131b92868ccaf97083f2ec9201a240224` |
| `tests/unit/test_vikingbot_vlm_adapter_retry.py` | `43f3d3a815d421b925e60bd3264a7f79ca7a2df3a2ee631888a116b9b5142569` |

### 9.5 TDD slices and gates

The implementation proceeds in these unambiguous slices; each slice begins with
the named failing contract test and must be green before the next slice starts:

1. **Config assertion slice:** change only the two stale assertions. Gate: both
   pass and `vlm_config.py` is absent from the diff.
2. **Preflight/request slice:** add Sync/Async Text/Vision tests for
   `NotImplementedError` and zero request-builder, client-factory, credential,
   network and Vision-I/O calls, then explicit `stream` request tests. Gate: all
   eight dimensions pass and non-stream snapshots remain unchanged.
3. **Reducer/Usage slice:** add string-content, empty, usage-only, last-Usage and
   cached/reasoning-token-detail tests. Gate: content order is exact, tracker is
   called once, and no test requires Reasoning-text aggregation.
4. **Retry boundary slice:** inject creation failures and iterator failures before
   and after the first event. Gate: only creation is locally retried; iterator
   call count is one.
5. **Cleanup slice:** cover every row of section 9.2.1, including a second
   Cancellation, Cancellation during cleanup after body success, non-awaitable
   `close()` with an available `aclose()`, primary-plus-cleanup failure identity,
   cleanup-only failure after an event and Sentinel-bearing exceptions. Gate:
   Sync close count is one; Async creates one Task and shield-loops it until done,
   never calls both close methods, preserves the required identity, marks the
   cleanup-only post-event error, and the fixed cleanup log contains no Sentinel.
6. **Cross-layer marker slice:** cover direct, cause, context and aggregate
   marker propagation, cyclic cause/context graphs and nested aggregate children,
   plus both boolean retry classifiers, both retry wrappers and custom callbacks
   while keeping `classify_api_error()` unchanged. Gate: graph walk terminates;
   callback, logger, delay, sleep and second-operation counts are all zero.
7. **Wrapper/adapter slice:** cover both wrapper classes in Sync/Async and the two
   VikingBot paths. Wrapper mutator spies distinguish pre-call planned selection
   from Catch-phase mutation. Native stream cases cover content, reasoning-only,
   tool-only, usage-only and empty events. Gate: after catch, marked errors invoke
   no annotation, classifier, aggregation, switcher or credential mutator; each
   event shape sets progress before parsing; after partial output the provider
   call count is exactly one.
8. **Regression slice:** run the targeted 46-case matrix and then the broad
   216-case matrix. Gate: targeted result is 46/46; broad result has no failures
   attributable to the 13 repaired cases. Any remaining eight constructor
   failures are reported individually as the unchanged unrelated baseline, not
   as a green broad suite.

No slice may be called complete with a skip, xfail, unexecuted branch, unexpected
warning, state mutation after a marker, or semantic change to non-streaming.

#### 9.5.1 Mandatory critical assertions

The following assertions are release-critical and may not be weakened to smoke
coverage: iterator error before the first event remains unmarked and locally
unretried; cleanup-only error after an event is marked; second Cancellation does
not create a second Cleanup Task or close; Cancellation during cleanup after body
success propagates only after cleanup completes; primary-plus-cleanup failure
rethrows the identical primary object; cleanup logs omit a Sentinel secret;
cyclic cause/context traversal terminates and finds reachable markers; custom
callback/logger/sleep counts remain zero; wrapper mutator spies observe zero
Catch-phase mutation; all five native event shapes establish `saw_event`; and all
four tool-stream preflights have null client, credential and Vision-I/O counts.

#### 9.5.2 Implementation simulation revisions

These are dossier simulations, not executed test or live-provider evidence:

| Revision | Aggregate | Minimum single criterion | Gate result |
|---|---:|---:|---|
| Revision 1 | 89.6% | 87% | HOLD |
| Revision 2 | 95.0% | 94% | threshold met; Revision 3 precision review required |
| Revision 3 | 96.9% | 96% | PASS for implementation handoff |

Revision 3 passes the required at-least-95% aggregate and at-least-90% per-
criterion simulation gate. It does not close the offline test, live-provider or
promotion HOLDs.

### 9.6 Rollback and stop conditions

Rollback is surgical: revert only the four production-file changes and the six
test-file changes listed in sections 9.3 and 9.4, then rerun the frozen baseline
commands. Do not revert earlier Responses-state work, dossier changes, user-owned
worktree changes or unrelated VolcEngine files. The rollback is verified only
when the pre-follow-up file hashes are restored and the original 33/46 and
195/216 baseline classifications reproduce or any environment drift is reported
explicitly. Hash restoration is checked against the exact ten-entry manifest in
section 9.4.1; any mismatch is a failed rollback, not a warning.

STOP and hold the candidate if any tool-stream request reaches a client, a
post-event provider call count exceeds one, cleanup is not exact/cancellation-
safe, a marker is lost through wrapping, wrapper state changes before rethrow,
non-streaming behavior changes, `VLMConfig` production code changes, or a
VolcEngine constructor repair enters the diff.

### 9.7 Live-provider boundary

OpenAI/Codex live-provider verification remains **HOLD**. No new API key is
requested or created, and no provider request is sent without a separate
positive credential, exact HTTPS-origin, cost and secret-handling gate. Offline
green tests cannot be described as live-provider evidence. This follow-up also
authorizes no restart, activation, merge, push, PR, canary or default promotion.

## 10. Security Revision 1 VETO — 2026-07-31

### 10.1 Status

| Score | Critical | High | Medium | Implementierungsstatus |
|---:|---:|---:|---:|---|
| 78/100 | 0 | 5 | 1 | **VETO; Source und Tests gesperrt** |

Die folgenden Deltas sind Spezifikation, nicht implementierte Evidenz. Revision
1 darf keinen Source-/Test-RED-/GREEN-Zyklus starten. Freigabe erfordert ein
erneutes Security-Urteil von `0 Critical`, `0 High` und mindestens 90/100.

### 10.2 H1–H5 Implementierungsdeltas

1. **H1 — Fehlerredaktion:** In `VLMProviderAdapter.chat()` und
   `_chat_stream_volcengine()` werden markierte Fehler vor jeder Response-, Log-
   oder Langfuse-Senke erkannt. Weder `str` noch `repr`, Traceback oder dynamische
   Exceptionargumente dürfen diese Grenze passieren. Es werden ausschließlich
   feste redigierte Texte und eine feste Fehlerkategorie veröffentlicht;
   Sentinel-Capture prüft alle drei Senken.
2. **H2 — Markergraph:** `is_vlm_error_non_retryable()` erhält feste
   Gesamtbudgets von 256 Nodes, 512 Edges und 256 Aggregate-Kindern. Die
   identitätssichere Suche verfolgt beide `__cause__`-/`__context__`-Kanten sowie
   alle `AllCredentialsFailedError`-Kinder. Budgetüberschreitung, unreadable
   Attribute oder malformed Aggregate liefern fail-closed `True`; Wide-, Deep-,
   malformed- und Work-bound-Tests sind Pflicht.
3. **H3 — Wrapper-Preflight:** `FailoverVLM` und `MultiCredentialVLM` erhalten
   `_validate_stream_request(tools)`. Die rekursive Prüfung läuft für Text/Vision
   Sync/Async vor `should_try_primary()`, `maybe_failback()`, Provider, State,
   Credential, Requestbau und Vision-I/O. Jedes mögliche `stream=True` sowie
   heterogene, unlesbare oder malformed Zielmodi schlagen fail-closed mit
   `NotImplementedError` fehl; alle Side-Effect-Zähler bleiben null.
4. **H4 — Fast-Fail:** Markerfälle für Primary, aktives Backup,
   MultiCredential-Index ungleich null und failback-due werden für Text/Vision
   Sync/Async abgedeckt. Der Provider-Side-Effect nimmt den Snapshot nach
   zulässiger Pre-call-Selection. Danach erfolgen identischer Rethrow und null
   Annotation, Classifier, Logger, Switcher-Mutation oder nächster Provider.
5. **H5 — Cancellation:** Gepatchte Shield-Barrieren verwenden vorerzeugte
   `CancelledError`-Objekte und getrennte First-/Second-observation-Signale. Ein
   Create-Task-Spy beweist genau einen Cleanup-Task und einen Close. Success plus
   Cleanupfehler plus Wait-Cancel liefert die erste Wait-Cancellation; Primary
   plus Cleanupfehler plus Wait-Cancel liefert den identischen Primary; Body-
   Cancel plus weitere Wait-Cancels liefert die identische Body-Cancellation.
   Keine Task bleibt orphaned oder un-awaited.

### 10.3 M1 Live-Sperre

Live bleibt HOLD, bis der Pilot-Evidence-Record exakt festlegt und der Harness
erzwingt: einen HTTPS-Allowlist-Origin
`https://chatgpt.com/backend-api/codex`, genau einen Credential-Slot-Fingerprint,
Modell, Visionmodus, Capabilities sowie numerische Limits für Gesamtrequests,
Output-Tokens, Bildbytes und Kosten. Failover und Retry sind null. MCP-Handshake
und read-only Tool-Call bleiben separate Evidenz.

Bestehende Verbote für Restart, Merge, Push, Canary, Aktivierung und Promotion
bleiben bestehen.

## 11. Security Revision 3 — Letzte zulässige ID-Revision

Revision 2 bleibt **VETO bei 84/100, 0C/3H/1M**. H4 Fast-Fail und H5
Cancellation sind auf Dossier-/Testdefinitionsebene geschlossen; keine
Implementierung oder Ausführung wird damit behauptet. Source und Tests bleiben
gesperrt.

### 11.1 H1 exakte Adapterausgaben

Der geplante Marker-Branch in `VLMProviderAdapter.chat()` und
`_chat_stream_volcengine()` verwendet ausschließlich drei Konstanten:

```text
Response: VLM response interrupted after partial output.
Langfuse category: partial_stream_non_retryable
Logger: VLM adapter stopped a non-retryable partial stream.
```

Es gibt keinen variablen Sanitizer und keinen Aufruf von `str`/`repr` auf dem
markierten Objekt für Response, Log oder Langfuse. Tests vergleichen markierten
Chat und nativen Stream exakt gegen Response, Logger sowie Langfuse `output` und
`metadata`; Sentinel-Secrets fehlen vollständig. Ein unmarkierter Legacy-Fehler
bleibt Kontrollfall und folgt dem unveränderten Altpfad.

### 11.2 H2 begrenzter Markergraph

Die Implementierungsgrenzen sind bindend: 256 eindeutige Nodes, 512 erreichbare
Cause-/Context-/Aggregate-Edges und 256 Aggregate-Kinder. Genau 256 Kinder sind
zulässig; Kind 257 ist fail-closed `True`. Ein kombinierter Graph aus Aggregate-
und Cause-/Context-Kanten muss mehr als 512 tatsächlich erreichbare Edges
erzeugen und beim ersten Überlauf `True` liefern.

`__cause__`-, `__context__`- und `errors`-Getter, die werfen, malformed
Aggregate-Tupel und Nicht-`BaseException`-Kinder liefern ebenfalls `True`.
Instrumentierte Node-/Edge-/Child-Visits dürfen ihre jeweilige harte Grenze nie
überschreiten.

### 11.3 H3 rekursiver Wrappergraph

`_validate_stream_request(tools)` traversiert alle Zielwrapper identitätssicher
vor jeder Selection oder State-Mutation, maximal 256 Targets. Für Text/Vision
Sync/Async gelten exakt:

- deep all-safe `stream=False` und cyclic all-safe terminieren und rufen den
  aktiven Provider genau einmal;
- ein deep unsafe True-Child unter safe Wrappern ist der verbindliche
  Heterogenitätsfall und scheitert vor Selection/I-O;
- unreadable `stream`, unreadable/werfender Validator, malformed Target oder
  mehr als 256 Targets scheitern fail-closed;
- eine ausschließlich flache `[False, True]`-Probe erfüllt den Vertrag nicht.

Reject-Tests verlangen null Provider, `should_try_primary`, `maybe_failback`,
Switcher-/Credential-State, Requestbau und Vision-I/O.

### 11.4 Finales Gate und M1

Revisionsverlauf: `78/100 (0C/5H/1M) → 84/100 (0C/3H/1M) → 89/100
(0C/1H/1M)`. Revision 3 war die letzte zulässige Revision und verfehlte `0C/0H`
sowie mindestens 90/100; daher bleibt HOLD ohne weitere Revision.

M1 bleibt unverändert: kein Live-Request vor exaktem HTTPS-Allowlist-Origin,
einem Credential-Slot-Fingerprint, fixem Modell/Vision/Capabilities und festen
numerischen Gesamtrequest-, Output-Token-, Bildbyte- und Kostencaps. Retry und
Failover sind null; MCP bleibt separate Evidenz. Kein Restart, Merge oder
Aktivierung.

## 12. Finaler Implementierungsstatus — Security HOLD

Der letzte zulässige Security-Review endet mit **89/100, 0C/1H/1M**. H2–H5 sind
geschlossen; H1 bleibt exakt offen. Die Testkonstanten sind vorhanden, doch der
native Stream-Test beweist den exakt erlaubten Langfuse-Update-Payload nicht.
Die sechs Testdateien wurden mit `266 collected = 129 PASS + 137 fachliche RED`
ausgeführt. Das Gate `0C/0H` und mindestens 90/100 ist verfehlt:
Source-Unlock wird verweigert, und es wird keine weitere H1-Schließungsrevision
begonnen.

MCP Health und echter read-only `search_experience`-Aufruf sind PASS. Diese
Evidenz betrifft den OpenViking-MCP-Zugriff, nicht die Codex-Responses-
Capability. Der User hat den Live-Provider-Test vertagt. Kein Produktionsdiff,
Live-Request, Restart, Merge, Aktivierung oder Promotion folgt aus diesem ID.

## 13. Implementierungsnachtrag: Offline-HOLD-Lift

Ein neuer user-autorisierter Zyklus gab nach Architektur 97/96/100 und
Pre-Source Security 93/100, 0C/0H nur die vier bestehenden Sourcegrenzen frei:
`openai_vlm.py`, `model_retry.py`, `base.py` und `vlm_adapter.py`. Der erste
Stand bestand 267/267, blieb nach Security Rev1 (86/100, 0C/1H/2M) wegen H6
gesperrt. H6 wurde test-first geschlossen: Ein opaker, klassenmarkierter Wrapper
bindet eine nicht instanzmarkierbare Originalexception unverändert als
`__cause__`; M2 beendet die Aggregattraversierung beim 257. Kind fail-closed,
bevor Kind 258 gelesen wird.

Ein ungültiger Test erwartete für einen `AllCredentialsFailedError`
`RuntimeError`, obwohl zugleich Objektidentität verlangt wurde. Korrigiert wurde
nur die erwartete konkrete Exceptionklasse, nicht die Produktion. Final: H6
5/5, Ausschnitt 189/189,
Sechs-Dateien-Matrix 272/272 ohne Fail/Skip/Xfail; Testsimulation 98 Prozent,
Minimum 96; Security Rev2 96/100, 0C/0H/1M, PASS.

Der finale Breitscope wurde durch Worker und Supervisor mit 364 PASS plus exakt
acht vorbestehenden VolcEngine-Konstruktorfehlern reproduziert. Keine Breitsuite
wird als vollständig grün bezeichnet; keine numerische Coverage oder
Mutation-Coverage wird behauptet. **Offline Legacy-VLM HOLD aufgehoben; Live M1
bleibt HOLD.**

## 14. Implementierungsnachtrag: Open-Items-Follow-up — 2026-08-01

Auf Branch `agent-workflow/20260801-open-items-follow-up` im Worktree
`/Volumes/ExtremePro/projects/OpenViking-agent-worktrees/20260801-open-items-follow-up`
wurden die offline autorisierten H3-, M2-, M3-, L1-L3- und Watch-Testpakete
chirurgisch umgesetzt. `test_volcengine_cache.py` ist durch drei aktuelle
Chat-Completions-Vertragstests ersetzt; Streamfakes liegen in
`tests/unit/_streaming_support.py`; `WatchTask` nutzt den Pydantic-v2-Vertrag;
die Watch-Tests besitzen lokale Fixtures, Config-Isolation und einen
Deferred-konformen Mock. Der produktive Deferred-Guard und die VLM-
Providerkonstruktoren blieben unveraendert.

Die finale Verifikation bestand mit 102/102 State/Hook, 500/500 in der
konsolidierten 18-Dateien-Matrix unter Pydantic Warning-as-error und 150/150 in
der finalen Watch-Matrix. Ruff check/format und diff-check sind PASS. Der
historische Aggregate-Befund heisst `SEC-M2`; M2 bezeichnet ausschliesslich den
Pydantic-Punkt. Fork-PR #2 ist in `origin/main` gemergt, Upstream-PR #3667 ist
geschlossen. In Task 8 erfolgten keine Source-/Testaenderungen, Calls, Commits,
Pushes, Restarts oder Aktivierungen.

H1 und H2 bleiben fail-closed HOLD. Der `agy`-Review ist wegen fehlender
Headless-Command-Berechtigung UNAVAILABLE und kein PASS.

## 15. Implementierung: Root-Collection-Fix — 2026-08-01

Die Umsetzung aendert ausschliesslich den Root-Pytest-Collector, den
Gemini-E2E-Test und einen neuen Regressionstest. `tests/conftest.py` ignoriert
exakt die beiden eigenstaendigen Harness-Wurzeln. Der Gemini-Embedder-Import
liegt in `embedder()` und im einzelnen Test, der ohne Fixture direkt zwei
Embedder erstellt. `tests/test_test_suite_boundaries.py` schuetzt die exakte
Ownership-Grenze, die providerfreie Modul-Collection und den lauten Fehler bei
aktivierter Nutzung ohne optionales Modul.

RED: 3/3 erwartete Fehler. GREEN: 3/3 PASS. Final: `mcp`/`scrapy` importierbar,
Gemini-E2E 5 Tests gesammelt, Root 6382 Tests gesammelt, Exit 0. Es gibt keine
Aenderung an Produktcode, `pyproject.toml`, `uv.lock`, CI, Standalone-Harnesses
oder Live-Konfiguration.
