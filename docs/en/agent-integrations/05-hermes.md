# Hermes

Use OpenViking for long-term memory in [Hermes Agent](https://hermes-agent.nousresearch.com/).

## Get started

Run these commands in the Hermes profile you want to use:

```bash
hermes plugins install openviking --enable
hermes memory setup openviking
hermes
```

Accept the dependency prompt during installation. If your Hermes release still
includes OpenViking, skip the install command. That release uses its built-in
copy and does not provide Quick Local.

Hermes uses conversation history to follow the current chat. OpenViking saves
useful facts as long-term memory for later chats. Common memory is not linked
to a sender.

Choose **Personal Agent** to recall common memory and the current sender's
memory while keeping your conversation history settings. Choose **Shared Agent**
to share history within each group or thread and recall all senders' memories
under the same OpenViking user. Shared Agent asks for confirmation.

Then choose a connection:

- **Quick Local** installs a local server and embedding model. It reuses your
  supported Hermes language model for extraction, which can use a remote API.
- **OpenViking Service (VolcEngine Cloud)** connects to VolcEngine's
  [managed OpenViking cloud service](https://www.volcengine.com/product/openviking-service)
  with a service API key. You do not need to install a server or configure
  local models.
- **Custom** connects to your own server with its URL and credentials. Setup
  can also reuse a saved `ovcli.conf`.

After setup, chat as usual. The plugin requests a commit at 20,000 pending
tokens by default, at session end and when switching sessions. Memories become
available after OpenViking finishes extraction. Existing server data is kept.

Quick Local keeps its server running after Hermes exits. See the
[plugin guide](https://hermes-agent.nousresearch.com/docs/plugins/openviking)
for model support, server controls and the known local embedding issue.

## Check status

```bash
hermes memory status
```

`available` means the provider is configured. It does not confirm server
health or successful memory extraction.

## Update

```bash
hermes plugins update openviking
```

Restart Hermes or the gateway afterward. Your connection settings and data
are kept. To update a Quick Local server, run setup again and choose Quick
Local. Normal chat uses the installed server without checking for updates.

When a Hermes update removes the built-in provider, it attempts to install
the catalog plugin for profiles already using OpenViking. If that fails,
run the install command above in that profile.

## More information

- [Plugin guide](https://hermes-agent.nousresearch.com/docs/plugins/openviking)
- [Capability reference](./16-capability-reference.md)
- [Server deployment](../guides/03-deployment.md)
- [API keys](../guides/04-authentication.md)
