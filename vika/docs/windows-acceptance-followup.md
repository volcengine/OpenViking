# Windows preview acceptance follow-up (2026-11-07)

## Evidence and scope

The contributor reports a 26-category acceptance matrix comparing Windows
`vika-win` at `8bcde778` with Linux Docker release `0.4.0-24e73d5a3f7a`.
These are different code revisions: platform alone cannot explain a difference.
Both used Durable, the same provider/model and configured low effort.
Reported passes cover private-chat send/receive, long-text summarization,
interruption, workspace file writes, OpenViking writes and retrieval after restart.
These are contributor-run real-system results, not independently reproduced by
this change's Linux unit tests. A completed matrix is not 26 passing categories.

Untested: group mentions/topics, reactions, retractions, cards, attachments,
skills/MCP and other connectors. Group tests were blocked by missing test-group
setup/application scopes. Windows source automatic upgrade is unsupported;
Docker upgrade was intentionally not tested. A web listener bound to a
container's loopback interface cannot be reached merely by publishing its port.

## Changes and issue disposition

- PR #3 records source-install validation and release blockers. PR #5 remains a
  draft release-tooling experiment; no Windows artifact or hosted one-line
  installer has been validated or published by this work.
- PR #8 / issue #7: expand Windows `~/` and `~\` workspace prefixes, preserving
  POSIX literal backslashes. The Windows jobs now run the home-expansion test
  explicitly. Existing saved paths containing a literal `~` are not migrated:
  choose the intended absolute workspace through setup, preserve old files,
  and verify the saved path before moving any existing data.
- Issue #6: the failure policy now consults durable decisions before notifying
  or retrying an input. A confirmed SENT final delivery or silent decision suppresses a second failure
  message for that input, while unanswered scopes in a mixed turn still follow
  normal failure handling. Runtime errors remain diagnostic events. This fixes
  the misleading post-delivery notice, not the underlying provider/Pi stop error;
  native/provider reproduction is still needed before closing the incident.
- Issue #9 remains unconfirmed as an execution defect. `/effort` and persisted
  settings establish configuration, not provider-side reasoning. Generated model
  self-description is not authoritative. Capture sanitized request parameters and
  the restored conversation's actual agent configuration before changing runtime
  behavior. No prompt-only patch is claimed to resolve this issue.

## Next native verification

1. Record the installed commit and the test commit separately. Run the targeted
   home-expansion test from PR #8 on Windows and confirm the setup default resolves
   under the actual home, including spaces and non-ASCII characters.
2. Pi: repeat the first-turn reproduction. If a reply is delivered and the runtime
   subsequently errors, retain the sanitized error but verify no duplicate failure
   notice. Also verify a genuine failure before a decision still notifies.
3. Durable: ask for configured effort after restart, after `/effort low`, and after
   an effort change during a busy turn. Check the authoritative setting and applied
   boundary separately from model prose. Never include credentials in evidence.
4. Re-run the previously passing chat/file/OV tests. Keep missing-scope and
   unconfigured capabilities as blocked/untested, not passing.
5. Compare latency with fixed inputs and matching runtime/model/configuration;
   separate cold-start time, model calls, tools, OV and delivery. A single cold
   turn is not a platform performance benchmark.

## Background console windows

Durable host and connector-worker children now set `windowsHide` on Windows.
Both retain piped stdio and existing shutdown supervision. Verify on the real
Windows desktop after restarting only the test instance: no extra Node console
windows, working Feishu delivery, working Durable tools, and clean stop/restart.
This was reported by screenshots; local Linux tests cannot validate window visibility.

## Matrix interpretation corrections

The attachment labels a Debian container populated by install.sh as an "official
Docker image"; it is a custom reference deployment of the official Linux release.
Its 714-character summary exercise is not an ultra-long-context acceptance test.
The appended interruption evidence describes Windows; independent Docker evidence
is still needed. The latency entries compare a Windows Durable cold turn with a
Docker Pi cold turn, so they are not a matched runtime performance comparison.
The restart-recall entries quote the resulting text; retain actual retrieval tool
results and source URIs to distinguish OV retrieval from restored conversation text.
