# Agent Memory Leaderboard adapter

This directory contains three deliberately separated stages:

1. `server.py` exposes the competition-facing AML Add/Search API;
2. `eval/run_public.py` reads the already prepared datasets under
   `result/aml_data` and produces retrieval results;
3. `eval/evaluate.py` optionally runs each dataset's public Answer/Judge pipeline
   after retrieval, including deterministic PersonaMem MCQ grading.

The deployed adapter never imports the local evaluation code. In a formal AML
run, the platform supplies the datasets and controls Answer, Judge, and score
aggregation.

## Add/Search adapter

For a local OpenViking server using `server.auth_mode: dev`:

```bash
export OPENVIKING_URL=http://127.0.0.1:1933
export AML_API_KEY='<memory-system key>'

python -m benchmark.aml.server --host 127.0.0.1 --port 8088
```

Do not set an OpenViking key or account for this local `dev` setup. The SDK
passes each AML `user_id` directly as the OpenViking user; there is no hash or
second identity mapping. Like other OpenViking SDK clients, it also reads an
existing `~/.openviking/ovcli.conf`, so that file must match the server being
used (or be disabled with `OPENVIKING_CLI_CONFIG_FILE` pointing to a missing
file).

For a remote multi-user deployment, configure the OpenViking server with
`server.auth_mode: trusted`, then set the trusted-mode root key and account:

```bash
export OPENVIKING_URL='https://openviking.example.com'
export OPENVIKING_API_KEY='<trusted-mode root key>'
export OPENVIKING_ACCOUNT=default
export AML_API_KEY='<memory-system key>'

python -m benchmark.aml.server --host 0.0.0.0 --port 8088
```

OpenViking's standard `api_key` mode cannot use one root key to switch among
dynamic AML users: in that mode a request must use the key belonging to the
target user. `AML_API_KEY` is unrelated; it only protects this adapter's
incoming `/add` and `/search` endpoints.

The adapter provides:

- `GET /health`
- `POST /add`
- `POST /search`

`/add` calls the OpenViking SDK's `batch_add_messages`, `commit_session`, and
`get_task` in that order. `/search` calls OpenViking `find`, restricted to
`viking://~/memories` and level 2 under the same AML user. Each dataset case is
isolated by its `user_id`; session IDs only separate histories inside that
user's space. Transient SDK calls are retried up to three times by
default; use `AML_RETRY_ATTEMPTS` and `AML_RETRY_DELAY_SECONDS` to adjust that
behavior. `Token`, `Bearer`, and `X-Api-Key` authentication are accepted when
`AML_API_KEY` is set.

`/add` accepts either a content string (Textual/Coding) or an ordered array of
`text` and `image_url` parts in `messages[].content` (Multimodal). Strings are
forwarded as native `content`; arrays are forwarded as native Session `parts`,
preserving part order, message roles, and timestamps. Images must be inline
Base64 JPEG, PNG, or WebP Data URIs, with at most 10 MiB decoded per image and
30 MiB decoded across one Add request. For example:

```json
{
  "request_id": "example:add:1",
  "user_id": "example:user",
  "session_id": "example:session",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "Remember this receipt."},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,<image-bytes>"}}
    ]
  }]
}
```

The example's image payload is a placeholder; supply actual Base64 image bytes.
Image understanding and memory extraction use OpenViking's existing Session
commit pipeline and require a configured vision-capable VLM. The adapter does
not perform separate image captioning.

`/search` accepts a query string or an array of the same `text` and `image_url`
parts, including image-only and multiple-image queries. Text parts are joined
with newlines and image URLs retain their relative order. The adapter forwards
them to native `find` using `query` and `image_url`, without an extra VLM call.
String queries keep their original request and response behavior. For example:

```json
{
  "user_id": "example:user",
  "query": [
    {"type": "text", "text": "Which workshop was this receipt for?"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,<image-bytes>"}}
  ],
  "top_k": 5
}
```

The same 10 MiB per-image limit applies, with a 30 MiB decoded total per Search
request. Image queries require a multimodal dense embedding model compatible
with the stored vectors. In hybrid mode, OV embeds the complete image/text
input through dense and only the text through sparse; image-only queries skip
sparse. Volcengine text-only queries retain their combined dense/sparse request.
Keep the configured dense model and dimension consistent with the existing
index. Search responses remain text memory evidence rather than raw images.

The native `find` call uses quick retrieval without reranking, even when the
OpenViking server has a reranker configured. The adapter returns each hit's
`content` when present, otherwise its `abstract`, without an additional
full-text read.

Run these modules from a source checkout with the project's Python dependencies
installed. The standard OpenViking image and installed package do not include
`benchmark/aml`.

## Local Add/Search

The input files already exist locally and are not generated by this code:

```bash
python -m benchmark.aml.eval.run_public \
  --prepared result/aml_data/prepared/lme_locomo_script.json \
  --prepared result/aml_data/prepared/persona_cl.json \
  --prepared result/aml_data/prepared/beam.json \
  --namespace local-check-v1 \
  --base-url http://127.0.0.1:8088 \
  --output-dir result/aml-public/retrieval \
  --add-concurrency 32 --search-concurrency 16
```

The main flow stops after Add/Search and writes one `retrieval.jsonl` under
each runnable dataset directory, plus a small top-level `summary.json`. Empty
private-data declarations are ignored because they contain nothing that can be
sent to Add or Search.

The prepared files contain `history_key`, not an AML `user_id`. The local
runner therefore creates one stable AML user for each unique
`namespace + unit + history_key + history contents`: questions sharing a
history share that user, while different histories stay isolated. This is ID
creation in the local runner; `server.py` forwards the resulting ID unchanged.

Use repeated `--unit` arguments to select individual datasets. Add runs up to
16 histories (AML users) concurrently by default, while Search runs up to 16 questions
concurrently. Override them independently with `--add-concurrency` and
`--search-concurrency`; the existing `--concurrency` option remains a shorthand
baseline for both. Phase-specific options take precedence. Within each history,
sessions run in prepared order, and chunks within each session run in order.
Every Add waits for its commit to finish before the next request for that user;
Search starts after all histories finish importing. For BEAM, this preserves
batch order within a conversation while different conversations run concurrently.

For smoke/full presets, `run_ecs` accepts a provider URL through `--base-url`
or `AML_BASE_URL`; it defaults to the local adapter. It reads authentication
from `AML_API_KEY` or `--api-key-file` and creates a fresh namespace per run:

```bash
python -m benchmark.aml.eval.run_ecs list
python -m benchmark.aml.eval.run_ecs full beam_100k \
  --base-url http://127.0.0.1:8088 --add-concurrency 32 --dry-run
```

Remove `--dry-run` to import. The generic callers do not resume an interrupted
import. Reconcile the original server tasks and archives before retrying an
ambiguous Add; a new namespace starts a separate experiment.

## PersonaMem-v2 text preparation

`eval/prepare_personamem.py` follows the exact `chat_history_32k_link` and
`chat_history_128k_link` paths in `benchmark/text/benchmark.csv`. Each size is a
separate condition; their histories are not concatenated. Shared histories
are imported once per run and reused by their questions.

The preparer removes all system persona messages and blank messages, preserves
the remaining user/assistant text and order, and omits timestamps absent from
the source. It does not assemble histories from raw persona records or gold
snippets. Reference answers, preference annotations and distractors stay in
case metadata and never enter Add messages.

```bash
python -m benchmark.aml.eval.prepare_personamem \
  --source-dir result/aml_data/datasets/personamem_v2 \
  --verification result/aml_data/personamem_v2_download/verification.json \
  --output-dir result/aml_data/prepared/full
```

The downloaded source and its verification manifest must already exist. The
manifest contains `success: true`, the source `revision`, and a `files` array
with relative `path` and `sha256` for the CSV and linked chat files. Preparation
checks those hashes, writes both size-specific prepared files and backs up any
replaced outputs. Optional `--smoke-file` refreshes existing 32K smoke cases.
Preparation makes no Add/Search or model calls and covers text inputs only.

## Memory-type contribution (LoCoMo-Refined and LongMemEval-S)

`eval/memory_contribution.py` measures how Event, Preference, Entity, and Profile
help or harm final answer correctness. Its primary mode runs **16 independent
native Find searches**, one per allowed type combination, on the same frozen
memory corpus. Type directories restrict candidates **before TopK**. Removing
Event therefore allows other types to compete for its places; this is not
filtering an old Top20/Top100. No Add or extraction is performed.

Use an isolated, immutable copy of the complete memory files **and vector index**.
Keep source version, embedding model/index, query, TopK, threshold and account/user
scope fixed. Native Find uses vector ranking without rerank, planner, link expansion
or time reordering. This mode does not reproduce an experiment that used an
external reranker or owner-label formatting; disclose that difference rather than
comparing its score to a historical baseline as an isolated feature effect.

### Inputs and preparation

The question JSONL needs `id`, `unit`, `user_id`, `question`, a gold field accepted
by the pinned public pipeline, and original question metadata/categories.
`user_id` is the exact native user namespace. `history_key` or `--group-field`
can identify bootstrap clusters, but retrieval still requires explicit `user_id`.
Old `retrieved_context` and provenance arrays are ignored in retrieval mode.
Fields that override the public renderer (`speaker_1_memories`, `speaker_2_memories`,
`memories`, `chat_history`) are rejected to prevent evidence leaking into ablations.

Supply a **complete** memory inventory JSONL for those users, not just past hits.
Each row has `id` (the URI), `user_id`, canonical `uri`, `memory_type`, and `content`
(the complete public read-visible body, with the reserved MEMORY_FIELDS trailer
removed). Types must agree with their directory provenance. For example:

```json
{"id":"viking://user/u1/memories/entities/product/bike.md","user_id":"u1","uri":"viking://user/u1/memories/entities/product/bike.md","memory_type":"entities","content":"# Bike\nThe user's bicycle."}
```

Include every non-hidden memory `.md` file, including Identity and Soul. The runner
checks the complete namespace through paginated native listings before searching,
then compares every retrieved full body to the frozen inventory. The server must
stay immutable while retrieving: the runner cannot prevent another process from
changing its index. Freeze and verify the source/index independently as well.

```bash
export ANSWER_MODEL='<answer model ID>'
export JUDGE_MODEL='<judge model ID>'

python -m benchmark.aml.eval.memory_contribution prepare \
  --unit longmemeval_s --retrieval questions.jsonl \
  --memory-inventory frozen-user-memories.jsonl \
  --ov-api-base http://127.0.0.1:1984 --ov-account isolated-benchmark \
  --top-k 100 --other-types exclude \
  --aml-repo result/aml-public-eval/agent-memory-leaderboard \
  --output-dir result/aml-public/contribution/longmemeval_s
```

Preparation makes no network/model calls and prints the planned counts. The
models are required environment variables, frozen at preparation and checked on
resume. No built-in model or model CLI override exists. Defaults are five rounds,
temperature 0, max_tokens 16384, Search concurrency 4 and TopK 20 for LoCoMo-R /
100 for LME-S. For reasoning endpoints, explicitly prepare with
`--token-limit-parameter max_completion_tokens --temperature omit --reasoning-effort low`.
The runner never guesses request compatibility or silently changes parameters.

`--other-types exclude` (default) excludes types outside the four players in every
condition; subset 0 is genuinely memory-free, while the full condition means all
**four** types. With `--other-types fixed`, Identity/Soul/other types remain eligible
in every Search and compete for TopK; subset 0 is then a background-memory baseline.
Their available document count is disclosed as `other_contexts_fixed`.

### Search, Answer/Judge and resume

Set `OV_API_KEY` for the frozen native service. Freeze retrieval separately:

```bash
python -m benchmark.aml.eval.memory_contribution retrieve \
  --output-dir result/aml-public/contribution/longmemeval_s
```

All combinations are searched once and frozen **before** any Answer/Judge calls.
An empty allowed scope returns no memory without sending an unrestricted native
query. Returned bodies/URIs must match the user, allowed types and inventory.
Successful Search results are retained across interruption, including a malformed
success that blocks validation. The runner does not repeatedly Search for a better
answer. After retrieval is complete the temporary native service can be stopped.

Set `ANSWER_API_BASE`, `ANSWER_API_KEY`, `JUDGE_API_BASE`, `JUDGE_API_KEY`, and the
same frozen model variables. Run or resume:

```bash
python -m benchmark.aml.eval.memory_contribution run \
  --aml-repo result/aml-public-eval/agent-memory-leaderboard \
  --output-dir result/aml-public/contribution/longmemeval_s \
  --answer-concurrency 48 --judge-concurrency 48
```

`run` also completes retrieval first if needed. A fully cached retrieval needs no
native key or live service. Exact public dataset prompts/gold and Judge parsing
come from the verified clean AML checkout/commit (`--aml-commit`), independently
of the explicitly frozen request settings. Original question metadata is retained;
this does not repair fields omitted by the public prompt, such as question_date.

Each round/subset uses independent Answer/Judge receipts, with complete request,
response/usage, hashes and every technical attempt. Resume retains the first valid
response, including wrong answers. Transport failures and HTTP 408/429/500/502/503/504
have at most three attempts across resumes, with shared cooldown/Retry-After.
Successful empty, truncated or invalid responses stop and are never resampled.
Credentials/auth headers are not saved. Local receipts are independent; provider
prompt caching, if any, remains visible in the original usage response.

Input/inventory/script/pipeline hashes and runtime settings must match. A `.running`
lock rejects overlapping commands; inspect the PID before removing a stale lock.
For N questions and R rounds, Answer/Judge cost is **32 × N × R** requests before
technical retries, plus 16 × N retrieval cells (empty scopes need no query).
Five rounds on both full datasets would require 217,760 Answer/Judge requests;
a small pilot is useful before committing that budget.

### Reports and optimization cases

The run reports automatically when complete; repeat it offline with:

```bash
python -m benchmark.aml.eval.memory_contribution report \
  --output-dir result/aml-public/contribution/longmemeval_s \
  --bootstrap-samples 1000 --seed 0
```

`summary.json`/`summary.md` contain overall and category results. Missing or corrupt
receipts block reporting, rather than becoming WRONG. Each type reports:

- **Helped / harmed:** paired question-round counts where full is correct and
  removal is wrong, or the reverse. Unique question counts are also retained;
  a question can have both transitions across rounds. Net effects do not hide these.
- **Removal loss (pp):** all-four accuracy minus accuracy without this type.
- **Standalone gain (pp):** this type alone minus subset-0 accuracy; standalone
  correct question-round counts are also reported.
- **Shapley (pp):** exact average marginal gain over all 24 addition orders,
  calculated from 16 subsets. Redundant/complementary evidence shares credit;
  values can be negative and sum to full minus subset 0. This is an allocation
  convention, not a unique causal percentage.

The three effects have paired history-cluster/round bootstrap 95% intervals.
Entire histories retain their unequal question counts; resampled round indices
are shared across conditions. Few histories/rounds severely limit inference.

`per-question.json` retains all 16 scores, grouping, question/gold and transitions.
`opportunities.json` and the readable summary list changed cases with full/removed
answers, URI changes and exact evidence paths. These are **review candidates**:
inspect the source dialogue, stored memories, retrieval and actual Answer prompt
to distinguish extraction gaps, retrieval misses, context omission and Answer
errors. String matching or a score flip alone cannot identify that failure stage.
Priority should reflect affected cases and evidence confidence, not just the
largest aggregate score or most frequent type.

### Optional fixed-context diagnostic

`prepare --attribution-scope context` retains the original deletion diagnostic.
It removes excluded entries from the exact original Answer context without new
Search, refill, rewriting or reordering. Provide aligned `retrieved_uris`,
`retrieved_memory_types` or original MEMORY_FIELDS provenance; other types remain
fixed. This mode measures dependence on already selected context, **not** stored
memory/retrieval-system contribution. It is explicitly labeled in every report.

## Optional Answer/Eval

The optional evaluator consumes one completed retrieval artifact and never
repeats Add or Search. It reads the artifact's `unit` and dispatches to the
matching public AML pipeline for LongMemEval-S, LoCoMo-Refined, BEAM,
CLBench, or PersonaMem-v2:

```bash
export ANSWER_API_BASE='https://answer-endpoint/v1'
export ANSWER_API_KEY='...'
export ANSWER_MODEL='...'
export JUDGE_API_BASE='https://judge-endpoint/v1'
export JUDGE_API_KEY='...'
export JUDGE_MODEL='...'

python -m benchmark.aml.eval.evaluate \
  --retrieval result/aml-public/retrieval/longmemeval_s/retrieval.jsonl \
  --aml-repo result/aml-public-eval/agent-memory-leaderboard \
  --answer-concurrency 16 --eval-concurrency 32
```

This produces `answers.jsonl`, `judgments.jsonl`, and `summary.json` beside the
retrieval file. Answer prompts and scoring stay inside the pinned public
dataset pipeline; `evaluate.py` only selects it and adapts the generic
`retrieved_context` field where CLBench or PersonaMem expects a different input
shape. Answer and Judge each run up to 32 pipeline shards concurrently by
default; use `--answer-concurrency` and `--eval-concurrency` to tune them
independently. Outputs are merged back into retrieval order and published only
after every shard succeeds. These remain local proxy results rather than
official leaderboard scores. Model options and retries follow the selected
public pipeline; this wrapper does not provide the experiment-specific pacing
or resume logic. The pinned pipelines currently combine
an asynchronous HTTP client and a regular output file in one `async with`
statement; the wrapper corrects only that context-manager syntax in a temporary
runtime copy, leaving the verified checkout, prompts, and scoring code unchanged.

PersonaMem defaults to `--persona-mode mcq`, even for older retrieval files
marked as generative. Answer receives only retrieved memory, the question and
four shuffled choices from `correct_answer` plus `incorrect_answers`; preference
labels and the correct option marker are not added to the prompt. Search does
not receive these choices. The public pipeline maps the predicted letter to
its option text and grades it locally; invalid answers count as incorrect.
Only the three `ANSWER_*` variables are required for MCQ. Use
`--persona-mode generative` explicitly for the public narrow LLM judge instead.
Subprocesses default to `PYTHONHASHSEED=0` to keep option mappings stable across
shard counts. These are OV retrieval-based evaluations, not reproductions of
the authors' trained MemAgent implementation.

## Static checks

```bash
python -m ruff check benchmark/aml
python -m ruff format --check benchmark/aml
```
