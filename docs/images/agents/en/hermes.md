## Step 1: Install

1. Run the following command to start the OpenViking memory setup wizard:

   ```bash
   hermes memory setup openviking
   ```

2. The wizard first asks for the configuration source:

   ```text
   OpenViking config source
     ↑↓ navigate  ENTER/SPACE select  ESC cancel
    → (●) Use existing OpenViking profile - choose from detected ovcli.conf profiles
      (○) Create new OpenViking profile - enter a new URL/API key
   ```

   Options:

   - Reuse an existing Profile: read the OpenViking URL and secret from a local `ovcli.conf`, with no need to enter them again.
   - Create a new Profile: manually provide the OpenViking service endpoint and credentials. Use this for first-time setup or when connecting to a new instance.

3. If you choose **Create new OpenViking profile**, select **OpenViking Service (VolcEngine Cloud)** when asked for the connection type:

   ```text
   OpenViking connection
     ↑↓ navigate  ENTER/SPACE select  ESC cancel

    → (●) OpenViking Service (VolcEngine Cloud) - use the managed OpenViking endpoint
      (○) Custom - use a local, VPS, or self-hosted OpenViking server
   ```

4. Enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

5. Fill in **Hermes peer ID in OpenViking**. This identifies the Hermes Agent in OpenViking so memories produced by different Agents can be separated. Press Enter to use the default `hermes`, or enter a custom value.
6. Choose how to save the configuration. We recommend **Mirror to OpenViking store**:

   ```text
   Save OpenViking config
     ↑↓ navigate  ENTER/SPACE select  ESC cancel
      (○) Keep in Hermes only - write values only to Hermes .env
    → (●) Mirror to OpenViking store - write ~/.openviking/ovcli.conf.<name> and link it
   ```

7. Fill in **OpenViking profile name**. Hermes' multi-tenant capabilities can isolate models, memories, configuration, and credentials across Profiles. We recommend configuring an independent OpenViking environment or identity for each Hermes Profile, and using an easy-to-recognize local name here. This name is local only; it does not create a new user or change account identity or permissions.
8. When setup completes, Hermes shows:

   ```text
   OpenViking memory is ready
     Created and linked OpenViking profile.
     Config file: ~/.openviking/ovcli.conf.hermes
     Start a new Hermes session to activate.
   ```

## Step 2: Verify

1. Check the memory plugin status:

   ```bash
   hermes memory status
   ```

2. A result similar to the following means the integration is successful:

   ```text
   Memory status
   ────────────────────────────────────────
     Built-in (MEMORY.md / USER.md):
       Memory injection:   enabled ✓
       User profile:       enabled ✓
       Memory tool:        enabled ✓
     Provider:  openviking

     openviking config:
       use_ovcli_config: True
       ovcli_config_path: ~/.openviking/ovcli.conf.hermes
       endpoint: `https://api.vikingdb.cn-beijing.volces.com/openviking`
       agent: hermes

     Plugin:    installed ✓
     Status:    available ✓

     Installed plugins:
       • byterover  (API key / local)
       • hindsight  (API key / local)
       • holographic  (local)
       • honcho  (API key / local)
       • mem0  (API key / local)
       • openviking  (API key / local) ← active
       • retaindb  (API key / local)
       • supermemory  (requires API key)
   ```

## Troubleshoot

| Problem | Fix |
|---|---|
| Provider is not openviking | Re-run `hermes memory setup openviking` |
| Status is not available | Check the API key |

## Reference

- Docs on Manual Settings: [Hermes](https://docs.openviking.net/en/agent-integrations/05-hermes)
- Blog about how it works: [OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)
