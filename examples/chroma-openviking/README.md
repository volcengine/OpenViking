# Chroma + OpenViking Minimal Example

This example implements the minimal split proposed in RFC #1190:

- `Chroma` handles static document retrieval
- `OpenViking` handles session context and archive memory

The goal is not to present this as the default OpenViking architecture. The goal is to provide a
clear, reproducible example with sharply separated responsibilities.

## What This Example Shows

### 1. Short-context ask flow

- load a small static knowledge base into Chroma
- retrieve static evidence from Chroma for the current question
- pass the same question through OpenViking retrieval with a live `session_id` so immediate
  session context can participate

### 2. Long-memory flow

- add conversation turns to an OpenViking session
- explicitly commit the session
- retrieve long-term memories from `viking://user/memories/`

## Layout

```text
examples/chroma-openviking/
├── README.md
├── requirements.txt
├── demo.py
├── ov.conf.example
├── test_smoke.py
└── sample_docs/
    ├── deployment.md
    ├── incident-playbook.md
    └── retention-policy.md
```

## Install

```bash
pip install -r examples/chroma-openviking/requirements.txt
```

If you already have OpenViking installed, the only extra runtime dependency here is `chromadb`.

## Run

Embedded mode:

```bash
python examples/chroma-openviking/demo.py --mode embedded --workspace ./data/chroma-ov-demo
```

HTTP mode:

```bash
python examples/chroma-openviking/demo.py --mode http --url http://localhost:1933
```

Run only one flow:

```bash
python examples/chroma-openviking/demo.py --flow short
python examples/chroma-openviking/demo.py --flow long
```

## Behavior

The demo script:

1. seeds Chroma with static operational docs from `sample_docs/`
2. seeds OpenViking with one active session
3. runs a short-context question using:
   - Chroma top-k static evidence
   - OpenViking `search(..., session_id=...)` for immediate conversational context
4. commits the session
5. queries `viking://user/memories/` to show long-memory retrieval after commit

## Notes

- This example keeps all scoring and output human-readable. It is not a benchmark.
- `chromadb` is intentionally isolated to this example instead of becoming a repo-wide dependency.
- The script prints structured JSON so the integration shape is easy to inspect or adapt.
