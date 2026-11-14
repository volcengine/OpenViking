# Live context

Project progress, task counts, and service status change over time. Live Context lets an Agent query a data source through a fixed `viking://` path and receive the result of that read's upstream call.

OpenViking binds a remote MCP tool and preset arguments to a searchable Markdown node. After finding the node, an Agent reads it in full, and OpenViking calls the tool. In this walkthrough, you will mount a pending-feedback query for the fictional Lighthouse project, change the count from 3 to 1, and read the same path to see the update.

## How this differs from direct MCP calls

Both approaches can query current data. They differ in how the Agent finds and uses it:

| | Agent calls MCP directly | Tool mounted through OpenViking |
| --- | --- | --- |
| Query workflow | Select a tool, supply arguments according to its definition, and call it | Retrieve a node described in business terms, then read it in full |
| In this example | Call `project_status` with `project=Lighthouse` | Find the “Lighthouse pending feedback” node and read its URI |
| Connection and maintenance | Configure the upstream MCP connection in the Agent host | Manage the upstream connection, tool binding, and preset arguments in OpenViking; the Agent uses OpenViking retrieval and reading |

Mounting gives these queries the same retrieval and reading workflow as ordinary resources, with fixed arguments reused through the node. Configuration happens when mounting; the Agent does not need to construct those arguments when using the node. Direct MCP calls are often simpler for a few tools whose arguments change frequently. Mounting fits frequent queries with stable arguments.

## Prepare your environment

- **OpenViking server and CLI**: Complete the connection setup in [Quickstart](02-quickstart.md). Both must support live context; run `ov mcps --help` to check CLI support. Registration and mounting use the server's model and indexing configuration and may incur model costs.
- **Python and uv**: The sample requires Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). Check with `python3 --version` and `uv --version`. `uv run` installs the MCP dependency declared by the script.
- **Access permissions**: Use the same OpenViking user throughout and ensure that it can create nodes under `viking://resources/onboarding-live-context-v1/`.

Before starting, check the server and identity selected by the CLI and confirm that the MCP management API is available:

```bash
ov health
ov mcps list
```

`health` should report a healthy server. With API key authentication, it should also identify the current Account, User, and role. `mcps list` should return a list; an empty list is fine. Use a User/Admin key with access to the target directory. In `api_key` mode, a Root key cannot perform this walkthrough's data operations. If either command reports a 404, a missing API key, or an unexpected identity, use `ov config` to check and select the correct connection.

This example assumes that the OpenViking server runs directly on your machine alongside the sample MCP server. With a containerized or remote OpenViking server, `127.0.0.1` refers to the server's own environment. Provide a Streamable HTTP MCP address reachable from that environment; see [Live Context Integration and Arrangement](../guides/24-live-context-arrangement.md).

## Start the sample data source

Download the [live context sample package](/quickstart/live-context-demo-v1.zip) and open a terminal in the download directory. Extract and start the sample. The included `status.json` starts with a fictional pending-feedback count of 3:

```bash
unzip live-context-demo-v1.zip
cd live-context-demo
uv run live-context-demo.py
```

Keep this terminal running. The script exposes a `project_status` tool at `http://127.0.0.1:8765/mcp`. Each call reads `status.json` from the script's directory again. The tool only queries data; it does not modify feedback.

If port 8765 is already in use, start with `uv run live-context-demo.py --port 18765` and change the port to `18765` in the subsequent `validate` and `add` commands as well.

Open another terminal in the same directory. Verify that the OpenViking server can connect to the sample and discover its tool:

```bash
ov mcps validate '{"mcpServers":{"onboarding-live-v1":{"url":"http://127.0.0.1:8765/mcp"}}}'
```

Continue when validation succeeds and reports one discovered tool. If the connection fails, check that the sample process is running and that OpenViking runs directly on the same machine.

## Register and mount the node

Register the data source in the current user's tool directory and wait for tool processing:

```bash
ov mcps add '{"mcpServers":{"onboarding-live-v1":{"url":"http://127.0.0.1:8765/mcp"}}}' --wait
ov mcps show onboarding-live-v1
```

Confirm that the tool list contains `project_status` with a string parameter named `project`. `viking://~/tools/` refers to the current user's tool directory. If a repeat run reports that the Server already exists, use `show` to confirm that its address and tool still match this example before continuing.

Mount the tool in the demo directory with Lighthouse as the preset project:

```bash
ov mcps mount \
  viking://~/tools/onboarding-live-v1/project_status.json \
  viking://resources/onboarding-live-context-v1/status.md \
  --title "Lighthouse pending feedback" \
  --description "Query the current pending feedback count for the Lighthouse demo project." \
  --args '{"project":"Lighthouse"}' \
  --wait
```

After mounting, inspect the binding:

```bash
ov mcps mounts --mcp onboarding-live-v1
```

The result should include the `status.md` URI, the `project_status` tool, and the `project=Lighthouse` argument. Mounting the same URI again updates its binding; on repeat runs, confirm that it belongs to this demo.

## Read and observe the change

First, retrieve the node using a business question, without specifying the upstream tool name:

```bash
ov find "How many pending feedback items does Lighthouse have now?" \
  --uri viking://resources/onboarding-live-context-v1
```

Find `status.md` in the results and check that its title and description refer to Lighthouse pending feedback. Retrieval locates the query entry point; read it in full to get the current count:

```bash
ov read viking://resources/onboarding-live-context-v1/status.md
```

The result should contain these fields:

```json
{"project":"Lighthouse","pending_feedback":3}
```

In the second terminal, update the data source and read the same URI again:

```bash
printf '%s\n' '{"pending_feedback":1}' > status.json
ov read viking://resources/onboarding-live-context-v1/status.md
```

`pending_feedback` should now be `1`. You changed only the sample data source, without reimporting a resource, syncing the tool catalog, or changing the mount. OpenViking called the same tool again when you read the node.

“Live” means that reading triggers a query. Data freshness depends on the upstream system. The source does not push changes through this mechanism, and answers an Agent has already produced do not update automatically.

## Let an Agent use the node

If you have completed [Agent onboarding](07-agent-onboarding.md), let the Agent retrieve and read the node itself. It must connect to the same server with an identity that can access both the node and the current user's tool directory. Use an Agent session without a direct connection to the sample MCP Server and enter:

```text
How many pending feedback items does Lighthouse have now?
Search OpenViking under viking://resources/onboarding-live-context-v1/
for relevant context, read the matching node in full, and cite its URI in your answer.
If the read fails, explain the failure instead of reusing the previous count.
```

Check the tool trace: the Agent should retrieve `status.md`, read it in full, and answer with 1 pending feedback item and the node URI. The prompt supplies only a business question and a search scope. It does not provide the upstream tool name, argument format, or full node path; OpenViking handles the binding.

The change from 3 to 1 demonstrates querying the data source at read time. Finding the node through retrieval and reading it through the common interface demonstrates how the Agent uses a mounted query.

## Check the result

| Check | Expected result |
| --- | --- |
| Tool available | `show` includes `project_status` and its parameter definition |
| Correct binding | `mounts` shows the expected node URI, tool, and project argument |
| Node discoverable | Retrieval by business question includes `status.md` |
| First read | Lighthouse has 3 pending feedback items |
| Read after updating the source | The same URI returns 1 without remounting |
| Agent use (optional) | No direct sample MCP connection is needed; the trace includes retrieval and a full read, and the answer includes the latest count and source URI |

## Common issues

| Symptom | What to check |
| --- | --- |
| CLI does not recognize `mcps`, or the server lacks the endpoint | Update both to versions supporting live context; see [CLI Setup](05-cli-setup.md) |
| Validation or reading cannot connect | Keep the sample running and confirm that the MCP address is reachable from the OpenViking server |
| The count is still 3 | Update `status.json` in the sample script's directory, then read again |
| Retrieval does not find the node | Confirm the mount with `mounts`, then check mount processing, indexing, and the search scope; a successful direct read does not prove index readiness |
| Reading returns the node description | Use `ov read` without pagination; paginated reads and API reads with `raw=true` do not execute the upstream tool |
| Reading reports that the tool is not read-only | This sample declares a read-only tool. Tools explicitly marked as non-read-only or destructive require an explicit [tool invocation](../api/26-mcp-live-context.md) |
| The Agent answers with the old count | Check for a new full read and verify the server and user identity |

`find`, `search`, and directory browsing do not call live nodes. An upstream tool error also causes the read to fail; check the data source first.

## Continue using live context

Use this pattern for frequent queries such as project progress, ticket counts, or service status. Queries with stable arguments fit mounted live context; queries whose arguments depend on the question can use tool discovery and invocation. For Studio arrangement, authentication, tool-definition synchronization, and Context as Code, see [Live Context Integration and Arrangement](../guides/24-live-context-arrangement.md).

When finished, unmount this demo node before removing its Server registration:

```bash
ov mcps unmount viking://resources/onboarding-live-context-v1/status.md --wait
ov mcps remove onboarding-live-v1
```

Return to the sample server's terminal and press `Ctrl+C` to stop it.
