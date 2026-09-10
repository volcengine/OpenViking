# Validating Vikingbot Metrics with Real Q&A

This document is used to verify that a complete pipeline has been wired up end to end:

```text
user question -> /bot/v1/chat -> Vikingbot session persistence -> /bot/v1/feedback or follow-up -> /metrics -> Prometheus -> Grafana
```

How it differs from [Using Prometheus and Grafana to View OpenViking Metrics](11-grafana-prometheus.md):

- `11-grafana-prometheus.md` addresses "how to set up the pipeline"
- This document addresses "once the pipeline is set up, how to confirm with real Q&A scenarios that the metrics are actually changing"

This document only uses metrics that are confirmed to exist in the current repository and are relatively easy to observe, in particular:

- `openviking_service_readiness`
- `openviking_component_health`
- `openviking_queue_pending`
- `openviking_vikingdb_collection_vectors`
- `openviking_model_usage_available`
- `openviking_feedback_*`
- `openviking_feedback_channel_*`

## Prerequisites

Before you begin, confirm the following:

- The OpenViking Server has been started with `server.observability.metrics.enabled=true`
- Vikingbot has been started via `openviking-server --with-bot`
- `curl http://127.0.0.1:30300/bot/v1/health` returns `200`
- `curl http://127.0.0.1:30300/metrics` returns Prometheus exposition text
- Prometheus is already able to scrape OpenViking
- Grafana is already connected to Prometheus

If you are using the Linux localhost setup already covered earlier in this documentation, the default addresses are:

- Prometheus: `http://127.0.0.1:30909`
- Grafana: `http://127.0.0.1:13000`
- OpenViking: `http://127.0.0.1:30300`

## First, understand the semantics of this set of feedback metrics

This step is important; otherwise it is easy to misread what you observe.

In the current repository, the Vikingbot feedback-related metrics are not online monotonically increasing counters, but rather a `scrape-time snapshot gauge`: each time Prometheus scrapes `/metrics`, the `FeedbackCollector` scans `bot/sessions/*.jsonl` and re-aggregates a fresh snapshot from the persisted sessions' `metadata.feedback_events` and `metadata.response_outcomes`.

This means:

- They are better suited for viewing "current totals / proportions / distribution by channel"
- They are better suited for directly reading the current value or step changes over a short window
- It is not recommended to treat them as ordinary counters and prioritize `rate()`
- When querying, prefer filtering on `valid="1"`

You can first run the following in Prometheus or the Grafana Explore view:

```promql
{__name__=~"openviking_feedback.*"}
```

If what you see is `valid="0"`, it means the current sample is a fallback/stale snapshot, and it is not recommended to treat it as the primary validation result.

## Acceptance cases executed by real Q&A scenario

If you would rather not understand the document by "metric category," but instead prefer to ask questions turn by turn like a real user, observe Vikingbot's replies, and then go to Prometheus / Grafana to verify metric changes, you can execute the following 7 scenarios in order.

It is recommended to use a fresh set of `session_id`s, which makes it easier to see the step changes caused by a single operation. The examples below consistently use:

- `realcase-01-chat`
- `realcase-02-thumb-up`
- `realcase-03-thumb-down`
- `realcase-04-reask`
- `realcase-05-followup-no-feedback`
- `realcase-06-resolved`
- `realcase-07-channel-demo`

### Scenario 1: A real user first sends a normal question, validating session persistence and response counting

User question:

```text
请用一句话介绍 OpenViking，控制在 20 个字以内。
```

Expected characteristics of the Vikingbot reply:

- Returns 200
- The response body contains `session_id`
- The response body contains `response_id`
- `message` is non-empty

Command to execute:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-01-chat",
    "user_id": "metrics-validation-user",
    "message": "请用一句话介绍 OpenViking，控制在 20 个字以内。"
  }'
```

Focus on:

- `openviking_feedback_responses_total{valid="1"}`
- `openviking_feedback_sessions_scanned_total{valid="1"}`

PromQL:

```promql
openviking_feedback_responses_total{valid="1"}
```

```promql
openviking_feedback_sessions_scanned_total{valid="1"}
```

Expected changes:

- Will change: `responses_total` usually increases by `1`; if this is a brand-new session file, `sessions_scanned_total` may also increase by `1`.
- Should not change: `events_total`, `thumb_up_total`, `thumb_down_total`, `responses_with_feedback_total`, and the various outcome totals should generally not increase directly at this step, because only a single ordinary Q&A occurred here, with no explicit feedback and no follow-up.
- May be inconspicuous: rate-type metrics usually will not fluctuate noticeably from this round of ordinary Q&A; in this scenario prioritize checking whether the totals jump, and do not treat an unchanged rate as an anomaly.

### Scenario 2: The user finds the answer helpful and gives a thumbs up, validating the positive feedback pipeline

User question:

```text
帮我总结一下 OpenViking 的主要作用。
```

Expected characteristics of the Vikingbot reply:

- Provides a short introduction
- `response_id` can be obtained from the response body

Subsequent user action:

```text
这次回答有帮助，我给一个赞。
```

Steps to execute:

1. First call `/bot/v1/chat` to send the question.
2. Record the `response_id` from the response.
3. Then call `/bot/v1/feedback` to submit `thumb_up`.

Chat command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-02-thumb-up",
    "user_id": "metrics-validation-user",
    "message": "帮我总结一下 OpenViking 的主要作用。"
  }'
```

Feedback command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-02-thumb-up",
    "response_id": "<response_id>",
    "feedback_type": "thumb_up",
    "feedback_text": "helpful"
  }'
```

Focus on:

- `openviking_feedback_events_total{valid="1"}`
- `openviking_feedback_thumb_up_total{valid="1"}`
- `openviking_feedback_responses_with_feedback_total{valid="1"}`
- `openviking_feedback_positive_outcomes_total{valid="1"}`
- `openviking_feedback_coverage{valid="1"}`
- `openviking_feedback_thumbs_up_rate{valid="1"}`
- `openviking_feedback_one_turn_resolution_rate{valid="1"}`

PromQL:

```promql
openviking_feedback_events_total{valid="1"}
```

```promql
openviking_feedback_thumb_up_total{valid="1"}
```

```promql
openviking_feedback_positive_outcomes_total{valid="1"}
```

```promql
openviking_feedback_one_turn_resolution_rate{valid="1"}
```

Expected changes:

- Will change: `feedback_events_total`, `thumb_up_total`, `responses_with_feedback_total`, and `positive_outcomes_total` each usually increase by `1`.
- Should not change: `thumb_down_total`, `negative_outcomes_total`, `reasked_outcomes_total`, and `follow_up_without_feedback_outcomes_total` should generally not increase directly in this scenario, because the current action submits explicit positive feedback for the same response, rather than a negative rating or a follow-up.
- May be inconspicuous: `coverage`, `thumbs_up_rate`, and `one_turn_resolution_rate` usually rise or stay flat, but if there are already many historical responses and historical feedback, they may appear nearly unchanged; among these, `one_turn_resolution_rate` is affected by the current implementation, which also counts `positive_feedback` toward one-turn resolution.

### Scenario 3: The user finds the answer unhelpful and gives a thumbs down, validating the negative feedback pipeline

User question:

```text
请告诉我 OpenViking 的部署步骤，越短越好。
```

Expected characteristics of the Vikingbot reply:

- Provides a short deployment description
- `response_id` can be obtained from the response body

Subsequent user action:

```text
这次回答不够有帮助，我给一个踩。
```

Steps to execute:

1. First call `/bot/v1/chat`.
2. Record the `response_id`.
3. Call `/bot/v1/feedback` to submit `thumb_down`.

Chat command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-03-thumb-down",
    "user_id": "metrics-validation-user",
    "message": "请告诉我 OpenViking 的部署步骤，越短越好。"
  }'
```

Feedback command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-03-thumb-down",
    "response_id": "<response_id>",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

Focus on:

- `openviking_feedback_events_total{valid="1"}`
- `openviking_feedback_thumb_down_total{valid="1"}`
- `openviking_feedback_negative_outcomes_total{valid="1"}`
- `openviking_feedback_thumbs_down_rate{valid="1"}`
- `openviking_feedback_negative_feedback_rate{valid="1"}`

PromQL:

```promql
openviking_feedback_thumb_down_total{valid="1"}
```

```promql
openviking_feedback_negative_outcomes_total{valid="1"}
```

```promql
openviking_feedback_negative_feedback_rate{valid="1"}
```

Expected changes:

- Will change: `thumb_down_total` and `negative_outcomes_total` each usually increase by `1`, and `events_total` should also increase by `1` with this explicit feedback.
- Should not change: `thumb_up_total`, `positive_outcomes_total`, `reasked_outcomes_total`, `resolved_outcomes_total`, and `follow_up_without_feedback_outcomes_total` should generally not increase directly in this scenario, because only explicit negative feedback was submitted here, with no follow-up or silent ending.
- May be inconspicuous: `thumbs_down_rate` and `negative_feedback_rate` usually rise or stay flat, but if there are many historical samples the proportional change may be very small, so still prioritize checking whether the totals jump as expected.

### Scenario 4: The user gives no explicit feedback but immediately follows up, validating `reasked`

User's first question:

```text
请解释 OpenViking 的 metrics 是做什么的。
```

Characteristics of Vikingbot's first-round reply:

- Provides an explanation, but the user subjectively still feels it is not specific enough

User's second question:

```text
我还是没明白，请再具体一点。
```

Steps to execute:

1. Send the first question with the same `session_id`.
2. Do not call `/bot/v1/feedback`.
3. Within 10 minutes, send the second question with the same `session_id`.

First-round command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-04-reask",
    "user_id": "metrics-validation-user",
    "message": "请解释 OpenViking 的 metrics 是做什么的。"
  }'
```

Second-round command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-04-reask",
    "user_id": "metrics-validation-user",
    "message": "我还是没明白，请再具体一点。"
  }'
```

Focus on:

- `openviking_feedback_reasked_outcomes_total{valid="1"}`
- `openviking_feedback_reask_rate{valid="1"}`

PromQL:

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
```

```promql
openviking_feedback_reask_rate{valid="1"}
```

Expected changes:

- `reasked_outcomes_total` usually increases by `1`
- `reask_rate` usually rises, or stays flat when there are many historical samples
- The key point is not the content of the second reply itself, but that the first assistant response's outcome was evaluated as `reasked`
- `responses_total` usually increases by another `1` because of the second `/bot/v1/chat`, but this does not affect the core judgment that "the previous response was reclassified as `reasked`"
- `events_total`, `thumb_up_total`, `thumb_down_total`, and `responses_with_feedback_total` usually stay unchanged, because this scenario has no explicit feedback
- `positive_outcomes_total`, `negative_outcomes_total`, and `resolved_outcomes_total` should generally not increase directly from this step

Additional notes:

1. `reask_rate` is not a strictly monotonic online counter ratio, but a proportion re-aggregated from the current persisted session snapshot, so it is better suited for checking "whether it does not decrease, and whether step changes appear."
2. If the second follow-up already exceeds the 10-minute window and still has no explicit feedback, it is more likely to fall into `follow_up_without_feedback` rather than `reasked`.
3. If you see `follow_up_without_feedback_outcomes_total` increase in this scenario, first check whether the second question's time already exceeded 10 minutes, or whether samples from another session got mixed in.

### Scenario 5: The user neither thumbs up nor thumbs down, and follows up after a while, validating `follow_up_without_feedback`

User's first question:

```text
OpenViking 和普通向量数据库有什么关系？
```

Characteristics of Vikingbot's first-round reply:

- Provides a conceptual answer

The user continues later:

```text
那你能再举一个更具体的例子吗？
```

Steps to execute:

1. Send the first question with a new `session_id`.
2. Do not call `/bot/v1/feedback`.
3. Wait at least 10 minutes, then continue the follow-up with the same `session_id`.

Note: in the current implementation, a follow-up within 10 minutes is preferentially classified as `reasked`; only when it exceeds this window and there is no explicit feedback does it fall into `follow_up_without_feedback`.

First-round command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-05-followup-no-feedback",
    "user_id": "metrics-validation-user",
    "message": "OpenViking 和普通向量数据库有什么关系？"
  }'
```

Second-round command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-05-followup-no-feedback",
    "user_id": "metrics-validation-user",
    "message": "那你能再举一个更具体的例子吗？"
  }'
```

Focus on:

- `openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}`

PromQL:

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

Expected changes:

- Will change: `follow_up_without_feedback_outcomes_total` usually increases by `1`; at the same time the second `/bot/v1/chat` also causes `responses_total` to increase by another `1`, but the one actually reclassified as `follow_up_without_feedback` is still the first-round assistant response.
- Should not change: `events_total`, `thumb_up_total`, `thumb_down_total`, and `responses_with_feedback_total` usually stay unchanged, because the entire scenario makes no explicit call to `/bot/v1/feedback`; `positive_outcomes_total`, `negative_outcomes_total`, and `reasked_outcomes_total` should also generally not increase directly at this step.
- May be inconspicuous: if you see the increase land on `reasked_outcomes_total`, first check whether the second question was still within the 10-minute window; rate-type metrics may likewise change inconspicuously due to many historical samples, so this scenario prioritizes checking whether the totals jump.

### Scenario 6: The user asks and then ends, with no further follow-up, validating `resolved`

User question:

```text
请用一句话解释什么是 OpenViking 的 readiness 指标。
```

Expected characteristics of the Vikingbot reply:

- Provides a short answer
- The user does not continue to follow up, and does not submit feedback

Steps to execute:

1. Open a new `session_id` and send one round of Q&A.
2. Do not call `/bot/v1/feedback`.
3. Do not continue with a follow-up.
4. Wait for the system to complete persistence and subsequent outcome evaluation, then observe the metrics.

Command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-06-resolved",
    "user_id": "metrics-validation-user",
    "message": "请用一句话解释什么是 OpenViking 的 readiness 指标。"
  }'
```

Focus on:

- `openviking_feedback_resolved_outcomes_total{valid="1"}`

PromQL:

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

Expected changes:

- Will change: `resolved_outcomes_total` has a chance to increase by `1`, and `one_turn_resolution_rate` may also rise or stay flat.
- Should not change: `events_total`, `thumb_up_total`, `thumb_down_total`, and `responses_with_feedback_total` usually stay unchanged in this scenario, because there is neither explicit feedback nor a subsequent follow-up; `negative_outcomes_total`, `reasked_outcomes_total`, and `follow_up_without_feedback_outcomes_total` should also generally not increase directly.
- May be inconspicuous: compared with `thumb_up`, `thumb_down`, and `reasked`, `resolved` is better used as a supplementary validation item, because it depends on the timing of the subsequent outcome evaluation, and both its appearance time and magnitude are less stable than explicit feedback; therefore, not seeing an immediate jump within a short time does not necessarily indicate a pipeline anomaly.

### Scenario 7: A real multi-channel user accessing from a specified channel, validating `openviking_feedback_channel_*`

Prerequisite:

- Your bot configuration already has the `bot_api` channel corresponding to `channel_id="demo"` enabled

User question:

```text
请简单介绍一下 OpenViking。
```

Expected characteristics of the Vikingbot reply:

- The request needs to go through `POST /bot/v1/chat/channel`
- The response sample should ultimately land on `channel="bot_api__demo"`

Steps to execute:

1. Initiate the chat with `POST /bot/v1/chat/channel`.
2. Record the `response_id` from the response.
3. Call `/bot/v1/feedback` with the same `channel_id`.
4. Compare against the channel-dimension metrics.

Chat command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat/channel" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-07-channel-demo",
    "user_id": "metrics-validation-user",
    "channel_id": "demo",
    "message": "请简单介绍一下 OpenViking。"
  }'
```

Feedback command:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-07-channel-demo",
    "response_id": "<response_id>",
    "channel_id": "demo",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

Focus on:

- `openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}`
- `openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}`
- `openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}`

PromQL:

```promql
openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_events_total{valid="1"}
```

Expected changes:

- Will change: you can see this group of samples with `channel="bot_api__demo"`, and totals such as `events_total` and `negative_outcomes_total` under that channel will jump with this Q&A and negative feedback.
- Should not change: samples of other unrelated channels should generally not jump because of this request; if you mistakenly used `POST /bot/v1/chat` instead of `POST /bot/v1/chat/channel`, you usually will not get the aggregated samples for the target `bot_api__demo` either.
- May be inconspicuous: proportions such as `channel_thumbs_down_rate` and `channel_one_turn_resolution_rate` may change inconspicuously when there are many historical channel samples, so channel acceptance prioritizes checking whether that channel's totals jump, and then whether the rate direction is reasonable.

If you are unsure which `channel_id`s are available in the current environment, you can first check the `bot_api` channels enabled in the bot configuration; if the configuration does not exist, the corresponding request will return `404 Channel '<channel_id>' not found`.

### Recommended order for a one-off manual acceptance

If you just want to run the most practical manual acceptance once, the recommended order is:

1. Run "Scenario 1" to confirm that `/bot/v1/chat`, session persistence, and `responses_total` work correctly.
2. Run "Scenario 2" to confirm that `thumb_up`, `positive_outcomes_total`, and `coverage` work correctly.
3. Run "Scenario 3" to confirm that `thumb_down` and `negative_outcomes_total` work correctly.
4. Run "Scenario 4" to confirm that `reasked_outcomes_total` works correctly.
5. Run "Scenario 5" to confirm that `follow_up_without_feedback_outcomes_total` works correctly.
6. Run "Scenario 6" to use `resolved` as a supplementary validation.
7. If your environment has the `bot_api` channel enabled, then run "Scenario 7" to validate the channel-dimension metrics.

## Step 0: First look at the baseline metrics

Before actually starting the Q&A, first confirm that the basic pipeline metrics are alive.

It is recommended to run the following queries in order.

Service readiness:

```promql
openviking_service_readiness{valid="1"}
```

Expected: returns `1`.

Component health:

```promql
openviking_component_health{valid="1"}
```

Expected:

- At least components such as `queue` and `vikingdb` can be seen
- Under normal conditions most values are `1`

Queue backlog:

```promql
openviking_queue_pending
```

Expected:

- Usually `0` or a small value
- If it has already been greater than `0` for a long time at this point, when doing Q&A validation later you need to distinguish between "triggered by new traffic" and "already had a backlog"

Current vector count of the VikingDB collection:

```promql
openviking_vikingdb_collection_vectors{valid="1"}
```

Expected:

- At least some collection samples exist
- The value may not necessarily change during this validation, but it proves that this kind of state metric has already been scraped by Prometheus

Availability of model usage statistics:

```promql
openviking_model_usage_available{valid="1"}
```

Expected:

- You may see `model_type="vlm"`, `embedding`, `rerank`
- A value of `1` indicates that the corresponding statistic is currently available

Feedback snapshot overview:

```promql
openviking_feedback_sessions_scanned_total{valid="1"}
```

```promql
openviking_feedback_responses_total{valid="1"}
```

```promql
openviking_feedback_events_total{valid="1"}
```

Expected:

- If your environment already has historical bot sessions, the values are usually greater than `0`
- If it is a brand-new environment, they may also be `0`

It is recommended to first write down the following values and use them for comparison in each subsequent scenario:

- `openviking_feedback_responses_total{valid="1"}`
- `openviking_feedback_responses_with_feedback_total{valid="1"}`
- `openviking_feedback_events_total{valid="1"}`
- `openviking_feedback_thumb_up_total{valid="1"}`
- `openviking_feedback_thumb_down_total{valid="1"}`
- `openviking_feedback_positive_outcomes_total{valid="1"}`
- `openviking_feedback_negative_outcomes_total{valid="1"}`
- `openviking_feedback_reasked_outcomes_total{valid="1"}`
- `openviking_feedback_resolved_outcomes_total{valid="1"}`
- `openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}`

## Step 1: First do one round of minimal chat to confirm the bot session can be persisted

This scenario is not meant to validate feedback metrics, but to confirm that the `/bot/v1/chat` pipeline itself works.

Example user input:

```text
请用一句话介绍 OpenViking，控制在 20 个字以内。
```

Expected characteristics of the Vikingbot reply:

- Returns 200
- The response body contains `session_id`
- The response body contains `response_id`
- `message` is non-empty

Command to execute:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-session-a",
    "user_id": "metrics-validation-user",
    "message": "请用一句话介绍 OpenViking，控制在 20 个字以内。"
  }'
```

Example shape of the response body:

```json
{
  "session_id": "metrics-validation-session-a",
  "response_id": "<response_id>",
  "message": "..."
}
```

After this round, the most worthwhile thing to observe is not the thumbs up/down metrics, but:

```promql
openviking_feedback_responses_total{valid="1"}
```

Expected:

- Compared with the Step 0 baseline, it usually increases by `1`
- If the scrape has not refreshed yet, wait 15-30 seconds and check again

Optional observation:

```promql
openviking_queue_pending
```

Expected:

- It may fluctuate briefly, but a change is not guaranteed
- It is better suited for confirming that the system did not develop an abnormal backlog due to the bot request

## Step 2: Explicit thumb up, validating positive feedback metrics

This is the main path most recommended to do first, because the behavior is the most stable and the easiest to understand.

User Q&A scenario:

1. User question:

```text
帮我总结一下 OpenViking 的作用。
```

2. Vikingbot gives a short answer.

3. The user subjectively judges "this answer was helpful" and submits `thumb_up`.

First initiate the chat:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-positive",
    "user_id": "metrics-validation-user",
    "message": "帮我总结一下 OpenViking 的作用。"
  }'
```

Note the `response_id` from the response, then submit feedback:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-positive",
    "response_id": "<response_id>",
    "feedback_type": "thumb_up",
    "feedback_text": "helpful"
  }'
```

Expected feedback response:

```json
{
  "accepted": true,
  "response_id": "<response_id>",
  "session_id": "metrics-validation-positive",
  "feedback_type": "thumb_up",
  "feedback_delay_sec": 1.234,
  "timestamp": "..."
}
```

Focus on these queries.

Total feedback events:

```promql
openviking_feedback_events_total{valid="1"}
```

Total thumbs up:

```promql
openviking_feedback_thumb_up_total{valid="1"}
```

Number of responses with feedback:

```promql
openviking_feedback_responses_with_feedback_total{valid="1"}
```

Total positive outcomes:

```promql
openviking_feedback_positive_outcomes_total{valid="1"}
```

Coverage:

```promql
openviking_feedback_coverage{valid="1"}
```

Thumbs-up rate:

```promql
openviking_feedback_thumbs_up_rate{valid="1"}
```

One-turn resolution rate:

```promql
openviking_feedback_one_turn_resolution_rate{valid="1"}
```

Expected changes:

- `openviking_feedback_events_total{valid="1"}` increases by `1`
- `openviking_feedback_thumb_up_total{valid="1"}` increases by `1`
- `openviking_feedback_responses_with_feedback_total{valid="1"}` usually increases by `1`
- `openviking_feedback_positive_outcomes_total{valid="1"}` usually increases by `1`
- `openviking_feedback_coverage{valid="1"}` may rise or stay flat, depending on the historical total samples
- `openviking_feedback_thumbs_up_rate{valid="1"}` may rise or stay flat, depending on the historical feedback structure
- `openviking_feedback_one_turn_resolution_rate{valid="1"}` may rise, because the current implementation counts both `resolved + positive_feedback` toward one-turn resolution

If you want to check "whether this operation has already been refreshed into Prometheus," the most reliable approach is to switch the query to table view and refresh it 1-2 times in a row, rather than starting out by looking at the time-series line chart.

## Step 3: Explicit thumb down, validating negative feedback metrics

The second main path is `thumb_down`, which is equally stable and forms a contrast with positive feedback.

User Q&A scenario:

1. User question:

```text
请告诉我 OpenViking 的部署步骤，越短越好。
```

2. Vikingbot returns an answer.

3. Suppose the user finds this answer unhelpful and submits `thumb_down`.

First send the chat:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-negative",
    "user_id": "metrics-validation-user",
    "message": "请告诉我 OpenViking 的部署步骤，越短越好。"
  }'
```

Then submit the thumbs down:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-negative",
    "response_id": "<response_id>",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

Key queries.

```promql
openviking_feedback_events_total{valid="1"}
```

```promql
openviking_feedback_thumb_down_total{valid="1"}
```

```promql
openviking_feedback_negative_outcomes_total{valid="1"}
```

```promql
openviking_feedback_thumbs_down_rate{valid="1"}
```

```promql
openviking_feedback_negative_feedback_rate{valid="1"}
```

Expected changes:

- `openviking_feedback_events_total{valid="1"}` increases by another `1`
- `openviking_feedback_thumb_down_total{valid="1"}` increases by `1`
- `openviking_feedback_negative_outcomes_total{valid="1"}` usually increases by `1`
- `openviking_feedback_thumbs_down_rate{valid="1"}` may rise
- `openviking_feedback_negative_feedback_rate{valid="1"}` may rise

After completing this step, you have validated that:

- `/bot/v1/feedback` can be associated with a historical `response_id`
- The feedback event has been written into the session metadata
- The scrape-time feedback aggregation has already been reflected in `/metrics`

## Step 4: Follow-up question, validating `reasked`

This scenario is used to validate "no explicit feedback, but the user follows up quickly, and the previous answer is judged as `reasked`."

User Q&A scenario:

1. User's first question:

```text
请解释 OpenViking 的 metrics 是做什么的。
```

2. Vikingbot gives an answer.

3. The user continues the follow-up within the same `session_id`:

```text
我还是没明白，请再具体一点。
```

First-round chat:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-reask",
    "user_id": "metrics-validation-user",
    "message": "请解释 OpenViking 的 metrics 是做什么的。"
  }'
```

Second-round follow-up, note that it must be sent within 10 minutes:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-reask",
    "user_id": "metrics-validation-user",
    "message": "我还是没明白，请再具体一点。"
  }'
```

Key queries:

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
```

```promql
openviking_feedback_reask_rate{valid="1"}
```

Expected changes:

- `openviking_feedback_reasked_outcomes_total{valid="1"}` usually increases by `1`
- `openviking_feedback_reask_rate{valid="1"}` may rise

The key point here is not the content of the second `/chat` reply, but that the first assistant response's outcome has already been recorded as `reasked`.

## Step 5: Continue the follow-up without submitting explicit feedback, validating `follow_up_without_feedback`

This scenario is similar to the previous step, but the thing to validate is another outcome dimension.

User Q&A scenario:

1. User's first question:

```text
OpenViking 和普通向量数据库有什么关系？
```

2. Vikingbot gives an answer.

3. The user neither thumbs up nor thumbs down, but continues to ask after 10 minutes:

```text
那你能再举一个更具体的例子吗？
```

The way to execute is similar to Step 4, but here do not follow up within 10 minutes. You should use a new `session_id`, not call `/bot/v1/feedback`, and after the assistant reply, wait at least 10 minutes before sending the next follow-up.

Recommended query:

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

Expected changes:

- This value usually increases by `1`

Notes:

- In the current implementation, both `reasked` and `follow_up_without_feedback` depend on a subsequent user turn
- But their analytical semantics differ: the former emphasizes "followed up / re-asked within 10 minutes after the answer," while the latter emphasizes "the user has a follow-up, no explicit feedback, and the follow-up already exceeds the 10-minute window"
- If your environment has a lot of historical data, the proportional change caused by a single validation may be inconspicuous, so prioritize checking whether the total metrics jump

## Step 6: `resolved` as a supplementary scenario validation

`resolved` is not recommended as the top-priority validation item, because it is not like thumb up / thumb down where "you can easily observe it immediately after completing one action."

According to the current implementation rules:

- No explicit feedback
- No new user follow-up
- The response is evaluated as resolved by the outcome evaluator

Recommended validation method:

1. Open a new session and send one round of short Q&A
2. Do not do `/bot/v1/feedback`
3. Do not continue to follow up
4. Wait for the system to complete the relevant persistence and subsequent evaluation
5. Then look at:

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

Expected:

- This value has a chance to increase by `1`

If there is no obvious change at this step, you should not immediately judge that the system has a problem; it is recommended to use the results of Steps 2, 3, and 4 as the primary acceptance basis.

## Step 7: View feedback metrics by channel

The current implementation supports carrying `channel_id` via `POST /bot/v1/chat/channel`, routing the request into a channel dimension such as `bot_api__<channel_id>`, so you can validate `openviking_feedback_channel_*`.

This step is optional; it is recommended to do it after all the main paths have passed.

Example chat request:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat/channel" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-channel-demo",
    "user_id": "metrics-validation-user",
    "channel_id": "demo",
    "message": "请简单介绍一下 OpenViking。"
  }'
```

Note: an ordinary `POST /bot/v1/chat` will not automatically switch to the `bot_api` route based on `channel_id`; when validating by channel you should use `POST /bot/v1/chat/channel`.

Example feedback request:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-channel-demo",
    "response_id": "<response_id>",
    "channel_id": "demo",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

Key queries:

```promql
openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}
```

If you want to compare the default channel and `bot_api__demo` at the same time, you can query:

```promql
openviking_feedback_channel_events_total{valid="1"}
```

Expected:

- You can see `channel="bot_api__demo"`
- The corresponding values will change with the chat/feedback under that channel

Note: if your current bot configuration does not have the corresponding `bot_api` channel enabled, `POST /bot/v1/chat/channel` will directly return `404 Channel 'demo' not found`, and you need to first confirm that the corresponding channel exists in the bot configuration. The most direct way is to look at the enabled `bot_api` channel definitions in the bot configuration file.

## Recommended Grafana Explore query list

If you just want to quickly run through a manual acceptance once, you can execute in this order.

Basic pipeline:

```promql
openviking_service_readiness{valid="1"}
```

```promql
openviking_component_health{valid="1"}
```

```promql
openviking_queue_pending
```

Feedback overview:

```promql
openviking_feedback_responses_total{valid="1"}
```

```promql
openviking_feedback_events_total{valid="1"}
```

```promql
openviking_feedback_coverage{valid="1"}
```

Positive feedback:

```promql
openviking_feedback_thumb_up_total{valid="1"}
```

```promql
openviking_feedback_positive_outcomes_total{valid="1"}
```

Negative feedback:

```promql
openviking_feedback_thumb_down_total{valid="1"}
```

```promql
openviking_feedback_negative_outcomes_total{valid="1"}
```

Follow-ups and resolution:

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
```

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

By channel:

```promql
openviking_feedback_channel_events_total{valid="1"}
```

```promql
openviking_feedback_channel_thumbs_up_rate{valid="1"}
```

```promql
openviking_feedback_channel_thumbs_down_rate{valid="1"}
```

## Common misjudgments

### 1. Why do some feedback metrics not change right after I send `/chat`

Because the feedback-type metrics mainly come from:

- `metadata.feedback_events`
- `metadata.response_outcomes`

When there is only ordinary chat and no explicit feedback or follow-up yet, not all feedback metrics will move immediately.

### 2. Why did the total change, but the rate looks inconspicuous

Because they are essentially snapshot gauges, not counters designed for `rate()`. Prioritize the current value, the table view, or step changes over a short time range.

### 3. Why do I only see `valid="0"`

It means the collector's refresh failed this time, and what is currently exposed is the last successful snapshot. During primary validation, prefer using `valid="1"`.

### 4. Why does the channel dimension not show `bot_api__demo`

There are usually three possible reasons:

- You called `POST /bot/v1/chat` instead of `POST /bot/v1/chat/channel`
- The request did not carry `channel_id`
- The corresponding `bot_api` channel is not enabled in the bot configuration

## Acceptance recommendations

If you only do a single minimal acceptance, it is recommended to use these four items as the "pass" criteria:

1. `openviking_service_readiness{valid="1"}` is `1`
2. After one `thumb_up`, `openviking_feedback_events_total{valid="1"}` and `openviking_feedback_thumb_up_total{valid="1"}` increase
3. After one `thumb_down`, `openviking_feedback_thumb_down_total{valid="1"}` and `openviking_feedback_negative_outcomes_total{valid="1"}` increase
4. After one follow-up, `openviking_feedback_reasked_outcomes_total{valid="1"}` increases

As long as these four items pass, it basically demonstrates that:

- The bot session has been persisted
- The feedback/outcome has been written into the metadata
- `/metrics` has successfully aggregated the feedback snapshot
- Prometheus / Grafana can correctly see this set of metrics

## Related documents

- [Observability and Troubleshooting](05-observability.md)
- [Using Prometheus and Grafana to View OpenViking Metrics](11-grafana-prometheus.md)
- [Metrics](../concepts/12-metrics.md)
- [Metrics API](../api/09-metrics.md)
- [Vikingbot Real User Q&A Metrics Validation Cases](https://github.com/volcengine/OpenViking/blob/main/bot/docs/vikingbot-real-user-metrics-cases.md)
- [Vikingbot Phase 2 Feedback Pipeline Validation Guide](https://github.com/volcengine/OpenViking/blob/main/bot/docs/vikingbot-phase2-feedback-validation-with-openviking-server.md)
- [Vikingbot Phase 3 Outcome Validation Guide](https://github.com/volcengine/OpenViking/blob/main/bot/docs/vikingbot-phase3-outcome-validation-with-openviking-server.md)
