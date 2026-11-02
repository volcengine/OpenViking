<a id="import-and-retrieve-your-first-document"></a>

# Quickstart

To answer questions about your project, an AI agent first needs to find relevant material and read the source. OpenViking is a context database for agents: it stores and retrieves that material so the agent can ground its answers in your sources.

In this walkthrough, you will import fictional Lighthouse project notes, ask an agent about feedback collection, and open its citations to check the answer. Choose Volcengine's cloud service or run OpenViking locally. Both paths take you through **import → retrieve and read → generate an answer → verify its sources**.

## Prerequisites

Choose where to run OpenViking, then prepare the corresponding tools:

| Option | When to choose it | What to prepare |
| --- | --- | --- |
| **Volcengine cloud service** | Use a managed service and ask questions through an existing agent | Node.js/npm, a Volcengine account, and an agent that can run terminal commands on your machine |
| **Local service** | Run OpenViking on your computer with its built-in agent, VikingBot | Node.js/npm, uv, Python, and model credentials |

### Install Node.js

Both paths use the `ov` CLI to work with your sources. Open the [Node.js download page](https://nodejs.org/en/download), select an **LTS** release for your operating system, and follow its installer or command instructions. npm is included with Node.js.

Reopen your terminal after installation and confirm that both commands print a version:

```bash
node --version
npm --version
```

For the cloud service, continue to [Set up the service](#set-up-the-service).

### Prepare the local runtime

The local service uses uv to install OpenViking and Python to run it. The commands below use a macOS / Linux terminal. For other systems, see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) and [server deployment](../guides/03-deployment.md).

Install uv first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Reopen your terminal and confirm that uv is available:

```bash
uv --version
```

Then install Python 3.13 for the service:

```bash
uv python install 3.13
```

You can use an existing Python 3.13 installation. Other installation options are available on the [Python download page](https://www.python.org/downloads/) and in the [uv Python guide](https://docs.astral.sh/uv/guides/install-python/).

Prepare credentials for two models: an **embedding model** for indexing and retrieval, and a **VLM with tool-call support** for content understanding and VikingBot answers. For each, you need a model name, endpoint, and API key. Follow the [Volcengine model activation guide](../guides/02-volcengine-purchase-guide.md) to obtain these details. With Agent Plan, choose a model and endpoint supported by your plan. Importing sources and generating answers invoke models and incur charges according to your provider's pricing.

<a id="_1-start-the-server"></a>

<a id="_1-choose-a-service"></a>

<a id="_1-set-up-the-service"></a>

## Set up the service

Complete one of the following paths, then continue to [Connect the CLI](#connect-the-cli).

<a id="managed-service-on-volcengine"></a>

### Volcengine cloud service

The cloud service runs OpenViking and processes your sources. Your computer only needs the client tools.

1. Open the [OpenViking console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing), sign in to Volcengine, and follow the console's activation steps. See the [service description](https://docs.volcengine.com/docs/84313/2374478) for plans and quotas.
2. Obtain an **OpenViking API key** from the console for the CLI connection in the next step.
3. Prepare an agent that can run terminal commands on your machine. In “Ask and verify,” it will use `ov` to retrieve cloud-hosted sources and generate an answer. See [CLI basics](05-cli-setup.md) for agent access.

Once you have the API key, continue to [Connect the CLI](#connect-the-cli).

<a id="self-host-a-server"></a>

### Local service

Install OpenViking with VikingBot to manage sources and answer questions on your machine:

```bash
uv tool install --python 3.13 "openviking[bot]" --upgrade
```

Run the setup wizard. Choose local access and enter the embedding and VLM settings you prepared. VikingBot uses this VLM configuration by default.

```bash
openviking-server init
```

Check the model and service configuration. If the check reports an error, correct the indicated setting and run it again:

```bash
openviking-server doctor
```

Once the checks pass, start the service with VikingBot. Keep this terminal running and use another terminal for the remaining commands.

```bash
openviking-server --with-bot
```

In the new terminal, check that both the data service and agent are ready:

```bash
curl -f http://127.0.0.1:1933/health
curl -f http://127.0.0.1:1933/bot/v1/health
```

Continue when the first response reports `healthy: true` and the Bot response reports `status: healthy`.

<a id="_2-install-and-connect-the-cli"></a>

<a id="_2-connect-the-cli"></a>

## Connect the CLI

Install the CLI to import, retrieve, and read sources from your terminal. Skip installation if `ov` is already available.

```bash
npm install -g @openviking/cli
```

Set the interface language and open the connection wizard:

```bash
ov language en
ov config
```

Add a connection using the settings for your chosen path, then save and activate it:

| Option | Connection type | Settings |
| --- | --- | --- |
| Volcengine cloud service | **OpenViking Service** | Use the service address provided by the wizard and enter the API key from the console |
| Local service | **Custom** | Set the URL to `http://127.0.0.1:1933`; leave the key empty for default local mode |

If your local service already has authentication configured, use its User/Admin key. See [Authentication](../guides/04-authentication.md) for setup details.

Check the active connection before importing:

```bash
ov health
```

<a id="_3-import-a-document"></a>

<a id="_3-import-the-sample"></a>

## Import the sample

<a href="/quickstart/lighthouse.en.txt" download="lighthouse.en.md">Download the Lighthouse notes</a>, save them as `lighthouse.en.md`, and open your terminal in that directory. The notes describe the pilot's network constraints, feedback collection, and open questions.

Import the file into a separate demo directory. `--wait` waits for processing to finish. For a repeat run, replace `lighthouse-demo` with a new directory name and use that same name in subsequent commands.

```bash
ov add-resource ./lighthouse.en.md --to viking://resources/lighthouse-demo --wait
```

Once processing succeeds, inspect the imported files:

```bash
ov tree viking://resources/lighthouse-demo
```

If waiting times out, run `ov task status <task_id>` with the returned task ID to check progress. Continue after completion. If the task fails, correct the model or connection settings indicated by the error before retrying. See [Background tasks](../api/17-tasks.md) for task states.

<a id="_4-browse-and-search"></a>

<a id="_4-ask-and-verify"></a>

## Ask and verify

### Retrieve and read a source

Find the material about feedback collection:

```bash
ov find "How does the first version of Lighthouse collect feedback?" --uri viking://resources/lighthouse-demo
```

The results include URIs for matching content. Copy a source file URI, replace the placeholder below, and read the original text:

```bash
ov read "<source-file-uri>"
```

Once you can read sections such as "Pilot environment" and "Feedback collection," ask an agent to use those sources.

### Generate an answer

**Cloud service:** Send the following task to an agent that can run local terminal commands. It should use the CLI connection you just configured. If the agent runs on another machine or in a separate container, [connect the CLI](#connect-the-cli) in that environment first.

```text
Use the locally configured ov CLI to answer a question from project sources in OpenViking.
Run ov health to check the connection, then run:
ov find "How does the first version of Lighthouse collect feedback?" --uri viking://resources/lighthouse-demo
Use ov read to open the relevant original text from the results, then answer:
How should the first version of Lighthouse collect user feedback, and what must be prepared?
Use only sources in that directory. Cite the full source file viking:// URI for each
key conclusion, and identify anything the sources do not specify.
```

**Local service:** Use `ov chat` to ask the running VikingBot the same question. It will use its retrieval and reading tools:

```bash
ov chat -m "Use only the sources under viking://resources/lighthouse-demo. Run a semantic search for the question, then read the relevant original text and answer: How should the first version of Lighthouse collect user feedback, and what must be prepared? Cite the full source file viking:// URI for each key conclusion. Explicitly identify anything the sources do not specify."
```

### Check the supporting evidence

Open the source files cited in the answer and compare the following facts. Wording can vary; check that each conclusion agrees with the original text.

| Expected conclusion | Supporting section |
| --- | --- |
| Save feedback locally; the pilot administrator exports it weekly to the project owner | Feedback collection |
| Prepare an offline installation package and a local storage directory | Pilot environment |
| No cloud synchronization in the first version; future support is undecided | Feedback collection |

If a conclusion lacks a citation, ask the agent to add it and check the source again. Then ask when the pilot ends and check that the answer identifies the date as undecided.

You have now imported sources, retrieved and read them, and checked an answer against its citations. Try the same workflow with your own material next.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `uv`, `node`, or `ov` is not found after installation | Reopen the terminal. If needed, follow the relevant installation guide to check the command path. |
| `ov health` cannot connect | Check the active connection, service URL, and API key in `ov config`. For a local service, also check its running terminal. |
| Bot health returns `503` | Confirm the service started with `--with-bot`; inspect the service terminal and [VikingBot logs](../guides/17-vikingbot.md). |
| Import fails or retrieval is empty | Inspect the returned task, model availability, processing completion, and target URI. |
| A model returns `UnsupportedModel` | Check that the model, endpoint, and plan match; see the [model activation guide](../guides/02-volcengine-purchase-guide.md). |
| The agent cannot run `ov` | Check `ov --version` and `ov health` in the agent's terminal, then complete installation and connection setup there. |

<a id="use-an-sdk"></a>

## Next steps

Continue to [Resource retrieval](08-resources.md) to try questions requiring multiple sources or information missing from the material. Use [Evaluation](06-evaluate.md) to record results on your own tasks.
