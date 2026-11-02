# Evaluation

Choose a small task you understand, record expected results, and check actual behavior with fixed inputs. Download the [evaluation CSV](/quickstart/evaluation-v1.csv) for initial trials, configuration changes, and upgrade checks.

The v1 samples were verified on isolated local services with OpenViking, CLI, and VikingBot 0.5.2. See the [verification record](/quickstart/verification-v1.txt) for models, date, executed scope, observed issues, and unverified cases.

## Fix the conditions

Record server, CLI, agent or plugin versions, models, deployment mode, identity, and project scope. Preserve inputs and expected sources in isolated demo directories. For comparisons, keep questions, sources, and models consistent where possible, noting differences.

Start with the four files in [Resource retrieval](08-resources.md), then substitute your own sources. Record preparation time, time to the first useful result, help requests, and pages left along the way to assess onboarding usability.

## Check retrieval with your sources

| Case | Question type | Check |
| --- | --- | --- |
| R1 | Direct answer in one source | Accurate answer with an accessible citation |
| R2 | Evidence across sources | Complete evidence and faithful conclusions |
| R3 | No answer in the sources | Missing information clearly identified |

Record import tasks, retrieval results, read URIs, final answers, and citations. For failures, identify the earliest stage that diverged and inspect its logs.

## Check memory across two sessions

Follow [Memory use](09-memory.md) to save a test convention, then start a new session with the same identity. Ask for the task without repeating the convention. Record the artifact, recall evidence, and how the answer uses it. Add an unrelated question to check relevance.

| Case | Task | Check |
| --- | --- | --- |
| M1 | A new session needs prior background | Saved conventions are applied |
| M2 | A new session is unrelated | The answer stays focused on the current question |

## Inspect Skill and compiled output

[Skill use](10-skills.md) compares two inputs for output structure, fact preservation, and unknowns, with evidence that the agent read the Skill. For [Context compilation](13-compile.md), open at least one body page and check citations, omissions, and unsupported claims.

Use [Version management](14-versions.md) and [Import and export](15-transfer.md) to check restore scope, file content, metadata, and retrieval readiness.

## Fill in the record

| Field | What to record |
| --- | --- |
| Question and input | Complete prompt, source version, or test session |
| Expected facts and sources | Known answers and evidence URIs, recorded before execution |
| Actual answer and sources | Original response and citations |
| Stage and outcome | Import, processing, retrieval, reading, answering, capture, or recall; passed, failed, or unverified |
| Duration | Actual time from submission to a verifiable result |
| Environment and notes | Versions, models, identity, configuration differences, and manual revisions |

Sample rows in the sheet are blank for your results. Define quality criteria beforehand, such as evidence for every key fact, explicit unknowns, and whether the output supports the intended task.

## Diagnose and repeat

For missing files, inspect import. For empty search with existing files, inspect indexing and scope. For poor answers with correct matches, inspect agent reads and generation. For memory, follow capture, extraction, recall, and use in order. See [Observability and troubleshooting](../guides/05-observability.md).

Keep each evaluation and repeat the same tasks after changes. Technical validation records describe what was checked. Whether users can finish independently, explain the evidence, and substitute their own inputs is assessed during review and user trials.

<a id="check-context-compilation-with-one-output"></a>
<a id="choose-a-path-after-getting-a-result"></a>
<a id="evaluate-on-your-own-tasks"></a>

<!-- Keep incoming links to previous evaluation headings working. -->
<a id="evaluate-openviking-on-your-tasks"></a>
<a id="check-retrieval-with-your-own-sources"></a>
<a id="check-one-compiled-output"></a>
<a id="choose-what-to-do-next"></a>
