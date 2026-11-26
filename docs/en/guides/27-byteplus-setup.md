# Connect to OpenViking on BytePlus

[BytePlus](https://openviking.ai/byteplus?utm_source=opensource_docs&utm_medium=referral&utm_campaign=guides_27-byteplus-setup) hosts OpenViking in Asia Pacific (Johor). You connect a client to your managed instance. You do not run an OpenViking server on your computer.

## What you need

- An OpenViking instance on BytePlus.
- Your instance's OpenViking API key. Do not use a Volcengine key.

The default service URL is `https://api.vikingdb.ap-southeast-1.bytepluses.com/openviking`. If the URL field is blank, enter this full URL. If BytePlus gives you a different URL, use that instead.

## Connect the CLI

1. [Install the `ov` CLI](../getting-started/05-cli-setup.md#install).
2. Run `ov config`. Add a connection, select **OpenViking Managed Service**, then **BytePlus**. Confirm the service URL and enter your API key. Save the connection and select it as active.
3. Run `ov health` to check the connection. To import and search a sample document, continue from [Import the sample](../getting-started/02-quickstart.md#import-the-sample).

If your CLI does not show the managed-service menu, use **Custom** with the same URL and API key.

## Connect an agent

If you use [Hermes](../agent-integrations/05-hermes.md#get-started), follow its integration guide. The shared installer is for the agents listed in its menu, including Claude Code, Codex, and Cursor. It requires macOS or Linux, Node.js 18 or later, and `curl`.

Run the installer on the machine that runs your agent:

```bash
curl -fsSL https://openviking.ai/install | bash
```

Select your agent. If the installer finds an existing connection, choose **Reconfigure**. Select **OpenViking Managed Service**, then **BytePlus**. Confirm the service URL and enter your API key. If BytePlus is not listed, select **Custom URL** and enter the same URL and key.

The installer updates the shared `~/.openviking/ovcli.conf`. The CLI and other integrations that read this file also switch to the new connection. Check the active URL with `ov config show` before restarting your agent.
