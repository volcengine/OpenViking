# Conflict comparison and final-page contract

## Detect and handle conflicts

### Compare sources before drafting

Compare material claims **between input sources**, within each source, against related
existing Wiki pages, and across this compile's output. This applies on the first compile
even when no Wiki exists. Align subject, attribute, scope, conditions, version, period,
environment, units, and measurement definitions before judging incompatibility.

Check values, dates, negation, states, responsibilities, procedures, relationships,
identity/unit mistakes, unsupported citations, overgeneralized scope, opinions or
inferences written as facts, and contradictions among body, tables, summaries, and index.
Do not record a conflict when:
- The claims apply to different versions, periods, environments, or populations.
- One source explicitly replaces another, or the conflicting content is explicitly
  withdrawn or marked as draft.
- The claims are complementary or express opinions that can coexist.
- A discussion has an applicable final decision.
- The difference comes from an example, hypothesis, external practice,
  or personal preference rather than a claim about the subject.
- Different wording, units, or measurement definitions express the same underlying claim.
Attribution alone does not remove a contradiction between requirements for the same subject and conditions.
Style preferences are not conflicts. Comments and repair history are metadata, not
evidence that an old problem still exists.

For each material source disagreement, write a record on the page that carries the
affected claim, even if the prose accurately attributes both sides.
In `reason`, identify the conflicting claims and their known locations, which may
be in the same source. State any gaps in the evidence.
Do not silently choose a side, omit a relevant opposing claim, or replace the record
with only "sources disagree." Do not manufacture an incorrect Wiki sentence to justify
a record. Recency, repetition, and model familiarity alone do not establish authority;
use directly applicable specifications, versions, or explicit precedence. Do not use
outside searches or assumptions to settle the dispute.

### Conflict types

Assign one conflict_type to each conflict. Check these definitions in order
and use the first matching type:

- human_conclusion: A source contradicts a conclusion recorded as manually resolved.
- opinion: Sources propose different answers to a question that requires a decision,
  and no applicable decision is available.
- fact: Sources give incompatible facts or rules about the same subject under
  the same conditions, including values, dates, states, requirements, and responsibilities.

### Severity

Severity describes the consequence of acting on the wrong claim.
It determines review priority, not whether the conflict is automatically repaired.
Judge the worst plausible consequence and use the highest applicable level.

| severity | Criteria |
| --- | --- |
| high | Affects external commitments, safety, permissions, privacy, compliance, money, irreversible operations, or blocks other people's work. |
| medium | Affects internal decisions or execution and can cause recoverable costs, including rework, duplicated effort, inconsistent practices, or scheduling changes. |
| low | Affects understanding but normally does not drive action, such as historical or background information. |

A clearer description does not resolve a source disagreement.
Mark a factual conflict fixed only when the supplied evidence supports the chosen answer.

Apply a verified correction wherever the same claim appears in this compile's output.
Record an unresolved conflict on every affected output page.
Preserve unrelated content and protected human conclusions.

### Handle conflicts by type

- human_conclusion: Keep the human conclusion and its resolution record unchanged.
  Add a separate unfixed record anchored to the human conclusion.
  Describe the conflicting source in that record; do not add its claim to the page body.

- opinion: Do not choose a proposal automatically.
  Present the proposals with attribution, state that the decision is unresolved,
  and record the conflict as unfixed.

- fact: If the supplied evidence establishes which claim applies, update the Wiki
  and record the conflict as fixed. Explain the supporting evidence in resolution_basis.
  Otherwise, record it as unfixed. On a new page, present both claims and state that
  the answer is unresolved. On an existing page, preserve the affected sentence.

### Current results and human resolutions

- On each updated page, recheck existing conflict records against the evidence
  available for each claim. Preserve human resolutions as specified below. If the
  same issue remains unresolved, keep its `unfixed` state and original `begin_time`.
  A source omitted from an incremental batch does not establish resolution. Remove
  an obsolete conflict record when evidence shows the issue no longer applies.
  Keep automatic repairs verified in this compile and drop automatic `fixed`
  records from earlier runs. Leave pages outside this update untouched.
- Recognize human resolutions by the exact `fix_plan` value in the field table below.
  Preserve their fields, original timestamps, severity, and `match_text` spans
  verbatim. Keep retained human-resolution records fixed and preserve their protected text.
  If a source contradicts that text, add a separate unfixed human_conclusion record. Human protection takes precedence
  over automatic repairs. Do not invent a human resolution or its timestamps.
- The two fixed Chinese human-resolution values stay exact in every output language.
  If a human record cannot be parsed or its protected text cannot be located,
  report the issue and leave that page unchanged; do not discard it or invent an anchor.
- Merge current ordinary records and retained human resolutions into one trailing
  block, deduplicated by affected claim, issue, and evidence. Preserve distinct issues.
  Retained human records do not count as unresolved conflicts.

## Conflict record format

Each new conflict record has exactly these eleven fields.
Preserve retained human-resolution records verbatim, even if they lack the new fields.
Keep keys and enum values unchanged;
write descriptive values in the output language, except the fixed Chinese values.

| Field | Type and meaning |
| --- | --- |
| `match_text` | One complete sentence copied verbatim from the final Wiki page body that identifies the affected claim. Exclude frontmatter and HTML comments. For a repaired claim, use the corrected sentence. |
| `problem_description` | Describe the specific issue. When sources conflict, state what each source says and where their claims disagree. For a repaired Wiki claim, quote the original wording and explain what was wrong. |
| `reason` | Non-empty explanation of the conflicting claims or evidence gap, actual source locations, and why their scopes conflict. |
| `severity` | Exactly `high`, `medium`, or `low`. |
| `state` | `fixed` when resolved, `unfixed` when unresolved; never infer it solely from severity. |
| `begin_time` | Integer Unix seconds when the issue is first detected, not a source date or estimated origin time. |
| `fix_plan` | Automatic repair: non-empty actual correction and scope. Human resolution: exactly `用户手动解决内容冲突，修正后的内容如正文所示`. Unresolved: `""`. |
| `fix_time` | For `fixed`, integer Unix seconds after repair and verification, no earlier than `begin_time`; for `unfixed`, `null`. Retained human times do not change. |
| `resolution_basis` | Automatic repair: non-empty evidence location, supporting content, and applicability. Human resolution: exactly `人工修复`. Unresolved: `""`. |
| `conflict_type` | Exactly human_conclusion, opinion, or fact, as defined above. |
| `resolved_by` | Automatic repair: "compile_agent". Unfixed: "". For a retained human resolution, preserve its existing value. |

Read real time from the execution environment at detection and after verification
(for example, `date +%s`); never estimate, copy example timestamps, use milliseconds,
or serialize times as strings.

After finalizing the Wiki page body, copy the complete sentence that addresses
the affected claim verbatim into `match_text`, including its punctuation.
Exclude frontmatter and HTML comments. For a repaired claim, use the corrected
sentence. If the claim appears only in a table, code block, or frontmatter,
explain it in a body sentence and use that sentence as `match_text`. For automatic factual repairs,
also correct any affected frontmatter. Preserve the
original `match_text` of retained human resolutions verbatim.

End every new/updated file, including `index.md`, after its body and Sources section with
**exactly one** `<!-- CONFLICTS_CONTENT ... -->` comment. Do not create a conflict page or
visible conflict section. Its payload is comma-separated JSON objects **without an
outer array or wrapper** and without a final trailing comma.
Use JSON escaping and Unicode-escape embedded comment delimiters or consecutive hyphens
so strings cannot nest or terminate the comment; decoded anchors must still match.

When there are no current records or retained human resolutions, output:

```markdown
<!-- CONFLICTS_CONTENT
-->
```

For a first compile, the following fictional same-version sources disagree about access
control. The final body contains this complete, attributed sentence:

```markdown
The access guide permits anonymous exports, but the security policy requires authentication for the same production endpoint; the applicable rule is unresolved.
```

Its unresolved record is below. Paths and times are illustrative; actual output uses
real evidence and event times.

```markdown
<!-- CONFLICTS_CONTENT
{
  "match_text": "The access guide permits anonymous exports, but the security policy requires authentication for the same production endpoint; the applicable rule is unresolved.",
  "problem_description": "The access guide allows anonymous exports from the production endpoint, while the security policy requires users to authenticate before exporting from it.",
  "reason": "The Export access section of sources/access.md permits anonymous production exports in v2; the same-version Export authentication section of sources/security.md requires authentication. Scope and effective period coincide, with no established precedence.",
  "severity": "high",
  "state": "unfixed",
  "begin_time": 1792500000,
  "fix_plan": "",
  "fix_time": null,
  "resolution_basis": "",
  "conflict_type": "fact",
  "resolved_by": ""
}
-->
```

## Before saving

- Wiki quality: Check that each new or updated page covers a clear subject,
  uses the appropriate page type, and has valid frontmatter with an H1 matching
  its title.
- Index coverage: Verify that `index.md` lists every active knowledge page with
  its canonical link and a one-line description of what readers can find there.
- Conflict records: Check all eleven fields in newly generated records against the schema and verify the
  cited evidence. For each new record, confirm that the decoded `match_text`
  exactly matches one complete sentence about the affected claim in the final
  Wiki page body, excluding frontmatter and HTML comments. When sources
  disagree, verify that `problem_description` identifies the specific point
  of disagreement. On updates, preserve existing human resolutions and their
  protected text verbatim.
