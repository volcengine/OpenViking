---
name: ov-session-report
description: Analyze local Q&A session JSONL files and produce a complete, auditable English Markdown weekly report over consecutive time windows, with all data required for downstream Feishu document and whiteboard rendering. Use for requests such as "analyze the latest week of sessions," "generate a community Q&A weekly report," or "compare with last week." This skill does not ingest other message sources or create/upload online documents.
---

# OpenViking Session Q&A Weekly Report

Read only the session data supplied by the user. Clean and normalize it, apply time windows, compute metrics, classify topics, review risks, compare with the previous period, and produce one self-contained Markdown file. The default timezone is `Asia/Shanghai`.

## Delivery Boundary

- The only analytical source is the session files under `sessions_root`. Do not fetch or merge other communities, message streams, directories, or online chat data.
- Deliver exactly one `.md` file. Temporary scripts and intermediate files are allowed during analysis, but the report must remain understandable without them.
- Do not call online document, messaging, contact, or whiteboard APIs. Do not create XML, HTML, SVG, or PNG files, and do not upload anything.
- Preserve document and whiteboard data requirements. The Markdown must contain enough aggregates, time series, rankings, conclusions, and human-message excerpts for a downstream renderer to build the complete report body, summary dashboard, and comparison dashboard without reopening the sessions.
- Never estimate counts from intuition. Every number must come from parsed data or a reproducible formula, and every important conclusion must be traceable to concrete session messages.
- When the sessions cannot prove a claim, write "no supporting evidence was found before the data cutoff" instead of presenting it as a global fact.

Default output filename:

```text
OpenViking_Community_QA_Weekly_Report_YYYY-MM-DD_to_YYYY-MM-DD.md
```

## Inputs

- `sessions_root`: Required. Recursively read `.jsonl` session files from this directory.
- `current_start` / `current_end`: Optional boundaries for the current reporting window.
- `previous_start` / `previous_end`: Optional boundaries for the previous comparison window.
- `previous_report`: Optional Markdown report created by this skill. Use it to preserve published definitions and historical figures.
- `output_path`: Optional. If omitted, write to `reports/qa-weekly-YYYYMMDD-YYYYMMDD/` in the current workspace.
- `session_scope`: Optional subdirectory, filename pattern, or explicit session set supplied by the user.
- `history`: Optional verified report titles and links for downstream document rendering. Pass these through without querying for them.

Infer missing values from the directory structure, timestamps, and previous report when possible. Ask the user only when the target session set or reporting boundary cannot be resolved and different choices would materially change the result.

## Report Order

The Markdown report must use this order:

1. Title: `OpenViking Community Q&A Weekly Report: YYYY-MM-DD to YYYY-MM-DD`
2. `Executive Summary`
3. `Releases This Week`
4. `Weekly Topic Ranking`
5. `Open High-Priority Issues`
6. `Week-over-Week Comparison`
7. `Weekly Activity`
8. `Trends and Recommendations`
9. `Methodology and Data Notes`
10. `Previous Reports`
11. `Appendix: Structured Report Data`

`Releases This Week` may only summarize versions for which the sessions contain explicit evidence such as "released," "deployed," or "available to install." Statements such as "planned," "expected," or "will be included in the next release" are not completed releases. When there is no qualifying evidence, write: "No formal release could be confirmed from this week's sessions." Do not browse for confirmation.

## Time Windows

Use half-open intervals:

```text
Current:  [current_start, current_end)
Previous: [previous_start, previous_end)
```

Choose the cutoff in this order:

1. An explicit cutoff supplied by the user.
2. For an established weekly series, the previous report's `current_end + 7 days`.
3. When the user says "up to now," the current time in `Asia/Shanghai`.
4. Use the latest message timestamp only when the user explicitly states that the directory is a point-in-time export snapshot.

Comparison requirements:

- Current and previous windows must have equal duration, meet at one boundary, and never overlap.
- Use consecutive seven-day windows by default. If the user asks for a shorter first period, show the actual range in both the title and methodology notes.
- Messages at or after the current cutoff belong to the next report.
- If the earliest exported message is later than the requested start, preserve the requested window and disclose incomplete coverage.
- Record the earliest and latest valid messages and their distance from the requested boundaries.
- If the current directory lacks previous-window data, prefer the structured appendix in `previous_report`. If neither is available, keep the comparison section and mark the previous period as unavailable. Never replace unknown values with zero.

## Discover Session Files

List candidate files first:

```bash
rg --files "$SESSION_ROOT" -g '*.jsonl'
```

Do not assume every file in the directory belongs to the target dataset:

1. Group files by directory level, filename prefix, and available metadata. Count files and determine the time range for each group.
2. Sample human messages from the beginning, middle, and end of each group to verify that it contains OpenViking community Q&A sessions.
3. Exclude test conversations, internal debugging, bot self-tests, unrelated products, and clearly irrelevant data.
4. When the user supplies an exact `session_scope`, honor it but still verify that it contains data.
5. Record discovered, included, and excluded file counts plus exclusion reasons in the methodology section. Only disclose masked summaries of internal identifiers.

Do not select the target dataset solely because it contains the largest number of files.

## Parse and Normalize JSONL

Use a real JSON parser on every line:

- Count malformed lines in `bad_lines`. Keep filenames and line numbers for local auditing, but do not expose them in the final report.
- Skip non-message rows such as `_type=metadata`, while still using their session identifiers and contextual metadata.
- Try timestamp fields in this order: `timestamp`, `created_at`, `create_time`, `time`. Support numeric timestamps in both seconds and milliseconds.
- Convert timezone-aware values to `Asia/Shanghai`. Treat timezone-naive values as `Asia/Shanghai` and disclose that assumption.
- Prefer plain string message bodies. For structured bodies, retain the original object during analysis and extract readable text through its structured fields rather than fragile string slicing.
- Store the original role, normalized role, timestamp, session key, message key, internal speaker key, display-name candidates, and normalized text.

Normalize roles as follows:

- `user`, `human`, and equivalent roles count as human messages.
- `assistant`, `bot`, and equivalent roles count as assistant messages.
- `system`, `tool`, metadata, and equivalent records remain separate and never contribute to human activity or topic initiators.
- Put unresolved roles in `unknown_role_messages` and manually sample them before making a decision. Never classify an unknown role as human by default.

## Deduplication and Discussion Units

One unique session is one discussion unit.

Deduplicate in this order:

1. Prefer stable message IDs.
2. When a message ID is absent, hash `session_key + timestamp + sender_key + normalized_text`.
3. Keep only one copy of duplicate session exports, preferring the file with more complete messages and broader time coverage.
4. If only part of a file falls inside a window, count only the in-window messages. Adjacent out-of-window messages may be read for context but must not be counted.
5. Merge files for the same issue only when they share a root key, reply chain, or other strong duplicate evidence. Text similarity alone is insufficient.

An effective discussion contains a real technical question, failure, design comparison, usage feedback, or actionable answer. Pure sharing, status updates, greetings, bot tests, and content-free link forwarding may remain in total message counts but do not count as effective discussions.

## Resolve and Mask Identities

Display names may come only from the session files:

1. An explicit display-name field on the message.
2. A stable `[Name]:` or `[Name]：` prefix repeatedly associated with the same internal identity.
3. If neither is available, use `Name unavailable`. Never guess a name or display an internal identity key.

When one identity has multiple display-name candidates, review frequency, temporal continuity, and context. Keep identities separate when the evidence is insufficient.

Masking rules:

- One character: display `*`.
- Two characters: preserve the first character, for example `张三 -> 张*`.
- Three or more characters: preserve the first and last characters and replace every middle character with `*`, for example `秦浩杰 -> 秦*杰`.
- Apply the same character-based rule to Latin, numeric, and mixed aliases, for example `darren -> d****n` and `Lin101 -> L****1`.
- User-declared sensitive names override the general rule and preserve only the first character, for example `胡江涛 -> 胡**`.
- In Markdown prose, escape masking asterisks when they appear inside emphasis, for example `Q\*n`; keep the raw value as `Q*n` in structured JSON.

The final Markdown and structured JSON must not contain complete human names, email addresses, phone numbers, raw identity keys, Authorization values, API keys, root keys, tenant keys, or long credential-like strings.

## Metrics

Use the same code and definitions for both windows. At minimum, compute:

- `discussion_units`: Unique sessions.
- `effective_discussions`: Effective discussion units.
- `participants`: Unique human identities in the window.
- `human_messages`: Human messages.
- `assistant_messages`: Assistant replies.
- `system_messages`: System and tool messages.
- `unknown_role_messages`: Messages whose roles remain unresolved.
- `total_messages`: All counted messages.

The following invariant must hold:

```text
human_messages + assistant_messages + system_messages + unknown_role_messages = total_messages
```

Participant counts, Top 10 users, and keywords must use human messages only. Assistant messages may help interpret the Q&A context or determine whether a question received a response, but they must not inflate human rankings.

## Topics and Keywords

Use stable topic buckets across weeks. Recommended baseline buckets:

- Storage, Indexing, and Retrieval
- Bot, Agent, and Skill Applications
- Deployment, Runtime, and Environment Configuration
- Memory, Compression, and Archiving
- Models, Parsing Quality, and Cost
- Permissions, Multi-Tenancy, and Collaboration
- SDKs, APIs, and Tooling
- Documentation, Examples, and Usage Questions
- Other

Classification rules:

- Read the entire session before assigning multi-label topics.
- A discussion unit may match multiple topics, so topic counts do not have to sum to total discussion units.
- For each topic, compute discussion units, participants, human messages, keywords, and one concise insight.
- Rank topics by discussion units, then human messages, participants, and the stable topic order.
- Normalize keyword case, width variants, singular/plural forms, and common aliases before calculating the Top 20.
- Remove stop words, greetings, template text, masked names, and path fragments that carry no topical meaning.
- Preserve recognizable technical spellings such as `Memory`, `add_resource`, `VLM`, and `MCP`.

Manually sample sessions behind popular topics and keywords to catch misclassification, log noise, or frequency inflation caused by assistant repetition.

## Open High-Priority Issues

Include only high-priority issues that remain unresolved at the data cutoff, with a default maximum of four. Here, unresolved means that the available sessions contain no complete chain of an explicit fix, usable release, existing-instance recovery, and user-side verification before the cutoff.

A candidate must satisfy at least one of these conditions:

- Multiple independent humans report the same issue.
- There are concrete logs, a failure ratio, or reproducible steps, plus maintainer confirmation.
- The issue affects a core path such as Session Commit, resource ingestion, semantic indexing, permission isolation, or data integrity.

Exclude:

- Issues fixed and verified by users within the available data window.
- One-off errors without context, routine configuration questions, or hypotheses found only in assistant replies.
- Security tests, casual chat, bot-to-bot exchanges, or unsupported root-cause claims.

Every issue must contain these six fields:

1. `Priority / Category`
2. `Background`
3. `User Impact`
4. `Evidence`
5. `Current Assessment`
6. `Community Feedback`

Immediately follow each issue with its own `Human Feedback Excerpt`. Include human messages only, omit assistant replies, and omit internal links:

```markdown
> **08-31 21:16 | O\*\*\*r**
> Session Commit has failed repeatedly...
>
> **08-31 21:19 | 秦\*杰**
> The current fix direction is still incorrect and needs another revision.
```

Every excerpt must come from sessions associated with that issue and preserve the timestamp, masked name, and essential meaning. Long logs may be shortened and unrelated text may be omitted, but never invent dialogue or mix messages from another issue.

## Releases This Week

Extract only explicit completed-release signals from the sessions: version, confirmation time, major changes, fixes, upgrade warnings, and related discussion.

- Count only statements such as "released," "deployed," or "the new version is available."
- Treat "preparing a release," "expected," and "next version" as plans, not completed releases.
- When messages conflict, prefer the later statement backed by user verification, and preserve uncertainty in the evidence note.
- If evidence is insufficient, keep the section and write: "No formal release could be confirmed from this week's sessions."
- Pass through only URLs explicitly present in the sessions and valid in basic format. Do not browse or fabricate links.

## Week-over-Week Comparison

Compare the scale first, then the content:

1. Previous and current values, absolute changes, and percentage changes for discussion units, effective discussions, participants, human messages, assistant messages, and total messages.
2. Previous and current values plus percentage changes for every stable topic bucket.
3. Changes in top keywords and top active users.
4. Previous focus, current focus, and interpretation of each meaningful shift.
5. One overall conclusion that distinguishes activity-volume changes from discussion-content changes.

Percentage-change formula:

```text
(current - previous) / previous * 100
```

When the previous value is zero and the current value is positive, display `New`. When both are zero, display `0.0%`. When the previous value is unknown, display `Not comparable`; never substitute zero for unknown.

Focus shifts must come from human review of high-frequency session digests from both windows. Do not infer them from keyword deltas alone.

## Weekly Activity

Include:

- Participants, human messages, assistant messages, system messages, and total messages.
- Daily human, assistant, and total message counts across the seven-day window.
- Human message counts for every hour from 00 through 23.
- Peak hour, second-highest hour, and peak day.
- Top 10 active humans with rank, masked display name, message count, discussion-unit count, and core keywords.
- Optional answer coverage and response latency. Compute these only when message ordering and reply relationships are reliable, and label them as heuristic metrics.

Render the peak-time series as Mermaid while retaining the full 24-hour table:

```mermaid
xychart-beta
    title "Hourly Human Message Distribution (CST)"
    x-axis ["00","01","02","03","04","05","06","07","08","09","10","11","12","13","14","15","16","17","18","19","20","21","22","23"]
    y-axis "Messages" 0 --> MAX
    line [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
```

Replace `MAX` and all 24 data points. If Mermaid cannot render, the full data table must still preserve the result.

## Trends and Recommendations

Produce three to six actionable recommendations. Each recommendation must include:

- The observed fact or change.
- Its impact on Q&A efficiency, product experience, or maintenance work.
- A concrete action involving a fix, documentation, monitoring, release, regression test, or communication.

Prioritize unresolved P1 issues, repeated high-frequency questions, post-release compatibility warnings, activity changes, and data-quality gaps. Avoid empty language such as "continue monitoring" or "optimize further" without a named action.

## Document and Whiteboard Data Requirements

This skill does not create online documents, but its Markdown output must directly support the current weekly-report layout.

### Summary Dashboard

The structured data must be sufficient to generate:

- Four KPIs: discussion units, effective discussions, participants, and total messages.
- Top 6 weekly topics with discussion-unit bars.
- Up to four unresolved P1 cards.
- A 24-hour human-message line chart and peak-day annotation.
- Top 10 active community members.
- Top 20 core keywords. A compact board may show the first 12 while the Markdown retains all 20.

### Comparison Dashboard

The structured data must be sufficient to generate:

- Three previous-to-current metric cards: discussion units, participants, and total messages.
- Paired bars for the Top 6 stable topic buckets.
- A focus-shift list.
- An overall conclusion covering both activity volume and discussion content.

### Document Body

- Build the executive summary from structured metrics and conclusions.
- Build the release section from explicit completed-release evidence found in sessions.
- Render every P1 with six fields and an independent simulated human-chat block.
- Put all six comparison metrics in the body while keeping only three core metrics on the board.
- Keep independent fields for recommendations, methodology notes, and previous-report links.

No important number may exist only in narrative prose while being absent from the structured data.

## `qa-report.v3` Data Contract

The Markdown must end with one `qa-report.v3` JSON object. The prose is for reading; the JSON is the authoritative source for downstream document and whiteboard rendering.

Rules:

- The JSON must be valid and contain no comments, trailing commas, `NaN`, ellipses, or placeholders.
- Wrap it between `<!-- QA_REPORT_DATA_START -->` and `<!-- QA_REPORT_DATA_END -->`, and make it the final fenced JSON block in the document.
- Use ISO 8601 timestamps with `+08:00`, `YYYY-MM-DD` dates, and integer hours from 0 through 23.
- Store counts as integers and percentages as numbers. Use `null` for unknown values; never substitute zero.
- `daily` must cover every date slice in the current window. `hourly` must contain exactly 24 entries, including zeros for inactive hours.
- Mask all human display names before writing JSON. Do not store name mappings or internal identity keys.
- Every numeric value in the prose must be directly readable from this object or reproducible through simple arithmetic, sorting, or the percentage-change formula.

Required fields:

```json
{
  "schema_version": "qa-report.v3",
  "report": {
    "title": "OpenViking Community Q&A Weekly Report: YYYY-MM-DD to YYYY-MM-DD",
    "generated_at": "YYYY-MM-DDTHH:mm:ss+08:00",
    "timezone": "Asia/Shanghai",
    "window": {
      "current": {"start": "YYYY-MM-DDTHH:mm:ss+08:00", "end": "YYYY-MM-DDTHH:mm:ss+08:00"},
      "previous": {"start": "YYYY-MM-DDTHH:mm:ss+08:00", "end": "YYYY-MM-DDTHH:mm:ss+08:00"}
    },
    "coverage": {
      "sessions_root": "user-supplied local path",
      "discovered_files": 0,
      "included_files": 0,
      "excluded_files": 0,
      "included_sessions": 0,
      "earliest_message": null,
      "latest_message": null,
      "bad_lines": 0,
      "missing_timestamps": 0,
      "complete": true,
      "notes": []
    }
  },
  "summary": {
    "headline": "One overall conclusion",
    "top_topic_names": [],
    "peak_hour": 0,
    "peak_hour_messages": 0,
    "peak_day": "YYYY-MM-DD",
    "peak_day_messages": 0,
    "open_p1_count": 0,
    "release_versions": []
  },
  "presentation": {
    "section_order": [
      "Executive Summary",
      "Releases This Week",
      "Weekly Topic Ranking",
      "Open High-Priority Issues",
      "Week-over-Week Comparison",
      "Weekly Activity",
      "Trends and Recommendations",
      "Methodology and Data Notes",
      "Previous Reports"
    ],
    "summary_dashboard": {
      "kpi_metric_order": ["discussion_units", "effective_discussions", "participants", "total_messages"],
      "topic_limit": 6,
      "anomaly_limit": 4,
      "hourly_series": "human_messages",
      "top_user_limit": 10,
      "keyword_limit": 20,
      "compact_keyword_limit": 12
    },
    "comparison_dashboard": {
      "metric_order": ["discussion_units", "participants", "total_messages"],
      "show_topic_change": true,
      "show_focus_shift": true
    }
  },
  "metrics": {
    "current": {
      "discussion_units": 0,
      "effective_discussions": 0,
      "participants": 0,
      "human_messages": 0,
      "assistant_messages": 0,
      "system_messages": 0,
      "unknown_role_messages": 0,
      "total_messages": 0
    },
    "previous": {
      "discussion_units": 0,
      "effective_discussions": 0,
      "participants": 0,
      "human_messages": 0,
      "assistant_messages": 0,
      "system_messages": 0,
      "unknown_role_messages": 0,
      "total_messages": 0
    }
  },
  "topics": [
    {
      "rank": 1,
      "name": "Topic name",
      "current": {"discussion_units": 0, "participants": 0, "human_messages": 0},
      "previous": {"discussion_units": 0, "participants": 0, "human_messages": 0},
      "delta_pct": 0.0,
      "keywords": [],
      "insight": ""
    }
  ],
  "keywords": [
    {"rank": 1, "keyword": "Memory", "current_count": 0, "previous_count": 0, "delta_pct": 0.0}
  ],
  "activity": {
    "daily": [
      {"date": "YYYY-MM-DD", "human_messages": 0, "assistant_messages": 0, "total_messages": 0}
    ],
    "hourly": [
      {"hour": 0, "human_messages": 0}
    ],
    "peaks": {
      "top_hours": [{"hour": 0, "messages": 0}],
      "top_day": {"date": "YYYY-MM-DD", "messages": 0}
    },
    "top_users": [
      {"rank": 1, "display_name": "Q*n", "messages": 0, "discussion_units": 0, "keywords": []}
    ],
    "response_metrics": {
      "available": false,
      "answered_discussions": null,
      "answer_rate_pct": null,
      "median_latency_seconds": null,
      "p90_latency_seconds": null,
      "method": ""
    }
  },
  "releases": [
    {
      "version": "vX.Y.Z",
      "published_at": "YYYY-MM-DDTHH:mm:ss+08:00",
      "type": "main|hotfix|component",
      "url": null,
      "highlights": [],
      "reliability_fixes": [],
      "breaking_changes": [],
      "related_topics": [],
      "evidence": []
    }
  ],
  "anomalies": [
    {
      "rank": 1,
      "title": "Issue title",
      "short_label": "Short label for the dashboard risk card",
      "priority": "P1",
      "category": "Category",
      "background": "",
      "user_impact": "",
      "evidence": "",
      "current_assessment": "",
      "community_feedback": "",
      "unresolved_at_cutoff": true,
      "chat": [
        {"time": "MM-DD HH:mm", "speaker": "Q*n", "message": "Masked human message"}
      ]
    }
  ],
  "comparison": {
    "available": true,
    "metric_conclusion": "",
    "topic_conclusion": "",
    "focus_shifts": [
      {"previous_focus": "", "current_focus": "", "interpretation": ""}
    ],
    "overall_conclusion": ""
  },
  "recommendations": [
    {"rank": 1, "observation": "", "impact": "", "action": ""}
  ],
  "history": [
    {"title": "Previous report title", "url_or_path": "verified link or relative path"}
  ]
}
```

When previous-period data is unavailable, set `metrics.previous` to `null`; set each topic and keyword `previous`/`delta_pct` value to `null`; set `comparison.available=false`; and explain the reason in the prose. Never preserve a fabricated all-zero previous period.

## Markdown Template

````markdown
---
report_schema: qa-report.v3
report_type: openviking_qa_weekly
timezone: Asia/Shanghai
current_start: YYYY-MM-DDTHH:mm:ss+08:00
current_end: YYYY-MM-DDTHH:mm:ss+08:00
previous_start: YYYY-MM-DDTHH:mm:ss+08:00
previous_end: YYYY-MM-DDTHH:mm:ss+08:00
generated_at: YYYY-MM-DDTHH:mm:ss+08:00
---

# OpenViking Community Q&A Weekly Report: YYYY-MM-DD to YYYY-MM-DD

## Executive Summary

- Reporting window: ...
- Weekly volume: ...
- Participation: ...
- Leading topics: ...
- Peak period: ...
- Main conclusion: ...

## Releases This Week

| Version | Time | Type | Highlights | Upgrade Notes | Session Evidence |
| --- | --- | --- | --- | --- | --- |

## Weekly Topic Ranking

| Rank | Topic | Discussion Units | Participants | Human Messages | Keywords | Insight |
| ---: | --- | ---: | ---: | ---: | --- | --- |

### Top 20 Keywords

| Rank | Keyword | Current | Previous | Change |
| ---: | --- | ---: | ---: | ---: |

## Open High-Priority Issues

### 1. Issue Title

- **Priority / Category:** P1 / ...
- **Background:** ...
- **User Impact:** ...
- **Evidence:** ...
- **Current Assessment:** ...
- **Community Feedback:** ...

#### Human Feedback Excerpt

> **Time | Masked Name**
> Message...

## Week-over-Week Comparison

### Core Metrics

| Metric | Previous | Current | Absolute Change | Percentage Change |
| --- | ---: | ---: | ---: | ---: |

### Topic Heat Changes

| Topic | Previous | Current | Change | Interpretation |
| --- | ---: | ---: | ---: | --- |

### Focus and Discussion Shifts

| Previous Focus | Current Focus | Interpretation |
| --- | --- | --- |

## Weekly Activity

### Activity Overview
### Daily Distribution
### Peak-Time Line Chart
### 24-Hour Data
### Top 10 Active Community Members

## Trends and Recommendations

1. ...

## Methodology and Data Notes

- Data directory, discovered files, and included scope.
- Both time windows, timezone, and actual data coverage.
- Deduplication, roles, discussion units, effective discussions, and multi-label topic definitions.
- Malformed lines, missing timestamps, unknown roles, and coverage gaps.
- Display-name resolution and masking rules.

## Previous Reports

- [OpenViking Community Q&A Weekly Report: YYYY-MM-DD to YYYY-MM-DD](verified link or path)

## Appendix: Structured Report Data

<!-- QA_REPORT_DATA_START -->
```json
{complete qa-report.v3 object}
```
<!-- QA_REPORT_DATA_END -->
````

## End-to-End Procedure

1. Scan `sessions_root` and identify the target session set from directories, filename prefixes, and metadata.
2. Parse every JSONL line and record malformed rows, missing timestamps, unknown roles, and actual data coverage.
3. Determine consecutive, equal-duration current and previous windows. Read `previous_report` when raw previous-window data is absent.
4. Normalize message fields, roles, and timestamps, then deduplicate messages and duplicate session exports.
5. Resolve display names only from session contents, apply masking, and scan for sensitive information.
6. Compute discussion units, effective discussions, participants, and message metrics for both windows.
7. Build a digest for every session, classify it with stable topic buckets, and manually sample the highest-volume topics.
8. Compute Top 20 keywords, daily and hourly series, peaks, and the Top 10 active humans.
9. Follow each candidate issue through later messages and retain only P1 issues with no closure evidence before the cutoff.
10. Extract explicit completed-release signals from sessions. Do not count planned releases.
11. Compare core metrics, topics, keywords, and focus areas, then write actionable recommendations.
12. Assemble the complete `qa-report.v3` object first, and render both Markdown prose and Mermaid from that same object to prevent numeric drift.
13. Reparse the final JSON and validate tables, formulas, ordering, the 24-hour series, masking, and all document/whiteboard fields.

## Acceptance Checklist

- The final deliverable is one self-contained `.md` file that remains understandable without temporary scripts or caches.
- All analytical data comes from the target sessions and optional previous Markdown report; no other message source is mixed in.
- Frontmatter declares `report_schema: qa-report.v3`, and the final JSON parses successfully.
- The title dates match the current window; current and previous windows are equal, consecutive, and non-overlapping.
- Discovered, included, and excluded file counts, malformed rows, and data coverage are recorded.
- Message roles and totals reconcile, and unknown roles are not silently treated as human.
- The weekly ranking includes discussion units, participants, human messages, keywords, and insights.
- Weekly activity contains the daily table, complete 24-hour table, line chart, peaks, and Top 10.
- The comparison covers scale, topics, and focus shifts; unavailable history is explicitly marked as not comparable.
- Releases include only completed states confirmed in sessions; insufficient evidence is stated clearly.
- Open issues contain only P1 items with no closure evidence before the cutoff, and each has all six fields plus its own human excerpt.
- Human excerpts contain no assistant replies, invented messages, or internal links.
- Every human name is masked; no complete name, internal identity key, or credential appears in the report.
- `activity.hourly` is ordered from 0 through 23 with exactly 24 entries; `daily` covers every current-window date slice.
- Top 10, Top 20, topic rankings, and all percentage changes are reproducible.
- The structured data is sufficient to generate the summary dashboard, comparison dashboard, P1 body sections, and simulated chat modules directly.
- Previous-report entries pass through only verified links or paths; never fabricate historical URLs.
