# Validate Vikingbot metrics with real conversations

Use this guide after [setting up Prometheus and Grafana](11-grafana-prometheus.md) to check the complete path:

```text
Chat → persisted bot session → feedback / follow-up → /metrics → Prometheus → Grafana
```

## Prerequisites

- Start OpenViking with `server.observability.metrics.enabled=true` and `openviking-server --with-bot --port 30300`.
- Confirm `/bot/v1/health` returns HTTP 200 and `/metrics` returns Prometheus exposition text.
- Confirm Prometheus scrapes OpenViking and Grafana uses that Prometheus data source.

The localhost setup in the previous guide uses OpenViking at `http://127.0.0.1:30300`, Prometheus at `http://127.0.0.1:30909`, and Grafana at `http://127.0.0.1:13000`. If authentication is enabled, include the credentials required by your deployment in each request; see [Authentication](04-authentication.md).

## How to interpret the metrics

Feedback metrics are **snapshot gauges**, including names ending in `_total`. `FeedbackCollector` aggregates persisted `metadata.feedback_events` and `metadata.response_outcomes` from `<bot_data_path>/sessions/*.jsonl`. Its default refresh TTL is 30 seconds, so repeated scrapes can reuse a snapshot. Allow a collector refresh and a subsequent Prometheus scrape before comparing values.

Use current values or a short time window, rather than counter-style `rate()`. Select `valid="1"`; `valid="0"` is a fallback snapshot after collection failure and is unsuitable for acceptance checks. See the [Prometheus metric types](https://prometheus.io/docs/concepts/metric_types/) for the gauge/counter distinction.

Use a fresh session ID for each scenario. Totals cover all persisted sessions, so other traffic can affect the result. Proportions may barely move when historical sample counts are large. Outcome totals can decrease when an existing response is reclassified.

## Record a baseline

Run these queries in Prometheus or Grafana Explore's table view:

```promql
openviking_service_readiness{valid="1"}
openviking_component_health{valid="1"}
openviking_queue_pending
openviking_vikingdb_collection_vectors{valid="1"}
openviking_model_usage_available{valid="1"}
```

Run each expression separately. Readiness should be `1`; healthy components normally report `1`. Queue depth should be small or zero. Collection vector counts and model-usage availability depend on the configured services and need not change during a bot conversation.

Record the feedback snapshot too:

```promql
{__name__=~"openviking_feedback.*",valid="1"}
```

## 1. Chat and confirm persistence

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-01-chat",
    "user_id": "metrics-validation-user",
    "message": "Describe OpenViking in one sentence."
  }'
```

Expect HTTP 200 with `session_id`, `response_id`, and a nonempty `message`. Save the response ID for feedback.

After refresh, `openviking_feedback_responses_total{valid="1"}` should normally increase by one. A new session file also increases `openviking_feedback_sessions_scanned_total{valid="1"}`. Ordinary chat alone does not create an explicit feedback event.

## 2. Submit positive feedback

Send another chat using `session_id: "realcase-02-thumb-up"`, then replace `<response_id>` below with that chat's response ID:

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

Expect `accepted: true`. For a response with no previous feedback, these gauges normally increase by one:

```promql
openviking_feedback_events_total{valid="1"}
openviking_feedback_thumb_up_total{valid="1"}
openviking_feedback_responses_with_feedback_total{valid="1"}
openviking_feedback_positive_outcomes_total{valid="1"}
```

`coverage`, `thumbs_up_rate`, and `one_turn_resolution_rate` can also change. The implementation includes both `resolved` and `positive_feedback` in one-turn resolution. Check totals first; aggregate proportions depend on all previous responses and outcomes.

## 3. Submit negative feedback

Chat with a fresh session, `realcase-03-thumb-down`, then send the same feedback request with that session's response ID, `feedback_type: "thumb_down"`, and `feedback_text: "not helpful"`.

Expect the following totals to increase by one after refresh:

```promql
openviking_feedback_events_total{valid="1"}
openviking_feedback_thumb_down_total{valid="1"}
openviking_feedback_negative_outcomes_total{valid="1"}
```

`openviking_feedback_thumbs_down_rate` and `openviking_feedback_negative_feedback_rate` may change too. Positive-feedback and reask totals should not increase from this action alone.

## 4. Follow up within ten minutes

Chat using `realcase-04-reask`. Without submitting feedback, send a second message to the same session within ten minutes of the assistant response:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-04-reask",
    "user_id": "metrics-validation-user",
    "message": "Please explain that more concretely."
  }'
```

The **previous** assistant response is classified as `reasked`; this classification uses timing, not the semantic similarity of the questions.

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
openviking_feedback_reask_rate{valid="1"}
```

The reasked total normally increases by one. The second assistant response also increases `responses_total`. Explicit feedback totals should stay unchanged.

## 5. Follow up after ten minutes

Chat using `realcase-05-followup-no-feedback`, submit no feedback, and wait **more than ten minutes** after the assistant response. Send another message to the same session.

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

The previous response should now be classified as `follow_up_without_feedback`, increasing this total by one. A follow-up at or before the ten-minute boundary instead qualifies as `reasked`. Explicit feedback totals stay unchanged.

## 6. Understand the `resolved` outcome

A response with no subsequent user message is classified as `resolved` **when the outcome evaluator runs**. The metrics collector only reads stored outcomes; it does not run an inactivity timer or create a resolved outcome after ten minutes.

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

A new chat followed by silence is therefore not a deterministic acceptance test for this metric. Inspect persisted `metadata.response_outcomes` if a resolved sample is expected. Use the positive, negative, and follow-up scenarios above as the repeatable acceptance path.

## 7. Check channel-level metrics

This scenario requires an enabled `bot_api` channel whose ID is `demo`. Chat through `/bot/v1/chat/channel`:

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat/channel" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-07-channel-demo",
    "user_id": "metrics-validation-user",
    "channel_id": "demo",
    "message": "Describe OpenViking briefly."
  }'
```

Submit feedback with the returned response ID and the same `channel_id`:

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

Check the target channel:

```promql
openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}
openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}
openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}
```

The channel's event and negative-outcome totals should increase. Other channels should not change because of this request. A missing configured channel returns `404`; plain `/bot/v1/chat` does not exercise this channel route.

## Acceptance and troubleshooting

For a minimal check, require readiness `1`, a persisted chat response, increased positive and negative feedback totals, and an increased reasked total after a prompt follow-up. Validate channel totals separately if your deployment uses channels.

| Symptom | Check |
| --- | --- |
| Chat succeeds but feedback totals stay unchanged | Submit explicit feedback; ordinary chat does not create feedback events. |
| A recent event is missing | Allow the 30-second collector TTL, background refresh, and the next Prometheus scrape; then inspect session persistence. |
| Totals change but proportions barely move | Historical samples dilute a single event. Compare current totals in table view. |
| Only `valid="0"` is present | Inspect collector errors and access to the bot session directory. |
| `bot_api__demo` is missing | Check the enabled channel ID, `/chat/channel` endpoint, and matching feedback `channel_id`. |

## Related documentation

- [Observability and troubleshooting](05-observability.md)
- [Prometheus and Grafana setup](11-grafana-prometheus.md)
- [Metrics concepts](../concepts/12-metrics.md)
- [Metrics API](../api/09-metrics.md)
