---
description: Turn on OpenViking Gateway, issue a gateway key, send a test request and confirm that memory works.
---

# Get started with OpenViking Gateway

This page takes you from a working OpenViking server to a [gateway key](../guides/15-gateway.md#key-terms), a test request through the gateway and proof that memory works. You need an OpenViking installation whose `ov.conf` has embedding and VLM models set up (see [Quick Start](../getting-started/02-quickstart.md)). The gateway is part of the core package, so there is nothing extra to install.

Pick the path that matches your server:

| Your server | Follow |
| --- | --- |
| Bound to `127.0.0.1` in local dev mode (no `root_api_key`) | [Local server](#local-server-no-api-key) |
| Reached over the network with `api_key` authentication | [Remote server](#remote-server-api-keys) |
| An existing OpenViking service whose configuration you do not want to change | [Standalone deployment](../guides/22-gateway-operations.md#standalone-deployment), then come back to [Send a test request](#send-a-test-request) |

## Local server (no API key) {#local-server-no-api-key}

A local server in dev mode needs no OpenViking API key: Studio opens without one, and the gateway key you issue reads and writes memory as the local `default` user.

1. **Turn the gateway on.** Run `openviking-server init` and answer yes to **Enable OpenViking Gateway on the same port?**. With an existing configuration, choose **Update gateway** from the menu. You can also merge `{"gateway": {"enabled": true}}` into `ov.conf` by hand.
2. **Start or restart `openviking-server`.** The gateway now answers on the same port as OpenViking, at `http://127.0.0.1:1933/gateway`.
3. **Open the gateway in Studio.** Open <http://127.0.0.1:1933/studio/> and choose **OpenViking Gateway** in the sidebar. If the default account has no [upstreams](../guides/15-gateway.md#key-terms) yet, the gateway imports the server's VLM settings as upstreams at startup and creates a context profile. You should see the **Get started** checklist on the Overview tab with its first two steps already done (see [Get started checklist and Quick mode](06-manage-in-studio.md#get-started-checklist)). An imported Coding Plan upstream needs your confirmation before use; see [Import OpenViking model settings](07-upstreams.md#import-openviking-model-settings). If nothing could be imported, add an upstream as described in [Upstreams](07-upstreams.md#upstreams).
4. **Issue a gateway key.** On the Overview tab, choose **Get a key** in the **Quick mode** card. Or issue one by hand: on the Keys tab, choose **Issue key**, enter a name, pick the context profile and an upstream, and issue it. You should see the **Copy your gateway key** dialog with the full `ovgw_…` key. It is shown only once, so copy it before you close the dialog.

Your gateway address is `http://127.0.0.1:1933/gateway`. Continue with [Send a test request](#send-a-test-request).

## Remote server (API keys) {#remote-server-api-keys}

A remote server is one that people reach over the network with `api_key` authentication. Each person signs in to Studio with their own OpenViking API key.

If you'd rather not run init on the server host, root can sign in to Studio and choose **Enable gateway** on the OpenViking Gateway page instead of steps 1-3. The confirmation dialog can also generate the gateway management key. See [Enable it in the server](../guides/22-gateway-operations.md#enable-it-in-the-server).

1. **Turn the gateway on.** On the server host, run `openviking-server init`, choose **Remote (0.0.0.0)** for the bind address, and answer yes to **Enable OpenViking Gateway on the same port?**.
2. **Choose who may change upstreams.** The next question, **Allow users to configure their own gateway upstreams?**, decides who may change upstreams and context profiles. Answer yes to let every signed-in user configure their account's upstreams and context profiles. Answer no to protect them with a [gateway management key](../guides/22-gateway-operations.md#gateway-management-key): init generates a key starting with `ovgm_`, prints it and saves it to `gateway.management_key` in `ov.conf`, and only requests that carry this key can change upstreams and context profiles. Keep the printed key; you need it in step 6.
3. **Restart the server.** The gateway now answers on the same port as OpenViking, at `https://ov.example.com/gateway`.
4. **Get an OpenViking API key.** Use your own user API key, or use the root API key to create an account and user as described in [Authentication](../guides/04-authentication.md).
5. **Sign in to Studio.** Open `https://ov.example.com/studio/`, enter your API key under **Connection Settings**, and choose **OpenViking Gateway** in the sidebar. You should see the Overview tab.
6. **Add an upstream.** On the Upstreams tab, choose **Add upstream**, pick the provider and the protocol your client uses (the test request below uses Chat Completions), paste the provider's API key and save. Then choose **Test** in the upstream list. You should see **Connected** or **Model works**, which means the gateway reaches the provider. If the tab is read-only, a gateway management key is set: choose **Enter gateway management key** to unlock editing (see [Unlock editing](06-manage-in-studio.md#unlock-editing)), or ask whoever deployed the gateway.
7. **Create a context profile.** On the Profiles tab, choose **Create with recommended settings**. You should see the new context profile among the cards.
8. **Issue a gateway key** for yourself, as in step 4 of the local path. You should see the **Copy your gateway key** dialog; copy the key now, because it is shown only once. To issue keys for other users, see [Issue a key](09-gateway-keys.md#issue-a-key).

Your gateway address is `https://ov.example.com/gateway`, with your server's address in place of `ov.example.com`.

## Send a test request {#send-a-test-request}

> **Tip**: step 3 of [Confirm that memory works](#confirm-that-memory-works) waits 10 quiet minutes before memories are extracted. To try it faster, first create a context profile with a shorter **Save the latest reply after** (for example 30 s), issue a key with it, and use that key as `GATEWAY_KEY` below. A context profile change reaches only conversations that start afterwards ([when changes take effect](06-manage-in-studio.md#when-changes-take-effect)).

Send two requests with curl: one lists the models the key can use, the other asks a model a question through the gateway. Set `GATEWAY_URL` to your gateway address (`http://127.0.0.1:1933/gateway` for a local server) and replace `<model>` with a model the upstream serves:

```bash
export GATEWAY_URL=https://ov.example.com/gateway
export GATEWAY_KEY='ovgw_...'

curl -s "$GATEWAY_URL/v1/models" -H "Authorization: Bearer $GATEWAY_KEY"

curl -s "$GATEWAY_URL/v1/chat/completions" \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -H "X-OpenViking-Session: quickstart-1" \
  -d '{"model": "<model>", "messages": [{"role": "user", "content": "Remember that I prefer short answers."}]}'
```

| Command | What you should see |
| --- | --- |
| `GET /v1/models` | The model names and aliases of the enabled upstreams bound to the key, limited to the key's allowed models if it has any |
| `POST /v1/chat/completions` | A normal reply from your provider |

The model list can be incomplete or even empty while requests still work: an upstream with an empty model list accepts any model name but adds nothing to this list. The `X-OpenViking-Session` header names the conversation, so the next request with the same value continues it; see [How conversations are recognized](03-connect-clients.md#how-conversations-are-recognized).

## Confirm that memory works {#confirm-that-memory-works}

Check three things in order: the request went through the gateway, the conversation was saved, and memories come back in a new conversation.

1. **The request went through the gateway.** Open the Requests tab. You should see your request as a **New message** with status 200. Once OpenViking has something relevant, the **Memory** column shows how many entries were added, for example `+3`; on a brand-new account it stays empty.
2. **The conversation is saved.** Send a second request with the same `X-OpenViking-Session` value. Its `messages` must hold the whole conversation: your first message, the reply you got back and a new message. Chat clients do this for you; with curl you add them yourself:

   ```bash
   curl -s "$GATEWAY_URL/v1/chat/completions" \
     -H "Authorization: Bearer $GATEWAY_KEY" \
     -H "Content-Type: application/json" \
     -H "X-OpenViking-Session: quickstart-1" \
     -d '{"model": "<model>", "messages": [
       {"role": "user", "content": "Remember that I prefer short answers."},
       {"role": "assistant", "content": "<the reply you got>"},
       {"role": "user", "content": "Thanks."}]}'
   ```

   The first turn is saved as soon as this request arrives ([why saving runs one turn behind](01-how-it-works.md#when-conversations-are-saved)). Open the **Sessions** page in Studio, and you should see a `gateway-…` session. Sessions belong to the key's user: if the key was issued for someone else, sign in under **Connection Settings** with that user's OpenViking API key to see it.
3. **Memories are extracted.** After **Save the latest reply after** (10 minutes by default) passes with no new message, the gateway commits the session, and OpenViking extracts memories in the background, which takes a moment. Then start a new conversation with another session value and ask something that depends on the memory, such as "How long should your answers be?". The **Memory** column should show recalled entries, and the reply should use them.

If the **Memory** column stays empty or no session appears, see [No memory is added](12-troubleshooting.md#no-memory-is-added) and [Conversations do not appear in OpenViking](12-troubleshooting.md#conversations-do-not-appear-in-openviking).

## Next steps {#next-steps}

- [Connect clients](03-connect-clients.md#connect-clients): point Claude Code, Codex CLI or a chat app at the gateway.
- [Issue a key](09-gateway-keys.md#issue-a-key): give other users their own gateway keys.
- [Deployment and operations](../guides/22-gateway-operations.md): run the gateway for a team with Docker Compose, Helm or a standalone deployment.
