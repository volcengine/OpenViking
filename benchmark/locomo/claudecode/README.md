# LoCoMo benchmark — Claude Code memory paths

Four reproducible evaluation paths over the LoCoMo-10 dataset, each
exercising a different way of giving Claude Code long-term memory.

| Mode | Ingest path | OV namespace | Entry point |
|---|---|---|---|
| **Prompted** | `claude -p` per session, CC writes `MEMORY.md` | — (no OpenViking) | `run_prompted.sh` |
| **SDK iso** | OpenViking Python SDK direct import | per-sample `agent_id` | `run_sdk_iso.sh` |
| **SDK no-iso** | OpenViking Python SDK direct import | shared (default) | `run_sdk_noiso.sh` |
| **e2e** | `claude -p` stream-json multi-turn, auto-capture into OV | shared | `run_e2e.sh` |

All four use the same QA / judge / stats pipeline (`eval.py` → `judge.py` →
`stat_judge_result.py`) so accuracy numbers are directly comparable.

## Prerequisites

1. **LoCoMo data** — drop `locomo10.json` into `.tmp/`:
   ```
   benchmark/locomo/claudecode/.tmp/locomo10.json
   ```
2. **Python deps** — managed by `uv` (use `uv run` invocations as below).
3. **Claude Code CLI** on `PATH` (the runners call `claude -p`).
4. **An Anthropic-compatible model endpoint.** Anything that speaks the
   Anthropic Messages API works. The original results were collected against
   doubao-seed-2-0 via Volces ARK:
   ```bash
   export ANTHROPIC_AUTH_TOKEN=...
   export ANTHROPIC_BASE_URL=https://ark.cn-beijing.volces.com/api/compatible
   export ANTHROPIC_MODEL=doubao-seed-2-0-code-preview-260215
   ```
5. **OpenViking server + plugin** (only for SDK iso / SDK no-iso / e2e modes):
   - Start `openviking-server` (defaults to `127.0.0.1:1933`). The e2e mode
     wants it running inside a tmux session so the runner can restart it
     across snapshots — set `OPENVIKING_SERVER_TMUX=<session>` (default
     `ovserver`).
   - Point at your clone of [`claude-code-memory-plugin`][plugin]:
     ```bash
     export OPENVIKING_PLUGIN_DIR=$HOME/Dev/OpenViking/examples/claude-code-memory-plugin
     ```
     This is referenced from `config/ov-hooks.json` and from
     `scripts/auto-capture.mjs`.
   - Optional: `export OPENVIKING_CLI_CONFIG_FILE=$HOME/.openviking/ovcli-local.conf`
     to pin the local OV target and avoid leaking prod URLs from the
     user-shell's `claude` wrapper.

[plugin]: https://github.com/OpenViking/openviking/tree/main/examples/claude-code-memory-plugin

## Running

Single-sample smoke test (any mode, any conv id):

```bash
./run_prompted.sh   conv-26
./run_sdk_iso.sh    conv-26
./run_sdk_noiso.sh  conv-26
./run_e2e.sh        conv-26
```

Full 10-conv run — omit the conv id:

```bash
./run_prompted.sh
./run_sdk_iso.sh
./run_sdk_noiso.sh
./run_e2e.sh
```

Each script ends with the result of `stat_judge_result.py` printed to stdout
and saved to `.tmp/result-<mode>/summary.txt`. Per-question CSVs land in
`.tmp/result-<mode>/qa_results.csv`.

## What each Python entry point does

- `ingest.py` — feeds each LoCoMo session as a `claude -p` invocation;
  CC's vanilla auto-memory writes `MEMORY.md` inside the project dir.
- `import_to_ov.py` — uses the OpenViking Python SDK to push LoCoMo
  conversations directly into OV. Per-sample namespace by default; pass
  `--no-user-agent-id` for shared namespace.
- `ingest_e2e.py` — opens one `claude -p` per LoCoMo session in
  `--input-format stream-json` mode and streams the messages one at a time
  through stdin. The plugin's Stop hook auto-captures per turn; SessionEnd
  fires once per LoCoMo session (one commit per session). The benchmark's
  `scripts/auto-capture.mjs` shim extracts the LoCoMo per-message timestamp
  so OV event archive dates line up with conv chronology.
- `eval.py` — drives QA. Per-question `claude -p`; reads `MEMORY.md` for
  Prompted mode, talks to OV via auto-recall hook + MCP for the OV modes.
- `judge.py` / `stat_judge_result.py` — LLM-based judging and per-category
  accuracy roll-up.

## Layout

```
benchmark/locomo/claudecode/
├── README.md
├── run_prompted.sh                # 4 entry points, one per published mode
├── run_sdk_iso.sh
├── run_sdk_noiso.sh
├── run_e2e.sh
├── ingest.py                      # Prompted ingest
├── ingest_e2e.py                  # e2e stream-json ingest
├── import_to_ov.py                # SDK pre-ingest (iso + no-iso)
├── eval.py
├── judge.py
├── stat_judge_result.py
├── config/
│   ├── ov-hooks.json              # hooks settings, references $OPENVIKING_PLUGIN_DIR
│   ├── ov-mcp.json                # MCP server (viking)
│   ├── ov-ingest.conf             # auto-capture on, auto-recall on
│   ├── ov-qa.conf                 # auto-capture off, auto-recall on
│   └── sys-prompts/               # optional system prompts (unused in default flows)
├── scripts/
│   └── auto-capture.mjs           # LoCoMo-aware Stop-hook (per-message created_at)
└── .tmp/                          # gitignored; data, snapshots, results
    ├── locomo10.json              # ← drop dataset here
    ├── legacy/                    # historical scripts/configs/reports kept aside
    └── result-<mode>/             # output of a run
```

Historical iterations of the experiment (`r8` … `r14b` snapshot scripts,
fine/listed/min ingest variants, intermediate reports) live under
`.tmp/legacy/` and are not part of the published numbers.
