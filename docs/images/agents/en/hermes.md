# OpenViking Service for Hermes

[OpenViking Service](https://www.volcengine.com/product/openviking-service) is
OpenViking hosted and operated by VolcEngine. You do not need to install a server
or configure local models. Activate the service and create an API key in the
[OpenViking console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing).
Open **User Management**, then **API Key**.

## Set up

Run these commands in the Hermes profile you want to use:

```bash
hermes plugins install openviking --enable
hermes memory setup openviking
```

Accept the dependency prompt during installation. Skip the install command
if your Hermes release already includes OpenViking.

In the wizard:

1. Choose **Personal Agent** to recall common memory and the current sender's
   memory while keeping history settings. Choose **Shared Agent** to share
   history within each group or thread and recall all senders' memories under
   the same OpenViking user. Shared Agent asks for confirmation.
2. If prompted, choose **Create new OpenViking profile**. You can also reuse
   a saved `ovcli.conf`.
3. Choose **OpenViking Service (VolcEngine Cloud)**.
4. Enter your service API key.
5. Choose where to save the connection. **Keep in Hermes only** saves it in
   the Hermes `.env`. **Mirror to OpenViking store** saves a local
   `ovcli.conf.<name>` and links Hermes to it. Both save credentials on this
   computer.
6. For a saved OpenViking profile, enter a name such as `hermes`. This is a
   local config name. It does not create a user or change access rights.

Then start a new Hermes session:

```bash
hermes
```

For a local server, the external plugin also offers **Quick Local**. It reuses
a supported Hermes language model and installs a local server and embedding model.
See the [plugin guide](https://hermes-agent.nousresearch.com/docs/plugins/openviking)
for requirements and known issues.

## Check status

```bash
hermes memory status
```

Confirm that the provider is `openviking` and its status is `available`.
This confirms saved configuration, not server health or successful extraction.

## Troubleshoot

| Problem | What to do |
|---------|------------|
| Plugin is missing | Run `hermes plugins install openviking --enable` in this profile. |
| Another provider is selected | Run `hermes memory setup openviking` again. |
| Status is not available | Check the saved connection settings and any linked `ovcli.conf`. |

## More information

- [Hermes integration](https://docs.openviking.net/en/agent-integrations/05-hermes)
- [Plugin guide](https://hermes-agent.nousresearch.com/docs/plugins/openviking)
