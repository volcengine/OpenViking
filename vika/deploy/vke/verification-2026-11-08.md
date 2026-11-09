# Phase 1 verification — 2026-11-08

This record distinguishes real provider execution, local recorder execution and
work that still needs an isolated live deployment. It is not a blanket end-to-end
acceptance claim.

## Verified

- Real MA provisioning created a separate `vika-channel-*` Agent, cloud
  Environment and version-pinned template. No existing video/knowledge Agent was
  changed and no connector credentials were installed in the sandbox.
- Actual `VolcAgent` + `ActionService` + SQLite completed a cloud custom-tool text
  turn with exactly one outbox entry. The connector in this test was a recorder.
- Real MA freshness run: an authenticated simulated correction arrived just
  before the old custom send crossed Core. The old send received `stale_decision`,
  MA ended that turn, the adapter processed its queued correction, and the
  recorder received exactly one new-marker delivery across two completed turns.
- Actual Files API uploaded and mounted an incoming file; cloud Bash read a
  sentinel and wrote an output; Channel verified the output's session scope,
  downloaded it, and Core delivered it to the recorder. Both custom results were
  POSTED. No media schema fields were removed.
- Native image/base64 custom results were accepted by MA. A two-color PNG was
  correctly described as red on the left and blue on the right, through a Core
  send recorded locally.
- The live verification process was SIGKILLed after its recording connector
  performed one effect. A replacement process rotated authority, resumed the
  accepted managed turn from SQLite/history, posted `uncertain_not_retried` for
  the RUNNING call, and reached end_turn. The effect count stayed **one**, and the
  outbox remained UNCERTAIN for explicit reconciliation.
- One actual Alice Feishu UI input traversed official SDK → Connector → SQLite →
  MA → Core outbox → Feishu. The user saw `ALICE_MA_TEXT_OK`; local custom call was
  POSTED and local outbox was SENT. This used an isolated profile/data/workspace.

A subsequent Alice input received a reply from a competing consumer but never
entered this instance's SQLite. Therefore freshness, image/file delivery,
reaction cleanup, recall, choices and timer acceptance on the real IM were paused.
The test daemon was stopped gracefully; the existing Zeus daemon was untouched.
Earlier CLI-versus-SDK event observations are inconclusive under competing
consumers and do **not** establish a pinned CLI defect. SDK remains an optional,
independently exercised transport.

## Isolated replacement bot

After the competing Alice consumer was identified, a separate **Vika** bot was
provisioned with new CLI/profile/data/workspace and a separate MA Agent, cloud
Environment and template. The authorized user's new app-scoped identity was
verified by matching its union ID to the previously confirmed named contact;
no owner OAuth identity was invented.

- Actual UI text input completed through SDK → SQLite → MA → Core → Feishu.
- Actual UI freshness test ran cloud Bash `sleep 30`. A newer UI correction arrived
  while that tool ran. Core returned `stale_decision` for the old reply; the next
  turn delivered only `VIKA_FRESH_NEW_OK`. The old marker has no outbox delivery.
- Cloud Bash generated a text artifact; Files API session-scoped download and
  Core delivered a real Feishu attachment. Its staged bytes were verified as
  `VIKA_CLOUD_FILE_OK`. A transient null Files listing exposed during this test
  is now treated as pending registration, with a focused regression.
- A real choices card was delivered, clicked in Feishu, normalized as
  `message.action`, and answered with `VIKA_CHOICE_pass` through Core.
- A real UI image upload was fetched through `get_message_media`, uploaded and
  mounted through Files API, and returned as a native image/base64 block. MA
  correctly replied that the left half was red and the right half blue; Core
  recorded the actual Feishu reply as SENT.
- A one-shot SQLite timer was created from a real DM, fired as an authenticated
  reminder, and delivered `VIKA_TIMER_OK` through `finish_timer_occurrence`.
  Its occurrence was marked completed and only one reminder was sent.
- A graceful daemon restart retained the same MA session and SQLite history.
  After restart, cloud ffmpeg produced a one-second H.264 MP4; the Files bridge
  staged it and Core sent it to Feishu. The initial silent H.264 High clip failed
  client playback despite valid upload metadata. A second clip combining H.264
  Baseline, yuv420p, silent AAC and `+faststart` played successfully in the user
  client. This does not isolate a single cause. The original test used the earlier
  20 MiB cap; the subsequent large-file work below removes that local cap.
- Actual `stay_silent` completed with no outbound message; the next normal input
  still completed. A real THUMBSUP reaction and explicitly requested recall of
  the failed first video also completed through Core.
- Real MA running-input steering processed a posted correction before the original
  `end_turn`, producing only the new marker. A second recorder scenario inserted
  temporary OnIt, continued with another Bash call, delivered one final result
  and removed the reaction. An initial recorder used a simulated idle hint; a
  second run used the actual staged-replies manifest without that hint and
  passed with exactly three effects (reaction, final message, reaction deletion).
- SIGKILL immediately after a steering message was accepted and durably recorded
  recovered under new authority without reposting the input; one final effect
  was recorded.
- Bot roster lookup reports missing chat/member scopes. That capability needs
  permission configuration; it is not counted as verified.

### Final live steering result

With staged replies enabled, T12 and T13 both accepted the new UI message into MA
while cloud Bash was running. They executed the new temporary OnIt before the
same run's final delivery, and Core removed it after the final send. However,
neither real UI run demonstrated the requested immediate reaction followed by
remaining original computation:

| Test | Appended input | Provider processing | Observation |
| --- | --- | --- | --- |
| T12: sleep 20 → sleep 15 | 01:53:37 | 01:54:23.176 | Both sleeps finished before the correction was processed. |
| T13: sleep 20 → Core get_time → sleep 20 | 01:55:42 | 01:56:55.733 | The intermediate Core boundary also did not force consumption. |

Times are Asia/Singapore on 2026-11-08. In both cases Core blocked the old final
send as stale. These are evidence for safe running-input acceptance and eventual
same-run handling, **not** a full pass for immediate collaborative steering. MA
needs a documented, controllable scheduling/priority mechanism for that UX.
No such public switch was verified; `user.interrupt` cancels execution and clears
pending messages, so it is not a substitute for non-destructive steering.

## Large-file follow-up

The MA bridge and Core file snapshots now stream through disk without the old
20 MiB cap. A real MA run generated a 24 MiB output and the verification harness
interrupted its storage connection after 3 MiB. The retry refreshed file detail,
sent `Range: bytes=3145728-`, received HTTP 206, and completed all 25,165,824 bytes.
The final Core snapshot SHA-256 matched the expected content and exactly one
file delivery reached the recording connector. This was real MA/Files/storage
with a simulated connector, not a new real Feishu send.

A second real run was SIGKILLed after downloading 3 MiB, before Core execution.
The replacement process recovered the pending call under new authority, resumed
with HTTP 206 from byte 3,145,728, and delivered exactly one 24 MiB file with the
same SHA-256. No outbox delivery existed before that restart. Independently, the
actual Node disk-backed multipart upload and session mount accepted a 24 MiB
local file in 148 seconds. The first combined upload/download run had a network
upload failure and timed out; the separate upload and download validations above
passed, rather than treating that failed combined run as successful.

Private evidence directories are `large-output-verification/` and
`large-download-crash-verification-2/`; the upload result is
`large-upload-node-evidence.json`, all under `mini-vika/.runtime/`.

Recovery state uses session/file identity, strong ETag and validated lengths;
200 responses to a Range request reset the partial, and changed validators or
invalid Content-Range cannot combine objects. The durable `ma_tool_calls.preparing`
flag separates resumable transfer from Core execution; RUNNING side effects still
fail closed on recovery. After confirming the replacement bot was idle, its
dedicated Agent was synchronized to version 3 and the daemon restarted gracefully
with the new template. The live Core socket schema, pinned remote Agent schema
and provisioning hash matched; the preparation column migrated successfully.
Health returned ready with its subscriber running and zero active turns. All 20
existing outbox rows retained an identical digest, proving no delivery was added
or changed during this deployment. No new real Feishu test message was sent.
The version change created a new MA conversation session while retaining local
SQLite history. Deployment evidence is `replacement-large-deployment.json` in
the same private runtime directory.

## Automated checks

- Syntax check (`npm run check`) and whitespace check passed.
- Daemon + Web + updater tests, with concurrency 2: 1005 passed, 1 skipped, no
  failures. Subsequent focused ActionService/Store/journal and SDK normalization
  regressions also passed after the final changes. The steering change passed
  133 related ActionService/Store/Volc regressions, plus the latest 15 focused
  tests including ambiguous HTTP append failure and recovery.
- Large-file changes passed 237 related Core/Store/media/MA tests. The final 24
  focused download/journal tests also passed, including process recovery,
  migration of an existing journal, signed-URL refresh, changed validators,
  malformed ranges, ignored Range, HTTP 416, truncation, cache integrity and
  progressing downloads that exceed the idle timeout's total duration.
- CLI tests: 230 passed, 2 existing platform-sensitive failures. Both failures
  reproduced on pristine repository HEAD with the same Node/macOS environment:
  `packages/cli/test/install.test.mjs:52` expects `.bashrc` although the installation
  shell is zsh; `packages/cli/test/qr.test.mjs:124` expects a QR footer layout that
  differs in this terminal environment. Neither is caused by MA changes.
- Coverage includes every Core tool's schema mapping/dispatch, DONE/POSTED replay,
  RUNNING uncertainty, cross-tool retry blocking, stale authority, changed tool
  event arguments, foreign session/fence rejection, decision scope and freshness,
  result-post history reconciliation, authenticated timer completion and nested
  delivery (30 focused timer tests passed), media staging and path rejection, and lock
  boot/start/PID-namespace identity. Individual Core semantics remain covered by
  the existing ActionService suite; the generic coverage test is not a claim that
  every external connector account was exercised.

## Remaining deployment acceptance

Image-rich cards were not independently exercised on the live bot. Immediate
collaborative steering remains limited by MA scheduling as described above. Owner notification
needs the profile's authenticated user identity; it was intentionally not forged.
OV memory integration is outside this requested phase and remains unconfigured.
SSE has a 15-minute no-byte idle timeout with history reconciliation on reconnect.
A connection that continues emitting only heartbeat bytes can still defer that
timeout; a separate no-semantic-event watchdog is not yet implemented.
The Dockerfile/StatefulSet and Secret templates passed static checks, but Docker
is unavailable on this machine and no VKE cluster rollout/pod-restart test has
been performed. Other connector credentials were not supplied for live testing.

Reproduce MA tests with `scripts/volc-verify.mjs` as documented in the runbook.
Session IDs, local SQLite/outbox evidence and recorder outputs are retained in the
operator's isolated `mini-vika/.runtime/` directories; these contain private test
state and are not part of this repository.
