# CLI basics

`ov` is the command-line client for OpenViking. Use it to import sources, retrieve content, and read original files from a terminal. An agent with terminal access can also run these commands to ground its answers in your sources.

This page walks through installation, connection setup, browsing a source, and retrieving and reading relevant content. You can then use the same OpenViking connection from your terminal or agent.

## Prerequisites

Prepare the following tools and connection details:

| Requirement | How to obtain it |
| --- | --- |
| Node.js and npm | Install an LTS release from the [Node.js website](https://nodejs.org/en/download); npm is included. If `ov` already works, proceed to [Connect](#connect). |
| An OpenViking service | Use a provisioned Volcengine service or a running self-hosted service. To set one up, follow [Set up the service](02-quickstart.md#set-up-the-service). |
| Access credentials | Use the cloud console's API key, or a User/Admin key from your self-hosted service administrator. Default local mode without authentication can leave the key empty. |
| An accessible source | Use existing content, or [import the Lighthouse sample](02-quickstart.md#import-the-sample) after connecting. |

After installing Node.js, open a new terminal and check that Node.js and npm are available:

```bash
node --version
npm --version
```

The CLI connects over HTTP. Installing only the CLI requires no local Python installation or model configuration; source processing uses the server's models.

## Install

Install `ov` with npm:

```bash
npm install -g @openviking/cli
```

Save a display language for subsequent interactive and agent commands. Use `en` for English or `zh-CN` for Chinese:

```bash
ov language en
```

Confirm that the terminal can find the CLI and display its version:

```bash
ov --version
```

If your server installation already provides `ov`, you can use that command.

## Connect

Open the connection wizard and add a named connection for the service you want to access:

```bash
ov config
```

Enter the details for your service, save the connection, and activate it. If a matching connection already exists, select it.

| Service | Connection type | Details |
| --- | --- | --- |
| Default local service | Custom | URL: `http://127.0.0.1:1933`; leave the key empty in default local mode without authentication. |
| Remote self-hosted service | Custom | Use the service URL and User/Admin key provided by the administrator. |
| Volcengine managed service | OpenViking Service | Use the wizard's service address and enter the API key from the console. |

Choose a recognizable connection name, such as `local` or `cloud`. The service URL points to the OpenViking data service; model API endpoints are configured on the server.

Entering keys through the wizard keeps them out of command history. With API key authentication, use a User/Admin key for ordinary data access and a Root key for administration. See [Authentication](../guides/04-authentication.md) for other identity configurations.

## Check the connection

Inspect the active connection and verify its service address. This command masks keys:

```bash
ov config show
```

Validate the configuration, connection, and authentication:

```bash
ov config validate
```

Once the checks pass, check the service health:

```bash
ov health
```

A result of `healthy: true` indicates a healthy service. Next, read a source to confirm that your identity can access the required content. Models used for imports and VikingBot's ability to answer questions are checked in their respective workflows.

## Browse and read

List the accessible shared source directories in the current service:

```bash
ov ls viking://resources
```

If no sources are available, [import the Lighthouse sample](02-quickstart.md#import-the-sample) and wait for processing to finish before returning here. The following commands use its directory, `viking://resources/lighthouse-demo`. Substitute your actual directory URI when using your own content.

Expand the directory to find the source file URI:

```bash
ov tree viking://resources/lighthouse-demo
```

Copy a file URI from the output, replace the placeholder below, and read the original text:

```bash
ov read "<file-uri>"
```

The sample includes sections on the pilot environment and feedback collection. Reading the original text confirms that the connection and file access work.

## Retrieve sources

For a specific question, retrieve relevant content before reading the matching files. Retrieval uses the server's model configuration and may incur model usage charges.

```bash
ov find "How should the first version of the Lighthouse project collect user feedback?" --uri viking://resources/lighthouse-demo
```

Results include matching content and its URI. Use `ov read` to read a relevant result and check who collects feedback and how it is delivered. An agent can use this retrieved content as evidence for its answer.

## Use from an agent

Send the following task to an agent with terminal access. It can use the configured CLI to retrieve sources, read them, and answer:

```text
Use the configured ov CLI to query sources under viking://resources/lighthouse-demo.
First use ov find for this question:
How should the first version of Lighthouse collect user feedback, and what should be prepared?
Read the relevant original files with ov read, then answer the question.
Cite the full viking:// source URI for key conclusions and identify details the sources do not specify.
```

The agent needs access to both the `ov` command and its connection configuration. If it runs on another computer, in a container, or under a different operating-system user, install and configure the CLI in that environment first.

`ov chat` sends a question to server-side **VikingBot**. The target service must have VikingBot enabled. For a local service, follow [VikingBot](11-vikingbot.md) to start it with `openviking-server --with-bot`. The Volcengine managed-service workflow uses the agent approach above. `ov health` checks the data service; check Bot availability separately.

## Switch connections

When using both cloud and local services, list your saved connections:

```bash
ov config list
```

Open the selector and switch to the intended connection:

```bash
ov config switch
```

Check the active address and service health after switching:

```bash
ov config show
ov health
```

Commands now access content in the selected service under the current identity. Sources imported into the cloud remain there; import the sample into the local service to try it locally. To migrate existing content, see [Import and export](15-transfer.md#migrate-between-services).

The two configuration files serve different purposes:

| Configuration file | Purpose |
| --- | --- |
| `~/.openviking/ovcli.conf` | Active CLI connection, access credentials, and command defaults |
| `~/.openviking/ov.conf` | Models, storage, and server settings for a self-hosted service |

For alternate configuration paths or scripted profile management, see [Client configuration](../configuration/02-client.md) and `ov config --help`.

## Common commands

| Purpose | Command |
| --- | --- |
| Browse directories | `ov ls "<directory-uri>"`, `ov tree "<directory-uri>"` |
| Retrieve by question | `ov find "question" --uri "<directory-uri>"` |
| Read original content | `ov read "<file-uri>"` |
| Read a generated directory overview | `ov overview "<directory-uri>"` |
| Inspect a background task | `ov task status <task_id>` |
| Look up options | `ov --help`, `ov <command> --help` |

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `npm` or `ov` not found | Open a new terminal after installing Node.js. If npm works but `ov` does not, check that npm's global executable directory is in `PATH`. |
| npm permission error | Follow the [npm guide](https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally) to fix global installation permissions. |
| A command asks for a language | Run `ov language en` or `ov language zh-CN` to save a display language. |
| Connection fails | Check the active URL with `ov config show`, keep the local service running, and run `ov config validate`. |
| Authentication or read denied | Check the key type and content permissions. See [Authentication](../guides/04-authentication.md). |
| Sources disappear after switching | Check the target service and identity; import the sample into that service or migrate the existing sources. |
| `ov chat` returns `404 page not found` | Check whether the active address provides VikingBot endpoints. For cloud services, use an existing agent to call the CLI. For local services, start and check the Bot as described in [VikingBot](11-vikingbot.md). |
| An import reports `UnsupportedModel` | Check that the model name, endpoint, and plan match in the server's `ov.conf`. See [Model setup](../guides/02-volcengine-purchase-guide.md). |
| Directory overview missing | Check source processing status. Overview generation depends on processing mode and configuration; see [Context layers](../concepts/03-context-layers.md). |

## Let an agent configure it

An agent can also help with installation and connection setup. Copy this prompt and supply the service type, connection name, and self-hosted service URL. Enter API keys through the interactive wizard or provide them through an existing trusted environment variable.

<AgentPrompt>

```text
Help me install and configure the OpenViking CLI (ov) for the service I specify.

Check whether ov is installed; if needed, install it with npm install -g @openviking/cli.
If Node.js/npm is missing, explain how to install it. Set the CLI language to en.

Use my target details and ov config list -o json to reuse a matching named connection.
Before adding one, read ov config add ov-service --help or ov config add custom --help.
Save it with an explicit --name and activate the confirmed target.
Ask for any missing service type, URL, or credential source.
If the name already exists with different settings, explain the difference and ask before replacing it.

Read an API key from an existing environment variable with --api-key-env, or pass it via --api-key-stdin.
If no secure input method is available, guide me to run ov config in my terminal to enter the key.
Use ov config show to display settings without exposing credentials.

Run ov config validate and ov health, and inspect their actual results.
Report the active service address, check results, and any remaining issues.
This task covers installation and connection; source imports will be specified in a later task.
```

</AgentPrompt>

## Next steps

Continue with [Ask and verify](02-quickstart.md#ask-and-verify) to produce source-based answers. To import your own project content, see [Resource search](08-resources.md).

<!-- Preserve incoming links to previous section titles. -->
<a id="_1-install"></a>
<a id="_2-connect"></a>
<a id="_4-browse-and-read"></a>
<a id="install-and-use-the-cli"></a>
<a id="set-up-with-an-agent"></a>
<a id="before-you-start"></a>
<a id="_1-install-ov"></a>
<a id="_2-add-a-connection"></a>
<a id="_3-check-the-connection"></a>
<a id="what-you-can-do-with-ov"></a>
<a id="manage-several-connections"></a>
<a id="api-keys"></a>
<a id="ov-is-not-found"></a>
<a id="npm-reports-a-permission-error"></a>
<a id="a-command-asks-for-a-display-language"></a>
<a id="the-local-server-does-not-respond"></a>
<a id="api-key-validation-fails"></a>
<a id="the-wrong-config-is-active"></a>
