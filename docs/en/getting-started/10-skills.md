# Skill use

A Skill describes a reusable way to perform a task, including its steps, output format, and checks. For recurring tasks such as organizing feedback, writing reports, or reviewing code, an agent can read the Skill and apply its instructions to different inputs.

OpenViking stores and retrieves Skills; the agent reads the instructions and carries out the task. In this guide, you will add a feedback-brief Skill and use VikingBot to process two sets of feedback, then compare how the same workflow applies across conversations.

## Prerequisites

Before you begin, prepare the following:

- **OpenViking service and CLI**: Configure your connection using the [quickstart](02-quickstart.md).
- **VikingBot**: Enable VikingBot on the service and configure its model. See [VikingBot](11-vikingbot.md) for setup instructions. This guide uses `ov chat` to generate briefs, which incurs model usage costs.
- **Skill directory permissions**: This guide adds the Skill to the shared directory `viking://agent/skills/`. Your account needs write access to that directory. The CLI and VikingBot should connect to the same service with accounts that can access the Skill.

Download the [feedback-brief Skill package](/quickstart/feedback-brief-v1.zip) and open a terminal in the download directory. The package includes `SKILL.md` and sample inputs in English and Chinese.

## Add the sample Skill

Extract the package to create the `feedback-brief` directory:

```bash
unzip feedback-brief-v1.zip
```

Add the directory to OpenViking. The `--wait` option waits for Skill processing to finish so you can inspect the content next.

```bash
ov add-skill ./feedback-brief -p viking://agent/skills --wait
```

List the Skills and check that `feedback-brief` appears:

```bash
ov skills list
```

Read `SKILL.md` to see the instructions the agent will follow:

```bash
ov read viking://agent/skills/feedback-brief/SKILL.md
```

This Skill asks the agent to use only the supplied input and produce the following content in the input language:

| Content | Requirements |
| --- | --- |
| Facts | Preserve supplied facts and numbers, and cite input IDs |
| Actions | Use a table with action, owner, and evidence columns; distinguish suggestions from facts and mark missing owners as Unspecified |
| Open questions | List missing information and mark missing dates as Undecided |

The Skill asks the agent to return the brief in the conversation. After importing it, use a conversation to have the agent read and apply it. If you have already added this sample, check its content and continue.

<a id="try-the-first-input"></a>

## Generate a brief

The first input contains a duration, a suggested improvement, and an undecided date. Provide the feedback in a new conversation and specify the Skill path so VikingBot can follow its instructions:

```bash
ov chat --session feedback-skill-a -m "First discover and read viking://agent/skills/feedback-brief/SKILL.md. Follow that Skill for this input and return the result only in the conversation: Feedback A: Offline export takes 8 minutes. The pilot administrator suggests a progress indicator. The release date is undecided."
```

The brief should contain Facts, Actions, and Open questions. Compare it with the input:

- Facts preserve the 8-minute export duration and cite input ID A.
- Adding a progress indicator appears as a suggested action, with the owner marked Unspecified.
- The release date remains Undecided and appears under Open questions.

You can also check tool call records or service logs to confirm that VikingBot read `SKILL.md`. This helps verify that the brief was generated using the specified Skill.

## Reuse with another input

Provide a second input in another conversation to see whether the agent applies the same workflow. A new conversation keeps the previous feedback out of this task's conversation history. When repeating the walkthrough, replace the sample session names with new ones.

```bash
ov chat --session feedback-skill-b -m "First discover and read viking://agent/skills/feedback-brief/SKILL.md. Follow that Skill for this input and return the result only in the conversation: Feedback B: The registration template lacks an issue category field. There are 3 duplicate reports. No owner is specified."
```

This brief should preserve the missing category field and the 3 duplicate reports, cite input ID B, and mark the owner as Unspecified. Suggested actions might include adding the category field and merging duplicate reports, clearly labeled as suggestions based on the input.

Compare the briefs: their content should reflect the different inputs, while the three-section structure, action table, and evidence references remain consistent. See the [sample briefs and Skill read records](/quickstart/skill-output-en-v1.txt) for examples of the output format.

## Check the result

| Check | Expected result |
| --- | --- |
| Skill added | `feedback-brief` appears in the Skill list and `SKILL.md` is readable |
| Agent read the Skill | Tool call records or logs include a read of `SKILL.md` |
| Workflow reused | Both briefs contain the three required sections, an action table, and input IDs |
| Content matches the input | Numbers and facts remain accurate, suggestions are labeled, and missing information remains open |

Model-generated wording can vary. Use the input facts and Skill requirements to evaluate the result.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The Skill does not appear in the list | Check the import result. If it reports an error, use the message to check the service connection, directory permissions, or model configuration |
| The CLI can read the Skill but VikingBot cannot | Check that both use the intended service and accounts, and that VikingBot's identity can read the directory |
| The conversation has no Skill read record | Confirm that the Skill path in the prompt is readable and check VikingBot's OpenViking tool configuration |
| The brief does not follow the required structure or content rules | Confirm that the agent read the Skill, then identify the differences and ask it to revise the brief against the instructions |

## Next steps

To reuse your own workflow, write its instructions in `SKILL.md` and add it to OpenViking. See the [Skills reference](../api/04-skills.md) for the format, update options, and integration with other clients.

To have an agent use a Skill to produce maintainable knowledge-base files, continue to [Context compilation](13-compile.md). To remove the sample afterward, identify `feedback-brief` in Studio and confirm it is the Skill you added before deleting it.
