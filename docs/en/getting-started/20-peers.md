# Peer

A Peer is a stable interaction counterpart under the current user, such as a person or another agent. A consistent `peer_id` groups related sources and memories so later conversations can reuse them.

For example, Alice's assistant often collaborates with `review-agent`. Alice's own preferences belong in user memory; preferences associated with review-agent can be stored in that Peer's memory.

## Identity and counterpart

| Concept | Meaning in this example |
| --- | --- |
| Account: `acme` | Alice's team |
| User: `alice` | Data owner and service-access identity |
| Peer: `review-agent` | Alice's stable counterpart |
| Session | One conversation with that counterpart |

Review-agent's content lives under `viking://user/alice/peers/review-agent/`. A Peer ID does not create a separate User or issue credentials. Even a Peer named `bob` remains a counterpart in Alice's space, separate from the real user Bob's personal space.

## Prerequisites

Complete [Authentication](17-authentication.md) and use the `acme-alice` connection. This example first checks Peer source scoping, then demonstrates memory extraction. Source writes and extraction require working server models and incur model calls.

Keep a stable ID for the same counterpart, such as `review-agent`. Use a single path segment without `/`. When repeating the walkthrough, choose a new session ID while keeping the Peer ID to retain continuity.

## Save counterpart sources

Alice creates source directories for two counterparts:

```bash
ov config switch acme-alice
ov mkdir viking://~/peers/review-agent/resources
ov mkdir viking://~/peers/research-agent/resources
```

Write a collaboration note for review-agent:

```bash
ov write viking://~/peers/review-agent/resources/brief.md \
  --mode create --content "review-agent handles reviews. Organize recommendations into Issue, Impact, and Recommendation columns." --wait
```

After processing finishes, read the source to check its content:

```bash
ov read viking://~/peers/review-agent/resources/brief.md
```

If waiting times out, retain the returned task ID and check it with `ov task status <task_id>`. Continue after completion.

## Scope requests to a Peer

With Alice's connection, use `actor_peer_id` to restrict a request's Peer collection to review-agent:

```bash
ov --actor-peer-id review-agent ls viking://~/peers
ov --actor-peer-id review-agent read viking://~/peers/review-agent/resources/brief.md
```

The listing should contain only the allowed Peer, and reading its note should succeed. Try accessing the other Peer:

```bash
ov --actor-peer-id review-agent ls viking://~/peers/research-agent/resources
```

Access should be denied. To retrieve review-agent's sources by question, target its directory:

```bash
ov --actor-peer-id review-agent find "What format should review recommendations use?" \
  --uri viking://~/peers/review-agent/resources
```

Actor Peer restricts only the current user's Peer collection. User-level memories, sources, and accessible shared sources retain their existing visibility, and sessions remain owned by Alice. For external participants, a trusted entry point should select the Peer scope; a client holding Alice's full key can change or omit it. Use distinct User identities for independent user permissions.

## Save Peer memory

A message's `peer_id` identifies its counterpart; `actor_peer_id` scopes request access. Set them separately. This example extracts only the counterpart's preferences, creating a session through HTTP with an explicit memory policy.

In Alice's terminal, set the service address and enter her User key with hidden input:

```bash
export OV_URL="http://127.0.0.1:1933"
read -r -s ALICE_KEY
export ALICE_KEY
```

Create a session with Peer memory enabled, Self extraction disabled for this session, and manual commits:

```bash
curl -sS -X POST "$OV_URL/api/v1/sessions" \
  -H "X-API-Key: $ALICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"peer-review-a","auto_commit_policy":null,"memory_policy":{"self":{"enabled":false},"peer":{"enabled":true},"memory_types":["preferences"]}}'
```

After confirming success, record and commit a message using Alice's CLI connection:

```bash
ov session add-message peer-review-a --role user --peer-id review-agent \
  --content "I am review-agent. I prefer review recommendations in a table with Issue, Impact, and Recommendation columns. Please use this format in future reviews with me."
ov session commit peer-review-a
```

Retain the returned `task_id` and `archive_uri`. Check every five seconds for up to two minutes initially; if still running, retain the ID and check later. Inspect the cause if the task is `failed` or `cancelled`:

```bash
ov task status <task_id>
```

Once `completed`, read the change record:

```bash
ov read "<archive_uri>/memory_diff.json"
```

Find the memory URI in `operations.adds` or `operations.updates` and read it with `ov read`. Expect a record under `viking://user/alice/peers/review-agent/memories/` retaining the three-column preference. Filenames depend on extraction. If nothing was added, check for an existing memory update and whether the active template supports Peer preferences.

## Use it in later tasks

In a new conversation, have an agent using Alice's connection retrieve the Peer's memory first:

```bash
ov --actor-peer-id review-agent find "review-agent's preferred review format" \
  --uri viking://~/peers/review-agent/memories
```

Read the matching memory, then ask the agent to produce a review table following the preference and cite the memory URI. Keep tagging relevant messages with `review-agent` in new sessions so later commits can accumulate its memories.

## Next steps

Peer content belongs to the current user. Continue to [Knowledge sharing](22-sharing.md) to make selected knowledge available to teammates. See [Multi-tenancy](../concepts/11-multi-tenant.md#peer-restricted-view) for complete filtering rules.
