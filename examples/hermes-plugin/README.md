# OpenViking for Hermes

OpenViking gives Hermes Agent long-term memory and tools to search your knowledge.
It captures conversations, extracts useful facts and recalls them in later chats.

## Get started

Run these commands in the Hermes profile you want to use:

```bash
hermes plugins install openviking --enable
hermes memory setup openviking
hermes
```

Accept the dependency prompt during installation. In setup, choose how you
use Hermes, then choose a connection.

### Personal or Shared

Hermes uses conversation history to follow the current chat. OpenViking saves
extracted facts as long-term memory for use in later chats. These are separate.

Common memory is not linked to a sender. Sender memory is linked to the person
who sent a message through Telegram, Discord or another messaging platform.
Both modes recall common memory under the same OpenViking user.

| Usage | What it does |
|-------|--------------|
| **Personal Agent** | Recalls common memory and the current sender's memory. Keeps your existing conversation history settings. |
| **Shared Agent** | Shares conversation history within each group or thread. Recalls common memory and all senders' memories under the same OpenViking user. |

Choose Personal for an assistant that recalls memory for the current person.
Choose Shared for a team that wants to share recalled memories. Shared Agent
asks for confirmation before changing conversation history settings. Different
groups keep separate histories, even in Shared mode. Restart the gateway if
setup changes its session settings.

Personal does not change existing history settings. If a group already shares
history, choosing Personal leaves that history shared. Neither mode changes
data access rights. Use separate OpenViking users and keys when people need
separate access.

### Choose a connection

| Connection | What you need |
|------------|---------------|
| **Quick Local** | A language model already configured in Hermes. Setup installs a local server and embedding model. |
| **OpenViking Service (VolcEngine Cloud)** | A service API key for VolcEngine's [managed OpenViking cloud service](https://www.volcengine.com/product/openviking-service). |
| **Custom** | Your server URL and credentials. Setup can also reuse a saved `ovcli.conf`. |

For the cloud service, activate it and create an API key in the
[OpenViking console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing).
Open **User Management**, then **API Key**. You do not need to install a server
or configure local models.

After setup, chat as usual. Memories become available after the captured
conversation is committed and OpenViking finishes extraction. The plugin
requests a commit at 20,000 pending tokens by default, at session end and when
switching sessions.

The plugin sends conversation turns, tool results and edits to `MEMORY.md`
and `USER.md` to your OpenViking server. Memory extraction can use a remote model.

## Quick Local

Quick Local reuses your Hermes language model for memory extraction. A local
embedding model prepares text for memory search. The language model must use
a supported static API key or Hermes's local llama.cpp server. OAuth logins
are not supported. See [model support](CONFIGURATION.md#quick-local-model-support).

Setup checks the language model, downloads the embedding model and starts the
server before it finishes. It saves a copy of the model credentials in this
profile's private server configuration. Downloads need access to PyPI, GitHub
and Hugging Face.
The model download is about 46 MB. Other packages add more than 1 GB per profile.

Prebuilt packages are available for Apple Silicon with macOS 14 or newer,
Linux x86-64 or ARM64 with glibc 2.31 or newer, and Windows x86-64. On other
platforms, setup asks before building from source. This needs development
tools and can take several minutes. You can use Custom instead.

Each Hermes profile has its own server, data and port. If the saved port is
occupied, Quick Local selects a free port. Run setup again after changing the
Hermes model or API key. Your data and downloaded model are kept.

**Known issue:** OpenViking 0.4.22 and 0.4.23 can crash when local embedding
calls overlap, such as during indexing and recall. This affects Quick Local
and Custom servers using that local backend. It does not affect connections
using remote embedding APIs. The [fix](https://github.com/volcengine/OpenViking/pull/5548)
is merged and is expected in OpenViking 0.4.24. After that release, run Quick
Local setup again to upgrade.

The server keeps running after Hermes exits. These commands are optional:

```bash
hermes openviking local status
hermes openviking local stop
hermes openviking local start
hermes openviking local restart
```

Stopping keeps the data. A running Hermes session or gateway can start the
server again when memory is used. Close those sessions or disable OpenViking
memory if you want it to stay stopped. Turns completed while the server is
starting or recovering are not captured.

Switching to OpenViking Service or Custom stops this profile's Quick Local
server and keeps its data. `local start` and `local restart` work only while
Quick Local is selected.

## Custom server

Run OpenViking in its own Python environment or container. For a new local
server, run these commands there:

```bash
openviking-server init
openviking-server doctor
openviking-server
```

Then run `hermes memory setup openviking` and choose Custom. For setup to
start a stopped local server, `openviking-server` must be on `PATH`.

## Check status

```bash
hermes memory status
```

`available` means the provider is configured. It does not confirm server
health or successful memory extraction. For Quick Local, use
`hermes openviking local status` to check the server.

## Update

```bash
hermes plugins update openviking
```

Restart Hermes or the gateway afterward. Your connection settings and server
data are kept. Updates use the version reviewed in the Hermes catalog.
For direct source installs, use the [source update command](CONFIGURATION.md#pinned-source-installation).

To update a Quick Local server, run `hermes memory setup openviking` again and
choose Quick Local. Setup selects the latest compatible OpenViking 0.4 release
and restarts the server if needed. Normal chat and recovery use the installed
server without checking for updates.

## Existing users

If a Hermes update removes its built-in OpenViking provider, it attempts to
install the catalog plugin for profiles already using OpenViking. Your settings
and server data are kept. If installation fails, run the install command above
in that profile.

Older Hermes releases load their built-in copy first. On those releases,
keep using `hermes memory setup openviking`. Quick Local and the external
plugin's changes become available when that copy is removed.

## Tools

Hermes calls these tools when needed. You do not need to run them yourself.

| Tool | What it does |
|------|--------------|
| `viking_search` | Search memories and knowledge. |
| `viking_read` | Read content at a `viking://` address. |
| `viking_browse` | List files and directories. |
| `viking_remember` | Submit a fact for memory extraction. |
| `viking_forget` | Delete one exact memory file. |
| `viking_add_resource` | Add a URL or document to the knowledge base. |

## Remove

Close Hermes sessions and stop the gateway for this profile. For Quick Local,
run `hermes openviking local stop`. Select another memory provider or disable
OpenViking memory, then run:

```bash
hermes plugins remove openviking
```

Removal does not stop a running server or delete the runtime, model and data
in `$HERMES_HOME/openviking/`.

## Requirements

- Python 3.11 or newer in the Hermes environment.
- OpenViking Service, Quick Local or a reachable OpenViking server.
- OpenViking 0.2.14 or newer for Custom connections.

Tested with Hermes v2026.9.24 and the Hermes main commit used in
[CI](../../.github/workflows/hermes-plugin-tests.yml).

## Config

The wizard saves the connection settings. For optional recall settings,
model support, backups and memory recovery, see [Advanced configuration](CONFIGURATION.md).
For development and migration notes, see [DEVELOPMENT.md](DEVELOPMENT.md).
This directory uses the [MIT license](LICENSE).
