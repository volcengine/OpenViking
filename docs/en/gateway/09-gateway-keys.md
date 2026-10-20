---
description: Issue gateway keys that connect clients to OpenViking Gateway, and manage them over time.
---

# Manage gateway keys

This page is for anyone issuing gateway keys: users issuing one for themselves, and account admins issuing keys for others or cleaning up after a user. You will issue a key, copy it into a client, and know what happens to it when it is revoked or its user's OpenViking API key changes. You need at least one [upstream](07-upstreams.md#upstreams) and one [context profile](08-context-profiles.md#context-profiles) first.

## What a gateway key is {#gateway-keys}

In Studio: **OpenViking Gateway** → **Keys**. A gateway key (`ovgw_…`) is what a client uses in place of a provider API key. Each key binds:

- **One OpenViking user.** The gateway recalls and saves memory as that user, with that user's own [OpenViking API key](../guides/22-gateway-operations.md#keys-and-credentials), so a key can never do more than its user; see [Isolation by user](11-security-and-data.md#isolation-by-user).
- **One context profile**, which decides how its conversations recall and save memory.
- **A set of upstreams**, optionally narrowed to a list of allowed models.

The **Keys** tab lists your own keys. Account admins and root see every key in the account, with an **OpenViking user** column. The **Key** column shows only the first characters of each key.

## Issue a key {#issue-a-key}

If the **Keys** tab asks you to add an upstream or create a context profile first, do that, or ask whoever deployed the gateway if you cannot. On a new gateway, **Get a key** in the Overview tab's **Quick mode** card does all of this in one click; see [Get started checklist and Quick mode](06-manage-in-studio.md#get-started-checklist).

1. On the **Keys** tab, choose **Issue key**. The **Issue a gateway key** dialog opens.
2. Enter a **Name** that tells keys apart; the person and the client make a good name, for example "Alice · Claude Code".
3. Under **Who is this for?**, choose the OpenViking user whose memory the key reads and writes; see [Who the key is for](#who-the-key-is-for).
4. Choose a **Context profile** and at least one of the **Upstreams**. The key can reach only the upstreams you select; for each request the gateway picks one of them that speaks the client's protocol and serves the requested model ([Routing](07-upstreams.md#routing)).
5. Optionally fill in **Allowed models**; see [Limit the models a key can use](#allowed-models).
6. Choose **Issue key**. The gateway first checks the user's OpenViking API key with OpenViking. You should see the **Copy your gateway key** dialog. If the dialog shows **Couldn't issue the key** instead, see [Issuing a key fails](12-troubleshooting.md#issuing-a-key-fails).
7. Choose **Copy gateway key**, and copy the setup for Claude Code, Codex CLI or a chat client, which already contains the real key and gateway address. If a client tab says the key has no enabled upstream for its protocol, that client cannot use this key. Choose **I've copied it** when you are done.

The key works as soon as it is issued. The **Copy your gateway key** dialog is the only time you see the full key: the gateway keeps only a hash and the first few characters. If a key is lost, issue a new one. For other clients, see [Connect clients](03-connect-clients.md#connect-clients).

### Who the key is for {#who-the-key-is-for}

The gateway recalls and saves memory as the user you choose under **Who is this for?**. Who you can choose depends on who you are:

| You are | Who you can issue keys for |
| --- | --- |
| A user | Only yourself. Studio shows "Read and write memory as you (&lt;user&gt;)", and you never need to paste a key. |
| An account admin | Any user or admin in your account. |
| Root | Switch account in the sidebar first, then choose a user in that account. A root API key cannot be bound to a gateway key. |
| Anyone, in local dev mode | Keys read and write memory as the `default` user. |

Issuing a key for yourself never needs a pasted key, unless you are root. To issue a key for someone else, the gateway reads that user's API key from OpenViking. If OpenViking stores only key hashes, it cannot: the user is marked "The server can't read this user's API key; paste it instead", and a **Paste &lt;user&gt;'s user API key** field appears for that user's current key. You can also choose **Paste a user API key** to enter a key directly. If the user list cannot be loaded, Studio asks for an **OpenViking API key** instead.

A gateway key lets its holder use the user's memory, so the right to issue keys for others amounts to reading every user's memory in the account; see [Access control](11-security-and-data.md#access-control).

### Limit the models a key can use {#allowed-models}

**Allowed models** is empty by default, which allows every model the key's [upstreams](07-upstreams.md#upstreams) serve. To restrict a key, enter the model names clients send, including aliases. The key's model list then shows only those names, and requests for any other model are rejected before they reach a provider. A key with allowed models also needs every request to name its model; for the exact errors, see [Clients get an error from the gateway](12-troubleshooting.md#clients-get-an-error-from-the-gateway).

## After issuing {#after-issuing}

A [gateway key](#gateway-keys) is fixed once issued, but the conversations it started belong to its user.

- **Keys cannot be edited.** To change a key's context profile, upstreams, allowed models or user, issue a new key and revoke the old one.
- **Conversations belong to the user, not the key.** If the client sends the same session ID, a new key for the same user continues the existing [conversation](03-connect-clients.md#how-conversations-are-recognized), so replacing a key does not interrupt it.
- **Revoking** a key (**Revoke** on the **Keys** tab, then **Revoke key**) stops it working at once. The user's conversation state and logs stay until they are explicitly deleted or expire. Turns not yet saved to OpenViking, such as the latest reply, are saved only if the conversation continues with a new key.

### One key per person {#one-key-per-person}

Memory belongs to the OpenViking user behind a [gateway key](#gateway-keys), so everyone who shares a key shares that user's memory. Issue one key per person. Also issue separate keys per client if you want different context profiles, or want to revoke each client on its own.

### When the user's OpenViking API key changes {#when-the-openviking-key-changes}

Each gateway key depends on its user's current [OpenViking API key](../guides/22-gateway-operations.md#keys-and-credentials). After that key is regenerated or the user is deleted, OpenViking rejects the old key the gateway stored. The gateway notices on a later request (up to about 30 seconds later) and deletes the gateway keys bound to it; conversation state and logs stay. Issue a new key with the current OpenViking API key.

If OpenViking is only unreachable or returns an error, the gateway keeps the key, and requests still reach the model, just without memory.

## Delete a user's gateway data {#delete-a-users-gateway-data}

Revoking keys does not delete what the gateway keeps for a user. To delete it, an account admin or root opens the **…** menu on one of the user's keys on the **Keys** tab, chooses **Delete this user's gateway data…** and confirms with **Delete gateway data**. This:

- revokes all of the user's gateway keys, so their clients lose access at once;
- deletes the conversation state the gateway keeps for them, including content not yet saved to OpenViking;
- deletes their request logs.

Memories already in OpenViking are not affected. For the API call, account-wide deletion and when to use it, see [Deleting data](11-security-and-data.md#deleting-data).

## Next steps {#next-steps}

- [Connect clients](03-connect-clients.md#connect-clients): put the key into Claude Code, Codex or another client.
- [Check the connection](03-connect-clients.md#check-the-connection): confirm requests and memory arrive.
- [OpenViking Gateway security and data](11-security-and-data.md): what the gateway stores for each user and how long.
