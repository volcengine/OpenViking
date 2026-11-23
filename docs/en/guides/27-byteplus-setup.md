# Connect to OpenViking on BytePlus

[BytePlus](https://openviking.ai/byteplus) hosts OpenViking in Asia Pacific (Johor). You connect a client to your managed instance. You do not run an OpenViking server on your computer.

## What you need

- An OpenViking instance on BytePlus.
- The service URL for that instance and its OpenViking API key. Use the full service URL, including any path. Do not use a Volcengine URL or key.

## Connect the CLI

1. [Install the `ov` CLI](../getting-started/05-cli-setup.md#install).
2. Run `ov config`. Add a **Custom** connection and enter the BytePlus service URL and API key. Save the new connection and select it as active.
3. Run `ov health` to check the connection. To import and search a sample document, continue from [Import the sample](../getting-started/02-quickstart.md#import-the-sample).

## Connect an agent

If you use [Hermes](../agent-integrations/05-hermes.md#get-started) or [OpenClaw](../agent-integrations/03-openclaw.md), follow its integration guide. The shared installer is for the agents listed in its menu, including Claude Code, Codex, and Cursor. It requires macOS or Linux, Node.js 18 or later, and `curl`.

Run the installer on the machine that runs your agent:

```bash
curl -fsSL https://openviking.ai/install | bash
```

Select your agent. If the installer finds an existing connection, choose **Reconfigure**. Select **Custom URL** and enter the BytePlus service URL and API key.

The installer updates the shared `~/.openviking/ovcli.conf`. The CLI and other integrations that read this file also switch to the new connection. Check the active URL with `ov config show` before restarting your agent.
