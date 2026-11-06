# Resource retrieval

Project descriptions, meeting notes, and feedback procedures often live in separate documents. After importing them into OpenViking, you can search by meaning, then have an agent read the originals, combine the information, and cite its sources.

This guide uses four fictional Lighthouse project files to walk through **importing → searching → reading originals → generating answers → verifying sources**. You will check questions answered by a single source, questions requiring multiple sources, and questions the sources leave unanswered.

## Prerequisites

Before you begin, prepare the following:

- **OpenViking service and CLI**: Configure your connection using the [quickstart](02-quickstart.md). Your account needs permission to import and read files in the target directory.
- **VikingBot**: This guide uses `ov chat` to generate answers. Enable VikingBot and configure its model using the [VikingBot guide](11-vikingbot.md). The CLI and VikingBot should connect to the same service and have access to the same sources.
- **Model configuration**: Processing sources, semantic retrieval, and answer generation use your configured models and incur model usage costs.

If you use your own agent, connect it to the service as described in the [quickstart](02-quickstart.md), then use the questions and verification criteria below.

## Prepare the sources

Download the [Lighthouse source pack](/quickstart/lighthouse-en-v1.zip), open a terminal in the download directory, and extract it:

```bash
unzip lighthouse-en-v1.zip
```

The resulting `lighthouse-en/` directory contains these files:

| File | Content |
| --- | --- |
| `project.md` | Network conditions, offline installer, and local storage preparation |
| `feedback.md` | Feedback export schedule, responsibility, and handoff |
| `meeting.md` | Feedback registration template, export rehearsal, and open questions |
| `limits.md` | The scope of the sources and information they do not provide |

## Import the sources

Import the directory into a dedicated demo path. The `--wait` option waits for processing to finish before you search. For a repeat run, replace `onboarding-en-v1` with a new directory name and update the URIs in subsequent commands.

```bash
ov add-resource ./lighthouse-en --to viking://resources/onboarding-en-v1 --wait
```

After processing succeeds, inspect the imported files:

```bash
ov tree viking://resources/onboarding-en-v1
```

Each Markdown file in this sample is processed into a directory with the same base name. Check that the following files are present before searching:

```text
viking://resources/onboarding-en-v1/
├── project/project.md
├── feedback/feedback.md
├── meeting/meeting.md
└── limits/limits.md
```

If waiting times out, use the returned task ID with `ov task status <task_id>` to check progress. Continue when the task completes. If it fails, use the error message to check model or connection settings. See [Task management](../api/17-tasks.md) for task status details.

<a id="retrieve-and-answer"></a>

## Search the sources

Start with a question that requires both the project description and meeting notes. Limit the search to the directory you imported:

```bash
ov find "How does Lighthouse collect feedback, and what must be ready before the pilot?" --uri viking://resources/onboarding-en-v1
```

The results list matching content and its URIs. Check whether the matches cover feedback collection and pilot preparation, then read the relevant originals. `ov find` locates relevant sources; reading them lets you check the supporting details.

## Read the originals

Open the source files using their URIs from the search results. For this sample, the relevant files include the project description, meeting notes, and feedback procedure:

```bash
ov read viking://resources/onboarding-en-v1/project/project.md
ov read viking://resources/onboarding-en-v1/meeting/meeting.md
ov read viking://resources/onboarding-en-v1/feedback/feedback.md
```

Notice that the project description specifies the installer and local directory, while the meeting notes specify the registration template and export rehearsal. A complete answer about pilot preparation needs both sources.

## Generate answers

Have VikingBot search, read, and answer in a new conversation. The following prompt asks three questions and requests citations and explicit handling of unknown information. When repeating the walkthrough, use a new session name.

```bash
ov chat --session lighthouse-resources-v1 -m "Use only viking://resources/onboarding-en-v1. Run semantic searches, read relevant originals, then answer: Who exports feedback and when? What must be ready before the pilot? When does the pilot end and what is its budget? Cite full source URIs for each conclusion and mark unspecified information as undecided."
```

The answer should cover the feedback export arrangements, pilot preparation, and open questions, with full `viking://` source URIs supporting its conclusions.

## Verify the sources

Open the cited files with `ov read` and compare the answer with the table below. Wording can vary; facts and citations should match the originals.

| Question type | Question | Expected facts | Sources |
| --- | --- | --- | --- |
| Single source | Who exports feedback and when? | The pilot administrator exports it every Friday and passes it to the project owner | `feedback/feedback.md` |
| Multiple sources | What must be ready before the pilot? | Offline installer, local storage directory, registration template, and one offline export rehearsal | `project/project.md`, `meeting/meeting.md` |
| Unanswered | When does the pilot end, and what is its budget? | Both are undecided | `meeting/meeting.md` |

Use tool output from `ov chat` or Bot logs to check that the agent searched the relevant sources, read the files, and used their content in its answer. For the multi-source question, check for requirements omitted from either file. For undecided matters, check that the answer preserves the uncertainty in the source.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Files are missing from the imported directory | Confirm the extracted directory contains all four files, then inspect the import result and task errors |
| Search returns no relevant matches | Confirm processing is complete and the search URI matches the import directory, then try a more specific question |
| The CLI can read sources but the agent cannot | Check the service connection, accounts, and directory access permissions for both |
| Search finds relevant content but the answer omits it | Inspect the files the agent actually read, then ask it to read the missing source and complete the answer |
| The answer gives a specific end date or budget | Compare with the meeting notes and ask the agent to mark unsupported conclusions as undecided |

## Try your own sources

Choose a set of project documents you know well. Prepare single-source, multi-source, and unanswered questions, then repeat the search and verification steps. Use the record sheet in [Evaluation](06-evaluate.md) to compare retrieval, source reading, and answer quality. See [Resource management](../api/02-resources.md) for supported formats, import options, and incremental updates.

You can keep the demo directory for [Context compilation](13-compile.md) and [Import and export](15-transfer.md). To clean up later, confirm the directory URI in Studio and delete that demo directory.
