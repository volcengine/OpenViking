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

## Frozen source-dialogue retrieval replay

`eval/dialogue_windows.py` reproduces the auxiliary retrieval layer of the
source-dialogue experiment. It does **not** change native OV Add/Search or the
deployed AML adapter, send model requests, or generate new memory files.
Install `numpy` and `tiktoken` in the benchmark environment before running it.

The two routes are deliberately separate:

1. Supply the existing memory route's already-reranked Top20 as frozen input.
   In the measured experiment this was native Find Top80 followed by
   `doubao-seed-rerank` `251028` on the full memory bodies. This replay does not
   silently rerank or reselect those memories.
2. Build one auxiliary window per raw source message. The embedding key uses
   previous/current/next messages; its returned value uses
   previous-two/current/next, within the same user and physical session.
3. Embed only the current evaluation question on the query side. Rank source
   windows within that user using cosine Top80 and positive sparse-dot Top80,
   combined with equal-weight RRF (`k=60`).
4. Keep the supplied memories in their original order. Append at most five
   windows within 2,000 extra `o200k_base` tokens, counting two separator tokens
   per window. Deduplicate overlapping message IDs; skip a window if all of its
   remaining raw utterances are already literally visible. Oversized windows
   are skipped, not truncated. This is not semantic fact deduplication.

The source JSON is a mapping of user IDs to raw message arrays. For example:

```json
{
  "example-user": [
    {"id": "m1", "session_id": "s1", "turn": 1, "role": "user", "created_at": "2023-10-13T10:00:00+00:00", "speaker": "Pat", "text": "Pat: When did your friend adopt?"},
    {"id": "m2", "session_id": "s1", "turn": 2, "role": "assistant", "created_at": "2023-10-13T10:00:01+00:00", "speaker": "Lee", "text": "Lee: Last year."}
  ]
}
```

Integer `session_number` is accepted instead of `session_id`. If `speaker` is
absent, LoCoMo's `[D1:2] Lee:` prefix is parsed; otherwise the speaker is marked
unresolved. Names, roles, source timestamps and raw text remain unchanged.
Only `[Image source]` URL lines are omitted from embedding keys; the returned
text retains them. Reference answers and evaluation questions do not enter
window construction.

The input retrieval JSONL uses the existing AML `question` and
`retrieved_context` fields, plus `user_id` and aligned `memory_hits` proving
the owner of every supplied memory. A one-memory example (fewer than twenty
is valid) is:

```json
{"id":"q1","unit":"locomo_refined","user_id":"example-user","question":"When did Lee's friend adopt?","retrieved_context":["Original memory body"],"memory_hits":[{"id":"viking://user/example-user/memories/events/example.md","user_id":"example-user"}]}
```

Embedding caches are mandatory. The source and query caches may be separate
directories. Each cache key is SHA256 of Python's
`json.dumps(request, sort_keys=True, ensure_ascii=False).encode()` for:

```json
{"model":"doubao-embedding-vision-251215","input":[{"type":"text","text":"exact embedding key or exact question"}],"sparse_embedding":{"type":"enabled"}}
```

`<cache>/<sha256-prefix-2>/<sha256>.json` must contain the exact `request` and
`response.data.embedding` / `response.data.sparse_embedding` from a successful
request. Missing, mismatched, invalid or failed cache entries stop replay;
there is no model/API fallback. Do not include credentials in cache records.
For offline execution, prepopulate the standard `TIKTOKEN_CACHE_DIR` with
`o200k_base` tokenizer data as well.

```bash
python -m benchmark.aml.eval.dialogue_windows \
  --sources result/dialogue-replay/sources.json \
  --retrieval result/dialogue-replay/reranked20.jsonl \
  --embedding-cache result/dialogue-replay/source-embeddings \
  --query-embedding-cache result/dialogue-replay/query-embeddings \
  --embedding-model doubao-embedding-vision-251215 --dimension 2048 \
  --output result/dialogue-replay/augmented.jsonl
```

The new JSONL preserves evaluation metadata, adds source bodies to
`retrieved_context`, and saves source message IDs, focal IDs, dense/sparse/RRF
scores and the token cost in `source_dialogue_retrieval`. It can be passed to
the existing `eval.evaluate` entrypoint. Existing output files and rows already
augmented by this module are rejected, preventing accidental double appends.

In the frozen, exploratory 861-question LoCoMo-Refined run, original Top20
scored 736; memory rerank alone scored 748; rerank plus these source windows
scored 767. A separate variant that also reranked the source windows scored
763. The variants are alternatives, not additive gains. Answer/Judge used
GPT-4o mini with unchanged prompts/gold. Context increased from 20 to at most
25 entries; some score flips were Judge-sensitive. These observations are
not a new import, a held-out validation, or a native OV quality guarantee.
Dataset files, frozen model responses and experiment runners are not shipped.

## Static checks

```bash
python -m ruff check benchmark/aml
python -m ruff format --check benchmark/aml
```
