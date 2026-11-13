# Import and export

Export a source directory as an OVPack, import it under a new parent or into another OpenViking service, and verify files, metadata, and retrieval.

<a id="connect-a-test-account"></a>

## Connect to the service

Configure the CLI with a user or admin API key supplied by your administrator to connect to OpenViking. OVPack import and export require API Key authentication with `server.root_api_key` configured on the server.

To set up a local service, use a separate configuration file and storage directory, configure the models, set `server.auth_mode` to `api_key`, and configure `server.root_api_key`. Start the service, create an account, and add the returned `user_key` to the CLI configuration. See [API Key setup](../guides/04-authentication.md) and [CLI basics](05-cli-setup.md) for the steps. When using a new account, import the sample sources below into that account first.

## Prepare a source

Connect to the service and complete the import step in [Resource retrieval](08-resources.md#prepare-the-sources). This example uses `viking://resources/onboarding-en-v1`. You need read access to the source and write access to the target parent.

```bash
ov tree viking://resources/onboarding-en-v1
ov export viking://resources/onboarding-en-v1 ./lighthouse.ovpack
```

The package contains the selected directory's source files, semantic sidecars, and portable index scalars. This tutorial uses the default export and recomputes vectors on import, calling the destination's embedding model. See [OVPack reference](../api/14-ovpack.md) for full-account backup and restore.

## Import into a new location

```bash
ov mkdir viking://resources/onboarding-transfer-v1
ov import ./lighthouse.ovpack viking://resources/onboarding-transfer-v1 --vector-mode recompute
```

The second argument is the **target parent**. Save the actual root URI returned by import and use it below. This example normally produces `viking://resources/onboarding-transfer-v1/onboarding-en-v1`.

The default conflict policy is `fail`. If the target root exists, choose a new demo parent. `overwrite` replaces an existing root; `skip` preserves it. A new directory makes repeat runs easy to compare.

## Verify content and indexing

```bash
ov tree viking://resources/onboarding-transfer-v1/onboarding-en-v1
ov read viking://resources/onboarding-transfer-v1/onboarding-en-v1/project/project.md
ov read viking://resources/onboarding-transfer-v1/onboarding-en-v1/meeting/meeting.md
ov wait
```

Compare the four relative source paths, their text, and preserved abstracts and overviews. Vector processing may continue after import returns. Use `ov wait` to wait for queued processing; it may also wait for other queued work on the service. If the request times out, check `ov status` and retry later. Search once processing completes without related errors.

```bash
ov find "What must be prepared before the Lighthouse pilot?" --uri viking://resources/onboarding-transfer-v1/onboarding-en-v1
```

Completion means source content matches, required metadata is readable, and relevant material can be retrieved from the new directory. Ranking and scores may vary with destination models and indexing; check whether retrieved sources support the question.

<a id="migrate-from-service-a-to-service-b"></a>

## Migrate between services

For example, move the Lighthouse sources from development service A to test service B. The computer running the CLI transfers the data: download an `.ovpack` from A, then upload it to B. The two services do not need a direct connection.

This example transfers a selected resource directory and keeps the original data in A. Configure B's models, accounts, and credentials separately. See [OVPack reference](../api/14-ovpack.md) for full-account backup and restore.

### Configure both connections

Prepare two services with OVPack support and the authentication setup described above. The account on A needs source read access; the account on B needs target-parent write access. B also needs a working Embedding model. Confirm that B supports the package format exported by A, and pause edits to the sample directory during export so its contents can be compared.

Create two CLI configuration files in one local working directory. Enter each service's actual URL and its account's API key. The following addresses are examples:

`ov-a.conf`:

```json
{
  "url": "https://openviking-a.example.com",
  "api_key": "your-service-a-user-key"
}
```

`ov-b.conf`:

```json
{
  "url": "https://openviking-b.example.com",
  "api_key": "your-service-b-user-key"
}
```

In a macOS / Linux terminal, use `OPENVIKING_CLI_CONFIG_FILE` to select the connection for each command. Restrict access to the configuration files, then check connectivity to both services:

```bash
chmod 600 ./ov-a.conf ./ov-b.conf
OPENVIKING_CLI_CONFIG_FILE=./ov-a.conf ov health
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov health
```

Run the remaining commands from this working directory. Keep these files in a controlled local location because they contain access credentials.

### Export from A

Confirm that A contains the [Lighthouse sources](08-resources.md#prepare-the-sources), then download the directory to your computer:

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-a.conf ov tree viking://resources/onboarding-en-v1
OPENVIKING_CLI_CONFIG_FILE=./ov-a.conf ov export viking://resources/onboarding-en-v1 ./lighthouse-a-to-b.ovpack
```

On success, `lighthouse-a-to-b.ovpack` appears in the current directory. If you use a different computer to connect to B, transfer this file to it; keep A's credentials on the original computer.

### Import into B

Create a new migration parent in B and import the local package:

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov mkdir viking://resources/from-a-v1
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov import ./lighthouse-a-to-b.ovpack viking://resources/from-a-v1 --on-conflict fail --vector-mode recompute
```

With `recompute`, B generates vectors using its own Embedding model, accommodating different model configurations across environments. Import calls B's model and incurs its associated costs. If the target root already exists, choose a new parent and retry.

Record the actual root URI returned by import. This example normally produces `viking://resources/from-a-v1/onboarding-en-v1`, which the commands below use.

### Verify in B

Inspect the directory and source text, then wait for background vector processing on B:

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov tree viking://resources/from-a-v1/onboarding-en-v1
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov read viking://resources/from-a-v1/onboarding-en-v1/project/project.md
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov wait
```

`ov wait` waits for queued processing and may also be affected by other queued work on B. If the request times out, inspect B's status:

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov status
```

Wait again later if processing continues. For errors, inspect B's model and storage configuration and server logs. Once processing completes without related errors, search in B:

```bash
OPENVIKING_CLI_CONFIG_FILE=./ov-b.conf ov find "What must be prepared before the Lighthouse pilot?" --uri viking://resources/from-a-v1/onboarding-en-v1
```

| Check | Success criterion |
| --- | --- |
| Files and text | The four source files in B have the same relative paths and text as in A |
| Abstracts and overviews | Semantic sidecars preserved in the package are readable in B |
| Retrieval | Relevant preparation material is found, and the source at each result URI can be read |
| References and integration | Absolute URIs, service addresses in the text, and the agent's search scope have been checked against their actual locations in B |

After verification, update the application or agent with B's service URL, credentials, and resource search scope. Ask the same question and check the answer and citations to confirm that the application uses B's data. Keep the original directory in A for comparison and any later cutover arrangements.

## Package and conflict checks

The manifest records files, sizes, and checksums. Missing files, checksum mismatches, or unsupported format versions cause import to fail. Export again and check the transfer if needed. See [OVPack reference](../api/14-ovpack.md) for vector compatibility, `auto`/`require` modes, and archive validation.

Keep the package for comparison if useful. To clean up, identify the imported root under the new parent in Studio and delete the demo content. The original source remains available.
