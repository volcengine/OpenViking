# Demo evidence

`pi-ctxwin-demo.zip` is a redacted snapshot of the sandbox this extension was
demonstrated in, captured on 2026-09-11 against a live OpenViking server and
Volcengine's Doubao 2.1 Pro (`doubao-seed-2-1-pro-260628`) on Ark, with
`reasoning_effort: high`. It is here as evidence for the numbers quoted in the
docs: unpack it and read the transcripts rather than taking them on trust.

    unzip pi-ctxwin-demo.zip && cd pi-ctxwin-demo && $EDITOR README.md

Two runs are included.

**`demo-long/`** — the interesting one. The agent is given 22 source files
(411 KB, about 105k tokens if read in full) and asked to inventory them one by
one. The context tools are never mentioned in the prompt. Over 96 provider
requests it fills its window to 47% of 128k, resets, fills it again, and resets
twice more, finishing 22 of 22 files across four windows. `pressure-timeline.txt`
plots every request with the resets marked; `context-tool-calls.json` has the
reasons the agent gave, one of which reads "Context window reached ~45% (soft
reminder). 8 of 22 files inventoried so far; need a fresh window to finish the
remaining 14 files."

**`demo/`** — the short scripted gate, where the reset is instructed rather than
chosen, so the mechanics are easy to follow: the request before the cut, the
request after it, the same frozen header surviving a process restart, and the
OpenViking archive with the handoff message inside it.

Both folders carry the pi session file, the extension debug log, the provider
payloads either side of each reset, and the archives pulled back off the server
(Working Memory, raw messages, a grep result).

Reproduce either run with `scripts/e2e-window.sh`; see the extension README.
`REDACTIONS.md` inside the archive lists exactly what changed: the secrets file
is absent, the operator's username is replaced, and the private proxy the runs
went through is named as the official Ark endpoint it forwards to.
