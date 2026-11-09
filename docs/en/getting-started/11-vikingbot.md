# VikingBot

VikingBot is OpenViking's built-in AI agent. It accepts tasks, calls models and tools, and uses resources, memories, and Skills stored in OpenViking to generate answers. You can chat with it from the command line to organize tasks, query project documents, or follow a reusable workflow.

This guide runs locally. You will start VikingBot, organize a task list, and ask a follow-up in the same session. By the end, you can check that the service is reachable, the model responds, and subsequent messages use the conversation context.

## Prerequisites

Prepare the following before you begin:

| Requirement | Purpose and setup |
| --- | --- |
| uv and Python | Install and run OpenViking and VikingBot. The commands below use Python 3.13; see [Prepare the local runtime](02-quickstart.md#prepare-the-local-runtime) |
| Node.js/npm | Install the `ov` CLI; see [Install Node.js](02-quickstart.md#install-node-js) |
| Model credentials | Prepare model names, endpoints, and API keys for an embedding model and a VLM that supports tool calling; see the [model setup guide](../guides/02-volcengine-purchase-guide.md) |

VikingBot uses the VLM in your OpenViking configuration by default. The embedding model supports later resource retrieval. For Agent Plan, select supported models and endpoints. Model calls incur charges from your chosen service.

If you already started a local service with VikingBot through the quickstart, go to [Connect the CLI](#connect-the-cli).

<a id="start-and-connect"></a>

## Start the service

Install OpenViking and VikingBot to enable resource management and agent conversations locally:

```bash
uv tool install --python 3.13 "openviking[bot]" --upgrade
```

For a first-time setup, run the initialization wizard, choose local access, and enter your model settings. If you already have a configuration, proceed to the configuration check.

```bash
openviking-server init
```

Check the model and service configuration. If the check reports errors, correct the indicated settings and check again:

```bash
openviking-server doctor
```

If your existing service uses API key authentication, first follow [Authentication](../guides/04-authentication.md) to configure the Bot connection identity and prepare the appropriate User/Admin Key for the CLI.

After the check passes, start OpenViking and VikingBot together. The `--with-bot` flag enables the agent conversation endpoint. Keep this terminal running and use another terminal for subsequent commands. If a local service is already running without Bot, stop it in its original terminal before starting it with the command below.

```bash
openviking-server --with-bot
```

The remaining commands use the default address, `http://127.0.0.1:1933`.

## Connect the CLI

Install the CLI in a new terminal. If `ov` is already available, proceed to connection setup.

```bash
npm install -g @openviking/cli
```

Set the interface language to English and open the connection wizard:

```bash
ov language en
ov config
```

Choose a Custom connection and enter `http://127.0.0.1:1933` as the URL. Leave the key empty for the default local mode, or enter your User/Admin Key if authentication is enabled. Save and select this configuration; if the connection already exists, select it directly. See [CLI basics](05-cli-setup.md) for other connection options.

First, check that the CLI can reach the data service:

```bash
ov health
```

The result should include `healthy: true`. Next, check the Bot endpoint:

```bash
curl -f http://127.0.0.1:1933/bot/v1/health
```

Once it returns `status: healthy`, you can start a conversation. The next task also checks that the model can generate a response.

<a id="complete-a-task-and-follow-up"></a>

## Complete a task

Give VikingBot a fictional task list to organize by completion status. The `--session` option names the conversation so you can continue discussing the list later. When repeating the walkthrough, choose a new session name.

```bash
ov chat --session bot-review-v1 -m "Fictional task list: source import is complete; citation verification and demo rehearsal are pending. Organize a brief completed and pending list."
```

The response should list source import as complete, with citation verification and demo rehearsal pending. Compare the states with the input; wording and formatting can vary.

## Ask a follow-up

Keep the same `--session` and ask VikingBot to suggest the next step based on the list:

```bash
ov chat --session bot-review-v1 -m "Using that list, suggest what to complete first and explain why in one sentence."
```

You do not need to repeat the list. The response should discuss citation verification or demo rehearsal. For example, it might recommend checking citations first to confirm accuracy before the demo. The suggested order can vary, but its reasoning should use the pending items from the previous turn.

Follow-ups in the same session use the existing conversation context. A new session name starts another conversation. To reuse project background or preferences across sessions, continue to [Memory use](09-memory.md) for memory storage and recall.

## Check the result

| Check | Expected result |
| --- | --- |
| Service connection | Both `ov health` and the Bot health check succeed |
| Single task | The response preserves all three tasks and their completion states |
| Continued conversation | The follow-up uses the previous pending items and explains its recommendation |

If the response omits a task or changes its state, point out the difference and ask VikingBot to revise it against the original list.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `openviking-server` or `ov` is unavailable after installation | Open a new terminal; if the command is still unavailable, check executable paths using the uv or npm installation instructions |
| The port is already in use | Check for an existing local service; to enable Bot, stop it in its original terminal and restart with `--with-bot` |
| `ov health` cannot connect | Check the current address in `ov config` and confirm that the service terminal is still running |
| Bot health returns `503` | Confirm that startup included `--with-bot` and inspect the Bot startup messages in the service logs |
| Health checks pass but model calls fail | Use the error message to check the model name, endpoint, credentials, and plan support |
| The follow-up does not use the list | Confirm that both commands use the same `--session` and CLI identity, and that the first turn returned a complete response |

## Next steps

Once connection and conversation work, use OpenViking context in your tasks:

| Goal | Next step |
| --- | --- |
| Answer questions from project documents and verify sources | [Resource retrieval](08-resources.md) |
| Reuse background and preferences in a new session | [Memory use](09-memory.md) |
| Apply a consistent workflow to different inputs | [Skill use](10-skills.md) |

See the [VikingBot guide](../guides/17-vikingbot.md) for chat platforms such as Feishu, standalone debugging, and model configuration. See [Bot API](../api/24-vikingbot.md) to send messages from an application. VikingBot Gateway handles Bot messages and chat channels; the [OpenViking gateway](12-gateway.md) provides a model request endpoint for compatible clients.

## Finish the trial

Keep the session name to continue the conversation later. Stop the local service with Ctrl+C in its terminal; the CLI conversation endpoint closes with it. To clean up test sessions, identify the corresponding sessions in Studio before deleting them.
